"""B1 cluster-aware bootstrap — difference in macro-F1 with 95% CI.

Implements B1 §3.1 R1 / R2 / R4:
    R1: ③ macroF1 − ② ≥ 0.03 AND bootstrap CI 不含 0 → ③ 胜 ②
    R2: ② − ① ≥ 0.03 AND CI 不含 0 → "读 metrics + 规则" 有价值
    R3: ③ − ② ∈ [−0.01, +0.01] → ③ ≈ ②
    R4: ③ − ② ≤ −0.03 → 结论反转

Cluster-aware resampling (preserves cluster structure):
    Each draw samples N leiden clusters with replacement; all cells of a
    drawn leiden are included verbatim. This matches the statistical unit
    that produced the cluster-level metrics (analyze_traps) and avoids the
    inflated cell-level variance of naive cell-level bootstrap.

Inputs:
    --per-cell <evaluation_report.per_cell.json>  (produced by evaluate_cell_level.py)
    --arms a,b,c                                      (two or more arms to compare pairwise)
    --n-boot 1000
    --seed 0
    --out  output/B1/eval/bootstrap_report.json

Output:
    {
      "pairwise": [
        {"ref": "arm1", "target": "arm2",
         "macroF1_ref": mean, "macroF1_target": mean,
         "delta_mean": mean(diff), "delta_ci_low": ..., "delta_ci_high": ...,
         "p_ge_0.03": fraction of draws where delta >= 0.03,
         "verdict": "R1" | "R2" | "R3" | "R4" | "inconclusive"}
      ]
    }
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter

STRICT_HIT = {"exact", "synonym"}
PARTIAL_HIT = {"subtype", "supertype"}
WEIGHT = {"exact": 1.0, "synonym": 1.0, "subtype": 1.5, "supertype": 0.5,
          "unrelated": 0.0, "unmatched": 0.0}


def _cluster_strict_relaxed_macro(cells: list[dict], true_labels: list[str]) -> dict:
    """Compute strict / relaxed / macro-F1 from a list of cell dicts.

    Identical semantics to evaluate_cell_level.evaluate_arm (relation weight
    only; confidence is not in the formula). Uses per_cell relation +
    mapped_true written at eval time (no pair-table hits[0]).
    """
    tp = {t: 0.0 for t in true_labels}
    fp = {t: 0.0 for t in true_labels}
    fn = {t: 0.0 for t in true_labels}
    strict_hits = 0
    score_sum = 0.0
    n = len(cells)
    for c in cells:
        rel = c["relation"]
        rel_w = c.get("relation_weight", WEIGHT.get(rel, 0.0))
        cell_w = rel_w
        is_strict = rel in STRICT_HIT
        if is_strict:
            strict_hits += 1
        score_sum += cell_w
        if rel in STRICT_HIT or rel in PARTIAL_HIT:
            tp[c["true"]] += cell_w
        else:
            fn[c["true"]] += 1.0
            mapped_true = c.get("mapped_true")
            if mapped_true and mapped_true in fp and mapped_true != c["true"]:
                fp[mapped_true] += 1.0
    f1s = []
    for t in true_labels:
        p = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        r = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1s.append(2 * p * r / (p + r) if (p + r) else 0.0)
    return {
        "strict": strict_hits / n if n else 0.0,
        "relaxed": score_sum / n if n else 0.0,
        "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
    }


def cluster_aware_bootstrap(arm_cells: dict[str, list[dict]],
                            n_boot: int = 1000,
                            seed: int = 0) -> dict:
    """Cluster-aware bootstrap for strict / relaxed / macroF1 across arms.

    Returns per-arm metric mean + 95% CI, plus pairwise deltas with verdict.
    """
    rng = random.Random(seed)
    arm_leidens: dict[str, list[str]] = {
        arm: sorted(set(c["leiden"] for c in cells)) for arm, cells in arm_cells.items()
    }
    arm_by_leiden: dict[str, dict[str, list[dict]]] = {
        arm: {lid: [] for lid in arm_leidens[arm]} for arm in arm_cells
    }
    for arm, cells in arm_cells.items():
        for c in cells:
            arm_by_leiden[arm][c["leiden"]].append(c)

    all_true_labels = sorted({c["true"] for cells in arm_cells.values() for c in cells})

    n_clusters = {arm: len(leidens) for arm, leidens in arm_leidens.items()}
    boot_metrics: dict[str, dict[str, list[float]]] = {
        arm: {"strict": [], "relaxed": [], "macro_f1": []} for arm in arm_cells
    }
    for _ in range(n_boot):
        for arm, leidens in arm_leidens.items():
            sample = []
            for _ in range(len(leidens)):
                lid = rng.choice(leidens)
                sample.extend(arm_by_leiden[arm][lid])
            m = _cluster_strict_relaxed_macro(sample, all_true_labels)
            for k, v in m.items():
                boot_metrics[arm][k].append(v)

    summary = {}
    for arm in arm_cells:
        summary[arm] = {"n_clusters": n_clusters[arm]}
        for metric in ("strict", "relaxed", "macro_f1"):
            vals = sorted(boot_metrics[arm][metric])
            summary[arm][f"{metric}_mean"] = sum(vals) / len(vals)
            summary[arm][f"{metric}_ci_low"] = vals[int(0.025 * len(vals))]
            summary[arm][f"{metric}_ci_high"] = vals[int(0.975 * len(vals)) - 1]

    # Pairwise deltas (target − ref) for each metric
    arms = list(arm_cells.keys())
    pairwise = []
    for i, ref in enumerate(arms):
        for tgt in arms[i + 1:]:
            row = {"ref": ref, "target": tgt}
            row["verdict"] = ""
            for metric, verdict_thresh in (
                ("macro_f1", 0.03),
                ("strict", 0.03),
                ("relaxed", 0.03),
            ):
                deltas = [boot_metrics[tgt][metric][k] - boot_metrics[ref][metric][k]
                          for k in range(n_boot)]
                deltas.sort()
                d_mean = sum(deltas) / len(deltas)
                d_low = deltas[int(0.025 * len(deltas))]
                d_high = deltas[int(0.975 * len(deltas)) - 1]
                row[f"delta_{metric}_mean"] = round(d_mean, 4)
                row[f"delta_{metric}_ci_low"] = round(d_low, 4)
                row[f"delta_{metric}_ci_high"] = round(d_high, 4)
                if d_low > verdict_thresh:
                    row[f"verdict_{metric}"] = f"R1: {tgt} > {ref} by ≥{verdict_thresh}"
                elif d_high < -verdict_thresh:
                    row[f"verdict_{metric}"] = f"R4: {tgt} < {ref} by >{verdict_thresh} (REVERSED)"
                elif d_low > -0.01 and d_high < 0.01:
                    row[f"verdict_{metric}"] = f"R3: {tgt} ≈ {ref} within ±0.01"
                else:
                    row[f"verdict_{metric}"] = "inconclusive (CI crosses ±threshold)"
            row["verdict"] = row["verdict_macro_f1"]  # primary verdict uses macro-F1 (R1 spec)
            pairwise.append(row)
    return {"per_arm": summary, "pairwise": pairwise, "n_boot": n_boot, "seed": seed}


def main() -> int:
    ap = argparse.ArgumentParser(prog="bootstrap_test.py",
                                 description="B1 cluster-aware bootstrap for macro-F1 diffs")
    ap.add_argument("--per-cell", required=True,
                    help="evaluation_report.per_cell.json (由 evaluate_cell_level.py 产出)")
    ap.add_argument("--arms", nargs="+", required=True,
                    help="要比较的 arm 名称(按顺序生成 pairwise)")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--label-map", default=None,
                    help="已停用; per_cell 已含 relation/mapped_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    bundle = json.load(open(args.per_cell, encoding="utf-8"))
    arm_cells = {arm: bundle[arm] for arm in args.arms}
    if any(arm not in bundle for arm in args.arms):
        raise SystemExit(f"--arms 缺失 arm;可用: {list(bundle.keys())}")

    if args.label_map:
        print("[bootstrap] WARNING: --label-map 已忽略;计分用 per_cell.relation",
              file=sys.stderr)

    result = cluster_aware_bootstrap(arm_cells, n_boot=args.n_boot, seed=args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 摘要
    print(f"[bootstrap] {args.n_boot} draws, seed={args.seed}")
    print(f"{'arm':<12} {'strict':>8} {'relaxed':>8} {'macroF1':>9}")
    for arm, s in result["per_arm"].items():
        print(f"{arm:<12} {s['strict_mean']:>8.4f} {s['relaxed_mean']:>8.4f} {s['macro_f1_mean']:>9.4f}")
    print()
    for p in result["pairwise"]:
        print(f"{p['ref']} → {p['target']}:")
        for metric in ("macro_f1", "strict", "relaxed"):
            d = p[f"delta_{metric}_mean"]
            lo, hi = p[f"delta_{metric}_ci_low"], p[f"delta_{metric}_ci_high"]
            print(f"  {metric:>9} Δ={d:+.4f}  CI=[{lo:+.4f}, {hi:+.4f}]  → {p[f'verdict_{metric}']}")
    print(f"\n  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())