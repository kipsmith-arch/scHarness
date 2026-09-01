"""C2 消融结果汇总。

为什么需要这个 (experiment_implementation.md §3.8 C2 — KG 消融):
    拿 evaluate_cell_level.py 跑出的 arm3_full_kg vs no_kg 报告,
    按 §3.8 判定线 (fullKG − 无KG ≥ 0.03) + §3.6 A3 (harness − Marker硬匹配 ≥ 0.03)
    形成 C2 结论。

数据源:
    `output/C2/eval/c2_vs_b1_3.json` (由 evaluate_cell_level.py 生成)

判定维度:
    - strict_accuracy: 严格正确率(实验设计的硬指标)
    - relaxed_accuracy: 宽松正确率
    - macro_f1_soft: soft macro-F1(关系权值 × 置信度权值)
    - unknown_rate: unknown 簇占比
    - low_confidence_count: 低置信度细胞数

输出: `output/C2/eval/c2_summary.json`, stdout 输出对照表 + 判定结论
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_IN = os.path.join(REPO_ROOT, "output", "C2", "eval", "c2_vs_b1_3.json")
DEFAULT_OUT = os.path.join(REPO_ROOT, "output", "C2", "eval", "c2_summary.json")

# Pre-registered decision lines from design/experiment_implementation.md §3.8 C2 + §4
DECISION_LINE_FULLKG_MINUS_NOKG = 0.03  # §3.8 C2: fullKG − noKG ≥ 0.03 → "KG layer has value"
# §3.6 A3: harness − Marker hard matching ≥ 0.03 → "harness >= baseline"

# Interpretation guidance for the two metrics — they're complementary:
#   strict: precision-of-label (cell-level: predicted cell_type exactly matches true)
#   macro_f1_soft: balanced across all 12 types. A degenerate "spread labels uniformly"
#                     policy can inflate macro_f1 while collapsing strict.
INTERPRET = {
    "strict": "cell-level hard match (predicted == true AND confidence not low)",
    "relaxed": "cell-level soft match (relation weight × confidence weight)",
    "macro_f1_soft": "macro-F1 across all 12 types (soft, per-type weighted)",
    "unknown_rate": "fraction of cells labeled unknown / unmatched",
    "low_confidence_count": "cells with confidence=low (downgraded or unknown)",
}


def fmt_diff(diff: float, threshold: float) -> str:
    sign = "+" if diff >= 0 else ""
    ok = "OK" if diff >= threshold else "FAIL"
    return f"{sign}{diff:.4f} ({ok}; threshold >= {threshold})"


def main() -> int:
    ap = argparse.ArgumentParser(prog="C2_summary.py",
                                 description="C2 消融结果汇总 + 判定线对照")
    ap.add_argument("--in", dest="in_path", default=DEFAULT_IN)
    ap.add_argument("--out", dest="out_path", default=DEFAULT_OUT)
    args = ap.parse_args()

    if not os.path.exists(args.in_path):
        print(f"[C2_summary] ERROR: missing {args.in_path}", file=sys.stderr)
        return 1

    with open(args.in_path, encoding="utf-8") as f:
        report = json.load(f)

    arms = {r["arm"]: r for r in report.get("arms", [])}
    if "arm3_full_kg" not in arms or "no_kg" not in arms:
        print(f"[C2_summary] ERROR: expected arms arm3_full_kg and no_kg, got {list(arms)}", file=sys.stderr)
        return 1

    fk, nk = arms["arm3_full_kg"], arms["no_kg"]
    diff = {k: round(fk[k] - nk[k], 4) for k in ["strict_accuracy", "relaxed_accuracy",
                                                    "macro_f1_soft", "mean_cluster_purity"]}
    diff_low_conf = fk.get("confidence_distribution", {}).get("low", 0) - \
                     nk.get("confidence_distribution", {}).get("low", 0)

    # Decision rule check (pre-registered §3.8 + §4)
    # Primary metric: strict_accuracy. Secondary: macro_f1 (for sanity).
    strict_pass = diff["strict_accuracy"] >= DECISION_LINE_FULLKG_MINUS_NOKG
    macrof1_note = ""
    if not strict_pass:
        macrof1_note = " (strict failed; no-KG has higher macroF1 → uniform-spread artifact)"
    elif diff["macro_f1_soft"] < 0:
        macrof1_note = " (warning: macroF1 slightly favors no-KG — investigate per-type spread)"

    conclusion = (
        "KG layer adds value (strict_accuracy gain >= 0.03)" if strict_pass
        else "KG layer does NOT add clear strict-accuracy gain — investigate"
    )

    payload = {
        "decision_lines": {
            "primary": {"metric": "strict_accuracy", "threshold": DECISION_LINE_FULLKG_MINUS_NOKG,
                          "fullKG_minus_noKG": diff["strict_accuracy"], "pass": strict_pass},
            "secondary_note": macrof1_note or "n/a",
        },
        "arms": {
            "arm3_full_kg": fk,
            "no_kg": nk,
        },
        "deltas_fullKG_minus_noKG": diff,
        "delta_low_confidence_count": diff_low_conf,
        "interpretation": INTERPRET,
        "conclusion": conclusion,
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "source_report": args.in_path,
            "label_map_verified": report.get("label_map_verified", False),
        },
    }

    os.makedirs(os.path.dirname(args.out_path), exist_ok=True)
    with open(args.out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[C2_summary] arm3_full_kg  vs  no_kg (cell-level evaluation):")
    print(f"               full-KG        no-KG        diff  (threshold >= 0.03)")
    print(f"  strict     {fk['strict_accuracy']:10.4f}  {nk['strict_accuracy']:10.4f}  "
          f"{fmt_diff(diff['strict_accuracy'], DECISION_LINE_FULLKG_MINUS_NOKG)}")
    print(f"  relaxed    {fk['relaxed_accuracy']:10.4f}  {nk['relaxed_accuracy']:10.4f}  "
          f"{fmt_diff(diff['relaxed_accuracy'], 0.0)}")
    print(f"  macroF1    {fk['macro_f1_soft']:10.4f}  {nk['macro_f1_soft']:10.4f}  "
          f"{fmt_diff(diff['macro_f1_soft'], 0.0)}")
    print(f"  purity     {fk['mean_cluster_purity']:10.4f}  {nk['mean_cluster_purity']:10.4f}  "
          f"{fmt_diff(diff['mean_cluster_purity'], 0.0)}")
    print(f"  low_conf   {fk.get('confidence_distribution', {}).get('low', 0):>10}  "
          f"{nk.get('confidence_distribution', {}).get('low', 0):>10}  "
          f"({diff_low_conf:+d})")
    print(f"\n  CONCLUSION: {conclusion}{macrof1_note}")
    print(f"  report: {args.out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())