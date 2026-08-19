"""D-2 标签映射表起稿:experiments/label_map.json。

依据 design/experiment_implementation.md §1.2 D-2:
- harness 输出 KG 本体术语(如 "root cap"),真值是人类命名("Columella root cap"),
  粒度/措辞不同,不定义映射就无法算准确率
- 每个预测术语 × 12 真值标签判定关系:exact / synonym / subtype / supertype / unrelated
- 预填映射来自 §1.2(须经 KG 核对后定案);KG 不可达时降级用预填映射起稿
- 产出 `_meta.verified: false` 初稿,人工核对 13 条后置 true 锁定

本脚本是 P4 D-2 的可复现实现,不在 harness / skill 包内,仅供实验层使用。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# Configuration (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD) is loaded from the
# project-root ``.env`` by ``harness/config.py``. When this script is invoked
# via the loop's ``scripted_driver.py`` or from a session, the parent process
# has already populated ``os.environ`` from .env. When invoked standalone
# (e.g. ``python scripts/build_label_map.py``) the caller must source
# ``.env`` first; see docs/CONFIGURATION_REFERENCE.md §2.

# 12 个真值标签(experiment_implementation.md §1.1)
TRUE_LABELS = [
    "Columella root cap", "Root cortex", "Root hair", "Non-hair",
    "Root endodermis", "Pericycle", "Lateral root cap", "Phloem",
    "Root stele", "Xylem", "Meristematic cell", "Stem cell niche",
]

# 预填映射(experiment_implementation.md §1.2;relation 语义见该节)
# 变体与人工定案条目:2026-08-11 用户核对(基于 output/p2、p2v2 实际输出术语)
PREBUILT: list[dict] = [
    {"predicted": "columella root cap", "true": "Columella root cap", "relation": "synonym"},
    {"predicted": "columella root cap cell", "true": "Columella root cap", "relation": "synonym", "note": "带 cell 后缀变体"},
    {"predicted": "lateral root cap", "true": "Lateral root cap", "relation": "synonym"},
    {"predicted": "root cap", "true": "Columella root cap", "relation": "supertype"},
    {"predicted": "root cap", "true": "Lateral root cap", "relation": "supertype"},
    {"predicted": "root cortex", "true": "Root cortex", "relation": "synonym"},
    {"predicted": "cortex", "true": "Root cortex", "relation": "synonym"},
    {"predicted": "root hair", "true": "Root hair", "relation": "synonym"},
    {"predicted": "root hair cell", "true": "Root hair", "relation": "synonym", "note": "带 cell 后缀变体"},
    {"predicted": "trichoblast", "true": "Root hair", "relation": "synonym", "note": "trichoblast 即根毛细胞"},
    {"predicted": "non-hair root epidermal cell", "true": "Non-hair", "relation": "synonym"},
    {"predicted": "endodermis", "true": "Root endodermis", "relation": "synonym"},
    {"predicted": "root endodermis", "true": "Root endodermis", "relation": "synonym", "note": "带 root 前缀变体"},
    {"predicted": "pericycle", "true": "Pericycle", "relation": "synonym"},
    {"predicted": "phloem", "true": "Phloem", "relation": "synonym"},
    {"predicted": "companion cell", "true": "Phloem", "relation": "subtype", "note": "伴胞是韧皮部组成(预测更具体)"},
    {"predicted": "xylem", "true": "Xylem", "relation": "synonym"},
    {"predicted": "stele", "true": "Root stele", "relation": "supertype"},
    {"predicted": "root stele", "true": "Root stele", "relation": "synonym", "note": "带 root 前缀变体"},
    {"predicted": "root procambium", "true": None, "relation": "unrelated", "note": "人工定案 2026-08-11:保留独立术语,不映射任何真值"},
    {"predicted": "branch", "true": None, "relation": "unrelated", "note": "人工定案 2026-08-11:异常术语,保留独立,不映射"},
    {"predicted": "meristematic cell", "true": "Meristematic cell", "relation": "synonym"},
    {"predicted": "stem cell niche", "true": "Stem cell niche", "relation": "synonym"},
]


def kg_ancestors(cfg: dict, terms: list[str], max_hops: int = 3) -> tuple[dict, bool]:
    """Query KG ancestors for each term. Returns ({term: [ancestors]}, ok).

    Cypher mirrors skills/cell-annotation/scripts/step3_kg.py op_query_hierarchy.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {}, False
    if not cfg.get("password"):
        print("[build_label_map] 缺少 NEO4J_PASSWORD,跳过 KG 查证(降级预填映射)", file=sys.stderr)
        return {}, False
    ancestors: dict[str, list] = {}
    try:
        driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
        with driver.session() as s:
            for term in terms:
                cypher = (
                    "MATCH (o:Ontology {Name: $name})-[:ontology_relation*1..%d]->(a:Ontology) "
                    "RETURN DISTINCT a.Name AS ancestor" % max(1, int(max_hops))
                )
                try:
                    res = s.run(cypher, name=term)
                    ancestors[term] = [r["ancestor"] for r in res if r["ancestor"] is not None]
                except Exception as exc:  # per-term query error
                    ancestors[term] = []
        driver.close()
        return ancestors, True
    except Exception as exc:
        print(f"[build_label_map] KG 连接失败: {exc}", file=sys.stderr)
        return {}, False


def main() -> int:
    ap = argparse.ArgumentParser(description="D-2 标签映射表起稿 -> experiments/label_map.json")
    ap.add_argument("--out", default="experiments/label_map.json")
    ap.add_argument("--uri", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--max-ancestor-hops", type=int, default=3)
    args = ap.parse_args()

    cfg = {
        "uri": args.uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": args.user or os.environ.get("NEO4J_USER", "neo4j"),
        "password": args.password or os.environ.get("NEO4J_PASSWORD"),
    }

    terms = sorted({e["predicted"] for e in PREBUILT})
    ancestors, kg_ok = kg_ancestors(cfg, terms, args.max_ancestor_hops)

    entries = []
    for e in PREBUILT:
        anc = ancestors.get(e["predicted"], [])
        entry = {
            "predicted": e["predicted"],
            "true": e["true"],
            "relation": e["relation"],
            "evidence": {
                "kg_ancestors": anc,
                "source": "experiment_implementation.md §1.2 预填" if not kg_ok else "KG 查证 + 预填",
            },
        }
        entries.append(entry)

    doc = {
        "entries": entries,
        "true_labels": TRUE_LABELS,
        "_meta": {
            "built_from": "scripts/build_label_map.py",
            "verified": False,
            "kg_checked": kg_ok,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    covered = {e["true"] for e in entries}
    missing = set(TRUE_LABELS) - covered
    print(f"[build_label_map] 写出 {args.out}:{len(entries)} 条映射,12 类型覆盖 {'完整' if not missing else '缺失: ' + str(missing)}")
    print(f"[build_label_map] KG 查证: {'通过' if kg_ok else '降级(未查证)'};人工核对后置 _meta.verified=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
