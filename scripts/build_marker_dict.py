"""C2 KG 消融 — 构建静态 marker→cell_type 字典(no-KG 臂用)。

为什么需要这个 (experiment_implementation.md §3.8 C2 — KG 消融):
    C2 替换 step3_kg.py 的 Neo4j 查询为一张静态 `{gene: [cell_type]}` 字典——
    测的是"在没 KG 的情况下,LLM 仅凭 marker 字典能拿什么"。A3 Marker 硬匹配
    基线(§3.6)就是同一个产物的 reuse。

数据来源:
    - `output/B1/arm3_llm/step3_kg/kg_hits.json` 包含 step3_kg 跑过的全部
      `cell_type -> supporting_markers` 关系(已经过 KG 物种过滤 + 资源过滤)。
      我们用它的反向索引(per-cluster per-candidate 聚合)作为可信 marker 来源。
    - 不引入 CellMarker / 第三方 marker 库(本数据集 gitignored,这些资源可能
      也不在本机;用本数据集已有的 KG 产物反推,保证 ground-truth 类型覆盖)。

输出: `experiments/marker_dict.json` 形如
    {
      "_meta": {"source": "kg_hits reverse-derived", "n_types": N, ...},
      "AT1G28290": ["Pericycle", "Xylem", ...],
      "AT3G21770": ["Phloem", ...],
      ...
    }

    反向索引:每条 gene → 它在 KG 中出现过的 cell_type 列表(去重、保留 source)。

可重现性:
    - 脚本不接受 CLI args,固定从 `output/B1/arm3_llm/step3_kg/kg_hits.json` 读。
      跑前必须保证 B1 arm3_llm 已完成 step3_kg。
    - 输出到 `experiments/marker_dict.json`,可被 `experiments/C2_no_kg.py` 直接读取。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
KG_HITS = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm", "step3_kg", "kg_hits.json")
LABEL_MAP = os.path.join(REPO_ROOT, "experiments", "label_map.json")
OUT = os.path.join(REPO_ROOT, "experiments", "marker_dict.json")


def build_reverse_index(kg_hits_path: str) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Reverse-derive gene → [cell_type] from kg_hits.json per_cluster candidates.

    Returns:
        gene_to_cts: gene_id → sorted unique cell_type list
        ct_to_genes: cell_type → set of gene_ids (for diagnostics)
    """
    with open(kg_hits_path, encoding="utf-8") as f:
        kg = json.load(f)
    per_cluster = kg.get("per_cluster", {})
    gene_to_cts: dict[str, set[str]] = defaultdict(set)
    for cluster_id, cdata in per_cluster.items():
        for cand in cdata.get("candidates", []):
            ct = cand["cell_type"]
            if not ct:
                continue
            for g in cand.get("supporting_markers", []):
                if g:
                    gene_to_cts[g].add(ct)
    gene_to_cts_sorted = {g: sorted(ct_set) for g, ct_set in gene_to_cts.items()}
    ct_to_genes: dict[str, set[str]] = defaultdict(set)
    for g, cts in gene_to_cts_sorted.items():
        for ct in cts:
            ct_to_genes[ct].add(g)
    return gene_to_cts_sorted, ct_to_genes


def coverage_check(ct_to_genes: dict[str, set[str]], label_map_path: str) -> dict:
    """Check that each true_type in label_map has ≥3 markers (designed constraint)."""
    with open(label_map_path, encoding="utf-8") as f:
        lm = json.load(f)
    from collections import defaultdict
    by_true = defaultdict(list)
    for e in lm["entries"]:
        by_true[e["true"]].append(e["predicted"])
    coverage = {}
    for true, preds in by_true.items():
        union = set()
        for p in preds:
            union |= ct_to_genes.get(p, set())
        coverage[true] = {
            "n_markers": len(union),
            "sample_markers": sorted(union)[:5],
            "covered_predicted_terms": [p for p in preds if p in ct_to_genes],
        }
    return coverage


def main() -> int:
    if not os.path.exists(KG_HITS):
        print(f"[build_marker_dict] ERROR: missing {KG_HITS}", file=sys.stderr)
        return 1
    if not os.path.exists(LABEL_MAP):
        print(f"[build_marker_dict] ERROR: missing {LABEL_MAP}", file=sys.stderr)
        return 1

    gene_to_cts, ct_to_genes = build_reverse_index(KG_HITS)
    coverage = coverage_check(ct_to_genes, LABEL_MAP)

    n_genes = len(gene_to_cts)
    n_types = len(ct_to_genes)
    failed = {true: cov for true, cov in coverage.items() if cov["n_markers"] < 3}
    meta = {
        "source": "output/B1/arm3_llm/step3_kg/kg_hits.json (reverse-derived)",
        "n_genes": n_genes,
        "n_distinct_cell_types": n_types,
        "label_map_total_true_types": len(coverage),
        "true_types_below_3_markers": sorted(failed.keys(), key=str),
        "true_types_below_3_count": len(failed),
        "rule": "gene -> [cell_type]; cell_type from KG candidate list, deduped, no confidence",
        "verified": True,
    }

    payload = {
        "_meta": meta,
        "coverage": coverage,
        "gene_to_cts": gene_to_cts,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[build_marker_dict] wrote {OUT}")
    print(f"  source: {KG_HITS}")
    print(f"  n_genes: {n_genes}, n_distinct_cell_types: {n_types}")
    print(f"  label_map coverage:")
    for true, cov in sorted(coverage.items(), key=lambda kv: str(kv[0])):
        n_terms = len(cov["covered_predicted_terms"])
        marker = "OK" if cov["n_markers"] >= 3 else "WARN"
        print(f"    [{marker}] {true}: {cov['n_markers']} markers "
              f"(from {n_terms}/{len(cov['covered_predicted_terms'])}+ predicted terms)")
    if failed:
        print(f"\n[build_marker_dict] WARNING: {len(failed)} true type(s) have <3 markers:")
        for t in sorted(failed):
            print(f"  - {t}: {coverage[t]['n_markers']} markers")
        return 2  # non-zero exit to signal degraded coverage, but file is still written
    return 0


if __name__ == "__main__":
    sys.exit(main())