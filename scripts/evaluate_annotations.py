"""细胞级评估:D-4 连接逻辑 + 准确率/F1/聚类纯度。

依据 design/experiment_implementation.md §1.2 D-4 / §1.3 / §1.4:
- 连接四张表(全部以 cell_barcode 为 key):
    obs_snapshot.csv(细胞→leiden)
  × final_annotations.json(leiden→预测标签)
  × label_map.json(预测术语→真值,relation)
  × gt_cells.csv(条码→真值)
- 输出:strict accuracy(exact/synonym 计对)、relaxed accuracy(subtype=1.5, supertype=0.5)、
  soft macro-F1(按关系权重累加 TP)、聚类纯度(每簇最大真值占比均值)、逐簇表、unmatched 报告
- label_map 缺预测术语 → 记 unmatched 且报告不掩藏;leiden 列缺失 → 报错

本脚本是 P5 测试循环评估工具,不在 harness / skill 包内。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

STRICT_HIT = {"exact", "synonym"}
PARTIAL_HIT = {"subtype", "supertype"}
WEIGHT = {"exact": 1.0, "synonym": 1.0, "subtype": 1.5, "supertype": 0.5, "unrelated": 0.0}


def load_obs_snapshot(path: str) -> dict[str, str]:
    """cell_id -> leiden. Requires a 'leiden' column."""
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "leiden" not in (reader.fieldnames or []):
            raise SystemExit(f"error: {path} 缺少 leiden 列(实际列: {reader.fieldnames})")
        for r in reader:
            cell = (r.get("cell_id") or "").strip()
            leiden = (r.get("leiden") or "").strip()
            if cell:
                mapping[cell] = leiden
    return mapping


def load_final_annotations(path: str) -> dict[str, str]:
    """leiden -> predicted label."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    ann = doc.get("annotations", doc)
    return {str(k): (v.get("label") if isinstance(v, dict) else str(v))
            for k, v in ann.items()}


def load_label_map(path: str) -> tuple[dict[str, list[dict]], dict]:
    """predicted -> [{true, relation}]; returns (map, meta)."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    mapping: dict[str, list[dict]] = {}
    for e in doc.get("entries", []):
        mapping.setdefault(e["predicted"], []).append(
            {"true": e["true"], "relation": e["relation"]})
    return mapping, doc.get("_meta", {})


def load_gt(path: str) -> dict[str, str]:
    """cell_barcode -> true_type."""
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            cell = (r.get("cell_barcode") or "").strip()
            tt = (r.get("true_type") or "").strip()
            if cell:
                mapping[cell] = tt
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description="细胞级评估:准确率/F1/聚类纯度")
    ap.add_argument("project_dir", help="跑测产物目录(含 obs_snapshot.csv + step6_validate/final_annotations.json)")
    ap.add_argument("--gt-csv", default="experiments/gt_cells.csv")
    ap.add_argument("--label-map", default="experiments/label_map.json")
    ap.add_argument("--out", default=None, help="评估报告 JSON 输出路径(默认 <project_dir>/evaluation_report.json)")
    args = ap.parse_args()

    obs_path = os.path.join(args.project_dir, "obs_snapshot.csv")
    if not os.path.exists(obs_path):
        obs_path = os.path.join(args.project_dir, "step1_prepare", "obs_snapshot.csv")
    ann_path = os.path.join(args.project_dir, "step6_validate", "final_annotations.json")

    obs = load_obs_snapshot(obs_path)
    ann = load_final_annotations(ann_path)
    label_map, meta = load_label_map(args.label_map)
    gt = load_gt(args.gt_csv)

    if meta.get("verified") is not True:
        print(f"[evaluate_annotations] WARNING: label_map 未定案(_meta.verified != true),结果仅供参考",
              file=sys.stderr)

    # 展开 per-cell 预测
    per_cell: list[dict] = []  # {cell, true, leiden, raw, relation, weight}
    unmatched_terms: Counter = Counter()
    unknown_leiden: set[str] = set()
    for cell, true in gt.items():
        leiden = obs.get(cell)
        if leiden is None:
            continue  # 该细胞不在本跑测 obs 内(例如子样本)
        raw = ann.get(str(leiden))
        if raw is None:
            unknown_leiden.add(str(leiden))
            raw = "unknown"
        hits = label_map.get(raw, [])
        if not hits:
            unmatched_terms[raw] += 1
            relation, weight = "unmatched", 0.0
        else:
            relation = hits[0]["relation"]
            weight = WEIGHT.get(relation, 0.0)
        per_cell.append({"cell": cell, "true": true, "leiden": leiden,
                         "raw": raw, "relation": relation, "weight": weight})

    n = len(per_cell)
    if n == 0:
        raise SystemExit("error: 无任何细胞可评估(gt 与 obs 无交集)")

    # 准确率
    strict_hits = sum(1 for c in per_cell if c["relation"] in STRICT_HIT)
    score_sum = sum(c["weight"] for c in per_cell)
    strict_acc = strict_hits / n
    relaxed_acc = score_sum / n

    # soft macro-F1:按 12 真值类型,以 weight 计 TP/FP/FN 的软计数
    true_labels = sorted(set(c["true"] for c in per_cell))
    tp = {t: 0.0 for t in true_labels}
    fp = {t: 0.0 for t in true_labels}
    fn = {t: 0.0 for t in true_labels}
    for c in per_cell:
        if c["relation"] in STRICT_HIT or c["relation"] in PARTIAL_HIT:
            # 软命中:映射到的真值标签(多对一时取第一条)
            mapped_true = label_map[c["raw"]][0]["true"]
            tp[mapped_true] += c["weight"]
    # FP:预测为 t 但真值不是 t(软计数,partial 计 0.5)
    fp = {t: 0.0 for t in true_labels}
    for c in per_cell:
        if c["relation"] in STRICT_HIT or c["relation"] in PARTIAL_HIT:
            mapped_true = label_map[c["raw"]][0]["true"]
            if mapped_true != c["true"]:
                fp[mapped_true] += c["weight"]
    # FN:真值是 t 但未被完全正确识别(unmatched/unrelated/映射到别的真值)
    fn = {t: 0.0 for t in true_labels}
    for c in per_cell:
        if c["relation"] in STRICT_HIT or c["relation"] in PARTIAL_HIT:
            mapped_true = label_map[c["raw"]][0]["true"]
            if mapped_true != c["true"]:
                fn[c["true"]] += c["weight"]
        else:
            fn[c["true"]] += 1.0
    f1s = {}
    for t in true_labels:
        p = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        r = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1s[t] = 2 * p * r / (p + r) if (p + r) else 0.0
    macro_f1 = sum(f1s.values()) / len(f1s) if f1s else 0.0

    # 聚类纯度:每簇内最大真值占比的均值
    cluster_true: dict[str, Counter] = {}
    for c in per_cell:
        cluster_true.setdefault(c["leiden"], Counter())[c["true"]] += 1
    purities = []
    cluster_rows = []
    for leiden, cnt in sorted(cluster_true.items()):
        total = sum(cnt.values())
        top_true, top_n = cnt.most_common(1)[0]
        purity = top_n / total
        purities.append(purity)
        hits = [c for c in per_cell if c["leiden"] == leiden]
        raw = ann.get(str(leiden), "?")
        rel = hits[0]["relation"] if hits else "?"
        cluster_rows.append({
            "leiden": leiden, "n_cells": total, "predicted": raw, "relation": rel,
            "top_true": top_true, "top_true_n": top_n, "purity": round(purity, 4),
            "correct_strict": sum(1 for c in hits if c["relation"] in STRICT_HIT),
        })
    mean_purity = sum(purities) / len(purities) if purities else 0.0

    report = {
        "project_dir": args.project_dir,
        "n_cells_evaluated": n,
        "strict_accuracy": round(strict_acc, 4),
        "relaxed_accuracy": round(relaxed_acc, 4),
        "macro_f1_soft": round(macro_f1, 4),
        "mean_cluster_purity": round(mean_purity, 4),
        "n_clusters": len(cluster_rows),
        "unmatched_terms": dict(unmatched_terms),
        "unknown_leiden": sorted(unknown_leiden),
        "per_cluster": cluster_rows,
        "label_map_verified": meta.get("verified") is True,
    }

    out_path = args.out or os.path.join(args.project_dir, "evaluation_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[evaluate_annotations] {args.project_dir}")
    print(f"  细胞数: {n}")
    print(f"  strict accuracy(exact/synonym): {report['strict_accuracy']}")
    print(f"  relaxed accuracy(subtype=1.5, supertype=0.5):  {report['relaxed_accuracy']}")
    print(f"  macro-F1(soft):                 {report['macro_f1_soft']}")
    print(f"  聚类纯度(均值):                  {report['mean_cluster_purity']}  ({len(cluster_rows)} 簇)")
    if unmatched_terms:
        print(f"  WARNING 未映射术语: {dict(unmatched_terms)}")
    if unknown_leiden:
        print(f"  WARNING 无注释的 leiden: {sorted(unknown_leiden)}")
    print(f"  报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
