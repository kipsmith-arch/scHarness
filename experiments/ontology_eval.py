"""GT-pin + alias + KG-hierarchy scoring for cell-level evaluation.

Replaces predicted×true pair tables (label_map.json). Relation is computed
per cell from (predicted → node A, this cell's GT → node B).

See `_bmad-output/implementation-artifacts/label-map-ontology-eval.md`.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field


def _fold(s: str) -> str:
    return (s or "").strip().casefold()


def _index_casefold(mapping: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in mapping.items():
        if v is None:
            continue
        key = _fold(str(k))
        val = str(v).strip()
        if key and val:
            out[key] = val
    return out


@dataclass(frozen=True)
class Hierarchy:
    """Ancestor index over Ontology.Name.

    ``skipped=True`` means the graph was unreachable: only alias/pin exact
    and synonym are allowed (label-map-ontology-eval.md offline fallback).
    """

    skipped: bool
    nodes: frozenset[str] = field(default_factory=frozenset)
    ancestors: dict[str, frozenset[str]] = field(default_factory=dict)
    _nodes_fold: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def offline(cls) -> "Hierarchy":
        return cls(skipped=True)

    @classmethod
    def from_maps(
        cls,
        nodes: set[str] | frozenset[str],
        ancestors: dict[str, set[str] | frozenset[str]],
        *,
        skipped: bool = False,
    ) -> "Hierarchy":
        node_set = frozenset(n for n in nodes if n)
        anc = {k: frozenset(v) for k, v in ancestors.items()}
        fold = {_fold(n): n for n in node_set}
        return cls(skipped=skipped, nodes=node_set, ancestors=anc, _nodes_fold=fold)

    def __post_init__(self) -> None:
        if not self._nodes_fold and self.nodes:
            object.__setattr__(self, "_nodes_fold", {_fold(n): n for n in self.nodes})

    def canonical_node(self, name: str) -> str | None:
        if not name or not str(name).strip():
            return None
        raw = str(name).strip()
        if raw in self.nodes:
            return raw
        return self._nodes_fold.get(_fold(raw))

    def ancestors_of(self, name: str) -> frozenset[str]:
        node = self.canonical_node(name) or name
        return self.ancestors.get(node, frozenset())


@dataclass
class OntologyScorer:
    pins: dict[str, str]
    aliases: dict[str, str]
    hierarchy: Hierarchy
    _pins_fold: dict[str, str] = field(init=False, repr=False)
    _aliases_fold: dict[str, str] = field(init=False, repr=False)
    _pin_target_fold: dict[str, str] = field(init=False, repr=False)
    _gt_by_node_fold: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        pins: dict[str, str] = {}
        for k, v in (self.pins or {}).items():
            if v is None:
                continue
            label = str(k).strip()
            node = str(v).strip()
            if label and node:
                pins[label] = node
        self.pins = pins
        self._pins_fold = _index_casefold(pins)
        self._aliases_fold = _index_casefold(self.aliases or {})
        self._pin_target_fold = {_fold(v): v for v in pins.values()}
        self._gt_by_node_fold = {_fold(v): k for k, v in pins.items()}

    def resolve_gt(self, true_label: str) -> str | None:
        if not true_label or not str(true_label).strip():
            return None
        raw = str(true_label).strip()
        if raw in self.pins:
            return self.pins[raw]
        return self._pins_fold.get(_fold(raw))

    def resolve_predicted(self, predicted: str) -> str | None:
        if not predicted or not str(predicted).strip():
            return None
        raw = str(predicted).strip()
        if _fold(raw) == "unknown":
            return None
        aliased = self._aliases_fold.get(_fold(raw))
        if self.hierarchy.skipped:
            if aliased:
                return aliased
            pin_hit = self._pin_target_fold.get(_fold(raw))
            if pin_hit:
                return pin_hit
            return None
        if aliased:
            node = self.hierarchy.canonical_node(aliased)
            return node or aliased
        node = self.hierarchy.canonical_node(raw)
        if node:
            return node
        return None

    def mapped_true(self, predicted: str) -> str | None:
        """GT label whose pin is the predicted node (exact/synonym class)."""
        node = self.resolve_predicted(predicted)
        if not node:
            return None
        return self._gt_by_node_fold.get(_fold(node))

    def relation(self, predicted: str, true_label: str) -> str:
        if not predicted or _fold(predicted) == "unknown":
            return "unmatched"
        node_b = self.resolve_gt(true_label)
        if not node_b:
            return "unmatched"
        node_a = self.resolve_predicted(predicted)
        if not node_a:
            return "unmatched"
        if _fold(node_a) == _fold(node_b):
            if str(predicted).strip() == str(true_label).strip():
                return "exact"
            return "synonym"
        if self.hierarchy.skipped:
            return "unrelated"
        if _fold(node_b) in {_fold(x) for x in self.hierarchy.ancestors_of(node_a)}:
            return "subtype"
        if _fold(node_a) in {_fold(x) for x in self.hierarchy.ancestors_of(node_b)}:
            return "supertype"
        return "unrelated"


def load_gt_ontology(path: str) -> tuple[dict[str, str], dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    raw_pins = doc.get("pins", doc.get("gt_ontology", {}))
    pins: dict[str, str] = {}
    for k, v in raw_pins.items():
        if v is None or str(v).strip() == "":
            continue
        pins[str(k)] = str(v).strip()
    return pins, doc.get("_meta", {})


def load_aliases(path: str) -> tuple[dict[str, str], dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    raw = doc.get("aliases", {})
    aliases = {str(k): str(v).strip() for k, v in raw.items() if v}
    return aliases, doc.get("_meta", {})


def fetch_hierarchy(
    uri: str,
    user: str,
    password: str | None,
    names: list[str],
    max_hops: int = 3,
) -> Hierarchy:
    """Load Ontology nodes + ancestors for ``names``. Offline on any failure."""
    if not password:
        return Hierarchy.offline()
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return Hierarchy.offline()
    hops = max(int(max_hops), 1)
    if int(max_hops) <= 0:
        return Hierarchy.offline()
    nodes: set[str] = set()
    ancestors: dict[str, set[str]] = {}
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as s:
            for name in names:
                if not name:
                    continue
                found = s.run(
                    "MATCH (o:Ontology) WHERE toLower(o.Name) = toLower($name) "
                    "RETURN o.Name AS name LIMIT 1",
                    name=name,
                ).single()
                if found is None or found["name"] is None:
                    continue
                canon = found["name"]
                nodes.add(canon)
                cypher = (
                    "MATCH (o:Ontology {Name: $name})-[:ontology_relation*1..%d]->(a:Ontology) "
                    "RETURN DISTINCT a.Name AS ancestor" % hops
                )
                res = s.run(cypher, name=canon)
                ancestors[canon] = {
                    r["ancestor"] for r in res if r["ancestor"] is not None
                }
        driver.close()
    except Exception as exc:
        print(f"[ontology_eval] KG 连接失败, kg_hierarchy skipped: {exc}", file=sys.stderr)
        return Hierarchy.offline()
    if not nodes:
        print("[ontology_eval] KG 可达但未命中任何 Ontology.Name, kg_hierarchy skipped",
              file=sys.stderr)
        return Hierarchy.offline()
    return Hierarchy.from_maps(nodes, ancestors, skipped=False)
