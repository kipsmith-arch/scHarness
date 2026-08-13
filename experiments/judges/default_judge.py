"""① Fixed-default judge (B1 arm 1).

Policy (``experiment_implementation.md`` §3.1):
    无判断,全 accept,不读 metrics。Minimal dumb baseline: every decision
    point takes the most permissive enum value (no rejection / no refinement
    / always confirm the first candidate). The point is to prove that the
    judgment layer *exists* — by quantifying how much of the cell-level
    accuracy comes from the upstream pipeline alone.

Outputs:
    - One ``write_judgment__add`` record per decision point (per cluster for
      cluster-level dp; one record for each session-level dp).
    - ``step6_validate/final_annotations.json`` rewritten with arm-derived
      label / confidence / status (each cluster's first candidate, confirmed).
    - One ``session_end`` record so ``validate_log --mode auto`` still passes.
"""
from __future__ import annotations

import argparse
import os
import sys

from ._common import (
    final_annotations_clusters,
    latest_exec_metrics,
    load_run_log,
    refined_clusters,
    rewrite_final_annotations,
    write_judgment,
    write_session_end,
)

# Per-dp default enum (B1 arm 1: most permissive, no rejection).
SESSION_DEFAULTS = [
    ("qc_threshold", "threshold_default", "arm-default: 不读 metrics,用脚本阈值"),
    ("resolution_select", "resolution_chosen", "arm-default: 选中等分辨率"),
    ("clustering_quality", "clustering_accept", "arm-default: 全 accept"),
    ("batch_effect", "well_mixed", "arm-default: 不查条件注释"),
    ("de_method", "wilcoxon", "arm-default: 用默认 wilcoxon"),
    ("marker_quality", "markers_accept", "arm-default: 全 accept"),
    ("kg_match", "id_match_ok", "arm-default: 不查 ID 系统"),
    ("unknown_cluster", "multiple_unknown_types", "arm-default: 不细分"),
    ("global_quality", "quality_good", "arm-default: 不总检"),
]

CLUSTER_DEFAULTS = [
    ("candidate_gap", "first_decisive", "arm-default: 永远 first_decisive(不看 gap)"),
    ("candidate_disambiguate", "ambiguous_true", "arm-default: ambiguous true"),
    ("refine_effect", "refine_skipped", "arm-default: 不细分"),
    ("label_confirm", "label_confirmed", "arm-default: 永远 confirmed"),
]


def judge(project_dir: str) -> dict:
    run_log = load_run_log(project_dir)
    session_run_ref = (latest_exec_metrics(run_log, "step1_prepare.write_output") or {}).get("run_id") \
        or (latest_exec_metrics(run_log, "step1_prepare.run") or {}).get("run_id") \
        or "step1_prepare.run#1"
    cluster_run_ref = (latest_exec_metrics(run_log, "step4_judge.rank_candidates") or {}).get("run_id") \
        or "step4_judge.run#1"
    refine_run_ref = (latest_exec_metrics(run_log, "step5_refine.write_refined") or {}).get("run_id") \
        or "step5_refine.run#1"
    global_run_ref = (latest_exec_metrics(run_log, "step7_diagnose.run") or {}).get("run_id") \
        or "step7_diagnose.run#1"

    # Session-level judgments (one each).
    for dp, decision, reason in SESSION_DEFAULTS:
        ref = global_run_ref if dp in ("unknown_cluster", "global_quality") else session_run_ref
        write_judgment(project_dir, dp, decision, "session", None, ref, reason)

    # Cluster-level judgments + rewrite final_annotations.
    refined = refined_clusters(project_dir)
    final = final_annotations_clusters(project_dir)
    clusters = sorted(set(refined) | set(final), key=lambda c: (0, int(c)) if str(c).isdigit() else (1, c))
    rewritten = {}
    for cid in clusters:
        r = refined.get(cid, {})
        f = final.get(cid, {})
        # cluster-level judgments: always the "permissive" enum.
        for dp, decision, reason in CLUSTER_DEFAULTS:
            ref = refine_run_ref if dp == "refine_effect" else cluster_run_ref
            write_judgment(project_dir, dp, decision, "cluster", cid, ref,
                           reason + f" (cluster={cid})")
        # Rewrite final_annotations entry: take the pipeline's first_candidate
        # and confirm it as the arm's label. confidence from judgment
        # (label_confirmed→high; label_downgraded→low) overrides the
        # deterministic _confidence_evidence so arm differences surface in
        # the cell-level evaluation.
        first = r.get("first_candidate") or f.get("first_candidate")
        if first:
            label = first.get("cell_type") or "unknown"
            rewritten[cid] = {
                "label": label,
                "confidence": "high",  # arm-default: all label_confirmed
                "status": "decisive" if r.get("status") != "no_candidates" else "unknown",
                "first_candidate": first,
                "second_candidate": r.get("second_candidate") or f.get("second_candidate"),
                "first_count": r.get("first_count") or (first.get("marker_count")),
                "second_count": r.get("second_count") or 0,
                "gap_metrics": r.get("gap_metrics") or f.get("gap_metrics"),
                "top3_expression": f.get("top3_expression", []),
                "mean_top3_pct1": f.get("mean_top3_pct1"),
                "mean_top3_specificity": f.get("mean_top3_specificity"),
                "marker_gene_overlap_score": f.get("marker_gene_overlap_score"),
                "subcluster": r.get("subcluster"),
                "arm_decision_source": "default",
            }
        else:
            rewritten[cid] = {
                "label": "unknown",
                "confidence": "low",
                "status": "unknown",
                "first_candidate": None,
                "second_candidate": None,
                "first_count": 0,
                "second_count": 0,
                "gap_metrics": {},
                "top3_expression": f.get("top3_expression", []),
                "mean_top3_pct1": f.get("mean_top3_pct1"),
                "mean_top3_specificity": f.get("mean_top3_specificity"),
                "marker_gene_overlap_score": f.get("marker_gene_overlap_score"),
                "arm_decision_source": "default",
            }

    final_path = rewrite_final_annotations(project_dir, rewritten)
    summary = {
        "n_clusters": len(rewritten),
        "n_unknown": sum(1 for v in rewritten.values() if v.get("status") == "unknown"),
        "unknown_rate": (sum(1 for v in rewritten.values() if v.get("status") == "unknown") / max(len(rewritten), 1)),
        "n_unique_labels": len({v.get("label") for v in rewritten.values() if v.get("label")}),
        "run_count": len([r for r in run_log if r.get("type") == "exec"]),
        "judgment_count": len([r for r in load_run_log(project_dir) if r.get("type") == "judgment"]),
    }
    end = write_session_end(project_dir, summary)
    return {
        "clusters_rewritten": len(rewritten),
        "final_annotations": final_path,
        "session_end": end,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="default_judge.py",
                                 description="B1 arm 1: ① Fixed-default judge (全 accept, 不读 metrics)")
    ap.add_argument("--project-dir", required=True)
    args = ap.parse_args()
    result = judge(args.project_dir)
    for k, v in result.items():
        print(f"[default_judge] {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())