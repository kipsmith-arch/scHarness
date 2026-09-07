"""① Fixed-default judge — decide() callback (never adjust, never refine)."""
from __future__ import annotations

import argparse
import sys

from ._common import cluster_entry, commit_label_patches, routing_accept_from_precheck

ACCEPT = {
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
}

DEFAULT_RESOLUTION = "0.8"


class DefaultJudge:
    def __init__(self, resolution: str = DEFAULT_RESOLUTION):
        self.resolution = resolution
        self._labels: dict[str, dict] = {}

    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        if dp not in ACCEPT:
            return {
                "decision": "quality_good",
                "confidence": "low",
                "action": "proceed",
                "reasoning": f"arm-default: unknown decision_point {dp}",
                "driver": {"kind": "proceed"},
            }
        cid = str((scope or {}).get("cluster_id") or "")
        driver: dict = {"kind": "proceed"}
        if dp == "cross_species_routing" and project_dir:
            routed = routing_accept_from_precheck(project_dir)
            routed["reasoning"] = "arm-default: " + routed["reasoning"]
            return routed
        if dp == "resolution_select":
            driver["node_args"] = {
                "step1_prepare.run": {"target_resolution": self.resolution},
                "step1_prepare.recluster": {"target_resolution": self.resolution},
            }
            driver["params"] = {"target_resolution": self.resolution}
        if dp == "label_confirm" and cid and cid != "_none" and project_dir:
            entry = cluster_entry(project_dir, cid)
            first = entry.get("first_candidate") or {}
            if first.get("cell_type") and entry.get("status") != "no_candidates":
                self._labels[cid] = {
                    "label": first["cell_type"],
                    "confidence": "high",
                    "status": "decisive",
                    "arm_decision_source": "default",
                }
            else:
                self._labels[cid] = {
                    "label": "unknown", "confidence": "low", "status": "unknown",
                    "arm_decision_source": "default",
                }
        blind = exec_record is None or not (exec_record.get("metrics") or {})
        return {
            "decision": ACCEPT[dp],
            "confidence": "high",
            "action": "proceed",
            "reasoning": (
                "arm-default: 盲判,不读 metrics" if blind
                else f"arm-default: accept without inspection ({dp})"
            ),
            "driver": driver,
        }

    def commit_labels(self, project_dir, history):
        if self._labels:
            commit_label_patches(project_dir, self._labels)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="default_judge.py",
        description="B1 arm 1 judge (callback). Use experiments/scripted_driver.py --arm default",
    )
    ap.add_argument("--project-dir", required=False)
    ap.parse_args()
    print("default_judge is a decide() callback; run: "
          "python experiments/scripted_driver.py --arm default --project-dir ... --raw ...",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
