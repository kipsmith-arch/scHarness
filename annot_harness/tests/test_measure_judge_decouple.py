"""I/O matrix for measure/judge decoupling (spec-measure-judge-decouple).

Fake dispatch only — no h5ad load. Covers ARM1_NO_RETRY / ARM2_RETRY /
CAP_PROCEED / CAP_NO_OK / RETRY_CAP / ARM2_REFINE / PIPELINE_NO_ENUM /
RENAME / EVALUATE / RES_EXPLICIT.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from annot_harness.dag import MAX_ATTEMPTS, DecisionAfter, Node
from annot_harness.scripted_driver import ScriptedRunError, run_scripted

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_SCRIPTS = PROJECT_ROOT / "skills" / "cell-annotation" / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class AcceptJudge:
    """①-like: never adjust, never route refine, always accept."""

    def __init__(self, resolution: str = "0.8"):
        self.resolution = resolution
        self._labels: dict[str, dict] = {}

    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        accept = {
            "qc_threshold": "threshold_default",
            "resolution_select": "resolution_chosen",
            "clustering_quality": "clustering_accept",
            "batch_effect": "well_mixed",
            "de_method": "wilcoxon",
            "marker_quality": "markers_accept",
            "kg_match": "id_match_ok",
            "candidate_gap": "first_decisive",
            "candidate_disambiguate": "ambiguous_true",
            "refine_effect": "refine_skipped",
            "unknown_cluster": "multiple_unknown_types",
            "label_confirm": "label_confirmed",
            "global_quality": "quality_good",
        }
        driver: dict = {"kind": "proceed"}
        if dp == "resolution_select":
            driver["node_args"] = {
                "step1_prepare.run": {"target_resolution": self.resolution},
            }
        cid = (scope or {}).get("cluster_id")
        if dp == "label_confirm" and cid and cid != "_none":
            self._labels[str(cid)] = {
                "label": "root", "confidence": "high", "status": "decisive",
            }
        return {
            "decision": accept[dp],
            "confidence": "high",
            "action": "proceed",
            "reasoning": "default: accept without inspection" if exec_record else "default: blind accept",
            "driver": driver,
        }

    def commit_labels(self, project_dir, history):
        _write_labels(project_dir, self._labels)


class AdjustThenAcceptJudge(AcceptJudge):
    """Returns clustering_adjust on first clustering_quality, then accept."""

    def __init__(self):
        super().__init__()
        self._cluster_seen = 0

    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        if dp == "clustering_quality":
            self._cluster_seen += 1
            if self._cluster_seen == 1:
                return {
                    "decision": "clustering_adjust",
                    "confidence": "high",
                    "action": "retry",
                    "reasoning": "silhouette low, recluster",
                    "driver": {"kind": "retry", "params": {"target_resolution": "0.6"}},
                }
        return super().decide(dp, exec_record, history, scope=scope, project_dir=project_dir)


class AlwaysAdjustJudge(AcceptJudge):
    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        if dp == "clustering_quality":
            return {
                "decision": "clustering_adjust",
                "confidence": "high",
                "action": "retry",
                "reasoning": "always adjust",
                "driver": {"kind": "retry", "params": {"target_resolution": "0.5"}},
            }
        return super().decide(dp, exec_record, history, scope=scope, project_dir=project_dir)


class RefineClusterCJudge(AcceptJudge):
    def decide(self, dp, exec_record, history, *, scope=None, project_dir=None):
        cid = str((scope or {}).get("cluster_id") or "")
        if dp == "candidate_gap":
            if cid == "C":
                return {
                    "decision": "ambiguous_true",
                    "confidence": "high",
                    "action": "refine",
                    "reasoning": "cluster C ambiguous",
                    "driver": {
                        "kind": "proceed",
                        "node_args": {"step5_refine.run": {"clusters": "C"}},
                    },
                }
            return {
                "decision": "first_decisive",
                "confidence": "high",
                "action": "proceed",
                "reasoning": f"cluster {cid} decisive",
                "driver": {"kind": "proceed"},
            }
        return super().decide(dp, exec_record, history, scope=scope, project_dir=project_dir)


def _write_labels(project_dir: str, labels: dict) -> None:
    if not labels:
        return
    path = Path(project_dir) / "step6_validate" / "final_annotations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"annotations": {}}
    anns = payload.setdefault("annotations", {})
    for cid, patch in labels.items():
        anns.setdefault(cid, {})
        anns[cid].update(patch)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tiny_dag(cluster_ids=("A", "C")):
    def clusters(_project_dir, _history):
        return list(cluster_ids)

    clustering = DecisionAfter(
        "clustering_quality",
        accept_decision="clustering_accept",
        retry_on=("clustering_adjust",),
    )
    gap = DecisionAfter(
        "candidate_gap", scope="cluster", clusters_from=clusters,
        accept_decision="first_decisive",
    )
    refine_effect = DecisionAfter(
        "refine_effect", scope="cluster", clusters_from=clusters,
        accept_decision="refine_skipped",
    )
    label = DecisionAfter(
        "label_confirm", scope="cluster", clusters_from=clusters,
        accept_decision="label_confirmed",
    )
    return [
        Node(
            id="step1_prepare.run",
            tool="step1_prepare__run",
            retry_tool="step1_prepare__recluster",
            decision_after=[clustering],
            ops=("leiden_cluster",),
        ),
        Node(
            id="step4_rank.run",
            tool="step4_rank__run",
            deps=["step1_prepare.run"],
            decision_after=[gap],
            ops=("rank_candidates",),
        ),
        Node(
            id="step5_refine.run",
            tool="step5_refine__run",
            deps=["step4_rank.run"],
            skippable=True,
            require_args=("clusters",),
            decision_after=[refine_effect],
            ops=("subcluster",),
        ),
        Node(
            id="step6_validate.run",
            tool="step6_validate__run",
            deps=["step4_rank.run"],
            decision_after=[label],
            ops=("write_final",),
        ),
    ]


def _runtime():
    return {
        "step1_prepare__run": {"type": "fake", "subcommand": "run", "step": "step1_prepare", "op": "leiden_cluster"},
        "step1_prepare__recluster": {"type": "fake", "subcommand": "recluster", "step": "step1_prepare", "op": "leiden_cluster"},
        "step4_rank__run": {"type": "fake", "subcommand": "run", "step": "step4_rank", "op": "rank_candidates"},
        "step5_refine__run": {"type": "fake", "subcommand": "run", "step": "step5_refine", "op": "subcluster"},
        "step6_validate__run": {"type": "fake", "subcommand": "run", "step": "step6_validate", "op": "write_final"},
    }


def _make_fake(project_dir: Path, *, fail_first_n: int = 0, fail_always: bool = False):
    """Return (dispatch_fn, calls) where calls is a list of (tool, args)."""
    calls: list[tuple[str, dict]] = []
    fail_left = {"n": fail_first_n}

    def dispatch(spec, args, state):
        node_id = spec.get("_node_id", "")
        step = spec.get("step", "step")
        op = spec.get("op", "op")
        tool_key = f"{step}__{spec.get('subcommand', 'run')}"
        calls.append((spec.get("subcommand"), dict(args), node_id, tool_key))
        log_path = project_dir / "run_log.jsonl"
        if fail_always or fail_left["n"] > 0:
            fail_left["n"] -= 1
            return {"status": "error", "error": "injected failure"}
        # write an exec record the way pipeline scripts would
        common = _load_module("common", SKILL_SCRIPTS / "common.py")
        metrics = {"ok": True}
        if step == "step4_rank":
            metrics = {"n_clusters": 2, "cluster_ids": ["A", "C"],
                       "annotations": {"A": {"status": "has_candidates"},
                                       "C": {"status": "has_candidates"}}}
            rank_dir = project_dir / "step4_rank"
            rank_dir.mkdir(parents=True, exist_ok=True)
            (rank_dir / "annotations.json").write_text(json.dumps({
                "annotations": {
                    "A": {"status": "has_candidates", "first_count": 10, "second_count": 1,
                          "first_candidate": {"cell_type": "root"}, "gap_metrics": {"count_ratio": 10}},
                    "C": {"status": "has_candidates", "first_count": 4, "second_count": 4,
                          "first_candidate": {"cell_type": "x"}, "second_candidate": {"cell_type": "y"},
                          "gap_metrics": {"count_ratio": 1.0}},
                }
            }), encoding="utf-8")
        if step == "step6_validate":
            out = project_dir / "step6_validate"
            out.mkdir(parents=True, exist_ok=True)
            payload = {
                "annotations": {
                    "A": {"first_candidate": {"cell_type": "root"}, "first_count": 10,
                          "gap_metrics": {"count_ratio": 10}},
                    "C": {"first_candidate": {"cell_type": "x"}, "first_count": 4,
                          "gap_metrics": {"count_ratio": 1.0}},
                }
            }
            (out / "final_annotations.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        common.exec_record(str(log_path), step, op, dict(args), metrics)
        return {"status": "ok", "data": {"op": op}}

    return dispatch, calls


def test_arm1_no_retry_skips_refine_and_writes_session_start(tmp_path):
    dispatch, calls = _make_fake(tmp_path)
    result = run_scripted(
        _tiny_dag(), AcceptJudge(), _runtime(), str(tmp_path),
        dispatch_fn=dispatch,
    )
    assert result["ok"]
    log = _load_log(tmp_path / "run_log.jsonl")
    types = [r["type"] for r in log]
    assert types[0] == "session_start"
    assert types[-1] == "session_end"
    run_ids = [r.get("run_id") for r in log if r.get("type") == "exec"]
    assert not any(rid and "recluster" in rid for rid in run_ids)
    assert "step1_prepare.leiden_cluster#2" not in run_ids
    assert not any(c[2] == "step5_refine.run" for c in calls)
    assert "step5_refine.run" in result["nodes_skipped"]
    assert any(r.get("run_id") == "step1_prepare.leiden_cluster#1" for r in log)


def test_arm2_retry_emits_leiden_cluster_hash2(tmp_path):
    dispatch, calls = _make_fake(tmp_path)
    run_scripted(
        _tiny_dag(), AdjustThenAcceptJudge(), _runtime(), str(tmp_path),
        dispatch_fn=dispatch,
    )
    log = _load_log(tmp_path / "run_log.jsonl")
    run_ids = [r.get("run_id") for r in log if r.get("type") == "exec"]
    assert "step1_prepare.leiden_cluster#1" in run_ids
    assert "step1_prepare.leiden_cluster#2" in run_ids
    reclusters = [c for c in calls if c[0] == "recluster"]
    assert len(reclusters) == 1
    assert reclusters[0][1].get("target_resolution") == "0.6"


def test_cap_proceed_no_hash7(tmp_path):
    dispatch, _calls = _make_fake(tmp_path)
    result = run_scripted(
        _tiny_dag(), AlwaysAdjustJudge(), _runtime(), str(tmp_path),
        dispatch_fn=dispatch,
    )
    assert result["ok"]
    log = _load_log(tmp_path / "run_log.jsonl")
    run_ids = [r.get("run_id") for r in log if r.get("type") == "exec"]
    assert "step1_prepare.leiden_cluster#6" in run_ids
    assert not any(rid and rid.endswith("#7") for rid in run_ids)
    cap = [r for r in log if r.get("type") == "judgment"
           and (r.get("output") or {}).get("action") == "cap_exhausted_proceed"]
    assert cap, "expected cap_exhausted_proceed judgment"
    assert cap[0]["output"]["decision"] == "clustering_accept"


def test_cap_no_ok_session_fails_without_labels(tmp_path):
    dispatch, _ = _make_fake(tmp_path, fail_always=True)
    with pytest.raises(ScriptedRunError):
        run_scripted(
            _tiny_dag(), AlwaysAdjustJudge(), _runtime(), str(tmp_path),
            dispatch_fn=dispatch,
        )
    log = _load_log(tmp_path / "run_log.jsonl")
    ends = [r for r in log if r.get("type") == "session_end"]
    assert ends and ends[-1]["final_summary"].get("ok") is False
    final = tmp_path / "step6_validate" / "final_annotations.json"
    if final.exists():
        payload = json.loads(final.read_text(encoding="utf-8"))
        anns = payload.get("annotations") or {}
        assert not any("label" in v for v in anns.values())


def test_retry_cap_common_refuses_hash7(tmp_path):
    common = _load_module("common", SKILL_SCRIPTS / "common.py")
    log = tmp_path / "run_log.jsonl"
    for _ in range(MAX_ATTEMPTS):
        common.exec_record(str(log), "step1_prepare", "leiden_cluster", {}, {"ok": True})
    with pytest.raises(common.AttemptCapExceeded):
        common.next_run_id(str(log), "step1_prepare", "leiden_cluster")
    with pytest.raises(common.AttemptCapExceeded):
        common.exec_record(str(log), "step1_prepare", "leiden_cluster", {}, {"nope": True})
    run_ids = [r.get("run_id") for r in _load_log(log) if r.get("type") == "exec"]
    assert not any(rid.endswith("#7") for rid in run_ids)


def test_arm2_refine_only_cluster_c(tmp_path):
    dispatch, calls = _make_fake(tmp_path)
    result = run_scripted(
        _tiny_dag(), RefineClusterCJudge(), _runtime(), str(tmp_path),
        dispatch_fn=dispatch,
    )
    assert result["ok"]
    refine_calls = [c for c in calls if c[2] == "step5_refine.run"]
    assert len(refine_calls) == 1
    assert refine_calls[0][1].get("clusters") == "C"


def test_pipeline_no_enum_step4_rank(tmp_path):
    step4 = _load_module("step4_rank", SKILL_SCRIPTS / "step4_rank.py")
    common = _load_module("common", SKILL_SCRIPTS / "common.py")
    kg_dir = tmp_path / "step3c_kg"
    kg_dir.mkdir()
    kg = {
        "ancestors": {"root": [], "leaf": ["plant"]},
        "per_cluster": {
            "0": {
                "candidates": [
                    {"cell_type": "root", "marker_count": 8, "mean_confidence": 0.9,
                     "supporting_markers": ["a"], "sources": [], "organ": ["Root"],
                     "organ_status": "root"},
                    {"cell_type": "leaf", "marker_count": 2, "mean_confidence": 0.4,
                     "supporting_markers": ["b"], "sources": [], "organ": ["Leaf"],
                     "organ_status": "mismatch"},
                ]
            }
        },
    }
    (kg_dir / "kg_hits.json").write_text(json.dumps(kg), encoding="utf-8")
    args = type("A", (), {"project_dir": str(tmp_path), "input": None})()
    result = step4.cmd_run(args)
    assert result["status"] == "ok"
    ann_path = tmp_path / "step4_rank" / "annotations.json"
    payload = json.loads(ann_path.read_text(encoding="utf-8"))
    blob = json.dumps(payload)
    for banned in ("first_decisive", "label_confirmed", "high", "medium", "low",
                   "label_downgraded"):
        assert banned not in blob
    entry = payload["annotations"]["0"]
    assert entry["status"] == "has_candidates"
    assert "label" not in entry
    assert "confidence" not in entry
    log = _load_log(tmp_path / "run_log.jsonl")
    assert all(r.get("run_id", "").startswith("step4_rank.") for r in log if r.get("run_id"))
    assert common.STEP_DIRS["step4_rank"] == "step4_rank"
    assert "step4_judge" not in common.STEP_DIRS


def test_rename_old_path_not_used(tmp_path):
    common = _load_module("common", SKILL_SCRIPTS / "common.py")
    assert "step4_judge" not in common.STEP_DIRS
    assert (SKILL_SCRIPTS / "step4_rank.py").exists()
    assert not (SKILL_SCRIPTS / "step4_judge.py").exists()
    with pytest.raises(ValueError, match="step4_rank"):
        common.step_dir(str(tmp_path), "step4_judge")
    assert "step3_kg_precheck" not in common.STEP_DIRS
    assert "step3_cross_species_map" not in common.STEP_DIRS
    assert "step3_kg" not in common.STEP_DIRS
    assert (SKILL_SCRIPTS / "step3a_kg_precheck.py").exists()
    assert (SKILL_SCRIPTS / "step3b_cross_species_map.py").exists()
    assert (SKILL_SCRIPTS / "step3c_kg.py").exists()
    with pytest.raises(ValueError, match="step3a_kg_precheck"):
        common.step_dir(str(tmp_path), "step3_kg_precheck")
    with pytest.raises(ValueError, match="step3b_cross_species_map"):
        common.step_dir(str(tmp_path), "step3_cross_species_map")
    with pytest.raises(ValueError, match="step3c_kg"):
        common.step_dir(str(tmp_path), "step3_kg")


def test_evaluate_missing_judge_fields(tmp_path):
    sys.path.insert(0, str(PROJECT_ROOT))
    from experiments.evaluate_cell_level import load_final_annotations
    path = tmp_path / "final_annotations.json"
    path.write_text(json.dumps({
        "annotations": {"0": {"first_candidate": {"cell_type": "root"}}}
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_final_annotations(str(path))
    assert "判断层" in str(exc.value)


def test_res_explicit_rejects_missing_target(tmp_path):
    step1 = _load_module("step1_prepare", SKILL_SCRIPTS / "step1_prepare.py")
    args = type("A", (), {
        "project_dir": str(tmp_path),
        "input": str(tmp_path / "x.h5ad"),
        "target_resolution": None,
        "resolution_list": "0.4,0.8",
        "organ": "root",
    })()
    result = step1.cmd_run(args)
    assert result["status"] == "error"
    assert "target-resolution" in result["error"]


def test_subcluster_delivery_keeps_computed_names():
    step5 = _load_module("step5_refine", SKILL_SCRIPTS / "step5_refine.py")
    got = step5.subcluster_delivery(
        ["c0", "c1", "c2"],
        ["0", "1", "0"],
        {
            "0": {"first_candidate": "columella root cap cell"},
            "1": {"first_candidate": None},
        },
    )
    assert got["labels"] == {"0": "columella root cap cell", "1": "unknown"}
    assert got["cell_subcluster"] == {"c0": "0", "c1": "1", "c2": "0"}


def test_step5_refuses_without_clusters(tmp_path):
    step5 = _load_module("step5_refine", SKILL_SCRIPTS / "step5_refine.py")
    for name in ("step4_rank", "step3c_kg", "step2_markers"):
        d = tmp_path / name
        d.mkdir()
    (tmp_path / "step4_rank" / "annotations.json").write_text(
        json.dumps({"annotations": {"0": {"status": "has_candidates"}}}), encoding="utf-8")
    (tmp_path / "step3c_kg" / "kg_hits.json").write_text(
        json.dumps({"query_config": {"organ": "root"}}), encoding="utf-8")
    (tmp_path / "step2_markers" / "markers.json").write_text(
        json.dumps({"per_cluster": {}}), encoding="utf-8")
    args = type("A", (), {
        "project_dir": str(tmp_path), "input": None, "clusters": None,
        "subcluster_resolution": 0.5, "min_cells": 100,
        "subcluster_n_pcs": 15, "subcluster_n_neighbors": 15,
        "sub_de_n_genes": 50, "sub_top_n": 10,
        "min_pct1": 0.5, "max_pct1": 0.9, "min_pct1_pct2": 0.25,
    })()
    result = step5.cmd_run(args)
    assert result["status"] == "error"
    assert "--clusters" in result["error"] or "clusters" in result["error"]


def test_cell_annotation_dag_binds_14_and_47():
    from experiments.cell_annotation_dag import (
        CELL_ANNOTATION_DAG, unique_pipeline_ops,
    )
    from annot_harness.dag import all_decision_points
    assert len(unique_pipeline_ops()) == 47
    assert len(all_decision_points(CELL_ANNOTATION_DAG)) == 14
    tools = {n.tool for n in CELL_ANNOTATION_DAG}
    assert "step4_rank__run" in tools
    assert "step4_judge__run" not in tools
    assert "step3a_kg_precheck__run" in tools
    assert "step3b_cross_species_map__run" in tools
    assert "step3c_kg__query" in tools
