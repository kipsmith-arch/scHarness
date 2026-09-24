"""B1 scoring: strict/relaxed from GT-pin + hierarchy (b1-r3-followup.md §2)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from experiments.evaluate_cell_level import evaluate_arm, load_gt, main
from experiments.ontology_eval import Hierarchy, OntologyScorer


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _scorer() -> OntologyScorer:
    return OntologyScorer(
        pins={
            "Lateral Root Cap": "lateral root cap",
            "Root stele": "root stele",
            "Root cortex": "root cortex",
        },
        aliases={
            "lateral root cap": "lateral root cap",
            "root stele": "root stele",
        },
        hierarchy=Hierarchy.from_maps(
            {"lateral root cap", "root stele", "root cortex"},
            {
                "lateral root cap": set(),
                "root stele": set(),
                "root cortex": set(),
            },
        ),
    )


def test_low_confidence_exact_counts_as_strict(tmp_path):
    obs = tmp_path / "obs_snapshot.csv"
    gt = tmp_path / "gt.csv"
    ann = tmp_path / "final_annotations.json"

    _write_csv(obs, ["cell_id", "leiden"], [
        {"cell_id": "c0", "leiden": "0"},
        {"cell_id": "c1", "leiden": "1"},
        {"cell_id": "c2", "leiden": "2"},
    ])
    _write_csv(gt, ["cell_barcode", "true_type"], [
        {"cell_barcode": "c0", "true_type": "Lateral Root Cap"},
        {"cell_barcode": "c1", "true_type": "Lateral Root Cap"},
        {"cell_barcode": "c2", "true_type": "Lateral Root Cap"},
    ])
    ann.write_text(json.dumps({
        "annotations": {
            "0": {"label": "lateral root cap", "confidence": "low", "status": "decisive"},
            "1": {"label": "unknown", "confidence": "low", "status": "unknown"},
            "2": {"label": "lateral root cap", "confidence": "high", "status": "decisive"},
        }
    }), encoding="utf-8")

    report = evaluate_arm(
        "arm_t", str(tmp_path), str(obs), str(ann), _scorer(), load_gt(str(gt)),
    )

    by_cell = {c["cell"]: c for c in report["per_cell"]}
    assert by_cell["c0"]["is_strict_correct"] is True
    assert by_cell["c1"]["is_strict_correct"] is False
    assert by_cell["c2"]["is_strict_correct"] is True
    assert by_cell["c0"]["cell_weight"] == 1.0
    assert by_cell["c1"]["cell_weight"] == 0.0
    assert report["accuracy"] == 0.6667
    assert report["hierarchy_score"] == 0.6667
    assert report["low_conf_rate"] == 0.6667
    assert report["confidence_distribution"].get("low") == 2


def test_evaluate_arm_scores_against_this_cell_gt(tmp_path):
    obs = tmp_path / "obs_snapshot.csv"
    gt = tmp_path / "gt.csv"
    ann = tmp_path / "final_annotations.json"
    _write_csv(obs, ["cell_id", "leiden"], [
        {"cell_id": "stele", "leiden": "0"},
        {"cell_id": "cortex", "leiden": "0"},
    ])
    _write_csv(gt, ["cell_barcode", "true_type"], [
        {"cell_barcode": "stele", "true_type": "Root stele"},
        {"cell_barcode": "cortex", "true_type": "Root cortex"},
    ])
    ann.write_text(json.dumps({
        "annotations": {
            "0": {"label": "root stele", "confidence": "high", "status": "decisive"},
        }
    }), encoding="utf-8")

    report = evaluate_arm(
        "arm_t", str(tmp_path), str(obs), str(ann), _scorer(), load_gt(str(gt)),
    )
    by_cell = {c["cell"]: c for c in report["per_cell"]}
    assert by_cell["stele"]["relation"] == "synonym"
    assert by_cell["stele"]["is_strict_correct"] is True
    assert by_cell["cortex"]["relation"] == "unrelated"
    assert by_cell["cortex"]["is_strict_correct"] is False
    assert report["accuracy"] == 0.5


def test_label_map_cli_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "evaluate_cell_level.py",
        "--arms", "arm1=output/missing",
        "--label-map", "experiments/label_map.json",
    ])
    with pytest.raises(SystemExit, match="--label-map"):
        main()
