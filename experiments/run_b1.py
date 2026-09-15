"""Wipe historical B1 trees and rerun the three arms (spec-b1-three-arm-rerun).

Usage (from repo root, conda env LM):

    python experiments/run_b1.py
    python experiments/run_b1.py --no-wipe
    python experiments/run_b1.py --raw dataset/h5ad/PRJNA935359.h5ad \\
        --out output/B1_PRJNA935359 --organism "Sorghum bicolor" \\
        --species sorghum_bicolor --gt-csv experiments/gt_cells_PRJNA935359.csv \\
        --gt-ontology experiments/gt_ontology_PRJNA935359.json \\
        --query-fasta dataset/fasta/Sorghum_bicolor.fasta
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RAW = REPO_ROOT / "dataset" / "h5ad" / "SRP171040.h5ad"
OUT = REPO_ROOT / "output" / "B1"
ARM1 = OUT / "arm1_default"
ARM2 = OUT / "arm2_rule"
ARM3 = OUT / "arm3_llm"
EVAL = OUT / "eval"
LOG = OUT / "orchestrator.log"
GT_CSV = REPO_ROOT / "experiments" / "gt_cells.csv"
GT_ONTOLOGY = REPO_ROOT / "experiments" / "gt_ontology.json"
ALIASES = REPO_ROOT / "experiments" / "kg_term_aliases.json"
ORGAN = "root"
SPECIES: str | None = "arabidopsis_thaliana"
SPECIES_TYPE = "Plant"
ORGANISM = "Arabidopsis thaliana"
QUERY_FASTA: str | None = None
PY = sys.executable
TOOL_TIMEOUT = 14400
ARM3_MAX_TURNS = 800


def _bind_paths() -> None:
    global ARM1, ARM2, ARM3, EVAL, LOG
    ARM1 = OUT / "arm1_default"
    ARM2 = OUT / "arm2_rule"
    ARM3 = OUT / "arm3_llm"
    EVAL = OUT / "eval"
    LOG = OUT / "orchestrator.log"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"{_now()} {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wipe_history() -> None:
    resolved = OUT.resolve()
    output_root = (REPO_ROOT / "output").resolve()
    if resolved.parent != output_root or not resolved.name.startswith("B1"):
        raise SystemExit(f"refusing to delete unexpected path: {resolved}")
    if not resolved.exists():
        print(f"{_now()} WIPE skip (missing) {resolved}", flush=True)
        return
    print(f"{_now()} WIPE {resolved}", flush=True)
    try:
        shutil.rmtree(resolved)
    except OSError as exc:
        raise SystemExit(f"wipe failed: {exc}") from exc


def arm3_task(project_dir: str) -> str:
    raw_rel = os.path.relpath(RAW, REPO_ROOT).replace("\\", "/")
    species_line = SPECIES or "(不传 --species，按 SKILL / KG 默认)"
    if QUERY_FASTA:
        fasta_rel = os.path.relpath(QUERY_FASTA, REPO_ROOT).replace("\\", "/")
        fasta_block = (
            f"- query FASTA: {fasta_rel}\n"
            "  有可读 FASTA 时 step3b 自动走 blastp（不必再写 --provider blastp）。"
            "  参考物种从 skills/cell-annotation/references/reference-species.md 按亲缘点名，最多 3 个；"
            "  高粱用 zea_mays、oryza_sativa、arabidopsis_thaliana。"
        )
    else:
        fasta_block = "- query FASTA: （无；step3b 默认 Ensembl Compara）"
    return f"""请对{ORGANISM} {ORGAN} 单细胞 RNA-seq 做完整细胞类型注释（B1 ③ LLM 臂）。

数据与目录：
- raw h5ad: {raw_rel}
- project-dir: {project_dir}
- organism: {ORGANISM}
- organ: {ORGAN}
- species（step3c_kg --species）: {species_line}
- species-type: {SPECIES_TYPE}
{fasta_block}

硬性要求：
1. 第一次调工具前先 write_judgment__session-start。
2. 严格按 SKILL SOP 走 step1→step7。每个决策点都要 write_judgment__add。
3. step1_prepare__run 与 recluster 必须带显式 --target-resolution（脚本不再 knee 选定）。
4. step3c_kg__query 必须带 --organ {ORGAN}。
5. marker 接受后先 step3a_kg_precheck__run（--target-species {species_line} --organ {ORGAN}），写 cross_species_routing。3a 只给覆盖档、不推荐参考物种。single_species 直接查 KG；mixed/cross_species_only 先 step3b_cross_species_map__run（自带 --query-fasta 若上面有 FASTA，并传入 --reference-species）再 step3c_kg__query --ortholog-map。同源是 SOP-3 查图谱的一部分，用来提高 KG 命中率。不要先打空 KG 再补同源。
6. 需要细化时再调 step5_refine__run，并传入 --clusters（逗号分隔簇 id）；不要让脚本自路由。
7. write_judgment 的 output.action 不会被 loop 执行：要重跑/换参必须再调对应工具。
8. 同一 {{step}}.{{op}} 最多 #1 + 5 次重试（#2–#6）。不要调用第 7 次；若闸门仍不满足，judgment 用该点的 accept 枚举且 action=cap_exhausted_proceed，然后继续 SOP。
9. 不要调用 write_note / retrieve_notes（本臂禁用笔记本；session 已 --no-notebook）。
10. 交付前 write_judgment__session-end。

请开始执行。
"""


def run(args: list[str]) -> None:
    log("RUN " + " ".join(args))
    env = dict(os.environ)
    env["RAG_EMBEDDING"] = "off"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(args, cwd=str(REPO_ROOT), env=env)
    if r.returncode != 0:
        log(f"FAIL exit={r.returncode}")
        raise SystemExit(r.returncode)
    log("OK")


def _last_json_object(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _require_openai_key() -> None:
    import harness.config  # noqa: F401 — load root .env

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing; halt (Ask First: OpenAI gateway)")


def preflight() -> None:
    if not RAW.is_file():
        raise SystemExit(f"missing raw h5ad: {RAW}")
    if not GT_CSV.is_file():
        raise SystemExit(f"missing {GT_CSV}")
    if not GT_ONTOLOGY.is_file():
        raise SystemExit(f"missing {GT_ONTOLOGY}")
    if not ALIASES.is_file():
        raise SystemExit(f"missing {ALIASES}")
    _require_openai_key()
    tmp = tempfile.mkdtemp(prefix="b1-preflight-")
    try:
        cmd = [
            PY, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts" / "step3c_kg.py"),
            "test-connection", "--project-dir", tmp,
        ]
        print(f"{_now()} RUN " + " ".join(cmd), flush=True)
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
        payload = _last_json_object(proc.stdout or "")
        prov = (payload.get("data") or {}).get("kg_provenance") or {}
        if (
            proc.returncode != 0
            or payload.get("status") != "ok"
            or prov.get("error")
            or not prov.get("node_labels")
        ):
            err = prov.get("error") or payload.get("error") or (proc.stderr or "")[:500]
            raise SystemExit(f"Neo4j preflight failed: {err}")
        print(f"{_now()} OK neo4j labels={list(prov.get('node_labels') or [])}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_arm12(arm: str, project_dir: Path, session_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, str(REPO_ROOT / "experiments" / "scripted_driver.py"),
        "--arm", arm,
        "--project-dir", str(project_dir),
        "--raw", str(RAW),
        "--organ", ORGAN,
        "--session-id", session_id,
    ]
    if SPECIES:
        cmd.extend(["--species", SPECIES])
    if QUERY_FASTA:
        cmd.extend(["--query-fasta", QUERY_FASTA])
    run(cmd)


def run_arm3() -> None:
    from harness.session import run_session
    from harness.skill_loader import load_skill

    ARM3.mkdir(parents=True, exist_ok=True)
    skill = load_skill(str(REPO_ROOT / "skills" / "cell-annotation"))
    for spec in skill.tool_runtime.values():
        spec["timeout"] = max(float(spec.get("timeout") or 0), TOOL_TIMEOUT)
    rel = os.path.relpath(ARM3, REPO_ROOT).replace("\\", "/")
    log(f"RUN harness.session arm3_llm notebook=False max_turns={ARM3_MAX_TURNS}")
    os.environ["RAG_EMBEDDING"] = "off"
    try:
        run_session(
            skill,
            str(ARM3),
            arm3_task(rel),
            max_turns=ARM3_MAX_TURNS,
            notebook=False,
        )
    except Exception as exc:
        log(f"FAIL arm3: {exc}")
        raise
    log("OK arm3 session returned")


def _assert_no_notebook_tool_calls(conv: Path) -> None:
    if not conv.is_file():
        raise SystemExit(f"missing conversation: {conv}")
    for line in conv.read_text(encoding="utf-8").splitlines():
        if '"name": "write_note"' in line or '"name": "retrieve_notes"' in line:
            raise SystemExit(f"notebook tool_call found in {conv}")


def materialize_or_halt() -> None:
    from experiments.judges._common import materialize_llm_labels

    _assert_no_notebook_tool_calls(ARM3 / "conversation.jsonl")
    try:
        path = materialize_llm_labels(str(ARM3))
    except SystemExit as exc:
        log(f"ASK_FIRST no label_confirm: {exc}")
        raise SystemExit(
            "arm3 finished without per-cluster label_confirm; "
            "not evaluating (spec Ask First). Do not resume automatically."
        ) from exc
    except Exception as exc:
        log(f"ASK_FIRST materialize: {exc}")
        raise SystemExit(f"arm3 materialize failed: {exc}") from exc
    log(f"OK materialized labels -> {path}")


def evaluate() -> None:
    EVAL.mkdir(parents=True, exist_ok=True)
    run([
        PY, str(REPO_ROOT / "experiments" / "evaluate_cell_level.py"),
        "--arms",
        f"arm1={ARM1}",
        f"arm2={ARM2}",
        f"arm3={ARM3}",
        "--gt-csv", str(GT_CSV),
        "--gt-ontology", str(GT_ONTOLOGY),
        "--aliases", str(ALIASES),
        "--out", str(EVAL / "evaluation_report.json"),
    ])
    run([
        PY, str(REPO_ROOT / "experiments" / "bootstrap_test.py"),
        "--per-cell", str(EVAL / "evaluation_report.per_cell.json"),
        "--arms", "arm1", "arm2", "arm3",
        "--out", str(EVAL / "bootstrap_report.json"),
    ])
    run([
        PY, str(REPO_ROOT / "experiments" / "analyze_traps.py"),
        "--eval-report", str(EVAL / "evaluation_report.json"),
        "--per-cell", str(EVAL / "evaluation_report.per_cell.json"),
        "--out", str(EVAL / "traps_report.json"),
    ])


def _refuse_stale_logs() -> None:
    for arm in (ARM1, ARM2, ARM3):
        stale = arm / "run_log.jsonl"
        if stale.is_file():
            raise SystemExit(f"--no-wipe refuses leftover {stale}; delete it or omit --no-wipe")


def main() -> int:
    global RAW, OUT, GT_CSV, GT_ONTOLOGY, ALIASES, ORGAN, SPECIES, SPECIES_TYPE, ORGANISM, QUERY_FASTA
    ap = argparse.ArgumentParser(description="Wipe B1 history and rerun three arms.")
    ap.add_argument("--no-wipe", action="store_true", help="Keep existing --out (do not delete).")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--out", type=Path, default=OUT, help="output/<B1...> tree; wipe only this dir")
    ap.add_argument("--organ", default=ORGAN)
    ap.add_argument("--species", default=SPECIES)
    ap.add_argument("--species-type", default=SPECIES_TYPE)
    ap.add_argument("--organism", default=ORGANISM)
    ap.add_argument("--gt-csv", type=Path, default=GT_CSV)
    ap.add_argument("--gt-ontology", type=Path, default=GT_ONTOLOGY,
                    help="GT 字符串 → Ontology.Name 钉表")
    ap.add_argument("--aliases", type=Path, default=ALIASES,
                    help="预测词变体 → 规范 Ontology.Name")
    ap.add_argument("--label-map", type=Path, default=None,
                    help="已停用;传入则报错")
    ap.add_argument("--query-fasta", type=Path, default=None,
                    help="Protein FASTA; when the file exists, step3b prefers blastp.")
    args = ap.parse_args()

    if args.label_map:
        raise SystemExit(
            "error: --label-map pair 表已停用。改用 --gt-ontology 与 --aliases"
        )

    RAW = args.raw if args.raw.is_absolute() else REPO_ROOT / args.raw
    OUT = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    GT_CSV = args.gt_csv if args.gt_csv.is_absolute() else REPO_ROOT / args.gt_csv
    GT_ONTOLOGY = args.gt_ontology if args.gt_ontology.is_absolute() else REPO_ROOT / args.gt_ontology
    ALIASES = args.aliases if args.aliases.is_absolute() else REPO_ROOT / args.aliases
    ORGAN = args.organ
    SPECIES = args.species or None
    SPECIES_TYPE = args.species_type
    ORGANISM = args.organism
    if args.query_fasta:
        qf = args.query_fasta if args.query_fasta.is_absolute() else REPO_ROOT / args.query_fasta
        if not qf.is_file():
            raise SystemExit(f"missing query FASTA: {qf}")
        QUERY_FASTA = str(qf)
        os.environ["CELL_ANNOTATION_QUERY_FASTA"] = QUERY_FASTA
    else:
        QUERY_FASTA = os.environ.get("CELL_ANNOTATION_QUERY_FASTA") or None
    _bind_paths()

    os.chdir(REPO_ROOT)
    os.environ["RAG_EMBEDDING"] = "off"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if not args.skip_preflight:
        preflight()
    else:
        _require_openai_key()
    if args.no_wipe:
        _refuse_stale_logs()
    else:
        wipe_history()
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"python={PY}")
    if "LM" not in PY.replace("\\", "/"):
        log(f"WARN interpreter may not be conda LM: {PY}")
    log(f"raw={RAW} out={OUT} organ={ORGAN} species={SPECIES} query_fasta={QUERY_FASTA}")
    run_arm12("default", ARM1, "sess-b1-arm1-default")
    run_arm12("rule", ARM2, "sess-b1-arm2-rule")
    run_arm3()
    materialize_or_halt()
    evaluate()
    log("ALL DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
