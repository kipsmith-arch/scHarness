"""② Rule-based judge — decide() callback. Threshold numbers unchanged (Story 6.9)."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    cluster_entry,
    commit_label_patches,
    latest_exec_metrics,
    load_run_log,
    rank_annotations,
    refined_clusters,
    routing_accept_from_precheck,
)

# Story 6.9 calibration — do not retune in this refactor.
DIFF_THRESH_FOR_DECISIVE = 3
GAP_RATIO_TIED = 1.2
PCT2_HIGH_THRESHOLD = 0.6
HIGH_CONFIDENCE_RATIO = 2.0
MEDIUM_CONFIDENCE_RATIO = 1.01
DEFAULT_RESOLUTION = "0.8"


def _is_ancestor_overlap(gap: dict) -> bool:
    ov = gap.get("first_second_ancestor_overlap") or {}
    if ov.get("related") is True:
        return True
    return bool(ov.get("first_is_ancestor_of_second"))


def _gap_decision(entry: dict) -> tuple[str, str]:
    first = entry.get("first_candidate")
    status = entry.get("status", "no_candidates")
    if not first or status == "no_candidates":
        return "unknown", "无 KG 候选,rule_judge 标 unknown"
    first_count = entry.get("first_count") or 0
    second_count = entry.get("second_count") or 0
    gap = entry.get("gap_metrics") or {}
    ratio = gap.get("count_ratio") or 0.0
    diff = gap.get("count_diff") or (first_count - second_count)
    if diff >= DIFF_THRESH_FOR_DECISIVE:
        return "first_decisive", f"count_diff={diff}>={DIFF_THRESH_FOR_DECISIVE},decisive"
    if ratio >= HIGH_CONFIDENCE_RATIO and diff < DIFF_THRESH_FOR_DECISIVE:
        return "ambiguous_true", (
            f"陷阱3:count_ratio={ratio:.2f}>=2 但 count_diff={diff}<{DIFF_THRESH_FOR_DECISIVE}"
        )
    if _is_ancestor_overlap(gap):
        return "ambiguous_parent_child", f"陷阱2:count_diff={diff}且 ancestor_overlap"
    if ratio < GAP_RATIO_TIED and second_count > 0:
        return "ambiguous_synonym", f"first≈second(ratio={ratio:.2f}<{GAP_RATIO_TIED})"
    return "first_decisive", f"diff={diff} ratio={ratio:.2f},默认 first_decisive"


class RuleJudge:
    def __init__(self):
        self._labels: dict[str, dict] = {}
        self._gap: dict[str, str] = {}

    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        cid = str((scope or {}).get("cluster_id") or "")
        metrics = (exec_record or {}).get("metrics") or {}
        run_log = load_run_log(project_dir) if project_dir else []

        if dp == "qc_threshold":
            qc = latest_exec_metrics(run_log, "step1_prepare.qc_distribution") or metrics
            cp_dist = qc.get("cp_distribution") or qc.get("chloroplast_distribution") or {}
            p90_cp = (cp_dist.get("p90") if isinstance(cp_dist, dict) else None)
            decision = "threshold_set" if (isinstance(p90_cp, (int, float)) and p90_cp > 0.05) else "threshold_default"
            return _j(decision, f"陷阱1: cp p90={p90_cp}")

        if dp == "resolution_select":
            return _j(
                "resolution_chosen",
                f"rule: 显式分辨率 {DEFAULT_RESOLUTION}",
                driver={"kind": "proceed", "params": {"target_resolution": DEFAULT_RESOLUTION},
                        "node_args": {
                            "step1_prepare.run": {"target_resolution": DEFAULT_RESOLUTION},
                            "step1_prepare.recluster": {"target_resolution": DEFAULT_RESOLUTION},
                        }},
            )

        if dp == "clustering_quality":
            sil = (latest_exec_metrics(run_log, "step1_prepare.leiden_cluster") or metrics).get("silhouette_overall")
            return _j("clustering_accept", f"silhouette={sil},连续谱下 accept(rule 不盲目 recluster)")

        if dp == "batch_effect":
            batch = latest_exec_metrics(run_log, "step1_prepare.batch_mixing") or {}
            batch_entropy = batch.get("overall_entropy") or batch.get("shannon_entropy")
            decision = "well_mixed" if (isinstance(batch_entropy, (int, float)) and batch_entropy >= 1.4) else "batch_effect"
            return _j(decision, f"陷阱6: batch_entropy={batch_entropy}")

        if dp == "de_method":
            return _j("wilcoxon", "rule: 默认 wilcoxon(稀有 < 5%)")

        if dp == "marker_quality":
            return _j("markers_accept", "rule: marker 漏斗过线,accept")

        if dp == "cross_species_routing" and project_dir:
            routed = routing_accept_from_precheck(project_dir)
            routed["reasoning"] = "rule: " + routed["reasoning"]
            return routed

        if dp == "kg_match":
            return _j("id_match_ok", "rule: id_match_ok(TAIR ID 物种特异)")

        if dp == "candidate_gap" and project_dir and cid and cid != "_none":
            entry = cluster_entry(project_dir, cid)
            decision, reason = _gap_decision(entry)
            self._gap[cid] = decision
            driver: dict = {"kind": "proceed"}
            if decision == "ambiguous_true":
                driver["node_args"] = {"step5_refine.run": {"clusters": cid}}
            return _j(decision, reason + f" (cluster={cid})", driver=driver)

        if dp == "candidate_disambiguate" and cid:
            gap_d = self._gap.get(cid, "first_decisive")
            if gap_d in ("ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true"):
                return _j(gap_d, f"gap={gap_d},confirm 路由 (cluster={cid})")
            return _j("ambiguous_true", f"gap={gap_d},disambiguate 占位 (cluster={cid})")

        if dp == "refine_effect" and project_dir and cid:
            r = refined_clusters(project_dir).get(cid) or {}
            sub = r.get("subcluster") or {}
            if sub.get("status") == "analyzed" or r.get("status") == "analyzed":
                if sub.get("split_resolved") or sub.get("outcome") == "analyzed":
                    return _j("refine_effective", f"subcluster 分出有意义的子簇 (cluster={cid})")
                return _j("refine_ineffective", f"subcluster 未分离 (cluster={cid})")
            return _j("refine_skipped", f"无 subcluster (cluster={cid})")

        if dp == "unknown_cluster" and project_dir:
            refined = refined_clusters(project_dir) or rank_annotations(project_dir)
            n_unknown = sum(1 for r in refined.values()
                            if r.get("status") in ("no_candidates", "unknown"))
            decision = "single_unknown_type" if n_unknown <= 1 else "multiple_unknown_types"
            return _j(decision, f"rule: n_unknown_clusters={n_unknown}")

        if dp == "label_confirm" and project_dir and cid and cid != "_none":
            entry = cluster_entry(project_dir, cid)
            first = entry.get("first_candidate") or {}
            gap = entry.get("gap_metrics") or {}
            ratio = gap.get("count_ratio") or 0.0
            top3 = entry.get("top3_expression") or []
            if not first or entry.get("status") in ("no_candidates", "unknown"):
                self._labels[cid] = {
                    "label": "unknown", "confidence": "low", "status": "unknown",
                    "arm_decision_source": "rule",
                }
                return _j("label_unknown", f"无 first 候选 (cluster={cid})")
            label = first.get("cell_type") or "unknown"
            pct1_high = any(m.get("pct1", 0) > 0.5 for m in top3) if top3 else False
            pct2_high = any(m.get("pct2", 0) > PCT2_HIGH_THRESHOLD for m in top3) if top3 else False
            if pct1_high and pct2_high:
                decision, conf = "label_downgraded", "low"
                reason = "陷阱4: top-3 marker pct1 高且 pct2 也高"
            elif ratio < MEDIUM_CONFIDENCE_RATIO:
                decision, conf = "label_downgraded", "low"
                reason = f"low confidence(ratio={ratio:.2f}<{MEDIUM_CONFIDENCE_RATIO})"
            else:
                decision = "label_confirmed"
                conf = "high" if ratio >= HIGH_CONFIDENCE_RATIO else "medium"
                reason = f"ratio={ratio:.2f} pct1≠pct2,label 确认"
            self._labels[cid] = {
                "label": label, "confidence": conf, "status": "decisive",
                "arm_decision_source": "rule",
            }
            return _j(decision, reason + f" (cluster={cid})")

        if dp == "global_quality" and project_dir:
            refined = refined_clusters(project_dir) or rank_annotations(project_dir)
            n_total = len(refined) or 1
            n_unk = sum(1 for r in refined.values()
                        if r.get("status") in ("no_candidates", "unknown"))
            unk_rate = n_unk / n_total
            if unk_rate <= 0.05:
                return _j("quality_good", f"rule: unknown_rate={unk_rate:.2f}<=0.05")
            if unk_rate <= 0.20:
                return _j("quality_acceptable", f"rule: unknown_rate={unk_rate:.2f}<=0.20")
            return _j("quality_poor", f"rule: unknown_rate={unk_rate:.2f}>0.20")

        return _j( {
            "qc_threshold": "threshold_default",
            "resolution_select": "resolution_chosen",
            "clustering_quality": "clustering_accept",
            "batch_effect": "well_mixed",
            "de_method": "wilcoxon",
            "marker_quality": "markers_accept",
            "cross_species_routing": "routing_accept",
            "kg_match": "id_match_ok",
            "candidate_gap": "first_decisive",
            "candidate_disambiguate": "ambiguous_true",
            "refine_effect": "refine_skipped",
            "unknown_cluster": "multiple_unknown_types",
            "label_confirm": "label_confirmed",
            "global_quality": "quality_good",
        }.get(dp, "clustering_accept"), f"rule fallback for {dp}")

    def commit_labels(self, project_dir, history):
        if self._labels:
            commit_label_patches(project_dir, self._labels)


def _j(decision: str, reasoning: str, driver: dict | None = None) -> dict:
    return {
        "decision": decision,
        "confidence": "high",
        "action": "proceed",
        "reasoning": reasoning,
        "driver": driver or {"kind": "proceed"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="rule_judge.py",
        description="B1 arm 2 judge (callback). Use experiments/scripted_driver.py --arm rule",
    )
    ap.add_argument("--project-dir", required=False)
    ap.parse_args()
    print("rule_judge is a decide() callback; run: "
          "python experiments/scripted_driver.py --arm rule --project-dir ... --raw ...",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
