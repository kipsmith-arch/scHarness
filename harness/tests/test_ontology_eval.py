"""GT-pin + KG-hierarchy scoring (label-map-ontology-eval.md)."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.ontology_eval import Hierarchy, OntologyScorer, load_aliases, load_gt_ontology


def _scorer(**kwargs) -> OntologyScorer:
    pins = kwargs.pop("pins", {
        "Root hair": "trichoblast",
        "Root epidermis": "root epidermis",
        "Root stele": "root stele",
        "Root cortex": "root cortex",
    })
    aliases = kwargs.pop("aliases", {
        "trichoblast": "trichoblast",
        "root hair": "trichoblast",
        "root hair cell": "trichoblast",
        "root stele": "root stele",
        "root procambium": "root procambium",
    })
    hierarchy = kwargs.pop("hierarchy", Hierarchy(
        skipped=False,
        nodes={
            "trichoblast", "root epidermis", "root stele",
            "root cortex", "root procambium",
        },
        ancestors={
            "trichoblast": frozenset({"root epidermis"}),
            "root epidermis": frozenset(),
            "root stele": frozenset(),
            "root cortex": frozenset(),
            "root procambium": frozenset(),
        },
    ))
    return OntologyScorer(pins=pins, aliases=aliases, hierarchy=hierarchy, **kwargs)


def test_same_predicted_different_gt_yields_different_relation():
    s = _scorer()
    assert s.relation("trichoblast", "Root hair") == "synonym"
    assert s.relation("trichoblast", "Root epidermis") == "subtype"
    assert s.relation("trichoblast", "Root cortex") == "unrelated"


def test_root_stele_on_cortex_is_unrelated_not_hits0_synonym():
    """Regression: pair-table hits[0] gave synonym to the wrong cell."""
    s = _scorer()
    assert s.relation("root stele", "Root stele") == "synonym"
    assert s.relation("root stele", "Root cortex") == "unrelated"


def test_unknown_predicted_term_is_unmatched():
    s = _scorer()
    assert s.relation("root meristem", "Root cortex") == "unmatched"
    assert s.relation("unknown", "Root cortex") == "unmatched"


def test_unpinned_gt_is_unmatched():
    s = _scorer()
    assert s.relation("root stele", "Unknown") == "unmatched"
    assert s.relation("root stele", "") == "unmatched"


def test_procambium_stays_unrelated():
    s = _scorer()
    assert s.relation("root procambium", "Root cortex") == "unrelated"


def test_offline_accepts_alias_same_node_only():
    s = _scorer(hierarchy=Hierarchy.offline())
    assert s.hierarchy.skipped is True
    assert s.relation("trichoblast", "Root hair") == "synonym"
    assert s.relation("trichoblast", "Root epidermis") == "unrelated"
    assert s.relation("root meristem", "Root cortex") == "unmatched"


def test_load_gt_ontology_skips_null_pins(tmp_path: Path):
    path = tmp_path / "gt_ontology.json"
    path.write_text(json.dumps({
        "pins": {"Root hair": "trichoblast", "Unknown": None},
        "_meta": {"verified": True},
    }), encoding="utf-8")
    pins, meta = load_gt_ontology(str(path))
    assert pins == {"Root hair": "trichoblast"}
    assert meta["verified"] is True


def test_load_aliases_casefold(tmp_path: Path):
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({
        "aliases": {"Trichoblast": "trichoblast"},
        "_meta": {"verified": True},
    }), encoding="utf-8")
    aliases, _ = load_aliases(str(path))
    s = _scorer(aliases=aliases)
    assert s.relation("TRICHOBLAST", "Root hair") == "synonym"


def test_migrated_sorghum_trichoblast_is_subtype_of_epidermis():
    root = Path(__file__).resolve().parent.parent.parent
    pins, _ = load_gt_ontology(str(root / "experiments" / "gt_ontology_PRJNA935359.json"))
    aliases, _ = load_aliases(str(root / "experiments" / "kg_term_aliases.json"))
    assert "Unknown" not in pins
    s = OntologyScorer(
        pins=pins,
        aliases=aliases,
        hierarchy=Hierarchy.from_maps(
            {"trichoblast", "root epidermis", "root cortex"},
            {"trichoblast": {"root epidermis"}},
        ),
    )
    assert s.relation("trichoblast", "Root epidermis") == "subtype"
    assert s.relation("trichoblast", "Root cortex") == "unrelated"
