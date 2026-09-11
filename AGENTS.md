# AGENTS.md

## Repo status
- **P1–P6 code-complete AND run end-to-end on real data; P7 (.skill packaging + SKILL iteration) pending.** Concrete status (verified `2026-08`):
  - **P1 Loop layer** (`harness/`): done — 6 files + 89 pytest, all passing (`python -m pytest`, 89 passed).
  - **P2 Pipeline scripts** (`skills/cell-annotation/scripts/`): code done — 7 `stepN_*.py` + `common.py` (1019 lines) + `write_judgment.py` (252) + `trajectory_schema.py` (22), 4,265 lines total, every script supports `--dump-schema`. **47 atomic ops all implemented and exercised end-to-end** on the 33,956-cell Arabidopsis root h5ad (run artifacts in `output/p2_smoke/`, `output/p2/`, `output/p2v2/`, `output/p2_dbg/`).
  - **P3 Skill packaging** (`skills/cell-annotation/`): done — `SKILL.md` (243 lines, <500) + `references/` (sop / metrics / traps / kg-schema, all with TOC) + `assets/` (2 files). Skill has been load-tested in the r2 P5 closure (see `output/p5_evals_r2/`).
  - **P4 Data substrate**: done — `experiments/gt_cells.csv` (33,956 rows; barcode → true type) + `experiments/label_map.json` (12 ground-truth types mapped to KG ontology terms, `_meta.verified`).
  - **P5 Skill eval loop** (round 1 + r2 closure): done — `skills/cell-annotation/evals/evals.json` (5 cases E-1..E-5) + `output/p5_evals*/run_log.jsonl`. r2 closure report (`_bmad-output/implementation-artifacts/p5-evals-round1-closure.md`): E-1 strict=0.9236 / relaxed=0.9434, E-5 PASS (117 judgments all compliant). r1→r2 fixed a skill knowledge gap (KG species format vs LLM-passed `--species`); see `references/kg-schema.md` "物种过滤与命名" section.
  - **P6 Experiments** (`experiments/`): code done (1,527 lines) **AND executed end-to-end**. B1 three-arm outputs exist at `output/B1/{arm1_default,arm2_rule,arm3_llm}/run_log.jsonl` and `output/B1/eval/{evaluation_report,bootstrap_report,traps_report}.json`. Headline numbers (from `_bmad-output/implementation-artifacts/b1-three-arm-eval.md`):
    | arm | strict | relaxed | macro-F1 | low-conf cells |
    |---|---|---|---|---|
    | ① default | **0.9236** | 0.9434 | 0.4501 | 0 |
    | ② rule    | 0.1435 | 0.5434 | 0.4419 | 28918 |
    | ③ LLM     | 0.6253 | 0.7943 | 0.4470 | 11312 |
    B1 §3.1 R1 (macroF1 ≥ 0.03 CI excludes 0) **does not hold** on this dataset (macroF1 CI all crosses 0); strict/relaxed is the more sensitive dimension and shows ① >> ③ >> ②. R-trap (③ vs ② decision diversity on trap-prone): 2/4. rule_judge is currently over-conservative (31/39 downgraded).
  - **P7 SKILL iteration + `.skill` packaging**: ⏳ pending — driven by evals + B1 feedback; description tuning; final `.skill` bundle.
  - **基因 ID 映射（TAIR locus → symbol 等）从 skill 中移除**：`name_map4Arabidopsis_thaliana_symbol.json` 仍随仓,但 step3c_kg 不再读、不再映射。ID 转换是数据处理责任,由用户在 pipeline 上游完成。
  - **两层配置分离**（已落地）：
    - `harness/config.py`：负责 harness 包自身配置（LLM gateway + RAG toggles；6 项 key），加载项目根 `.env`。任何 `from harness.X import ...` 触发加载。Disable：`export SC_HARNESS_SKIP_DOTENV=1`。
    - `skills/cell-annotation/scripts/common.py:load_skill_dotenv()`：负责 cell-annotation skill 的**环境类**配置（Neo4j 连接、KG 服务调优；3 项 key：`NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`），加载 `skills/cell-annotation/.env`（gitignored）。任何 step 脚本 import `common` 时触发。Disable：`export CELL_ANNOTATION_SKIP_DOTENV=1`。
    - **harness 完全不知道 cell-annotation skill 的存在**（domain-agnostic）。未来增加 skill时该 skill 自己拥有并加载自己的 `.env`，harness 不变。
    - **凭据归属（实现状态）**：根 `.env` 仅含 harness 自身 key（`OPENAI_*` + `RAG_*`）。Neo4j 凭据已在 `skills/cell-annotation/.env`，**不在**根 `.env`。两文件互不重叠（harness loader 也会把根 `.env` 的 unknown key 装入 `os.environ`，所以历史把 Neo4j 放根 `.env` 也能跑，但违反"harness 不知道 skill 存在"的设计原则）。
    - 完整 key 清单：`harness/config.py:RECOGNIZED_KEYS` (harness) / `skills/cell-annotation/scripts/common.py:SKILL_DOTENV_KEYS` (skill)。文档 `docs/CONFIGURATION_REFERENCE.md` §2 / §3.0。
    - **LLM 工具参数 vs skill 配置 区分原则**：除 step3c_kg 的 KG 服务/资源参数外，其它所有 argparse 参数**全部暴露给 LLM**——LLM 通过 SOP 决策点（`qc_threshold` / `marker_quality` / `resolution_select` / `refine_effect` / `label_confirm` 等）主动调整阈值、选择分辨率、切换 DE 方法、选 obs 列名 / 物种特异正则等。**已 SUPPRESS 化的只有 step3c_kg 的 KG 资源类参数**：`--uri` / `--user` / `--password`（Neo4j 连接凭据，harness 完全不知道其存在）+ `--min-confidence` / `--max-ancestor-hops`（KG 服务调优）。这些 SUPPRESS 让 LLM 工具 schema 不暴露它们、CLI 可手动临时覆盖，从代码默认值回落（参考 `harness/tests/test_step3c_kg_schema.py`）。helper `common.env_or_default(args, name, env_keys, default, cast)` 已实现；`common.arg_spec` 已把 SUPPRESS 化的 action 从 tool schema 里剔除——可直接套用。
- Test tooling: `pytest.ini` (`testpaths = harness/tests`), `harness/tests/conftest.py`, **89 tests passing** (`python -m pytest`), including 14 for step3c_kg schema discipline (4 task + 2 path A-class visible; 5 KG-resource B-class hidden; 3 loader / runtime contract) and 7 for the skill `.env` loader. Don't assume tests don't work — run them. `.opencode/package.json` only pins the opencode plugin and that dir is gitignored.
- **Known runtime risks** (from `implementation_plan.md` §11 and design docs): `scrublet` import verified on the dev box — `python -c "import scrublet"` returns OK; the h5ad is ~2 GB so full-load runs need real memory and time budget. The 4 items in `_bmad-output/implementation-artifacts/deferred-work.md` (organ substring match false positives; `_organ_status.title()` normalization; `validate_log` cluster-coverage on refine_effect / candidate_disambiguate; organ_status category naming `root/partial/unknown/mismatch` vs `match/partial/unknown/mismatch`) are non-blocking for the root dataset but matter for cross-organ portability.
- **Verification habit**: this project mixes "design says X" and "code does X" — they can drift. Before stating a status claim, `ls` / `wc -l` / `grep` to confirm. AGENTS.md itself has historically understated the breadth of completed code AND overstated the "not yet run" status — this revision reflects what actually shipped. Do not infer status from prose summaries; check `output/B1/eval/`, `output/p5_evals_r2/`, and `_bmad-output/implementation-artifacts/` for hard evidence.

## Architecture (from design/)
Single-cell RNA-seq cell-type annotation harness driven by an LLM agent. Three layers:
- **Loop** — domain-agnostic runner (LangGraph). Loads a Skill, runs LLM↔tool loop, writes `conversation.jsonl`. Deliberately minimal: **no context management** (messages only grow), **no domain knowledge hardcoded**. Don't over-engineer it. (`design/loop_design.md`, `readme.md`)
- **Skill** — domain spec: `system_prompt` + `tool_schemas` + `tool_runtime`. Tool types: `subprocess` (CLI script must print final stdout line as JSON `{"status":"ok","data":{...}}` / `{"status":"error",...}`) or `function` (importlib import).
- **Pipeline** — 7 stage CLI scripts: `step1_prepare`→`step2_markers`→`step3a_kg_precheck`→`step3b_cross_species_map`(optional)→`step3c_kg`→`step4_rank`→`step5_refine`→`step6_validate`→`step7_diagnose`. 47 atomic ops; the op id `stepN_name.op_name` is significant (used in `run_id`). SOP-3 文件名带 3a/3b/3c 以标明顺序。
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
- `harness/` — domain-agnostic loop package (real code: `loop.py`, `dispatcher.py`, `skill_loader.py`, `notebook.py`, `session.py`, `conversation.py`) + `harness/tests/` (pytest suite, 89 tests).
- `skills/cell-annotation/` — the skill package: `SKILL.md` (<500 lines, Chinese, 13 decision points + 6 traps + log guide), `references/` (sop / metrics / traps / kg-schema), `assets/` (report templates), `evals/evals.json` (gitignored test fixtures), and `scripts/`:
  - `common.py` (1019 lines) — 9 generic functions (`describe_distribution`, `filter_funnel`, `effect_size`, `pairwise_overlap`, `batch_mixing`, `cluster_quality`, `variance_explained`, `resolution_stability`, `candidate_autocorr`) + `append_log` / `next_run_id` / `current_metrics` / `dump_schema` / `arg_spec` / JSON envelope (`ok` / `fail` / `emit`) + `add_common_args` / `add_neo4j_args` + `env_or_default` (SUPPRESS-aware CLI > env > default reader) + `load_skill_dotenv`.
  - `step1_prepare.py` (777) — subcommands `metrics` (1× raw load, pre-filter distributions), `run` (1× raw load, all 16 ops + obs_snapshot.csv / var_snapshot.csv sidecars), `recluster` (1× proc load).
  - `step2_markers.py` (453) — `run` (1× proc load; DE + AUC + pct1/pct2 + filter funnel + pseudobulk for rare clusters).
  - `step3a_kg_precheck.py` — `run`(0 h5ad; KG 覆盖预检,输出 coverage_report.json)。
  - `step3b_cross_species_map.py` — `run`(0 h5ad; 同源映射以提高 KG 命中率,归属 SOP-3;默认 Ensembl Compara,provider 可插拔)。子包 `step3b_xmap_providers/`。
  - `step3c_kg.py` (477) — `query` (0 h5ad; Neo4j lookup; 可选 `--ortholog-map`; SUPPRESSed `--uri/--user/--password/--min-confidence/--max-ancestor-hops` per LLM-tool schema discipline; `--project-dir/--input` 与 task 类参数一同暴露给 LLM), `test-connection`.
  - `step4_judge.py` (214) — `run` (0 h5ad; pure JSON; emits an LLM-ready decision view per cluster).
  - `step5_refine.py` (411) — `run` (1× proc load; subset to ambiguous clusters → subcluster → sub-DE → sub-KG → overlap).
  - `step6_validate.py` (417) — `run` (1× proc load, optionally backed-mode per-column read for top-3 markers), `report` (0 h5ad).
  - `step7_diagnose.py` (221) — `run` (0 h5ad; reads obs_snapshot.csv + JSONs).
  - `write_judgment.py` (252) — `add` / `session-start` / `session-end`; validates decision enum + scope via `REQUIRED_SCOPE` (cross-directory import of `trajectory_schema`).
  - `trajectory_schema.py` (22) — `REQUIRED_SCOPE` dict (13 decision points → required scope type, 9 session + 4 cluster). Single source of truth.
  - Every script implements `--dump-schema` (hidden flag) so the skill loader can derive `tool_schemas` / `tool_runtime` from argparse — no hand-written JSON to drift.
  - `.env` (gitignored) holds Neo4j credentials; `.env.example` is the tracked template (defaults to `bolt://localhost:7687` / `neo4j` / empty password).
- `scripts/` (root, NOT part of the skill package) — P5 eval-loop tooling: `validate_log.py` (254) / `evaluate_annotations.py` (218) / `build_gt_cells.py` (100) / `build_label_map.py` (144). These import the shared schema from `skills/cell-annotation/scripts/trajectory_schema.py`.
- `experiments/` — P6 experiment framework (1,527 lines), all written, **executed end-to-end on the 33,956-cell h5ad**:
  - `scripted_driver.py` (127) — B1 deterministic DAG driver for arm1/arm2; calls `dispatcher.dispatch` directly, bypasses LangGraph loop.
  - `judges/default_judge.py` (153) — ① Fixed-default judge (no-op, all accept).
  - `judges/rule_judge.py` (314) — ② Rule-based judge; oracle table → if-then. Currently over-conservative — `count_diff >= 3` threshold lets in too many tied-pair cases; needs tightening.
  - `judges/_common.py` (160) — shared helpers for judges.
  - `evaluate_cell_level.py` (374) — cell-level accuracy / macro-F1 / confusion / purity diagnostics from `final_annotations.json` + `gt_cells.csv` + `label_map.json`.
  - `bootstrap_test.py` (220) — cluster-aware bootstrap (1000 resamples, 95% CI on macro-F1 delta).
  - `analyze_traps.py` (179) — trap oracle comparison (per-trap ③ vs ② correctness).
  - Pre-generated: `gt_cells.csv` (cell barcode → true type, 33,956 rows), `label_map.json` (12 ground-truth types mapped to KG ontology terms, locked version with `_meta.verified`).
  - Outputs: `output/B1/{arm1_default,arm2_rule,arm3_llm}/run_log.jsonl` + `output/B1/eval/{evaluation_report,bootstrap_report,traps_report}.json` (also `evaluation_report.per_cell.json` for per-cell drill-down).
- Tests live in `harness/tests/` and run via `python -m pytest` (89 passing).

## External deps
- **Neo4j knowledge graph**：凭据 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`在 `skills/cell-annotation/.env`（gitignored，根 `.gitignore` 的 `.env` 模式自然匹配该路径）设置，**不在**项目根 `.env`。模板 `skills/cell-annotation/.env.example` 跟踪进 git（默认值 `bolt://localhost:7687` / `neo4j` / 空密码）。优先级：CLI flags > `.env` > `.env.example` > 硬编码默认。绝不硬编码密码。（`design/tool_design.md` §10）
- **`docs/CONFIGURATION_REFERENCE.md` §2.1 / §2.2 / §3.0** 列出全部 env var（harness 自身 vs skill 配置）。loader 逻辑：`harness/config.py` （harness 自身）、`skills/cell-annotation/scripts/common.py:load_skill_dotenv()` （cell-annotation 专用）。

## Gitignore gotchas
Root `.gitignore` ignores `*.json`, `*.csv`, `*.h5ad`, `*.png`, `*.ipynb`, `*.xmind`, `*.ai`, `.env`, `.opencode*`. Consequences:
- Tracked content is essentially only `*.md` (under `design/`, `knowledge/`, `_bmad-output/implementation-artifacts/`), `readme.md`, `AGENTS.md`, `.gitignore`, `pytest.ini`, `harness/**/*.py`, `skills/cell-annotation/scripts/*.py`, `skills/cell-annotation/SKILL.md`, `skills/cell-annotation/references/*.md`, `skills/cell-annotation/assets/*.md`, `skills/cell-annotation/.env.example`, `scripts/*.py`, `dataset/init.py`.
- The `.env` pattern in root `.gitignore` also matches `skills/cell-annotation/.env` (gitignore patterns are anchored at repo root but match anywhere) — verified via `git check-ignore -v skills/cell-annotation/.env`. So skill-level secrets are auto-protected without an additional rule.
- `name_map4Arabidopsis_thaliana_symbol.json`, `dataset/h5ad/*.h5ad`, `dataset/index/*.csv`, `output/`, `skills/cell-annotation/.env`, `skills/cell-annotation/evals/evals.json`, and `.opencode/` all exist locally but are NOT tracked. New `.json`/`.csv` files you create are ignored unless force-added.

## Language convention
- Design docs and knowledge files are in **Simplified Chinese** — match Chinese when editing them. Code identifiers and docstrings are English (see `dataset/init.py`). LLM-facing prompts are expected to be Chinese.

## Where to look
- `design/loop_design.md` (RUN — the loop, including the L-1..L-6 implementation phases, all completed).
- `design/tool_design.md` (HOW — impl + data deps + subcommand structure; one-subcommand-one-load rule).
- `design/atomic_operations.md` (WHAT — 47 ops + DAG).
- `design/operations_metrics_catalog.md` (247 metrics per op).
- `design/trajectory_design.md` (LOG — run_log.jsonl format + record types + judgment schema).
- `design/rag_design.md` (MEM — loop-level notebook, generic, not skill-bound).
- `design/implementation_plan.md` (project plan, P1..P7 milestones; current status: P1–P6 done, P7 pending).
- `design/experiment_design.md` + `design/experiment_implementation.md` (12 experiments, pre-registered decision rules, cell-level eval + cluster-aware bootstrap).
- `knowledge/` (`cell-annotation-sop.md`, `cross-species-annotation-handbook.md`, `kg_schema.md`, `metrics_interpretation.md`) = source material for the skill's system prompt + references.
- `_bmad-output/implementation-artifacts/` — frozen-after-approval specs and closure reports; the canonical record of design intent vs shipped state. P5 r2 closure (`p5-evals-round1-closure.md`) and B1 eval (`b1-three-arm-eval.md`) 是已交付记录；下一步计分规划见 `label-map-ontology-eval.md`（GT 钉图谱）；`deferred-work.md` lists the open non-blockers.
- `docs/PROJECT_OVERVIEW.md` — résumé-oriented project write-up (architecture, status, keyword matrix for different JD directions).

## Next steps

The bottleneck is **not** coding or first-time end-to-end run — those are done. Remaining work, ordered by blocking dependency + impact:

1. **标签对照改为 GT 钉图谱 + 评估时算关系**（规划，未开工）。停掉 `label_map.json` / `label_map_PRJNA935359.json` 这种按数据集复制的 predicted×true 表；每套数据只钉 GT→`Ontology` 节点，predicted 来自注释跑次，relation 用图谱层次当场算。改 `evaluate_cell_level.py` 的 `hits[0]`。全文 `_bmad-output/implementation-artifacts/label-map-ontology-eval.md`。
2. **Tighten `rule_judge` thresholds** (B1 §3.1 oracle table calibration). Current ② is over-conservative: 31/39 clusters downgraded; this likely inflates ②'s strict_accuracy drop (0.14 vs ①'s 0.92). Targets: re-tune `DIFF_THRESH_FOR_DECISIVE` / `GAP_RATIO_TIED` / "pct1≈pct2 → label_downgraded" rule. Re-run B1 arm2 + arm1; expected: arm2 strict rises meaningfully (target ≥ 0.6), macroF1 delta arm2 vs arm1 stays inconclusive (R3 still holds). Use `experiments/evaluate_cell_level.py` + `bootstrap_test.py` for verification.
3. **Run the B1 §6 supplementary experiments** (S1 synthetic traps; A/E/N from `design/experiment_design.md`) on the existing arm1/arm2/arm3 outputs. These don't require a new pipeline run, only the existing `run_log.jsonl` + `final_annotations.json`. See `design/experiment_implementation.md` §6.
4. **B3 / B4 trajectory analysis** on `output/B1/arm3_llm/run_log.jsonl` (inputs[].path frequency → minimal sufficient set; same-decision-point multi-judgment → self-correction pairs). These inform SKILL.md description tuning.
5. ~~Apply the LLM-tool schema discipline (SUPPRESS `--project-dir` / `--input`) to step1/2/4/5/6/7~~ — **superseded**：按"除 KG 资源类外全部暴露给 LLM"的原则，`--project-dir` / `--input` 及其它所有非 KG 资源类参数有意保留在工具 schema 中供 LLM 传递。SUPPRESS 化目前仅适用于 step3c_kg 的 Neo4j 连接 / KG 服务调优参数，不扩展到其它 step。
6. **SKILL.md / references iteration** driven by B1 + S1 + N findings; tighten `description` to match real user phrasings seen in arm3 judgments.
7. **`.skill` packaging (P7 / M7)** — final delivery artifact for the cell-annotation skill.
8. **Deferred-work clean-up** (only when the cross-organ / cross-dataset scenario actually arrives): organ substring boundary matching, `_organ_status.title()` normalization, `validate_log` cluster-coverage on refine_effect / candidate_disambiguate, organ_status category rename. None of these block the root dataset.
9. **图谱改为 API、skill 不再直连 Neo4j**（未排期，与 BLASTP 无关）— 脚本现用 Bolt + 内嵌 Cypher；将来凭据与图 schema 留在服务端。清单与建议顺序见 `_bmad-output/implementation-artifacts/future-kg-api.md`。

Estimated wall-clock from "P1–P6 done" to "P7 shipped": ~1–2 weeks, mostly items 1, 5, 6.

Tests: `harness/tests/` — run `python -m pytest` (89 passing). `harness/tests/conftest.py` documents fixtures (FakeEmbedder replaces the real embedding model).
