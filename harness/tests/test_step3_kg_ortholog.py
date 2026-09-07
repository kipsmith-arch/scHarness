"""step3_kg --ortholog-map: map load + candidate aggregation (no Neo4j)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import step3_kg as kg  # noqa: E402


def test_load_ortholog_map_reads_cross_species_key(tmp_path):
    p = tmp_path / "cross_species_map.json"
    p.write_text(json.dumps({
        "reference_species": ["zea_mays"],
        "warnings": ["dns"],
        "cross_species_map": {
            "SORBI-1": [{"ref_gene_id": "Zm1", "ref_species": "zea_mays"}],
        },
    }), encoding="utf-8")
    cmap, refs, warns = kg.load_ortholog_map(str(p))
    assert refs == ["zea_mays"]
    assert "dns" in warns
    assert cmap["SORBI-1"][0]["ref_gene_id"] == "Zm1"


def test_load_ortholog_map_legacy_key(tmp_path):
    p = tmp_path / "ortholog_map.json"
    p.write_text(json.dumps({"ortholog_map": {"AT1": []}}), encoding="utf-8")
    cmap, refs, _ = kg.load_ortholog_map(str(p))
    assert "AT1" in cmap
    assert refs == []


def test_path_hit_stats_counts_direct_and_ortholog():
    gene_to_cts = {
        "g1": [{"source_path": "direct"}, {"source_path": "ortholog"}],
        "g2": [{"source_path": "ortholog"}],
        "g3": [{"source_path": "direct"}],
        "g4": [],
    }
    stats = kg._path_hit_stats(gene_to_cts)
    assert stats["n_direct_hits"] == 2
    assert stats["n_ortholog_hits"] == 2
    assert stats["n_mixed_hits"] == 1
    assert stats["n_genes_with_only_ortholog"] == 1


def test_rank_candidates_keeps_source_sets_when_annotated():
    markers = {"0": ["g1"]}
    gene_to_cts = {
        "g1": [
            {
                "cell_type": "root hair cell", "organ": "Root",
                "ontology_id": "x", "species_type": "Plant",
                "ontology_type": "cell_type", "confidence": 0.9,
                "source": "kg", "source_path": "ortholog",
                "ortholog_ref_gene": "Zm1", "ortholog_ref_species": "zea_mays",
            },
        ],
    }
    per_cluster = kg._rank_candidates(markers, gene_to_cts, "root")
    cand = per_cluster["0"]["candidates"][0]
    assert cand["source_path_set"] == ["ortholog"]
    assert cand["source_species_set"] == ["zea_mays"]
    assert per_cluster["0"]["n_markers_ortholog_hit"] == 1
    assert per_cluster["0"]["n_markers_direct_hit"] == 0


def test_rank_without_map_omits_source_fields():
    markers = {"0": ["g1"]}
    gene_to_cts = {
        "g1": [{
            "cell_type": "root hair cell", "organ": "Root",
            "ontology_id": "x", "species_type": "Plant",
            "ontology_type": "cell_type", "confidence": 0.9,
            "source": "kg",
        }],
    }
    per_cluster = kg._rank_candidates(markers, gene_to_cts, "root")
    cand = per_cluster["0"]["candidates"][0]
    assert "source_path_set" not in cand
    assert "n_markers_ortholog_hit" not in per_cluster["0"]
