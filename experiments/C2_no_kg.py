"""C2 KG 消融 — no-KG 臂驱动。

为什么需要这个 (experiment_implementation.md §3.8 C2 — KG 消融):
    跑一份"无 KG"臂:用静态 marker_dict 替代 step3c_kg.py 的 Neo4j 查询,
    其余 step1/2/4/5/6/7 与 B1 arm3_llm 一致(同 h5ad, 同 step1 处理, 同 markers)。

    输出:
          output/C2/no_kg/step3c_kg/kg_hits.json     — C2 专用,与 B1 同 schema 但 candidates 来自 marker_dict
          output/C2/no_kg/step4_rank/annotations.json
          output/C2/no_kg/step5_refine/refined_annotations.json
          output/C2/no_kg/step6_validate/final_annotations.json
          output/C2/no_kg/run_log.jsonl             — 与 B1 同格式的 exec + judgment 记录

    最终用 experiments/evaluate_cell_level.py 评估,与 B1 arm3_llm 对比 macro_f1。

设计原则:
    - 复用 B1 arm3_llm 的 step1/step2 产物(不重跑,省时间)。
      --source-project-dir 显式指定。
    - step3 这一步完全跳过 Neo4j(不连 driver),用 marker_dict 静态匹配。
    - step4/5/6/7 直接 subprocess 调用 skill scripts,期望它们读 kg_hits.json。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
SKILL_SCRIPTS = os.path.join(REPO_ROOT, "skills", "cell-annotation", "scripts")
DEFAULT_SOURCE = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm")
DEFAULT_OUT = os.path.join(REPO_ROOT, "output", "C2", "no_kg")
DEFAULT_MARKER_DICT = os.path.join(REPO_ROOT, "experiments", "marker_dict.json")
DEFAULT_ORGAN = "root"

# Pipeline DAG,仅 step3c_kg 替换;step5_refine 需 step3c_kg 输出的 candidates
# step6_validate 需 step4 output + step5 refined
PIPELINE = [
    ("step1_prepare.py", "run", True),     # 跳过,如果 source 已存在
    ("step2_markers.py", "run", True),     # 跳过,如果 source 已存在
    ("step3c_kg_no_kg.py", "main", False),  # 必跑,本脚本模拟
    ("step4_rank.py", "run", False),
    ("step5_refine.py", "run", False),
    ("step6_validate.py", "run", False),
    ("step6_validate.py", "report", False),
    ("step7_diagnose.py", "run", False),
]


def copy_from_source(out_dir: str, source_dir: str, force: bool = False):
    """Copy step1/step2 outputs from source (B1 arm3_llm) to out_dir; copy obs/var snapshots."""
    for sub in ("step1_prepare", "step2_markers"):
        src = os.path.join(source_dir, sub)
        dst = os.path.join(out_dir, sub)
        if os.path.exists(dst) and not force:
            print(f"[C2] reuse {dst} (force=True to overwrite)")
            continue
        if not os.path.exists(src):
            raise SystemExit(f"[C2] ERROR: source missing {src}")
        shutil.copytree(src, dst, dirs_exist_ok=True)
    # Copy obs/var snapshots and run_log for continuity
    for f in ("obs_snapshot.csv", "var_snapshot.csv"):
        src = os.path.join(source_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out_dir, f))


def load_markers(markers_json_path: str) -> dict:
    with open(markers_json_path, encoding="utf-8") as f:
        return json.load(f)


def load_marker_dict(path: str) -> tuple[dict[str, list[str]], dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("gene_to_cts", {}), doc.get("_meta", {})


def build_c2_kg_hits(markers: dict, gene_to_cts: dict[str, list[str]], target_organ: str) -> dict:
    """Reverse the markers → cell_type flow using marker_dict.

    For each cluster's marker_genes, intersect with marker_dict to enumerate candidates.
    Each candidate: {cell_type, supporting_markers, marker_count, sources}.
    No confidence (dict has none), no ancestors (no ontology), no organ_status (no organ column).

    Output's `gene_to_cts` field is normalised to the step3c_kg hit schema
    ({cell_type, confidence, source, organ, ...} per entry) so downstream
    _rank_candidates can re-use it without modification.
    """
    per_cluster: dict[str, dict] = {}
    for cid, cdata in markers.get("per_cluster", {}).items():
        marker_genes = cdata.get("marker_genes", [])
        # tally per-cell_type
        agg: dict[str, dict] = {}
        for g in marker_genes:
            for ct in gene_to_cts.get(g, []):
                if not ct:
                    continue
                e = agg.setdefault(ct, {"cell_type": ct, "supporting_markers": [],
                                         "marker_count": 0, "sources": set()})
                if g not in e["supporting_markers"]:
                    e["supporting_markers"].append(g)
                    e["marker_count"] += 1
        cands = []
        for ct, e in agg.items():
            cands.append({
                "cell_type": ct,
                "supporting_markers": e["supporting_markers"],
                "marker_count": e["marker_count"],
                "sources": ["marker_dict"],  # placeholder, marker_dict has no per-hit source
                "mean_confidence": None,  # dict has no confidence
                "min_confidence": None,
                "organ": [target_organ] if target_organ else [],
                "organ_status": "match" if target_organ else "unknown",
            })
        # rank by marker_count desc, then cell_type asc
        cands.sort(key=lambda x: (-x["marker_count"], x["cell_type"]))
        per_cluster[cid] = {
            "n_markers": len(marker_genes),
            "n_markers_hit": int(sum(1 for g in marker_genes if gene_to_cts.get(g))),
            "candidates": cands,
        }
    return {
        "kg_source": "marker_dict",
        "kg_version": ["n/a"],
        "kg_date": "C2 ablation",
        "kg_provenance": {
            "marker_dict_meta": {},  # filled by caller
            "note": "no KG, no ontology, no confidence — pure gene → cell_type reverse index",
        },
        "query_config": {"organ": target_organ, "species": None, "species_type": "Plant",
                          "min_confidence": 0.0, "strict_organ": False, "gene_key": "marker_dict"},
        "gene_to_cts": {
            # normalise to step3c_kg hit schema so step5_refine._rank_candidates can reuse
            g: [{
                "cell_type": ct,
                "confidence": 1.0,  # marker_dict has no per-hit confidence
                "source": "marker_dict",
                "organ": target_organ,
                "ontology_id": None,
                "species_type": "Plant",
                "ontology_type": None,
            } for ct in cts]
            for g, cts in gene_to_cts.items()
        },
        "ancestors": {},  # no ontology
        "query_stats": {"n_genes_queried": sum(len(c.get("marker_genes", []))
                                                for c in markers.get("per_cluster", {}).values())},
        "candidate_stats": {},
        "per_cluster": per_cluster,
    }


def write_step3kg(out_dir: str, payload: dict, log_path: str):
    """Write kg_hits.json equivalent, plus an exec record for step3c_kg.query_genes."""
    step3_dir = os.path.join(out_dir, "step3c_kg")
    os.makedirs(step3_dir, exist_ok=True)
    out_json = os.path.join(step3_dir, "kg_hits.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # append exec record mimicking step3c_kg.query_genes
    import time
    n_clusters = len(payload["per_cluster"])
    total_cands = sum(len(p["candidates"]) for p in payload["per_cluster"].values())
    rec = {
        "type": "exec", "step": "step3c_kg", "op": "query_genes_no_kg",
        "run_id": "step3c_kg.query_genes_no_kg#1", "seq": None,
        "params": {"source": "marker_dict"},
        "metrics": {
            "n_clusters": n_clusters,
            "total_candidates": total_cands,
            "mean_candidates_per_cluster": total_cands / max(n_clusters, 1),
            "kg_source": "marker_dict",
        },
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        # determine next seq
        n = 0
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as fr:
                for _ in fr:
                    n += 1
        rec["seq"] = n
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[C2] wrote {out_json} ({n_clusters} clusters, {total_cands} total candidates)")


def init_run_log(out_dir: str) -> str:
    log_path = os.path.join(out_dir, "run_log.jsonl")
    if not os.path.exists(log_path):
        open(log_path, "w", encoding="utf-8").close()
    return log_path


def run_step_subprocess(script: str, sub: str, project_dir: str) -> bool:
    cmd = [sys.executable, os.path.join(SKILL_SCRIPTS, script), sub, "--project-dir", project_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    if proc.returncode != 0:
        print(f"[C2] FAIL {script}.{sub}: rc={proc.returncode}", file=sys.stderr)
        print(f"  stdout_tail: {last}", file=sys.stderr)
        print(f"  stderr_tail: {(proc.stderr or '').strip().splitlines()[-1] if proc.stderr else ''}", file=sys.stderr)
        return False
    print(f"[C2] ok   {script}.{sub}: {last[:120]}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="C2_no_kg.py",
                                 description="C2 KG 消融 — no-KG 臂驱动")
    ap.add_argument("--source-project-dir", default=DEFAULT_SOURCE,
                    help="复用 step1/step2 的源 project_dir(默认 B1 arm3_llm)")
    ap.add_argument("--out-project-dir", default=DEFAULT_OUT,
                    help="C2 输出 project_dir")
    ap.add_argument("--marker-dict", default=DEFAULT_MARKER_DICT,
                    help="静态 marker→cell_type 字典路径")
    ap.add_argument("--organ", default=DEFAULT_ORGAN,
                    help="target organ,影响 organ_status 字段(默认 root)")
    ap.add_argument("--force", action="store_true",
                    help="强制覆盖已有 step1/step2 输出")
    args = ap.parse_args()

    out_dir = args.out_project_dir
    os.makedirs(out_dir, exist_ok=True)

    # 1) 复用 step1/step2
    copy_from_source(out_dir, args.source_project_dir, force=args.force)

    # 2) 初始化 run_log
    init_run_log(out_dir)

    # 3) 读 markers + marker_dict → 生成 kg_hits.json
    markers_json = os.path.join(out_dir, "step2_markers", "markers.json")
    if not os.path.exists(markers_json):
        raise SystemExit(f"[C2] ERROR: {markers_json} missing — check source-project-dir")
    markers = load_markers(markers_json)
    gene_to_cts, dict_meta = load_marker_dict(args.marker_dict)
    payload = build_c2_kg_hits(markers, gene_to_cts, args.organ)
    payload["kg_provenance"]["marker_dict_meta"] = dict_meta

    # 4) 写 step3c_kg/kg_hits.json + exec record
    log_path = os.path.join(out_dir, "run_log.jsonl")
    write_step3kg(out_dir, payload, log_path)

    # 5) 跑 step4-7 (subprocess, 复用 skill scripts)
    ok = True
    for script, sub, can_skip in PIPELINE:
        if script == "step3c_kg_no_kg.py":
            continue  # 已手工跑
        if can_skip and not args.force:
            continue  # step1/step2 已复制
        if not run_step_subprocess(script, sub, out_dir):
            ok = False
            break

    if not ok:
        print("[C2] ABORT at above step", file=sys.stderr)
        return 1
    print(f"[C2] complete in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())