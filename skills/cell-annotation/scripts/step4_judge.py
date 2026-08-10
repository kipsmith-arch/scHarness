"""Step 4 — cluster judgment (tool_design.md §5.4, atomic_operations.md stage 4).

Subcommand:
    run  — rank_candidates -> write_annotations. [0× h5ad — pure JSON]

For every cluster: pick first/second candidates from kg_hits.json, compute gap
metrics (count_ratio, count_diff, confidence_diff, n_tied_at_first,
frac_support_captured_by_first, first_second_ancestor_overlap) and write
annotations.json consumed by step5_refine / step7_diagnose.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step4_judge.py",
        description="Step 4 簇判断:候选排名 first/second + gap 指标(0 次 h5ad 加载)",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")
    p_run = sub.add_parser("run", help="rank_candidates + write_annotations")
    common.add_common_args(p_run)
    return parser


def _candidate_summary(cand: dict) -> dict:
    return {
        "cell_type": cand.get("cell_type"),
        "marker_count": cand.get("marker_count"),
        "mean_confidence": cand.get("mean_confidence"),
        "min_confidence": cand.get("min_confidence"),
        "supporting_markers": cand.get("supporting_markers", []),
        "sources": cand.get("sources", []),
    }


def _cluster_sort_key(x):
    """Sort cluster ids numerically when numeric, lexicographically otherwise."""
    return (0, int(x)) if str(x).isdigit() else (1, str(x))


def op_rank_candidates(kg_hits, log_path, params) -> dict:
    ancestors = kg_hits.get("ancestors", {})
    annotations = {}
    for c in sorted(kg_hits.get("per_cluster", {}), key=_cluster_sort_key):
        info = kg_hits["per_cluster"][c]
        cands = info.get("candidates", [])
        entry = {"n_candidates": len(cands)}
        if cands:
            first, second = cands[0], (cands[1] if len(cands) > 1 else None)
            entry["first_candidate"] = _candidate_summary(first)
            entry["second_candidate"] = _candidate_summary(second) if second else None
            entry["first_count"] = first["marker_count"]
            entry["second_count"] = second["marker_count"] if second else 0
            entry["first_mean_confidence"] = first.get("mean_confidence")
            entry["second_mean_confidence"] = second.get("mean_confidence") if second else None
            entry["first_supporting_markers"] = first.get("supporting_markers", [])
            entry["second_supporting_markers"] = second.get("supporting_markers", []) if second else []
            first_count = entry["first_count"]
            second_count = entry["second_count"]
            gap = {
                "count_ratio": float(first_count / max(second_count, 1)),
                "count_diff": int(first_count - second_count),
                "confidence_diff": (
                    float((first.get("mean_confidence") or 0) - (second.get("mean_confidence") or 0))
                    if second else None),
                "n_tied_at_first": int(sum(1 for x in cands if x["marker_count"] == first_count)),
                "frac_support_captured_by_first": float(
                    first_count / max(sum(x["marker_count"] for x in cands), 1)),
            }
            # ancestor overlap between first and second (ontology_relation ancestors)
            a_overlap = None
            if second:
                f, s = first["cell_type"], second["cell_type"]
                f_anc = set(ancestors.get(f, []))
                s_anc = set(ancestors.get(s, []))
                if f in s_anc or f_anc & {s}:
                    a_overlap = {"related": True, "first_is_ancestor_of_second": f in s_anc}
                elif s in f_anc or s_anc & {f}:
                    a_overlap = {"related": True, "first_is_ancestor_of_second": False}
                else:
                    shared = f_anc & s_anc
                    a_overlap = {"related": False, "n_shared_ancestors": len(shared),
                                 "shared_ancestors": sorted(shared)[:10]}
            gap["first_second_ancestor_overlap"] = a_overlap
            entry["gap_metrics"] = gap
            entry["status"] = "has_candidates"
        else:
            entry.update({
                "first_candidate": None, "second_candidate": None,
                "first_count": 0, "second_count": 0,
                "first_mean_confidence": None, "second_mean_confidence": None,
                "first_supporting_markers": [], "second_supporting_markers": [],
                "gap_metrics": None, "status": "no_candidates",
            })
        annotations[c] = entry
    m = {"annotations": annotations,
         "n_clusters": len(annotations),
         "n_clusters_with_candidates": int(sum(1 for a in annotations.values()
                                               if a["status"] == "has_candidates"))}
    rid = common.exec_record(log_path, "step4_judge", "rank_candidates", params, m)
    m["run_id"] = rid
    return annotations, m


def op_write_annotations(out_dir, log_path, params, annotations, rank_metrics) -> dict:
    path = os.path.join(out_dir, "annotations.json")
    payload = {
        "annotations": annotations,
        "gap_summary": {
            "mean_count_ratio": float(np.mean([a["gap_metrics"]["count_ratio"]
                                               for a in annotations.values()
                                               if a.get("gap_metrics")])) if any(
                a.get("gap_metrics") for a in annotations.values()) else None,
            "n_tied": int(sum(1 for a in annotations.values()
                              if a.get("gap_metrics") and a["gap_metrics"]["count_ratio"] <= 1.0)),
        },
        "meta": {"source_run_id": rank_metrics.get("run_id"),
                 "date": common.now_iso()[:10]},
    }
    common.write_json(path, payload)
    m = {"annotations_json": path, "n_clusters": len(annotations)}
    rid = common.exec_record(log_path, "step4_judge", "write_annotations", params, m)
    m["run_id"] = rid
    return m


def cmd_run(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step4_judge")
    log = common.run_log_path(args.project_dir)
    kg_path = os.path.join(common.step_dir(args.project_dir, "step3_kg"), "kg_hits.json")
    kg_hits = common.read_json(kg_path)
    if not kg_hits:
        return common.fail("缺少 step3_kg/kg_hits.json,请先运行 step3_kg query")
    p = {}
    annotations, rank_metrics = op_rank_candidates(kg_hits, log, p)
    m = op_write_annotations(out_dir, log, p, annotations, rank_metrics)
    return common.ok({"annotations_json": m["annotations_json"],
                      "n_clusters": m["n_clusters"],
                      "n_with_candidates": rank_metrics["n_clusters_with_candidates"],
                      "last_exec_run_id": m["run_id"]})


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(parser)
    if not args.subcommand:
        parser.print_help()
        return 2
    try:
        result = cmd_run(args)
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"run failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
