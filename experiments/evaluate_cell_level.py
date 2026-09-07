"""B1 cell-level evaluation — label–map accuracy; confidence is diagnostic only.

Why a new script (instead of editing the existing one):
    ``final_annotations.json`` 的 ``label`` / ``confidence`` / ``status`` 必须由判断层写入;
    本脚本不再从 ``run_log`` 做第二套覆盖。缺这三项则非零退出。

Cell-level semantics (b1-r3-followup.md §2):
    strict_correct = relation in {exact, synonym}
    relaxed        = relation weight only (exact/synonym=1, subtype/supertype=0.5)
    label_unknown  = raw "unknown" → relation=unmatched (no type to score)
    label_downgraded keeps the predicted label; low confidence is reported as
    ``low_conf_rate``, not multiplied into accuracy.

Usage:
    python experiments/evaluate_cell_level.py \\
        --arms arm1=output/B1/arm1_default arm2=output/B1/arm2_rule arm3=output/B1/arm3_llm \\
        --gt-csv experiments/gt_cells.csv --label-map experiments/label_map.json \\
        --out output/B1/eval/evaluation_report.json
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
WEIGHT = {"exact": 1.0, "synonym": 1.0, "subtype": 0.5, "supertype": 0.5, "unrelated": 0.0, "unmatched": 0.0}
# Recorded on per_cell for diagnostics; not used in strict / relaxed / macro-F1.
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 1.0, "low": 0.5}


def load_obs_snapshot(path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "leiden" not in (reader.fieldnames or []):
            raise SystemExit(f"error: {path} 缺少 leiden 列")
        for r in reader:
            cell = (r.get("cell_id") or "").strip()
            leiden = (r.get("leiden") or "").strip()
            if cell:
                mapping[cell] = leiden
    return mapping


def load_final_annotations(path: str) -> dict[str, dict]:
    """leiden -> {label, confidence, status, ...}. Requires judge-written fields."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    ann = doc.get("annotations", doc)
    out: dict[str, dict] = {}
    required = ("label", "confidence", "status")
    for k, v in ann.items():
        if not isinstance(v, dict):
            raise SystemExit(
                f"error: {path} cluster {k} 缺少判断层写入的 label/confidence/status"
                "（pipeline 不再写入这些字段）"
            )
        missing = [f for f in required if not v.get(f)]
        if missing:
            raise SystemExit(
                f"error: {path} cluster {k} 缺少判断层写入的 {missing}"
                "（pipeline 不再写入 label/confidence/status）"
            )
        out[str(k)] = v
    if not out:
        raise SystemExit(
            f"error: {path} 没有任何带判断层 label/confidence/status 的簇"
        )
    return out


def load_label_map(path: str) -> tuple[dict[str, list[dict]], dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    mapping: dict[str, list[dict]] = {}
    for e in doc.get("entries", []):
        mapping.setdefault(e["predicted"], []).append({"true": e["true"], "relation": e["relation"]})
    return mapping, doc.get("_meta", {})


def load_gt(path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            cell = (r.get("cell_barcode") or "").strip()
            tt = (r.get("true_type") or "").strip()
            if cell:
                mapping[cell] = tt
    return mapping


def evaluate_arm(name: str, project_dir: str, obs_path: str, ann_path: str,
                 label_map: dict, meta: dict, gt: dict) -> dict:
    """Return a per-arm evaluation report (accuracy from label–map only)."""
    obs = load_obs_snapshot(obs_path)
    ann = load_final_annotations(ann_path)

    per_cell: list[dict] = []
    unmatched_terms: Counter = Counter()
    unknown_leiden: set[str] = set()
    for cell, true in gt.items():
        leiden = obs.get(cell)
        if leiden is None:
            continue
        ent = ann.get(str(leiden))
        if ent is None:
            unknown_leiden.add(str(leiden))
            raw, conf, status = "unknown", "low", "unknown"
        else:
            raw = ent.get("label") or "unknown"
            conf = ent.get("confidence") or "medium"
            status = ent.get("status") or "decisive"
        hits = label_map.get(raw, []) if raw != "unknown" else []
        if not hits and raw != "unknown":
            unmatched_terms[raw] += 1
            relation = "unmatched"
        elif raw == "unknown":
            relation = "unmatched"
        else:
            relation = hits[0]["relation"]
        rel_w = WEIGHT.get(relation, 0.0)
        conf_w = CONFIDENCE_WEIGHT.get(conf, 0.5)
        cell_w = rel_w
        is_strict = relation in STRICT_HIT
        per_cell.append({
            "cell": cell, "true": true, "leiden": leiden, "raw": raw,
            "confidence": conf, "status": status, "relation": relation,
            "relation_weight": rel_w, "confidence_weight": conf_w, "cell_weight": cell_w,
            "is_strict_correct": is_strict,
        })

    n = len(per_cell)
    if n == 0:
        raise SystemExit(f"error: {name} 无可评估细胞(obs 与 gt 无交集)")

    strict_hits = sum(1 for c in per_cell if c["is_strict_correct"])
    score_sum = sum(c["cell_weight"] for c in per_cell)
    strict_acc = strict_hits / n
    relaxed_acc = score_sum / n

    # soft macro-F1: per true label, weight TP/FP/FN by cell_weight
    true_labels = sorted(set(c["true"] for c in per_cell))
    tp = {t: 0.0 for t in true_labels}
    fp = {t: 0.0 for t in true_labels}
    fn = {t: 0.0 for t in true_labels}
    for c in per_cell:
        if c["relation"] in STRICT_HIT or c["relation"] in PARTIAL_HIT:
            mapped_true = label_map[c["raw"]][0]["true"]
            if mapped_true == c["true"]:
                tp[mapped_true] += c["cell_weight"]
            else:
                fp[mapped_true] += c["cell_weight"]
                fn[c["true"]] += c["cell_weight"]
        else:
            fn[c["true"]] += 1.0  # full miss
    f1s: dict[str, float] = {}
    for t in true_labels:
        p = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        r = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1s[t] = 2 * p * r / (p + r) if (p + r) else 0.0
    macro_f1 = sum(f1s.values()) / len(f1s) if f1s else 0.0

    # per-cluster aggregation
    cluster_true: dict[str, Counter] = {}
    cluster_per_arm = {}
    for c in per_cell:
        cluster_true.setdefault(c["leiden"], Counter())[c["true"]] += 1
    for leiden, cnt in sorted(cluster_true.items()):
        total = sum(cnt.values())
        top_true, top_n = cnt.most_common(1)[0]
        hits = [c for c in per_cell if c["leiden"] == leiden]
        strict_count = sum(1 for c in hits if c["is_strict_correct"])
        cluster_per_arm[leiden] = {
            "leiden": leiden, "n_cells": total, "predicted": hits[0]["raw"],
            "confidence": hits[0]["confidence"], "relation": hits[0]["relation"],
            "gap_metrics": ent.get("gap_metrics", {}),
            "top3_expression": ent.get("top3_expression", []),
            "subcluster": ent.get("subcluster"),
            "top_true": top_true, "top_true_n": top_n, "purity": top_n / total,
            "strict_correct_cells": strict_count,
        }
    mean_purity = sum(v["purity"] for v in cluster_per_arm.values()) / max(len(cluster_per_arm), 1)

    conf_dist = Counter(c["confidence"] for c in per_cell)
    n_low = conf_dist.get("low", 0)

    return {
        "arm": name,
        "project_dir": project_dir,
        "n_cells_evaluated": n,
        "strict_accuracy": round(strict_acc, 4),
        "relaxed_accuracy": round(relaxed_acc, 4),
        "macro_f1_soft": round(macro_f1, 4),
        "mean_cluster_purity": round(mean_purity, 4),
        "n_clusters": len(cluster_per_arm),
        "low_conf_rate": round(n_low / n, 4),
        "confidence_distribution": dict(conf_dist),
        "unmatched_terms": dict(unmatched_terms),
        "unknown_leiden": sorted(unknown_leiden),
        "per_cluster": cluster_per_arm,
        "per_cell": per_cell,  # for bootstrap reuse
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="evaluate_cell_level.py",
                                 description="B1 多臂细胞级评估(strict/relaxed 只看标签–对照表)")
    ap.add_argument("--arms", nargs="+", required=True,
                    help="格式 <name>=<project_dir>(可多个),如 arm1=output/B1/arm1_default")
    ap.add_argument("--gt-csv", default="experiments/gt_cells.csv")
    ap.add_argument("--label-map", default="experiments/label_map.json")
    ap.add_argument("--out", default=None, help="评估报告 JSON 路径")
    args = ap.parse_args()

    label_map, meta = load_label_map(args.label_map)
    gt = load_gt(args.gt_csv)
    if meta.get("verified") is not True:
        print("[evaluate_cell_level] WARNING: label_map 未定案", file=sys.stderr)

    arms: list[dict] = []
    for spec in args.arms:
        if "=" not in spec:
            raise SystemExit(f"--arms 格式应为 name=path,收到: {spec}")
        name, pdir = spec.split("=", 1)
        obs_path = os.path.join(pdir, "obs_snapshot.csv")
        if not os.path.exists(obs_path):
            obs_path = os.path.join(pdir, "step1_prepare", "obs_snapshot.csv")
        ann_path = os.path.join(pdir, "step6_validate", "final_annotations.json")
        if not os.path.exists(ann_path):
            raise SystemExit(f"missing: {ann_path}")
        arms.append(evaluate_arm(name, pdir, obs_path, ann_path, label_map, meta, gt))

    # 汇总对比表(去掉 per_cell 减小 JSON 体积)
    summary_table = []
    for r in arms:
        summary_table.append({
            "arm": r["arm"],
            "n_cells": r["n_cells_evaluated"],
            "strict_accuracy": r["strict_accuracy"],
            "relaxed_accuracy": r["relaxed_accuracy"],
            "macro_f1_soft": r["macro_f1_soft"],
            "mean_cluster_purity": r["mean_cluster_purity"],
            "low_conf_rate": r["low_conf_rate"],
            "confidence_distribution": r["confidence_distribution"],
            "n_clusters": r["n_clusters"],
        })

    report = {
        "label_map_verified": meta.get("verified") is True,
        "weights": {"relation": WEIGHT,
                    "confidence_diagnostic_only": CONFIDENCE_WEIGHT,
                    "strict_requires": "relation in {exact, synonym}",
                    "relaxed": "relation weight; confidence not multiplied"},
        "arms_summary": summary_table,
        "arms": [
            {k: v for k, v in r.items() if k != "per_cell"} for r in arms
        ],
    }
    # 保留一份 per_cell(便于 bootstrap 复用,避免重读 GT + obs)
    per_cell_payload = {r["arm"]: r["per_cell"] for r in arms}
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        # 单独存 per_cell(可能较大)
        pc_path = args.out.replace(".json", ".per_cell.json")
        with open(pc_path, "w", encoding="utf-8") as f:
            json.dump(per_cell_payload, f, ensure_ascii=False)

    # 打印对比表
    print(f"[evaluate_cell_level] label_map_verified={meta.get('verified') is True}")
    print(f"{'arm':<12} {'cells':>6} {'strict':>8} {'relaxed':>9} {'macroF1':>9} {'purity':>8} {'low_conf':>10}")
    for r in arms:
        low = r["confidence_distribution"].get("low", 0)
        print(f"{r['arm']:<12} {r['n_cells_evaluated']:>6} "
              f"{r['strict_accuracy']:>8.4f} {r['relaxed_accuracy']:>9.4f} "
              f"{r['macro_f1_soft']:>9.4f} {r['mean_cluster_purity']:>8.4f} "
              f"{low:>10}")
    print(f"  report: {args.out or '<stdout only>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())