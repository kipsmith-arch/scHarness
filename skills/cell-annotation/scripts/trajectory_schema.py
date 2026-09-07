"""Shared trajectory schema constants used by judgment writers and validators."""

from __future__ import annotations

# Decision point -> required scope type, sourced from trajectory_design.md §3.1.
REQUIRED_SCOPE: dict[str, str] = {
    "qc_threshold": "session",
    "resolution_select": "session",
    "clustering_quality": "session",
    "batch_effect": "session",
    "de_method": "session",
    "marker_quality": "session",
    "kg_match": "session",
    "cross_species_routing": "session",
    "unknown_cluster": "session",
    "global_quality": "session",
    "candidate_gap": "cluster",
    "candidate_disambiguate": "cluster",
    "refine_effect": "cluster",
    "label_confirm": "cluster",
}

__all__ = ["REQUIRED_SCOPE"]
