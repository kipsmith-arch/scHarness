# AGENTS.md

## Repo status
- **Design-stage.** Only `dataset/init.py` is real code. The loop, dispatcher, skill, and `stepN_*.py` pipeline scripts described in `design/` do NOT exist yet — treat the design docs as the spec when implementing.
- No build/test/lint/typecheck tooling exists (no root `package.json`, `pyproject.toml`, `requirements.txt`, test runner). Don't assume `npm test` / `pytest` work. `.opencode/package.json` only pins the opencode plugin and that dir is gitignored.

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

## Data / I/O
- h5ad files are GB-scale. Central rule: **one subcommand = one h5ad load**; compute every metric needed for that load in-passing. 48% of ops need no h5ad (pure JSON). Sidecars `obs_snapshot.csv` / `var_snapshot.csv` (written alongside `processed.h5ad`) replace many h5ad reads. Don't add casual h5ad loads. (`design/tool_design.md`)
- Dataset is *Arabidopsis thaliana* root (plant): QC uses **chloroplast** genes (`pct_counts_chloroplast`) alongside mitochondrial; plant-specific filters apply.
- `dataset/init.py` ingestion conventions: force `adata.X` sparse; rebuild `adata.raw` for old scanpy versions (when `_index` is in `raw.var`); reject numeric `var_names` (must be gene symbols). Ground-truth labels live in `dataset/index/*.csv` (columns `Seurat_clusters`, `Celltype`, pulled out of the h5ad).

## External deps
- Neo4j knowledge graph. Credentials via env: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (set in `.env`, gitignored). Priority: CLI flags > env vars > hardcoded defaults. Never hardcode passwords. (`design/tool_design.md` §10)

## Gitignore gotchas
Root `.gitignore` ignores `*.json`, `*.csv`, `*.h5ad`, `*.png`, `*.ipynb`, `*.xmind`, `*.ai`, `.env`, `.opencode*`. Consequences:
- Tracked content is essentially only `*.md` (under `design/`, `knowledge/`), `readme.md`, `.gitignore`, `dataset/init.py`.
- `name_map4Arabidopsis_thaliana_symbol.json`, `dataset/h5ad/*.h5ad`, `dataset/index/*.csv`, `output/`, and `.opencode/` all exist locally but are NOT tracked. New `.json`/`.csv` files you create are ignored unless force-added.

## Language convention
- Design docs and knowledge files are in **Simplified Chinese** — match Chinese when editing them. Code identifiers and docstrings are English (see `dataset/init.py`). LLM-facing prompts are expected to be Chinese.

## Where to look
- `design/loop_design.md` (RUN — the loop), `design/tool_design.md` (HOW — impl + data deps + subcommand structure), `design/atomic_operations.md` (WHAT — 47 ops + DAG), `design/operations_metrics_catalog.md` (247 metrics per op), `design/trajectory_design.md` (LOG — run_log.jsonl format).
- `knowledge/` (`cell-annotation-sop.md`, `cross-species-annotation-handbook.md`, `kg_schema.md`, `metrics_interpretation.md`) = source material for the skill's system prompt.
