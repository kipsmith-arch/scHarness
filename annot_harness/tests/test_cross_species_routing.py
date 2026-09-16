"""SOP-3 routing helper for scripted ①② arms (KG coverage + optional homology)."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.judges._common import routing_accept_from_precheck


def _write_report(tmp_path: Path, payload: dict) -> str:
    d = tmp_path / "step3a_kg_precheck"
    d.mkdir()
    (d / "coverage_report.json").write_text(json.dumps(payload), encoding="utf-8")
    return str(tmp_path)


def test_routing_accept_skips_map_on_single_species(tmp_path):
    project = _write_report(tmp_path, {
        "recommended_strategy": "single_species",
        "target_species": "arabidopsis_thaliana",
        "recommended_reference_species": [{"species": "oryza_sativa"}],
    })
    out = routing_accept_from_precheck(project)
    assert out["decision"] == "routing_accept"
    assert out["driver"]["skip_nodes"] == ["step3b_cross_species_map.run"]
    assert "step3c_kg.query" not in out["driver"]["node_args"]


def test_routing_accept_wires_map_on_cross_species_only(tmp_path):
    project = _write_report(tmp_path, {
        "recommended_strategy": "cross_species_only",
        "target_species": "sorghum_bicolor",
        "recommended_reference_species": [
            {"species": "zea_mays"},
            {"species": "oryza_sativa"},
        ],
    })
    out = routing_accept_from_precheck(project)
    assert out["decision"] == "routing_accept"
    assert out["driver"]["skip_nodes"] == []
    map_args = out["driver"]["node_args"]["step3b_cross_species_map.run"]
    assert map_args["reference_species"] == ["zea_mays", "oryza_sativa"]
    assert map_args["target_species"] == "sorghum_bicolor"
    kg_args = out["driver"]["node_args"]["step3c_kg.query"]
    assert kg_args["ortholog_map"].endswith("cross_species_map.json")
    assert "zea_mays,oryza_sativa" in out["reasoning"]


def test_routing_accept_catalog_fallback_when_3a_omits_refs(tmp_path):
    project = _write_report(tmp_path, {
        "recommended_strategy": "cross_species_only",
        "target_species": "sorghum_bicolor",
        "target_species_type": "Plant",
    })
    out = routing_accept_from_precheck(project)
    map_args = out["driver"]["node_args"]["step3b_cross_species_map.run"]
    assert map_args["reference_species"] == [
        "zea_mays", "oryza_sativa", "arabidopsis_thaliana",
    ]
    assert "refs from catalog" in out["reasoning"]


def test_missing_report_skips_map(tmp_path):
    out = routing_accept_from_precheck(str(tmp_path))
    assert out["decision"] == "routing_accept"
    assert "step3b_cross_species_map.run" in out["driver"]["skip_nodes"]
