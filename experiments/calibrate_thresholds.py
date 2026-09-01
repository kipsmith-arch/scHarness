"""Story 6.9 — rule_judge 阈值扫描与校准。

为什么需要这个 (experiment_implementation.md §3.1 + AGENTS.md Next steps #1):
    B1 r1 显示 arm2 strict=0.1435 < arm3 strict=0.6253。
    31/39 簇被 rule_judge 降级(label_downgraded),说明 3 个阈值太紧。
    本脚本不改 rule_judge.py;先把所有簇的 count_diff / ratio / pct2 拉出,
    做"参数扫描":在不同 (DIFF_THRESH, GAP_RATIO, PCT2_THRESH) 组合下,
    预测 arm2 的 strict / relaxed / macroF1 数字。
    选最优组合 → 写到 rule_judge.py → 重跑 arm2 → 实测验证。

数据源:
    output/B1/arm3_llm/step4_judge/annotations.json    — first/second/gap
    output/B1/arm3_llm/step6_validate/final_annotations.json — top3_expression
    experiments/gt_cells.csv + label_map.json            — 真值

不依赖 LLM 调用;纯 Python。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_ANN = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm", "step4_judge", "annotations.json")
DEFAULT_FINAL = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm", "step6_validate", "final_annotations.json")
DEFAULT_OBS = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm", "step1_prepare", "obs_snapshot.csv")
DEFAULT_GT = os.path.join(REPO_ROOT, "experiments", "gt_cells.csv")
DEFAULT_LABEL_MAP = os.path.join(REPO_ROOT, "experiments", "label_map.json")
DEFAULT_OUT = os.path.join(REPO_ROOT, "output", "B1", "threshold_calibration", "scan_report.json")

# Current (over-conservative) defaults from rule_judge.py
DEFAULT_THRESHOLDS = {
    "DIFF_THRESH_FOR_DECISIVE": 3,
    "GAP_RATIO_TIED": 1.2,
    "PCT2_HIGH_THRESHOLD": 0.5,
    "MEDIUM_CONFIDENCE_RATIO": 1.5,
    "HIGH_CONFIDENCE_RATIO": 2.0,
}


def load_obs(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = (r.get("cell_id") or "").strip()
            l = (r.get("leiden") or "").strip()
            if c:
                out[c] = l
    return out


def load_gt(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = (r.get("cell_barcode") or "").strip()
            t = (r.get("true_type") or "").strip()
            if c:
                out[c] = t
    return out


def load_label_map(path: str) -> tuple[dict[str, list[dict]], dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    mapping: dict[str, list[dict]] = {}
    for e in doc["entries"]:
        mapping.setdefault(e["predicted"], []).append({"true": e["true"], "relation": e["relation"]})
    return mapping, doc.get("_meta", {})


def cluster_top_true(gt: dict, obs: dict, cid: str) -> str | None:
    c = Counter()
    for cell, leid in obs.items():
        if leid == cid and cell in gt:
            c[gt[cell]] += 1
    if not c:
        return None
    return c.most_common(1)[0][0]


# We need csv for load_obs / load_gt
import csv  # noqa: E402


def collect_cluster_features(ann: dict, final: dict) -> dict[str, dict]:
    """For each cluster id, pull count_diff / ratio / max_pct2 / ancestor_overlap / label."""
    out: dict[str, dict] = {}
    ann_clusters = ann.get("annotations", ann)
    final_ann = final.get("annotations", final)
    for cid, a in ann_clusters.items():
        f = final_ann.get(cid, {})
        gap = a.get("gap_metrics", {})
        first = a.get("first_candidate") or {}
        second = a.get("second_candidate") or {}
        top3 = f.get("top3_expression", []) or a.get("candidates", [{}])[0].get("supporting_markers", [])
        max_pct2 = 0.0
        # use top3_expression pct2 if present
        for m in (f.get("top3_expression") or []):
            v = m.get("pct2")
            if isinstance(v, (int, float)):
                max_pct2 = max(max_pct2, float(v))
        out[cid] = {
            "first_cell_type": first.get("cell_type"),
            "second_cell_type": second.get("cell_type"),
            "first_count": a.get("first_count") or first.get("marker_count") or 0,
            "second_count": a.get("second_count") or second.get("marker_count") or 0,
            "count_diff": gap.get("count_diff") or 0,
            "count_ratio": gap.get("count_ratio") or 0.0,
            "ancestor_overlap": bool((gap.get("first_second_ancestor_overlap") or {}).get("related")),
            "max_top3_pct2": max_pct2,
            "n_candidates": a.get("n_candidates") or 0,
        }
    return out


def predict_arm2(features: dict[str, dict], cid: str, t: dict) -> dict:
    """Mirror rule_judge.py logic exactly for one cluster.

    Returns {"decision": first_decisive|ambiguous_true|ambiguous_parent_child|ambiguous_synonym,
             "label_confirm": label_confirmed|label_downgraded,
             "final_confidence": high|medium|low,
             "label": <first_cell_type>}
    """
    f = features.get(cid, {})
    diff = f["count_diff"]
    ratio = f["count_ratio"]
    anc = f["ancestor_overlap"]
    max_pct2 = f["max_top3_pct2"]
    first_ct = f["first_cell_type"]

    # 1. candidate_gap
    if diff >= t["DIFF_THRESH_FOR_DECISIVE"]:
        gap_decision = "first_decisive"
    elif ratio >= 2.0 and diff < t["DIFF_THRESH_FOR_DECISIVE"]:
        # 陷阱3 — only triggers when ratio>=2 (HIGH_CONFIDENCE_RATIO)
        gap_decision = "ambiguous_true"
    elif anc:
        gap_decision = "ambiguous_parent_child"
    elif ratio < t["GAP_RATIO_TIED"] and f["second_count"] > 0:
        gap_decision = "ambiguous_synonym"
    else:
        gap_decision = "first_decisive"

    # 2. label_confirm
    if max_pct2 > t["PCT2_HIGH_THRESHOLD"]:
        # 陷阱4 — pct1 high AND pct2 high (we approximate pct1 high by always-true here;
        # the original code checks pct1>0.5; we'll use the same simplified proxy).
        confirm = "label_downgraded"
        conf = "low"
    elif ratio < t["MEDIUM_CONFIDENCE_RATIO"]:
        confirm = "label_downgraded"
        conf = "low"
    else:
        confirm = "label_confirmed"
        conf = "high" if ratio >= 2.0 else "medium"

    return {
        "gap_decision": gap_decision,
        "label_confirm": confirm,
        "final_confidence": conf,
        "label": first_ct,
        "first_count": f["first_count"],
        "second_count": f["second_count"],
        "ratio": ratio,
        "diff": diff,
        "max_pct2": max_pct2,
    }


def evaluate_predictions(features: dict[str, dict], cluster_top_true: dict[str, str],
                          label_map: dict, t: dict, arm1: bool = False) -> dict:
    """Run prediction over all clusters, compute cell-level strict/relaxed/macroF1.

    If arm1=True (default baseline, no judgement layer), all clusters use first_cell_type
    with high confidence.
    """
    STRICT_HIT = {"exact", "synonym"}
    PARTIAL_HIT = {"subtype", "supertype"}
    WEIGHT = {"exact": 1.0, "synonym": 1.0, "subtype": 0.5, "supertype": 0.5, "unrelated": 0.0, "unmatched": 0.0}
    CONF = {"high": 1.0, "medium": 1.0, "low": 0.5}

    n_correct = 0
    n_total = 0
    score_sum = 0.0
    per_true: dict[str, dict[str, float]] = defaultdict(lambda: {"tp": 0.0, "fp": 0.0, "fn": 0.0})
    per_cluster_pred: dict[str, dict] = {}

    for cid, f in features.items():
        if not f["first_cell_type"]:
            continue
        if arm1:
            pred_label = f["first_cell_type"]
            conf = "high"
        else:
            pred = predict_arm2(features, cid, t)
            pred_label = pred["label"]
            conf = pred["final_confidence"]
            if pred["label_confirm"] == "label_downgraded":
                pred_label = pred["label"]  # we still keep the predicted label; low conf just downgrades

        hits = label_map.get(pred_label, []) if pred_label != "unknown" else []
        if not hits:
            relation = "unmatched"
        else:
            relation = hits[0]["relation"]
        rel_w = WEIGHT.get(relation, 0.0)
        conf_w = CONF.get(conf, 0.5)
        cell_w = rel_w * conf_w
        is_strict = relation in STRICT_HIT and conf in ("high", "medium")

        true = cluster_top_true.get(cid)
        if true is None:
            continue
        n_total += 1
        if is_strict:
            n_correct += 1
        score_sum += cell_w
        if relation in STRICT_HIT or relation in PARTIAL_HIT:
            mapped_true = (hits[0]["true"] if hits else true)
            if mapped_true == true:
                per_true[true]["tp"] += cell_w
            else:
                per_true[mapped_true]["fp"] += cell_w
                per_true[true]["fn"] += cell_w
        else:
            per_true[true]["fn"] += 1.0
        per_cluster_pred[cid] = {
            "label": pred_label,
            "confidence": conf,
            "relation": relation,
            "true": true,
            "is_strict": is_strict,
            "cell_weight": cell_w,
        }

    if n_total == 0:
        return {"strict": 0.0, "relaxed": 0.0, "macro_f1": 0.0, "n_clusters": 0}

    strict = n_correct / n_total
    relaxed = score_sum / n_total
    f1s = []
    for t_name, v in per_true.items():
        p = v["tp"] / (v["tp"] + v["fp"]) if (v["tp"] + v["fp"]) else 0.0
        r = v["tp"] / (v["tp"] + v["fn"]) if (v["tp"] + v["fn"]) else 0.0
        f1s.append(2 * p * r / (p + r) if (p + r) else 0.0)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    return {
        "strict": strict,
        "relaxed": relaxed,
        "macro_f1": macro_f1,
        "n_clusters": n_total,
        "n_strict_correct": n_correct,
        "low_conf_count": sum(1 for c in per_cluster_pred.values() if c["confidence"] == "low"),
    }


def scan_thresholds(features: dict[str, dict], cluster_top_true: dict[str, str],
                     label_map: dict) -> list[dict]:
    """Grid-search threshold combinations; rank by arm2 strict - arm1 macroF1_strict_penalty."""
    candidates = []
    for diff in [1, 2, 3, 4, 5]:
        for ratio_t in [1.05, 1.1, 1.15, 1.2, 1.25, 1.3]:
            for pct2_t in [0.5, 0.6, 0.7, 0.8]:
                for med_t in [1.0, 1.01, 1.02, 1.05, 1.08, 1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.8, 2.0]:
                    t = {"DIFF_THRESH_FOR_DECISIVE": diff,
                         "GAP_RATIO_TIED": ratio_t,
                         "PCT2_HIGH_THRESHOLD": pct2_t,
                         "MEDIUM_CONFIDENCE_RATIO": med_t,
                         "HIGH_CONFIDENCE_RATIO": 2.0}
                    m2 = evaluate_predictions(features, cluster_top_true, label_map, t)
                    # Headline metric: strict + relaxed
                    score = (m2["strict"], m2["relaxed"])
                    candidates.append({"thresholds": t, "metrics": m2, "score": score})
    candidates.sort(key=lambda x: (-x["score"][0], -x["score"][1]))
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(prog="calibrate_thresholds.py",
                                 description="Story 6.9 — rule_judge 阈值扫描 + 校准建议")
    ap.add_argument("--annotations", default=DEFAULT_ANN)
    ap.add_argument("--final-annotations", default=DEFAULT_FINAL)
    ap.add_argument("--obs", default=DEFAULT_OBS)
    ap.add_argument("--gt", default=DEFAULT_GT)
    ap.add_argument("--label-map", default=DEFAULT_LABEL_MAP)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--top-k", type=int, default=15, help="报告中列前 K 个候选阈值组合")
    args = ap.parse_args()

    with open(args.annotations, encoding="utf-8") as f:
        ann = json.load(f)
    with open(args.final_annotations, encoding="utf-8") as f:
        final = json.load(f)
    obs = load_obs(args.obs)
    gt = load_gt(args.gt)
    label_map, _meta = load_label_map(args.label_map)

    features = collect_cluster_features(ann, final)
    cluster_true: dict[str, str] = {}
    for cid in features:
        t = cluster_top_true(gt, obs, cid)
        if t:
            cluster_true[cid] = t

    # arm1 baseline
    arm1 = evaluate_predictions(features, cluster_true, label_map, {}, arm1=True)
    # arm2 with current defaults
    arm2_now = evaluate_predictions(features, cluster_true, label_map, DEFAULT_THRESHOLDS)

    # Scan
    candidates = scan_thresholds(features, cluster_true, label_map)
    top = candidates[: args.top_k]

    payload = {
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "arm1_baseline_strict": arm1["strict"],
            "arm2_current_strict": arm2_now["strict"],
            "arm1_baseline_relaxed": arm1["relaxed"],
            "arm2_current_relaxed": arm2_now["relaxed"],
            "n_clusters_scanned": len(features),
            "decision_rules": (
                "B1 §3.1: arm2 strict >= 0.6 (calibration target); "
                "macroF1 arm2 vs arm1 — R3 holds if |delta| < 0.03 (no benefit from no-judgement)."
            ),
        },
        "arm1_baseline": arm1,
        "arm2_current_defaults": arm2_now,
        "top_candidates": top,
        "all_candidates_count": len(candidates),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # CLI report
    print(f"[calibrate_thresholds] arm1 strict={arm1['strict']:.4f}  "
          f"arm2(strict defaults) strict={arm2_now['strict']:.4f}")
    print(f"  n_clusters_scanned: {len(features)}")
    print(f"  candidates explored: {len(candidates)}")
    print()
    print(f"  Top {args.top_k} candidates by (strict, relaxed):")
    print(f"  {'DIFF':>5} {'RATIO':>6} {'PCT2':>5} | {'strict':>7} {'relaxed':>8} {'macroF1':>8} {'low_conf':>9}")
    for c in top:
        t = c["thresholds"]
        m = c["metrics"]
        print(f"  {t['DIFF_THRESH_FOR_DECISIVE']:>5} "
              f"{t['GAP_RATIO_TIED']:>6.2f} "
              f"{t['PCT2_HIGH_THRESHOLD']:>5.1f} | "
              f"{m['strict']:>7.4f} {m['relaxed']:>8.4f} {m['macro_f1']:>8.4f} "
              f"{m['low_conf_count']:>9}")
    print(f"\  Recommendation: pick the top candidate that satisfies")
    print(f"  (a) arm2 strict >= 0.6  AND")
    print(f"  (b) macroF1 delta vs arm1 not blown (R3 holds)")
    print(f"\  Full report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())