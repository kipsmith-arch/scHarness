"""Cell-annotation DAG instance (47 ops + 14 decision_after bindings).

Lives in experiments/ so harness stays domain-agnostic. The walker is
``harness.scripted_driver.run_scripted``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from harness.dag import DecisionAfter, Node, all_decision_points, all_ops

# Atomic op catalogs (design/atomic_operations.md). Unique across the pipeline = 47.
STEP1_OPS = (
    "load_data", "compute_qc", "qc_distribution", "qc_plot",
    "filter_cells", "filter_genes", "detect_doublets", "normalize",
    "select_hvg", "pca", "knn_graph", "leiden_cluster", "choose_resolution",
    "umap", "batch_mixing", "write_output",
)
STEP2_OPS = (
    "de_rank", "pct1_pct2", "pseudobulk_de", "filter_markers", "write_markers",
)
STEP3_OPS = (
    "connect", "query_genes", "query_hierarchy", "aggregate_candidates", "write_hits",
)
STEP4_OPS = ("rank_candidates", "write_annotations")
STEP5_OPS = (
    "candidate_autocorr", "subcluster", "subcluster_de", "subcluster_kg",
    "marker_overlap", "type_membership", "unknown_overlap", "write_refined",
)
STEP6_OPS = (
    "marker_expression", "violin_plot", "global_summary", "write_report", "write_final",
)
STEP7_OPS = (
    "hit_rate", "candidate_count", "first_second", "batch_entropy",
    "metadata_check", "cross_cluster",
)


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cluster_ids_from_rank(project_dir: str, history: dict) -> list[str]:
    """Cluster ids from step4_rank/annotations.json (measurement, not a decision)."""
    path = os.path.join(project_dir, "step4_rank", "annotations.json")
    data = _read_json(path)
    if isinstance(data, dict) and data.get("annotations"):
        return [str(c) for c in data["annotations"]]
    rec = history.get("step4_rank.run") or {}
    metrics = rec.get("metrics") or {}
    if metrics.get("cluster_ids"):
        return [str(c) for c in metrics["cluster_ids"]]
    anns = metrics.get("annotations") or {}
    if isinstance(anns, dict):
        return [str(c) for c in anns]
    return []


def cluster_ids_from_final(project_dir: str, history: dict) -> list[str]:
    path = os.path.join(project_dir, "step6_validate", "final_annotations.json")
    data = _read_json(path)
    if isinstance(data, dict) and data.get("annotations"):
        return [str(c) for c in data["annotations"]]
    return cluster_ids_from_rank(project_dir, history)


QC_THRESHOLD = DecisionAfter("qc_threshold", accept_decision="threshold_default")
RESOLUTION_SELECT = DecisionAfter("resolution_select", accept_decision="resolution_chosen")
CLUSTERING_QUALITY = DecisionAfter(
    "clustering_quality",
    accept_decision="clustering_accept",
    retry_on=("clustering_adjust",),
)
BATCH_EFFECT = DecisionAfter("batch_effect", accept_decision="well_mixed")
DE_METHOD = DecisionAfter("de_method", accept_decision="wilcoxon")
MARKER_QUALITY = DecisionAfter(
    "marker_quality",
    accept_decision="markers_accept",
    retry_on=("markers_adjust_filter",),
)
CROSS_SPECIES_ROUTING = DecisionAfter(
    "cross_species_routing",
    accept_decision="routing_accept",
)
KG_MATCH = DecisionAfter("kg_match", accept_decision="id_match_ok")
CANDIDATE_GAP = DecisionAfter(
    "candidate_gap",
    scope="cluster",
    clusters_from=cluster_ids_from_rank,
    accept_decision="first_decisive",
)
CANDIDATE_DISAMBIGUATE = DecisionAfter(
    "candidate_disambiguate",
    scope="cluster",
    clusters_from=cluster_ids_from_rank,
    accept_decision="ambiguous_true",
)
REFINE_EFFECT = DecisionAfter(
    "refine_effect",
    scope="cluster",
    clusters_from=cluster_ids_from_rank,
    accept_decision="refine_skipped",
)
UNKNOWN_CLUSTER = DecisionAfter("unknown_cluster", accept_decision="multiple_unknown_types")
LABEL_CONFIRM = DecisionAfter(
    "label_confirm",
    scope="cluster",
    clusters_from=cluster_ids_from_final,
    accept_decision="label_confirmed",
)
GLOBAL_QUALITY = DecisionAfter("global_quality", accept_decision="quality_good")


CELL_ANNOTATION_DAG: list[Node] = [
    Node(
        id="step1_prepare.metrics",
        tool="step1_prepare__metrics",
        decision_after=[QC_THRESHOLD, RESOLUTION_SELECT],
        ops=("load_data", "compute_qc", "qc_distribution", "qc_plot"),
    ),
    Node(
        id="step1_prepare.run",
        tool="step1_prepare__run",
        deps=["step1_prepare.metrics"],
        retry_tool="step1_prepare__recluster",
        decision_after=[CLUSTERING_QUALITY, BATCH_EFFECT],
        ops=STEP1_OPS,
    ),
    Node(
        id="step2_markers.run",
        tool="step2_markers__run",
        deps=["step1_prepare.run"],
        decision_before=[DE_METHOD],
        decision_after=[MARKER_QUALITY],
        ops=STEP2_OPS,
    ),
    Node(
        id="step3a_kg_precheck.run",
        tool="step3a_kg_precheck__run",
        deps=["step2_markers.run"],
        decision_after=[CROSS_SPECIES_ROUTING],
        ops=("target_coverage", "candidate_refs", "write_report"),
    ),
    Node(
        id="step3b_cross_species_map.run",
        tool="step3b_cross_species_map__run",
        deps=["step3a_kg_precheck.run"],
        skippable=True,
        require_args=("reference_species",),
        ops=("collect_marker_genes", "query_provider", "write_output"),
    ),
    Node(
        id="step3c_kg.query",
        tool="step3c_kg__query",
        deps=["step3b_cross_species_map.run"],
        decision_after=[KG_MATCH],
        ops=STEP3_OPS,
    ),
    Node(
        id="step3c_kg.test_connection",
        tool="step3c_kg__test-connection",
        deps=["step3c_kg.query"],
        ops=("connect",),
    ),
    Node(
        id="step4_rank.run",
        tool="step4_rank__run",
        deps=["step3c_kg.query"],
        decision_after=[CANDIDATE_GAP, CANDIDATE_DISAMBIGUATE],
        ops=STEP4_OPS,
    ),
    Node(
        id="step5_refine.run",
        tool="step5_refine__run",
        deps=["step4_rank.run"],
        skippable=True,
        require_args=("clusters",),
        decision_after=[REFINE_EFFECT],
        ops=STEP5_OPS,
    ),
    Node(
        id="step6_validate.run",
        tool="step6_validate__run",
        deps=["step4_rank.run"],
        decision_after=[LABEL_CONFIRM, UNKNOWN_CLUSTER],
        ops=STEP6_OPS,
    ),
    Node(
        id="step6_validate.report",
        tool="step6_validate__report",
        deps=["step6_validate.run"],
        ops=("write_report",),
    ),
    Node(
        id="step7_diagnose.run",
        tool="step7_diagnose__run",
        deps=["step6_validate.run"],
        decision_after=[GLOBAL_QUALITY],
        ops=STEP7_OPS,
    ),
]


def unique_pipeline_ops() -> list[str]:
    """47 unique atomic ops (recluster/metrics/report reuse is not re-counted)."""
    seen = []
    for name in (
        *STEP1_OPS, *STEP2_OPS, *STEP3_OPS, *STEP4_OPS,
        *STEP5_OPS, *STEP6_OPS, *STEP7_OPS,
    ):
        if name not in seen:
            seen.append(name)
    return seen


assert len(unique_pipeline_ops()) == 47, unique_pipeline_ops()
assert len(all_decision_points(CELL_ANNOTATION_DAG)) == 14, all_decision_points(CELL_ANNOTATION_DAG)
# catalog listing (with reuse on metrics/test-connection/report) is larger than 47
assert len(all_ops(CELL_ANNOTATION_DAG)) >= 47
