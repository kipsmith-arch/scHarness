"""S1 battery — 汇总 8 个用例的 ②③ vs deterministic ground truth。

为什么需要这个 (experiment_implementation.md §3.2 S1):
    合成场景探测 LLM/规则在 refine 决策点的能力,补 B1 真实数据统计力。
    汇总成 battery_report.json, 对照 S1-1 / S1-2 / S1-3 判定线给结论。

输入:
    experiments/S1/scenarios.json         — 用例 + oracle_decision + clusters
    experiments/S1/case_{id}/metrics.json — 每用例的决策点指标快照(确定性 op 产出)
    experiments/S1/case_{id}/llm_judgment.json  — ③ LLM 单轮判断 (若跑)
    experiments/S1/ground_truth.json       — 强制 step5 subcluster 后分离是否成功

输出:
    experiments/S1/battery_report.json
        {
          "cases": [{id, oracle, ground_truth, two_judge, three_judge, two_correct, three_correct}],
          "summary": {two_hit, three_hit, three_pass, three_minus_two, ...},
          "verdicts": {S1-1, S1-2, S1-3},
        }

判定线(预注册):
    S1-1: ③ 在 ≥ 6/8 用例上与 ground truth 一致(目前 5 用例可构造, 阈值按比例调)
    S1-2: ③ 在**全部反例** (S-N1..N3) 不误触发细分
    S1-3: ③ 正确数 − ② 正确数 ≥ 2
    S1-4: ③ 正例失败率 > 50% → 先修 skill

注记:
    当前 story 6.7 不执行 LLM 调用(无凭据 + 实验窗口限制)。
    脚本仍可在 `--llm-skipped` 模式下跑出 ② + oracle vs ground truth 的对比,
    ③ 列标记为 `null`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_SCENARIOS = os.path.join(REPO_ROOT, "experiments", "S1", "scenarios.json")
DEFAULT_OUT = os.path.join(REPO_ROOT, "experiments", "S1", "battery_report.json")

# Decision enum allowed
DECISION_ENUM_SET = {"first_decisive", "ambiguous_true",
                       "ambiguous_parent_child", "ambiguous_unknown"}

# Pre-registered decision lines (from §3.2 S1 + §4 总表)
S1_1_HIT_THRESHOLD = 6   # out of 8 (currently 5 due to insufficient data; reported on available)
S1_3_DIFF_THRESHOLD = 2


def load_scenarios(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"[S1_battery] ERROR: missing {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc["cases"]


def try_load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(prog="S1_battery.py",
                                 description="S1 battery — 汇总 ②③ vs ground truth + 判定线对照")
    ap.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    ap.add_argument("--ground-truth", default=os.path.join(REPO_ROOT, "experiments", "S1", "ground_truth.json"),
                    help="若存在则用,否则 ground_truth_decision 字段为 null")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--llm-skipped", action="store_true",
                    help="③ ③ 三字段为 null(默认,因当前 story 6.7 不跑真 LLM)")
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenarios)
    gt_doc = try_load(args.ground_truth)
    gt_map = {}
    if gt_doc and isinstance(gt_doc, dict):
        for k, v in gt_doc.items():
            if isinstance(v, dict) and "ground_truth_decision" in v:
                gt_map[k] = v["ground_truth_decision"]

    case_reports = []
    two_correct_count = 0
    three_correct_count = 0
    two_pass_total = 0
    three_pass_total = 0
    two_neg_correct = 0
    three_neg_correct = 0
    two_neg_total = 0
    three_neg_total = 0

    for case in scenarios:
        cid = case["id"]
        is_neg = case["type"].startswith("negative_")
        if not case["available"]:
            case_reports.append({"id": cid, "available": False, "reason": "insufficient pure-cluster pair"})
            continue

        # ② = rule_judge (we don't run rule_judge here — too script-heavy without
        #    a real metrics snapshot. Mark as 'not_run' for now.)
        # ③ = LLM judgment
        llm_j = try_load(os.path.join(REPO_ROOT, "experiments", "S1", f"case_{cid}", "llm_judgment.json"))
        gt_dec = gt_map.get(cid)

        oracle = case["oracle_decision"].split(" ")[0]  # first word, e.g. "ambiguous_true"
        # Map to enum
        if "first_decisive" in case["oracle_decision"]:
            expected = "first_decisive"
        elif "parent_child" in case["oracle_decision"]:
            expected = "ambiguous_parent_child"
        elif "ambiguous" in case["oracle_decision"]:
            expected = "ambiguous_true"
        else:
            expected = "ambiguous_true"

        three_decision = None if args.llm_skipped else (llm_j or {}).get("decision")
        three_correct = (three_decision == expected) if three_decision else None

        if three_correct is not None:
            three_pass_total += 1
            if three_correct:
                three_correct_count += 1
            if is_neg:
                three_neg_total += 1
                if not three_correct:
                    # ③ 在反例误触发(选了 ambiguous 但不应 refine)
                    pass
                else:
                    three_neg_correct += 1

        # ② simulated as oracle-equivalent for now (rule_judge calibration deferred)
        # Mark as N/A unless explicit rule_judge output exists.
        two_correct = None

        case_reports.append({
            "id": cid,
            "type": case["type"],
            "available": True,
            "clusters": case["clusters"],
            "trap": case["trap"],
            "expected_decision": expected,
            "two_decision": None,
            "two_correct": two_correct,
            "three_decision": three_decision,
            "three_reasoning": (llm_j or {}).get("reasoning"),
            "ground_truth_decision": gt_dec,
            "three_correct": three_correct,
        })

    # Verdict computation
    n_avail = sum(1 for c in case_reports if c.get("available"))
    s1_1_pass = None  # cannot compute without ③
    s1_2_pass = None
    s1_3_pass = None

    if three_pass_total > 0 and not args.llm_skipped:
        hit_rate = three_correct_count / three_pass_total
        # Scale S1-1 to available cases (proportional to 6/8)
        scaled_threshold = max(3, round(S1_1_HIT_THRESHOLD * n_avail / 8))
        s1_1_pass = three_correct_count >= scaled_threshold
        # S1-2: ③ must not refine on all negatives
        if three_neg_total > 0:
            s1_2_pass = three_neg_correct == three_neg_total
        # S1-3: ③ − ② ≥ 2 — ② is None here; report N/A
        s1_3_pass = None

    payload = {
        "scenarios_source": args.scenarios,
        "ground_truth_source": args.ground_truth if gt_doc else None,
        "llm_skipped": args.llm_skipped,
        "n_cases_total": len(scenarios),
        "n_cases_available": n_avail,
        "summary": {
            "three_pass_total": three_pass_total,
            "three_correct_count": three_correct_count,
            "three_neg_total": three_neg_total,
            "three_neg_correct": three_neg_correct,
        },
        "verdicts": {
            "S1-1_three_hit_ge_6_of_8_scaled_to_available": s1_1_pass,
            "S1-2_three_never_refine_on_negatives": s1_2_pass,
            "S1-3_three_minus_two_ge_2": s1_3_pass,
        },
        "cases": case_reports,
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "execution_window": "story 6.7 — deterministic parts only; LLM calls deferred",
            "boundaries_section": (
                "S1 tests refine-decision *capability* under controlled merge artifacts. "
                "Merged clusters produce artificial clean double-lobes (morans_i inflated), "
                "so S1 does NOT measure real weak-signal detection rate. "
                "Reports MUST annotate this caveat."
            ),
        },
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[S1_battery] {n_avail}/{len(scenarios)} cases available")
    print(f"  ③ LLM skipped: {args.llm_skipped}")
    if not args.llm_skipped and three_pass_total:
        print(f"  ③ correct: {three_correct_count}/{three_pass_total} "
              f"(negatives: {three_neg_correct}/{three_neg_total} avoided spurious refine)")
        print(f"  S1-1 (≥6/8 scaled): {'PASS' if s1_1_pass else 'FAIL'}  threshold: ≥{max(3, round(S1_1_HIT_THRESHOLD * n_avail / 8))}")
        if s1_2_pass is not None:
            print(f"  S1-2 (no spurious refine on negatives): {'PASS' if s1_2_pass else 'FAIL'}")
        print(f"  S1-3 (three minus two >= 2): N/A (rule_judge output not produced in story 6.7)")
    else:
        print(f"  S1-1/S1-2/S1-3 verdicts deferred — re-run with LLM calls completed to compute.")
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())