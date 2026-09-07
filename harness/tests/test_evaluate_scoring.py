"""B1 scoring: strict/relaxed from label–map only (b1-r3-followup.md §2)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from experiments.evaluate_cell_level import evaluate_arm, load_gt, load_label_map


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_low_confidence_exact_counts_as_strict(tmp_path):
    obs = tmp_path / "obs_snapshot.csv"
    gt = tmp_path / "gt.csv"
    ann = tmp_path / "final_annotations.json"
    lmap = tmp_path / "label_map.json"

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
    lmap.write_text(json.dumps({
        "_meta": {"verified": True},
        "entries": [
            {"predicted": "lateral root cap", "true": "Lateral Root Cap", "relation": "synonym"},
        ],
    }), encoding="utf-8")

    label_map, meta = load_label_map(str(lmap))
    gt_map = load_gt(str(gt))
    report = evaluate_arm("arm_t", str(tmp_path), str(obs), str(ann), label_map, meta, gt_map)

    by_cell = {c["cell"]: c for c in report["per_cell"]}
    assert by_cell["c0"]["is_strict_correct"] is True
    assert by_cell["c1"]["is_strict_correct"] is False
    assert by_cell["c2"]["is_strict_correct"] is True
    assert by_cell["c0"]["cell_weight"] == 1.0
    assert by_cell["c1"]["cell_weight"] == 0.0
    assert report["strict_accuracy"] == 0.6667
    assert report["relaxed_accuracy"] == 0.6667
    assert report["low_conf_rate"] == 0.6667
    assert report["confidence_distribution"].get("low") == 2
