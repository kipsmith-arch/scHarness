"""S1 ③ 单轮 LLM 判断 — 喂入决策点指标 + skill 决策指导,得到 LLM 的 refine_decision。

为什么需要这个 (experiment_implementation.md §3.2 S1):
    S1 不跑整条 LangGraph loop(隔离决策质量 + 省 token),
    只取每个用例的决策点指标(per_cluster.candidate_gap / refine_effect 上下文),
    让 LLM 在 temperature=0 下做一次单轮判断:
        decision ∈ {"first_decisive", "ambiguous_true", "ambiguous_parent_child", "ambiguous_unknown"}
    + reasoning 一句话(强制, 让后续人工抽查可解释性)。

CLI:
    python experiments/run_mini_session.py \
        --skill skills/cell-annotation \
        --case experiments/S1/case_P1/llm_judgment.json  # 写 output

LLM 调用方式:
    - 通过 harness.session.run_session 的 low-level 入口: 给定 skill + 一个 user task,
      该 task 把 case 决策点指标作为 system prompt 上下文, 让 LLM 调用 write_judgment.add
    - 复用 harness 的 loop 不增加重复代码

输入(每个 case):
    experiments/S1/case_{id}/metrics.json — case 决策点的指标快照
        (聚合 step4_rank.annotations[case_cluster] + step5_refine.refined 字段)

输出:
    experiments/S1/case_{id}/llm_judgment.json
        {
          "decision": "first_decisive" | "ambiguous_true" | ...,
          "reasoning": "...",
          "ts": "...",
          "session_id": "..."
        }

实现说明:
    - 这个脚本**需要真实 LLM 配置**(OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL)。
    - 当前 story 6.7 的执行环境无真凭据;脚本骨架已搭好,待运行时由你触发。
    - 如果跳过 LLM 调用,battery_report.json 中 ③ 字段会标记为 `skipped: no_llm_credentials`,
       但 ② (rule_judge) 可以基于纯指标算, 不受影响。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def build_user_task(case_id: str, metrics: dict) -> str:
    """Construct the user task prompt for the LLM single-turn session."""
    return (
        f"S1 合成场景用例 {case_id} — 决策点:refine_effect。\n"
        f"指标快照:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        f"请基于 SKILL.md §3.7 refine_effect 决策指导,\n"
        f"判断本用例是否需要 step5 refine。\n"
        f"调用 write_judgment add --decision-point refine_effect --scope cluster_id {case_id} "
        f"--decision <first_decisive|ambiguous_true|ambiguous_parent_child|ambiguous_unknown> "
        f"--reasoning '...'\n"
        f"决策枚举限定为这 4 个之一;reasoning 一句话。"
    )


def main() -> int:
    ap = argparse.ArgumentParser(prog="run_mini_session.py",
                                 description="S1 — ③ 单轮 LLM 判断(temperature=0)")
    ap.add_argument("--skill", default=os.path.join(REPO_ROOT, "skills", "cell-annotation"))
    ap.add_argument("--case-id", required=True, help="用例 id, 如 S-P1")
    ap.add_argument("--metrics", required=True, help="metrics.json 路径")
    ap.add_argument("--out", default=None, help="输出 llm_judgment.json 路径(默认 experiments/S1/case_{id}/)")
    ap.add_argument("--model", default=None, help="OPENAI_MODEL 覆盖")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将调用的命令 + metrics, 不真发 LLM 请求")
    args = ap.parse_args()

    if not os.path.exists(args.metrics):
        print(f"[run_mini_session] ERROR: missing metrics {args.metrics}", file=sys.stderr)
        return 1

    with open(args.metrics, encoding="utf-8") as f:
        metrics = json.load(f)

    out_dir = args.out or os.path.join(REPO_ROOT, "experiments", "S1", f"case_{args.case_id}")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "llm_judgment.json")

    task = build_user_task(args.case_id, metrics)

    if args.dry_run:
        print(f"[run_mini_session] DRY RUN — would invoke ChatOpenAI single-turn:")
        print(f"  --model {args.model or os.environ.get('OPENAI_MODEL') or 'gpt-4o-mini'}")
        print(f"  --task <{len(task)} chars>")
        print(f"  metrics keys: {list(metrics.keys()) if isinstance(metrics, dict) else type(metrics).__name__}")
        print(f"  output: {out_path}")
        return 0

    # Real invocation — single-turn LLM call (no LangGraph loop, no notebook).
    # Why direct ChatOpenAI: S1 tests a single refine-effect decision point;
    # spinning up the full harness.session loop adds 20+ rounds of tool calls
    # which drown the signal in noise and waste ~80k tokens per case.
    # Ensure harness package importable when called from project root
    _repo_root = REPO_ROOT
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from harness.config import load_dotenv
    load_dotenv()
    model_name = args.model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
    except ImportError as exc:
        print(f"[run_mini_session] FAIL missing dependency: {exc}", file=sys.stderr)
        return 1

    system_prompt = (
        "你是单细胞 RNA-seq 细胞类型注释助手。当前是合成场景 S1 battery 的"
        f"用例 {args.case_id},决策点 refine_effect(是否需要进一步细分簇)。\n"
        "基于指标快照,你需要做出决策:\n"
        "  decision ∈ {first_decisive, ambiguous_true, ambiguous_parent_child, ambiguous_unknown}\n"
        "  reasoning: 一句话中文解释。\n\n"
        "定义(对照 SKILL.md §3.7 refine_effect):\n"
        "- first_decisive: 第一候选明确胜出(count_diff ≥ 3 且 confidence_diff > 0.05),不 refine\n"
        "- ambiguous_true: 前两名并列/接近,应进 step5 subcluster\n"
        "- ambiguous_parent_child: 第二候选是第一候选的本体子/父,选更具体的,不 refine\n"
        "- ambiguous_unknown: 候选极少或全无,无法判断,不 refine(标 unknown)\n\n"
        "严格遵守:只输出 JSON: {\"decision\": \"<one of 4>\", \"reasoning\": \"<one sentence>\"}"
    )
    user_msg = (
        f"用例 {args.case_id} 指标:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        f"如 oracle_decision 中描述的场景提示: oracle={open(os.path.join(os.path.dirname(args.metrics), '..', 'scenarios.json'), encoding='utf-8').read() if False else 'see type'}\n"
        f"请输出 JSON:"
    )
    # 不要给 oracle — 让 LLM 独立判断
    user_msg = (
        f"用例 {args.case_id} 决策点指标(已合并两簇):\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        "请输出 JSON 决策:"
    )

    llm = ChatOpenAI(model=model_name, temperature=0, max_retries=2)
    try:
        resp = llm.invoke([SystemMessage(content=system_prompt),
                           HumanMessage(content=user_msg)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as exc:
        print(f"[run_mini_session] LLM FAIL: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1

    # Parse JSON from the LLM response — it may be wrapped in <think>...</think> or plain
    decision = None
    reasoning = None
    raw = text
    # Strip think block
    if "<think>" in raw and "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    # Try to locate JSON object
    import re as _re
    m = _re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            parsed = json.loads(m.group(0))
            decision = parsed.get("decision")
            reasoning = parsed.get("reasoning")
        except json.JSONDecodeError:
            pass
    if not decision:
        # Fallback: regex for the enum string
        for k in ["first_decisive", "ambiguous_true", "ambiguous_parent_child", "ambiguous_unknown"]:
            if k in raw:
                decision = k
                break
        reasoning = raw[:200].strip()

    valid_enum = {"first_decisive", "ambiguous_true", "ambiguous_parent_child", "ambiguous_unknown"}
    if decision not in valid_enum:
        print(f"[run_mini_session] WARN decision '{decision}' not in enum; raw={raw[:200]}", file=sys.stderr)
        payload_status = "unknown_no_valid_decision"
    else:
        payload_status = "ok"

    payload = {
        "case_id": args.case_id,
        "decision": decision,
        "reasoning": reasoning,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "status": payload_status,
        "raw_response": text,
        "model": model_name,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[run_mini_session] {args.case_id}: decision={decision} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())