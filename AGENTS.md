# AGENTS.md

## Repo status
- **Code-complete; not yet run end-to-end on real data.** Concretely:
  - **Loop layer** (`harness/`): done — 6 files + 61 pytest, all passing.
  - **Pipeline scripts** (`skills/cell-annotation/scripts/`): code done — 7 `stepN_*.py` + `common.py` + `write_judgment.py` + `trajectory_schema.py`, 4,092 lines total, every script supports `--dump-schema`. Pipeline has NOT been run end-to-end on real data — `output/` directory does not yet exist, no `run_log.jsonl`, no `processed.h5ad`.
  - **Eval tooling** (`scripts/`): done — `validate_log.py` (254) / `evaluate_annotations.py` (218) / `build_gt_cells.py` (100) / `build_label_map.py` (144), 716 lines total.
  - **Experiment framework** (`experiments/`): code done — `scripted_driver.py` (127) / `evaluate_cell_level.py` (374) / `bootstrap_test.py` (220) / `analyze_traps.py` (179) / `judges/_common.py` (160) / `judges/default_judge.py` (153) / `judges/rule_judge.py` (314), 1,527 lines total. None has been executed end-to-end. `gt_cells.csv` + `label_map.json` are pre-generated.
  - **Skill packaging** (`skills/cell-annotation/`): `SKILL.md` + `references/` (4 files) + `assets/` (2 files) + `evals/evals.json` (gitignored) present. The skill has NOT been load-tested against an LLM agent.
  - **Dataset / KG / gene-name map / `.env`**: wired and ready (Neo4j reachable per `.env`).
  - **两层配置分离**（重构后）：
    - `harness/config.py`：负责 harness 包自身配置（LLM gateway + RAG toggles；6 项 key），加载项目根 `.env`。任何 `from harness.X import ...` 触发加载。Disable：`export SC_HARNESS_SKIP_DOTENV=1`。
    - `skills/cell-annotation/scripts/common.py:load_skill_dotenv()`：负责 cell-annotation skill 的**环境类**配置（Neo4j 连接、KG 服务调优；7 项 key），加载 `skills/cell-annotation/.env`。任何 step 脚本 import `common` 时触发。Disable：`export CELL_ANNOTATION_SKIP_DOTENV=1`。
    - **harness 完全不知道 cell-annotation skill 的存在**（domain-agnostic）。未来增加 skill时该 skill 自己拥有并加载自己的 `.env`，harness 不变。
    - 完整 key 清单：`harness/config.py:RECOGNIZED_KEYS` (harness) / `skills/cell-annotation/scripts/common.py:SKILL_DOTENV_KEYS` (skill)。文档 `docs/CONFIGURATION_REFERENCE.md` §2 / §3.0。
    - **LLM 工具参数 vs skill 配置 区分原则**：任务类参数（生物决策：step3_kg 的 `--organ` / `--species` / `--species-type` / `--strict-organ`）保留在 argparse 中供 LLM 传递；环境类参数（KG 服务/资源：step3_kg 的 `--min-confidence` / `--gene-key` / `--max-ancestor-hops` + Neo4j `--uri/--user/--password` + `--project-dir` / `--input`）加 `argparse.SUPPRESS` 隐藏，从代码默认值回落（CLI 可临时覆盖）。**已落地：step3_kg**（参考 `harness/tests/test_step3_kg_schema.py`）；其它 step 后续独立重构。
      - 实现 helper：`common.env_or_default(args, name, env_keys, default, cast)` —— SUPPRESS-aware 读取器，CLI > env > 默认。
      - 实现 loader 耦合：`common.arg_spec` 现在把 `default=SUPPRESS` 或 `help=SUPPRESS` 的 action 返回 `None`，从 tool schema 里彻底剔除。
- **What is missing is runtime, not code**: no real-data run produces any `run_log.jsonl` / `processed.h5ad` / `final_annotations.json` / confusion matrix. The next blocking step is **step1_prepare end-to-end smoke** (see "Next steps" below), not writing new code.
- Test tooling EXISTS: `pytest.ini` (`testpaths = harness/tests`), `harness/tests/conftest.py`, and a suite that runs with `python -m pytest` (**90 tests passing** as of last run, including 15 for step3_kg B-class schema discipline). Don't assume tests don't work — run them. `.opencode/package.json` only pins the opencode plugin and that dir is gitignored.
- **Known runtime risks** (from `implementation_plan.md` §11 and design docs): `scrublet` may be missing (affects `step1_prepare.detect_doublets`) — verify with `python -c "import scrublet"` before relying on it; `h5ad` is 2 GB so full-load runs need real memory and time budget.
- **Verification habit**: this project mixes "design says X" and "code does X" — they can drift. Before stating a status claim, `ls` / `wc -l` / `grep` to confirm. AGENTS.md itself has historically understated the breadth of completed code (the experiments/ scaffolding is fully written, not just "partial"); do not infer status from prose summaries.

## Architecture (from design/)
Single-cell RNA-seq cell-type annotation harness driven by an LLM agent. Three layers:
- **Loop** — domain-agnostic runner (LangGraph). Loads a Skill, runs LLM↔tool loop, writes `conversation.jsonl`. Deliberately minimal: **no context management** (messages only grow), **no domain knowledge hardcoded**. Don't over-engineer it. (`design/loop_design.md`, `readme.md`)
- **Skill** — domain spec: `system_prompt` + `tool_schemas` + `tool_runtime`. Tool types: `subprocess` (CLI script must print final stdout line as JSON `{"status":"ok","data":{...}}` / `{"status":"error",...}`) or `function` (importlib import).
- **Pipeline** — 7 stage CLI scripts: `step1_prepare`→`step2_markers`→`step3_kg`→`step4_judge`→`step5_refine`→`step6_validate`→`step7_diagnose`. 47 atomic ops; the op id `stepN_name.op_name` is significant (used in `run_id`).
- Cross-species annotation is intentionally skipped. (`readme.md`)

## Trajectory / logging
- `run_log.jsonl` is the SINGLE trajectory file: append-only NDJSON at `<project-dir>/run_log.jsonl`. Don't invent parallel log files.
- Record types: `session_start` / `exec` / `judgment` / `session_end`. `run_id` = `{step}.{op}#{attempt}`. "Current" value = highest `seq` for that run_id prefix.
- `exec` records are written automatically by pipeline scripts; `judgment` records are written by the LLM (fields: `decision_point`, `scope`, `run_ref`, `inputs[]`, `output`, `reasoning`). (`design/trajectory_design.md`)
- **Shared schema module:** `skills/cell-annotation/scripts/trajectory_schema.py` owns `REQUIRED_SCOPE` (decision point → required scope type, 9 session + 4 cluster). Both `write_judgment.py` (skill) and `scripts/validate_log.py` (eval tooling) import it. Don't fork copies — extend the shared module.

## Data / I/O
- h5ad files are GB-scale. Central rule: **one subcommand = one h5ad load**; compute every metric needed for that load in-passing. 48% of ops need no h5ad (pure JSON). Sidecars `obs_snapshot.csv` / `var_snapshot.csv` (written alongside `processed.h5ad`) replace many h5ad reads. Don't add casual h5ad loads. (`design/tool_design.md`)
- Dataset is *Arabidopsis thaliana* root (plant): QC uses **chloroplast** genes (`pct_counts_chloroplast`) alongside mitochondrial; plant-specific filters apply.
- `dataset/init.py` ingestion conventions: force `adata.X` sparse; rebuild `adata.raw` for old scanpy versions (when `_index` is in `raw.var`); reject numeric `var_names` (must be gene symbols). Ground-truth labels live in `dataset/index/*.csv` (columns `Seurat_clusters`, `Celltype`, pulled out of the h5ad).

## Directory map
- `harness/` — domain-agnostic loop package (real code: `loop.py`, `dispatcher.py`, `skill_loader.py`, `notebook.py`, `session.py`, `conversation.py`) + `harness/tests/` (pytest suite, 61 tests).
- `skills/cell-annotation/` — the skill package: `SKILL.md` (<500 lines, Chinese, 13 decision points + 6 traps + log guide), `references/` (sop / metrics / traps / kg-schema), `assets/` (report templates), `evals/evals.json` (gitignored test fixtures), and `scripts/`:
  - `common.py` (840 lines) — 9 generic functions (`describe_distribution`, `filter_funnel`, `effect_size`, `pairwise_overlap`, `batch_mixing`, `cluster_quality`, `variance_explained`, `resolution_stability`, `candidate_autocorr`) + `append_log` / `next_run_id` / `current_metrics` / `dump_schema` / `arg_spec` / JSON envelope (`ok` / `fail` / `emit`) + `add_common_args` / `add_neo4j_args`.
  - `step1_prepare.py` (777) — subcommands `metrics` (1× raw load, pre-filter distributions), `run` (1× raw load, all 16 ops + obs_snapshot.csv / var_snapshot.csv sidecars), `recluster` (1× proc load).
  - `step2_markers.py` (453) — `run` (1× proc load; DE + AUC + pct1/pct2 + filter funnel + pseudobulk for rare clusters).
  - `step3_kg.py` (485) — `query` (0 h5ad; Neo4j lookup), `test-connection`.
  - `step4_judge.py` (214) — `run` (0 h5ad; pure JSON; emits an LLM-ready decision view per cluster).
  - `step5_refine.py` (411) — `run` (1× proc load; subset to ambiguous clusters → subcluster → sub-DE → sub-KG → overlap).
  - `step6_validate.py` (417) — `run` (1× proc load, optionally backed-mode per-column read for top-3 markers), `report` (0 h5ad).
  - `step7_diagnose.py` (221) — `run` (0 h5ad; reads obs_snapshot.csv + JSONs).
  - `write_judgment.py` (252) — `add` / `session-start` / `session-end`; validates decision enum + scope via `REQUIRED_SCOPE`.
  - `trajectory_schema.py` (22) — `REQUIRED_SCOPE` dict (13 decision points → required scope type, 9 session + 4 cluster). Single source of truth.
  - Every script implements `--dump-schema` (hidden flag) so the skill loader can derive `tool_schemas` / `tool_runtime` from argparse — no hand-written JSON to drift.
- `scripts/` (root, NOT part of the skill package) — P5 eval-loop tooling: `validate_log.py` (254) / `evaluate_annotations.py` (218) / `build_gt_cells.py` (100) / `build_label_map.py` (144). These import the shared schema from `skills/cell-annotation/scripts/trajectory_schema.py`.
- `experiments/` — P6 experiment framework (1,527 lines), all written, none run end-to-end:
  - `scripted_driver.py` (127) — B1 deterministic DAG driver for arm1/arm2; calls `dispatcher.dispatch` directly, bypasses LangGraph loop.
  - `judges/default_judge.py` (153) — ① Fixed-default judge (no-op, all accept).
  - `judges/rule_judge.py` (314) — ② Rule-based judge; oracle table → if-then.
  - `judges/_common.py` (160) — shared helpers for judges.
  - `evaluate_cell_level.py` (374) — cell-level accuracy / macro-F1 / confusion / purity diagnostics from `final_annotations.json` + `gt_cells.csv` + `label_map.json`.
  - `bootstrap_test.py` (220) — cluster-aware bootstrap (1000 resamples, 95% CI on macro-F1 delta).
  - `analyze_traps.py` (179) — trap oracle comparison (per-trap ③ vs ② correctness).
  - Pre-generated: `gt_cells.csv` (cell barcode → true type, 33,956 rows), `label_map.json` (12 ground-truth types mapped to KG ontology terms, locked version with `_meta.verified`).
- Tests live in `harness/tests/` and run via `python -m pytest` (61 passing).

## External deps
- **Neo4j knowledge graph**：凭据 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`在 `skills/cell-annotation/.env`（gitignored）设置，不在项目根 `.env`。模板 `skills/cell-annotation/.env.example` 跟踪进 git。优先级：CLI flags > `.env` > `.env.example` > 硬编码默认。绝不硬编码密码。（`design/tool_design.md` §10）
- **`docs/CONFIGURATION_REFERENCE.md` §2.1 / §2.2 / §3.0** 列出全部 env var（harness 自身 vs skill 配置）。loader 逻辑：`harness/config.py` （harness 自身）、`skills/cell-annotation/scripts/common.py:load_skill_dotenv()` （cell-annotation 专用）。

## Gitignore gotchas
Root `.gitignore` ignores `*.json`, `*.csv`, `*.h5ad`, `*.png`, `*.ipynb`, `*.xmind`, `*.ai`, `.env`, `.opencode*`. Consequences:
- Tracked content is essentially only `*.md` (under `design/`, `knowledge/`), `readme.md`, `.gitignore`, `pytest.ini`, `harness/**/*.py`, `skills/cell-annotation/scripts/*.py`, `scripts/*.py`, `dataset/init.py`, and `_bmad-output/implementation-artifacts/*.md`.
- `name_map4Arabidopsis_thaliana_symbol.json`, `dataset/h5ad/*.h5ad`, `dataset/index/*.csv`, `output/`, and `.opencode/` all exist locally but are NOT tracked. New `.json`/`.csv` files you create are ignored unless force-added.
- Note: `evals/evals.json` under the skill is gitignored by `*.json` (evals are test fixtures, not source).

## Language convention
- Design docs and knowledge files are in **Simplified Chinese** — match Chinese when editing them. Code identifiers and docstrings are English (see `dataset/init.py`). LLM-facing prompts are expected to be Chinese.

## Where to look
- `design/loop_design.md` (RUN — the loop, including the L-1..L-6 implementation phases, all completed).
- `design/tool_design.md` (HOW — impl + data deps + subcommand structure; one-subcommand-one-load rule).
- `design/atomic_operations.md` (WHAT — 47 ops + DAG).
- `design/operations_metrics_catalog.md` (247 metrics per op).
- `design/trajectory_design.md` (LOG — run_log.jsonl format + record types + judgment schema).
- `design/rag_design.md` (MEM — loop-level notebook, generic, not skill-bound).
- `design/implementation_plan.md` (project plan, P1..P7 milestones; current status: P1–P4 code-complete, P5+ pending).
- `design/experiment_design.md` + `design/experiment_implementation.md` (12 experiments, pre-registered decision rules, cell-level eval + cluster-aware bootstrap).
- `knowledge/` (`cell-annotation-sop.md`, `cross-species-annotation-handbook.md`, `kg_schema.md`, `metrics_interpretation.md`) = source material for the skill's system prompt + references.
- `docs/PROJECT_OVERVIEW.md` — résumé-oriented project write-up (architecture, status, keyword matrix for different JD directions).

## Next steps (runtime, not coding)

Code is complete. The bottleneck is **actually running the pipeline on real data** so we get a `run_log.jsonl` to analyze. Ordered by blocking dependency + risk:

1. **step1_prepare smoke (top priority).** Slice a 1–2K-cell subset h5ad (`dataset/h5ad/SRP171040_smoke.h5ad`), then run `step1_prepare metrics` + `step1_prepare run` against it. This is the biggest unknown (2 GB h5ad, multi-resolution Leiden, scrublet dependency, scanpy version). Validate outputs: `processed.h5ad`, `obs_snapshot.csv`, `var_snapshot.csv`, `qc_metrics.json`, `run_log.jsonl`. Run `scripts/validate_log.py --mode mini` to check the trajectory shape.
2. **Verify scrublet in parallel** with step 1: `python -c "import scrublet"`. If `ImportError`, add a downgrade branch in `step1_prepare.detect_doublets` and tag `run_log` with `de_method_fallback`.
3. **Full step1→step7 chain** on the real 33,956-cell h5ad (after smoke passes). Validate each step's JSON outputs + exec records in `run_log.jsonl`. End with `validate_log.py --mode e2e`.
4. **B1 arm1 (default) + arm2 (rule) end-to-end** via `experiments/scripted_driver.py` + `experiments/judges/{default,rule}_judge.py`. Both are deterministic, no API cost. Run `experiments/evaluate_cell_level.py` + `bootstrap_test.py` to get the baseline macro-F1.
5. **B1 arm3 (LLM-judge)** via `python -m harness.session --skill cell-annotation --project-dir output_arm3`. Needs API key + cell-annotation skill load-tested once. This is the experiment that validates the project's existence reason.
6. **B3 / B4 trajectory analysis** on arm3's `run_log.jsonl` (inputs[].path frequency → minimal sufficient set; same-decision-point multi-judgment → self-correction pairs).
7. **SKILL.md / references iteration** driven by evals/real-output feedback, then `.skill` packaging (M7).

Estimated wall-clock: ~1 week from "code-complete" to "B1 three-arm data in hand".

Tests: `harness/tests/` — run `python -m pytest` (61 passing). `harness/tests/conftest.py` documents fixtures (FakeEmbedder replaces the real embedding model).
