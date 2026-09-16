"""build_label_map.py now drafts GT pins, not predicted×true pairs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_label_map import ALIAS_SEED, draft_pins, write_aliases  # noqa: E402


def test_draft_pins_skips_unknown_and_uses_hints():
    pins, candidates = draft_pins(
        ["Root hair", "Unknown", "Root epidermis"],
        {},
    )
    assert "Unknown" not in pins
    assert pins["Root hair"] == "trichoblast"
    assert pins["Root epidermis"] == "root epidermis"
    assert "entries" not in candidates
    assert "relation" not in json.dumps(pins)


def test_draft_pins_prefers_kg_casefold_over_hint():
    pins, _ = draft_pins(
        ["Root hair"],
        {"Root hair": [{"name": "root hair cell", "match": "casefold"}]},
    )
    assert pins["Root hair"] == "root hair cell"


def test_write_aliases_has_no_relation(tmp_path: Path):
    path = tmp_path / "aliases.json"
    write_aliases(str(path))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["aliases"]["trichoblast"] == ALIAS_SEED["trichoblast"]
    assert "entries" not in doc
    assert doc["_meta"]["verified"] is False
