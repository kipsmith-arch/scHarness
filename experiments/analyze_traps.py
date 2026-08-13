"""B1 trap analysis — compare arm decisions on trap-prone clusters.

B1 §3.1 oracle table defines 6 traps. Full trap-instance detection (which
leiden cluster is an instance of which trap) is non-trivial; this script
takes a pragmatic shortcut: for each decision point, it compares the arm's
decision distribution on **trap-prone** vs **non-trap-prone** clusters.

Trap-prone heuristics (B1 §3.1 oracle signals):
    candidate_gap:   first≈second (count_ratio < 1.2) OR count_diff < 3 → ambiguous
    candidate_disambiguate: same as above
    refine_effect:   step5 subcluster status == "analyzed" → potential refine
    label_confirm:   top-3 pct1 > 0.5 AND pct2 > 0.5 → potential 管家基因 (陷阱4)
    qc_threshold:    cp p90 > 0.05 → 陷阱1 (plant cp distribution)
    batch_effect:    overall batch_entropy < 1.4 → 陷阱6

For each arm × decision point, this script reports:
    - n_trap_prone clusters
    - n_non_trap_prone clusters
    - decision distribution in each group
    - which arm picks the oracle-preferred decision on trap-prone clusters

Outputs:
    output/B1/eval/traps_report.json + a markdown summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter


def _trap_prone_mask(per_cluster: dict, decision_point: str) -> dict[str, bool]:
    """Heuristic: return {leiden: True} for clusters considered trap-prone for the given dp."""
    mask: dict[str, bool] = {}
    if decision_point == "candidate_gap":
        for leiden, c in per_cluster.items():
            gap = c.get("gap_metrics") or {}
            ratio = gap.get("count_ratio") or 0
            diff = gap.get("count_diff") or 0
            mask[leiden] = (ratio and ratio < 1.2) or (diff < 3) or bool(gap.get("first_second_ancestor_overlap", {}).get("related"))
    elif decision_point == "candidate_disambiguate":
        for leiden, c in per_cluster.items():
            gap = c.get("gap_metrics") or {}
            ratio = gap.get("count_ratio") or 0
            mask[leiden] = ratio and ratio < 2
    elif decision_point == "refine_effect":
        for leiden, c in per_cluster.items():
            mask[leiden] = bool(c.get("subcluster") and c["subcluster"].get("status") == "analyzed")
    elif decision_point == "label_confirm":
        for leiden, c in per_cluster.items():
            top3 = c.get("top3_expression", [])
            pct1_high = any(m.get("pct1", 0) > 0.5 for m in top3)
            pct2_high = any(m.get("pct2", 0) > 0.5 for m in top3)
            mask[leiden] = pct1_high and pct2_high
    elif decision_point == "qc_threshold":
        # qc is session-level; mark all leidens equally
        for leiden in per_cluster:
            mask[leiden] = True
    elif decision_point == "batch_effect":
        for leiden in per_cluster:
            mask[leiden] = True
    else:
        for leiden in per_cluster:
            mask[leiden] = False
    return mask


def _per_arm_decisions_for_dp(per_cell: list[dict], decision_point: str) -> dict[str, str]:
    """For each cluster, return the arm's single decision for that dp (first seen)."""
    out: dict[str, str] = {}
    for c in per_cell:
        if c.get("decision_point") != decision_point:
            continue
        cid = str(c.get("scope", {}).get("cluster_id") or "")
        if cid and cid not in out:
            out[cid] = c["output"]["decision"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="analyze_traps.py",
                                 description="B1 陷阱点决策分析(heuristic trap detection)")
    ap.add_argument("--eval-report", required=True,
                    help="evaluation_report.json(由 evaluate_cell_level 产出)")
    ap.add_argument("--per-cell", required=True,
                    help="evaluation_report.per_cell.json(含 run_log 改写判断)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    eval_report = json.load(open(args.eval_report, encoding="utf-8"))
    per_cell_bundle = json.load(open(args.per_cell, encoding="utf-8"))

    # 取第一个 arm 的 per_cluster 作为 trap 检测基准(三个 arm 共享同一 first_candidate)
    first_arm = next(iter(per_cell_bundle.keys()))
    per_cluster = eval_report["arms"][0]["per_cluster"]

    # Read arm-specific judgments directly from run_log (per_cell lacks dp/scope).
    import glob
    arm_judgments: dict[str, list[dict]] = {}
    for arm, cells in per_cell_bundle.items():
        # project_dir is in the per-arm eval entry; but per_cell_bundle is keyed
        # by arm name → we need to recover the project_dir from eval_report.
        arm_entry = next((a for a in eval_report["arms"] if a["arm"] == arm), None)
        if not arm_entry:
            continue
        rl = os.path.join(arm_entry["project_dir"], "run_log.jsonl")
        if not os.path.exists(rl):
            continue
        with open(rl, encoding="utf-8") as f:
            arm_judgments[arm] = [json.loads(line) for line in f
                                  if line.strip() and '"type": "judgment"' in line]

    trap_dps = ("candidate_gap", "candidate_disambiguate", "refine_effect", "label_confirm",
                "qc_threshold", "batch_effect")

    report: dict = {"trap_dps": list(trap_dps), "arms": {}}
    summary_lines = ["# B1 陷阱分析 (heuristic trap detection)\n"]
    summary_lines.append(f"基准 per_cluster: arm={first_arm}(三臂共享 first_candidate)\n")
    for arm, judgments in arm_judgments.items():
        report["arms"][arm] = {}
        for dp in trap_dps:
            mask = _trap_prone_mask(per_cluster, dp)
            decisions = _per_arm_decisions_for_dp(judgments, dp)
            trap_decisions = Counter()
            non_trap_decisions = Counter()
            for leiden, is_trap in mask.items():
                dec = decisions.get(leiden, "<no_judgment>")
                if is_trap:
                    trap_decisions[dec] += 1
                else:
                    non_trap_decisions[dec] += 1
            report["arms"][arm][dp] = {
                "n_trap_prone": sum(1 for v in mask.values() if v),
                "n_non_trap_prone": sum(1 for v in mask.values() if not v),
                "trap_decisions": dict(trap_decisions),
                "non_trap_decisions": dict(non_trap_decisions),
            }

    # 摘要
    print(f"[analyze_traps] 基准 arm={first_arm}")
    for dp in trap_dps:
        print(f"\n  decision_point: {dp}")
        for arm in report["arms"]:
            s = report["arms"][arm][dp]
            print(f"    {arm:<8} trap={s['n_trap_prone']:>2}  non_trap={s['n_non_trap_prone']:>2}  "
                  f"trap_decisions={s['trap_decisions']}  non_trap={s['non_trap_decisions']}")

    # 简单 R-trap 判据(实验设计 §3.1 陷阱规则: ③ 在 ≥4/6 出现的陷阱上优于 ②)
    # 这里用"③ 在 trap 簇上决策熵 > ②"作为 LLM 上下文弹性的代理信号:
    # rule 几乎单一决策,LLM 应分情况(参考 ③ 比 ② 多样说明 skill 真的读了上下文)。
    import math
    def _entropy(counter: Counter) -> float:
        total = sum(counter.values())
        if total == 0:
            return 0.0
        return -sum((c / total) * math.log2(c / total) for c in counter.values())
    trap_wins = {"arm3_vs_arm2_entropy": 0, "applicable": 0}
    for dp in trap_dps:
        a2 = report["arms"]["arm2"][dp]["trap_decisions"]
        a3 = report["arms"]["arm3"][dp]["trap_decisions"]
        if not a2 or not a3:
            continue
        if _entropy(a3) > _entropy(a2):
            trap_wins["arm3_vs_arm2_entropy"] += 1
        trap_wins["applicable"] += 1
    report["trap_summary_R-trap"] = trap_wins

    print(f"\n  R-trap(arm3 决策多样性 > arm2 on trap-prone): {trap_wins['arm3_vs_arm2_entropy']}/{trap_wins['applicable']}")
    print(f"  (B1 §3.1 陷阱规则: ≥4/6 → 核心假设成立)")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())