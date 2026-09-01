"""Story 6.7 / A1 端到端准确率报告。

Reuses `output/B1/eval/evaluation_report.json` + `evaluation_report.per_cell.json`
already produced by `experiments/evaluate_cell_level.py`. This script is a
**read-only packager**: it does NOT recompute GT comparison (that is
evaluate_cell_level's responsibility), it only derives the A1 view required
by `design/experiment_implementation.md` §3.5:

  1. overall_metrics_per_arm       — strict / relaxed / macro-F1 / weighted-F1 / purity / unknown_rate
  2. per_type_metrics              — per ground-truth label × (precision / recall / n_correct / n_total)
  3. confusion_matrix_12x12        — predicted × true (rows=pred, cols=true), per arm
  4. failure_top5_confusion_pairs  — arm3 worst (predicted → true) pairs by FP count
  5. unknown_concentration         — unknown clusters per arm

Usage:
    python experiments/A1_summary.py \\
        --eval-dir output/B1/eval \\
        --label-map experiments/label_map.json \\
        --out output/B1/eval/A1_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict


def load_label_map(path: str) -> tuple[list[str], dict, dict[str, str]]:
    """Returns (true_types_sorted, label_map_doc, raw_to_true_predicted_top1).

    raw_to_true is the predicted-term → its first mapped true label. Used by
    confusion matrix and per-type precision. Note: same term can map to multiple
    true labels via different entries; we use the FIRST as the canonical
    predicted bucket (same convention as evaluate_cell_level).
    """
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    true_types = sorted(set(e["true"] for e in doc["entries"] if e.get("true")))
    raw_to_true: dict[str, str] = {}
    for e in doc["entries"]:
        if not e.get("predicted") or not e.get("true"):
            continue
        raw_to_true.setdefault(e["predicted"], e["true"])
    return true_types, doc, raw_to_true


def load_per_arm_cell(path: str) -> dict[str, list[dict]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def weighted_f1_from_per_cell(per_cell: list[dict], raw_to_true: dict[str, str]) -> float:
    """Weighted (support-weighted) macro-F1.

    Faithful reuse of evaluate_cell_level's accounting: a cell is TP for `true`
    iff is_strict_correct; the predicted bucket for FP is `raw_to_true.get(raw)`
    (so off-vocab preds bucket under the last "<other>" extra label). FN is
    weighted full (1.0) for missed cells; partial hits (subtype/supertype)
    are not re-weighted because the per_cell `is_strict_correct` flag already
    reflects the strict relation/confidence gate.
    """
    extra = "<other>"
    # discover dynamic label set
    labels = set(c["true"] for c in per_cell if c.get("true"))
    for c in per_cell:
        if c["raw"] != "unknown":
            mapped = raw_to_true.get(c["raw"])
            if mapped:
                labels.add(mapped)
    labels = sorted(labels | {extra})
    label_idx = {l: i for i, l in enumerate(labels)}
    tp = {l: 0 for l in labels}
    fp = {l: 0 for l in labels}
    fn = {l: 0 for l in labels}
    for c in per_cell:
        true = c["true"]
        if c["raw"] == "unknown":
            pred = extra
        else:
            pred = raw_to_true.get(c["raw"]) or extra
        if c["is_strict_correct"]:
            tp[true] += 1
        else:
            fp[pred] += 1
            fn[true] += 1
    f1s: dict[str, float] = {}
    for l in labels:
        p = tp[l] / (tp[l] + fp[l]) if (tp[l] + fp[l]) else 0.0
        r = tp[l] / (tp[l] + fn[l]) if (tp[l] + fn[l]) else 0.0
        f1s[l] = 2 * p * r / (p + r) if (p + r) else 0.0
    support = Counter(c["true"] for c in per_cell if c.get("true"))
    total_sup = sum(support.values())
    if total_sup == 0:
        return 0.0
    return sum(f1s[l] * support[l] for l in labels if l in support) / total_sup


def build_arm_view(arm: str, per_cell: list[dict], true_types: list[str],
                   raw_to_true: dict[str, str]) -> dict:
    n = len(per_cell)
    if n == 0:
        return {"arm": arm, "n_cells": 0}
    strict_hits = sum(1 for c in per_cell if c["is_strict_correct"])
    relaxed_score = sum(c["cell_weight"] for c in per_cell)
    # unknown_rate = cells whose final prediction is "unknown" (status==unknown)
    unknown_n = sum(1 for c in per_cell if c["status"] == "unknown" or c["raw"] == "unknown")
    # 12x12 confusion: rows = predicted (mapped via label_map → true),
    # cols = true. Off-vocab preds and unknown get a 13th bucket.
    label_idx = {t: i for i, t in enumerate(true_types)}
    extra_label = "<other>"  # 13th row/col
    matrix = [[0] * (len(true_types) + 1) for _ in range(len(true_types) + 1)]
    extra_col_idx = len(true_types)
    for c in per_cell:
        t = c["true"]
        ti = label_idx.get(t, extra_col_idx)
        raw = c["raw"]
        if raw == "unknown":
            pi = extra_col_idx
        else:
            mapped = raw_to_true.get(raw)
            pi = label_idx.get(mapped, extra_col_idx) if mapped else extra_col_idx
        matrix[pi][ti] += 1
    # per-type metrics (using raw → true mapping via label_map for both precision & recall)
    per_type: dict[str, dict] = {}
    for t in true_types:
        n_total = sum(1 for c in per_cell if c["true"] == t)
        n_correct = sum(1 for c in per_cell if c["true"] == t and c["is_strict_correct"])
        n_pred_t = sum(1 for c in per_cell
                       if raw_to_true.get(c["raw"], c["raw"]) == t)
        n_tp = sum(1 for c in per_cell
                   if raw_to_true.get(c["raw"], c["raw"]) == t and c["is_strict_correct"])
        precision = n_tp / n_pred_t if n_pred_t else 0.0
        recall = n_correct / n_total if n_total else 0.0
        per_type[t] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "n_correct": n_correct,
            "n_total": n_total,
            "n_predicted": n_pred_t,
            "n_true_positive": n_tp,
        }
    # top5 confusion pairs: cells where predicted (via label_map) ≠ true
    pred_count: Counter = Counter()
    for c in per_cell:
        raw = c["raw"]
        if raw == "unknown":
            mapped = "<unknown>"
        else:
            mapped = raw_to_true.get(raw, raw)
        if mapped != c["true"]:
            pred_count[(mapped, c["true"])] += 1
    top5 = [{"predicted": k[0], "true": k[1], "n_wrong": v} for k, v in pred_count.most_common(5)]
    return {
        "arm": arm,
        "n_cells": n,
        "strict_accuracy": round(strict_hits / n, 4),
        "relaxed_accuracy": round(relaxed_score / n, 4),
        "weighted_f1": round(weighted_f1_from_per_cell(per_cell, raw_to_true), 4),
        "unknown_rate": round(unknown_n / n, 4),
        "n_unknown": unknown_n,
        "per_type_metrics": per_type,
        "confusion_matrix_12x12": {"labels": true_types, "matrix": matrix},
        "top5_confusion_pairs": top5,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="A1_summary.py",
                                 description="Story 6.7 / A1 — 端到端准确率报告(纯 reuse)")
    ap.add_argument("--eval-dir", default="output/B1/eval",
                    help="evaluate_cell_level 输出目录(含 evaluation_report.json + .per_cell.json)")
    ap.add_argument("--label-map", default="experiments/label_map.json")
    ap.add_argument("--out", default="output/B1/eval/A1_report.json")
    args = ap.parse_args()

    eval_report_path = os.path.join(args.eval_dir, "evaluation_report.json")
    per_cell_path = os.path.join(args.eval_dir, "evaluation_report.per_cell.json")
    if not os.path.exists(eval_report_path):
        raise SystemExit(f"missing: {eval_report_path}")
    if not os.path.exists(per_cell_path):
        raise SystemExit(f"missing: {per_cell_path}")

    with open(eval_report_path, encoding="utf-8") as f:
        eval_doc = json.load(f)
    per_arm_cells = load_per_arm_cell(per_cell_path)
    true_types, lm_doc, raw_to_true = load_label_map(args.label_map)
    if lm_doc.get("_meta", {}).get("verified") is not True:
        print("[A1] WARNING: label_map 未定案", file=sys.stderr)

    # Cross-check: n_cells in eval arms_summary matches per_cell
    summary = eval_doc.get("arms_summary", [])
    summary_by_arm = {s["arm"]: s for s in summary}

    overall_per_arm = []
    per_arm_views = {}
    for arm_name in sorted(per_arm_cells.keys()):
        per_cell = per_arm_cells[arm_name]
        view = build_arm_view(arm_name, per_cell, true_types, raw_to_true)
        per_arm_views[arm_name] = view
        # Add macro_f1_soft from existing summary (no recompute)
        s = summary_by_arm.get(arm_name)
        if s:
            view["macro_f1_soft"] = s.get("macro_f1_soft")
            view["mean_cluster_purity"] = s.get("mean_cluster_purity")
        overall_per_arm.append({
            "arm": arm_name,
            "n_cells": view["n_cells"],
            "strict_accuracy": view["strict_accuracy"],
            "relaxed_accuracy": view["relaxed_accuracy"],
            "macro_f1_soft": view.get("macro_f1_soft"),
            "weighted_f1": view["weighted_f1"],
            "mean_cluster_purity": view.get("mean_cluster_purity"),
            "unknown_rate": view["unknown_rate"],
            "n_unknown": view["n_unknown"],
        })

    report = {
        "label_map_verified": lm_doc.get("_meta", {}).get("verified") is True,
        "source_evaluation_report": eval_report_path,
        "source_per_cell": per_cell_path,
        "ground_truth_types": true_types,
        "overall_metrics_per_arm": overall_per_arm,
        "per_arm_detail": {a: {
            "per_type_metrics": v["per_type_metrics"],
            "confusion_matrix_12x12": v["confusion_matrix_12x12"],
            "top5_confusion_pairs": v["top5_confusion_pairs"],
        } for a, v in per_arm_views.items()},
        "failure_top5_confusion_pairs": per_arm_views.get("arm3", {}).get("top5_confusion_pairs", []),
        "unknown_concentration": {
            arm: {
                "n_unknown_cells": v["n_unknown"],
                "unknown_rate": v["unknown_rate"],
            } for arm, v in per_arm_views.items()
        },
        "verdict": {
            "arm3_strict_accuracy": per_arm_views.get("arm3", {}).get("strict_accuracy"),
            "arm1_strict_accuracy": per_arm_views.get("arm1", {}).get("strict_accuracy"),
            "delta_arm3_minus_arm1": (
                per_arm_views.get("arm3", {}).get("strict_accuracy", 0) -
                per_arm_views.get("arm1", {}).get("strict_accuracy", 0)
            ),
        },
        "notes": [
            "本报告纯 reuse evaluate_cell_level 产物,未重新读 GT 重算 cell-level 比较",
            "macro_f1_soft 来自 evaluation_report.json 的 arms_summary(与 B1 r1 报告一致);weighted_f1 由 per_cell 重新汇总(支持加权)",
            "12x12 混淆矩阵只覆盖出现在 label_map 里的 12 个 true 类型;raw=unknown 或未登录 raw 不计入矩阵行",
        ],
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # stdout 对比表
    print(f"[A1_summary] label_map_verified={report['label_map_verified']}")
    print(f"{'arm':<8} {'cells':>7} {'strict':>8} {'relaxed':>9} {'macroF1':>9} {'wtF1':>8} {'unkRate':>8}")
    for r in overall_per_arm:
        macro_str = f"{r['macro_f1_soft']:>9.4f}" if r['macro_f1_soft'] is not None else f"{'n/a':>9}"
        print(f"{r['arm']:<8} {r['n_cells']:>7} "
              f"{r['strict_accuracy']:>8.4f} {r['relaxed_accuracy']:>9.4f} "
              f"{macro_str} {r['weighted_f1']:>8.4f} "
              f"{r['unknown_rate']:>8.4f}")
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())