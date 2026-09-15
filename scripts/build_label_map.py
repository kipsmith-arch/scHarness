"""Draft GT → Ontology pin tables (no predicted×true pairs).

Replaces the D-2 pair-table generator. Input is a GT label list; output is
``experiments/gt_ontology.json`` (or ``gt_ontology_<id>.json``) with
``_meta.verified: false`` for human confirmation.

Optional ``--aliases-out`` writes the global wording table from the locked
PREBUILT predicted-side seed. Relation is never written into JSON.

See ``_bmad-output/implementation-artifacts/label-map-ontology-eval.md``.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# 12 Arabidopsis true labels (experiment_implementation.md §1.1)
TRUE_LABELS = [
    "Columella root cap", "Root cortex", "Root hair", "Non-hair",
    "Root endodermis", "Pericycle", "Lateral root cap", "Phloem",
    "Root stele", "Xylem", "Meristematic cell", "Stem cell niche",
]

# Migration hints when KG has no exact/casefold hit. Not a pair table.
PIN_HINTS: dict[str, str] = {
    "Columella root cap": "columella root cap cell",
    "Columella root cap cell": "columella root cap cell",
    "Root cortex": "root cortex",
    "Root hair": "trichoblast",
    "Non-hair": "non-hair root epidermal cell",
    "Root endodermis": "root endodermis",
    "Pericycle": "pericycle",
    "Pericycle cell": "pericycle",
    "Lateral root cap": "lateral root cap",
    "Phloem": "phloem",
    "Root stele": "root stele",
    "Xylem": "xylem",
    "Meristematic cell": "meristematic cell",
    "Stem cell niche": "stem cell niche",
    "Root epidermis": "root epidermis",
    "G2/M-phase cell": "g2/m-phase cell",
}

# Predicted-side wording variants from both locked PREBUILT tables.
ALIAS_SEED: dict[str, str] = {
    "columella root cap": "columella root cap cell",
    "columella root cap cell": "columella root cap cell",
    "lateral root cap": "lateral root cap",
    "root cap": "root cap",
    "root cortex": "root cortex",
    "cortex": "root cortex",
    "root hair": "trichoblast",
    "root hair cell": "trichoblast",
    "trichoblast": "trichoblast",
    "non-hair root epidermal cell": "non-hair root epidermal cell",
    "endodermis": "root endodermis",
    "root endodermis": "root endodermis",
    "pericycle": "pericycle",
    "pericycle cell": "pericycle",
    "phloem": "phloem",
    "companion cell": "companion cell",
    "xylem": "xylem",
    "stele": "stele",
    "root stele": "root stele",
    "meristematic cell": "meristematic cell",
    "stem cell niche": "stem cell niche",
    "root epidermis": "root epidermis",
    "epidermis": "root epidermis",
    "g2/m-phase cell": "g2/m-phase cell",
    "root procambium": "root procambium",
    "branch": "branch",
}

SKIP_GT = {"unknown"}


def _fold(s: str) -> str:
    return (s or "").strip().casefold()


def kg_candidates(cfg: dict, labels: list[str]) -> tuple[dict[str, list[dict]], bool]:
    """Look up Ontology.Name candidates for each GT label."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {}, False
    if not cfg.get("password"):
        print("[build_label_map] 缺少 NEO4J_PASSWORD,跳过 KG 候选(降级 PIN_HINTS)",
              file=sys.stderr)
        return {}, False
    out: dict[str, list[dict]] = {}
    try:
        driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
        with driver.session() as s:
            for label in labels:
                cypher = (
                    "MATCH (o:Ontology) "
                    "WHERE toLower(o.Name) = toLower($name) "
                    "   OR toLower(o.Name) CONTAINS toLower($name) "
                    "RETURN DISTINCT o.Name AS name LIMIT 8"
                )
                rows = []
                try:
                    for r in s.run(cypher, name=label):
                        name = r["name"]
                        if not name:
                            continue
                        if _fold(name) == _fold(label):
                            match = "casefold"
                        else:
                            match = "contains"
                        rows.append({"name": name, "match": match})
                except Exception:
                    rows = []
                out[label] = rows
        driver.close()
        return out, True
    except Exception as exc:
        print(f"[build_label_map] KG 连接失败: {exc}", file=sys.stderr)
        return {}, False


def draft_pins(labels: list[str], candidates: dict[str, list[dict]]) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Pick a pin per GT label. Never writes relation. Skips Unknown."""
    pins: dict[str, str] = {}
    kept_candidates: dict[str, list[dict]] = {}
    for label in labels:
        if _fold(label) in SKIP_GT:
            continue
        rows = list(candidates.get(label) or [])
        kept_candidates[label] = rows
        exact = next((r["name"] for r in rows if r.get("match") == "casefold"), None)
        if exact:
            pins[label] = exact
            continue
        hint = PIN_HINTS.get(label)
        if hint:
            pins[label] = hint
            continue
        if rows:
            pins[label] = rows[0]["name"]
    return pins, kept_candidates


def write_aliases(path: str) -> None:
    doc = {
        "aliases": dict(ALIAS_SEED),
        "_meta": {
            "built_from": "scripts/build_label_map.py ALIAS_SEED",
            "verified": False,
            "kg_checked": False,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    }
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="起稿 GT→Ontology 钉表(不再生成 predicted×true 全对)",
    )
    ap.add_argument("--true-labels", nargs="+", default=None,
                    help="GT 字符串列表;默认拟南芥 12 类")
    ap.add_argument("--dataset-id", default="SRP171040")
    ap.add_argument("--out", default="experiments/gt_ontology.json")
    ap.add_argument("--aliases-out", default=None,
                    help="可选:写出全球别名初稿")
    ap.add_argument("--uri", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    args = ap.parse_args()

    labels = list(args.true_labels or TRUE_LABELS)
    cfg = {
        "uri": args.uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": args.user or os.environ.get("NEO4J_USER", "neo4j"),
        "password": args.password or os.environ.get("NEO4J_PASSWORD"),
    }
    candidates, kg_ok = kg_candidates(cfg, labels)
    pins, kept = draft_pins(labels, candidates)

    doc = {
        "pins": pins,
        "candidates": kept,
        "_meta": {
            "dataset": args.dataset_id,
            "built_from": "scripts/build_label_map.py",
            "verified": False,
            "kg_checked": kg_ok,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    }
    parent = os.path.dirname(args.out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    skipped = [l for l in labels if _fold(l) in SKIP_GT]
    missing = [l for l in labels if _fold(l) not in SKIP_GT and l not in pins]
    print(f"[build_label_map] 写出 {args.out}: {len(pins)} pins, "
          f"跳过 Unknown={skipped or '无'}, 未钉={missing or '无'}")
    print(f"[build_label_map] KG 候选: {'通过' if kg_ok else '降级(未查证)'};"
          f"人工确认节点后置 _meta.verified=true")
    if args.aliases_out:
        write_aliases(args.aliases_out)
        print(f"[build_label_map] 别名初稿: {args.aliases_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
