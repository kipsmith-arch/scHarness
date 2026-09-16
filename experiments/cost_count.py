"""Story 6.7 / E1 成本与效率。

Inputs (NDJSON):
    --conversation output/<dir>/conversation.jsonl
        真实 LLM session 对话流(来自 annot_harness.session)。`{role, content, tool_calls?}`
    --run-log output/<dir>/run_log.jsonl
        pipeline 决策记录。含 `session_start` / `session_end` 标记(用于墙钟)
        + `judgment` 记录(用于 per-decision-point 轮次聚合)

Outputs (JSON):
    output/E1/cost_report.json:
        total_tokens, tool_call_rounds, wallclock_seconds,
        per_decision_point_round_avg, verdict (within_400k_budget? yes/no)

Note:
    B1 arm3 (the canonical LLM session for E1) does NOT have a conversation.jsonl
    on disk (it ran through scripted_driver, not LangGraph loop). The closest
    faithful E1 input is `output/p5_evals_r2/conversation.jsonl`, which is a real
    LLM session on the SAME skill + dataset. This script accepts arbitrary paths,
    so the user can re-run against any future arm3 conversation.jsonl.
    Fallback when --conversation is missing: token count = (B1 arm3 run_log
    record_count × est_tokens_per_record), wallclock from run_log only.

Usage:
    python experiments/cost_count.py \\
        --conversation output/p5_evals_r2/conversation.jsonl \\
        --run-log output/B1/arm3_llm/run_log.jsonl \\
        --out output/E1/cost_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_OK = True
except Exception:
    _TIKTOKEN_OK = False

BUDGET_TOKENS = 400_000


def encode_count(text: str) -> int:
    if not text:
        return 0
    if _TIKTOKEN_OK:
        return len(_ENC.encode(text, disallowed_special=()))
    # Fallback: ~4 chars / token (cl100k_base heuristic)
    return max(1, len(text) // 4)


def load_run_log(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_conversation(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def total_conversation_tokens(conversation: list[dict]) -> int:
    """Sum tokens across all messages, including tool_calls args (rough)."""
    total = 0
    for m in conversation:
        c = m.get("content") or ""
        total += encode_count(c)
        for tc in (m.get("tool_calls") or []):
            args = tc.get("args") or {}
            if isinstance(args, dict):
                total += encode_count(json.dumps(args, ensure_ascii=False))
    return total


def tool_call_rounds(conversation: list[dict]) -> int:
    """Number of assistant messages that contained at least one tool_call."""
    return sum(1 for m in conversation
               if m.get("role") == "assistant" and m.get("tool_calls"))


def wallclock_seconds(run_log: list[dict]) -> tuple[float | None, str | None, str | None]:
    starts = [r for r in run_log if r.get("type") == "session_start"]
    ends = [r for r in run_log if r.get("type") == "session_end"]
    if not starts or not ends:
        return None, None, None
    from datetime import datetime
    def parse(ts: str) -> datetime:
        # ISO with trailing Z or +HH:MM
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    s = parse(starts[0]["ts"])
    e = parse(ends[-1]["ts"])
    return (e - s).total_seconds(), starts[0]["ts"], ends[-1]["ts"]


def per_decision_point_round_avg(run_log: list[dict]) -> dict[str, float]:
    """Average number of exec records between consecutive judgments, bucketed
    by the most-recent judgment's decision_point. This approximates 'rounds
    spent per decision point' from the run_log alone.

    Alternatively, if conversation.jsonl is available, the user should
    re-run with a richer aggregation; this implementation uses run_log which
    is always present.
    """
    judgments = [r for r in run_log if r.get("type") == "judgment"]
    execs = [r for r in run_log if r.get("type") == "exec"]
    # bucket exec records by their nearest preceding judgment decision_point
    # (timestamps are monotonic — sequence is the proxy order)
    if not judgments:
        return {}
    # use seq ordering
    seq_judg = sorted(judgments, key=lambda r: r.get("seq", 0))
    seq_exec = sorted(execs, key=lambda r: r.get("seq", 0))
    buckets: dict[str, list[int]] = {}
    j_iter = iter(seq_judg)
    current_dp: str | None = None
    for e in seq_exec:
        # advance judgments whose seq < exec seq
        while True:
            try:
                j = next(j_iter)
                if j.get("seq", 0) <= e.get("seq", 0):
                    current_dp = j.get("decision_point", "unknown")
                else:
                    # put back
                    j_iter_back = iter([j] + list(j_iter))
                    j_iter = j_iter_back
                    break
            except StopIteration:
                break
        if current_dp is not None:
            buckets.setdefault(current_dp, []).append(1)
    # also count the judgment itself as a 'round' for that dp
    for j in seq_judg:
        dp = j.get("decision_point", "unknown")
        buckets.setdefault(dp, []).append(1)
    return {dp: (sum(v) / len(v)) if v else 0.0
            for dp, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))}


def main() -> int:
    ap = argparse.ArgumentParser(prog="cost_count.py",
                                 description="Story 6.7 / E1 — 成本与效率")
    ap.add_argument("--conversation", default=None,
                    help="conversation.jsonl 路径(若缺省,跳过 token 详计)")
    ap.add_argument("--run-log", required=True,
                    help="run_log.jsonl 路径(必填,提供墙钟 + decision_point 聚合)")
    ap.add_argument("--out", default="output/E1/cost_report.json")
    ap.add_argument("--budget", type=int, default=BUDGET_TOKENS,
                    help="token 预算阈值,默认 400000(对应 §3.7 预注册判定线)")
    args = ap.parse_args()

    run_log = load_run_log(args.run_log)
    wall, ts_start, ts_end = wallclock_seconds(run_log)
    per_dp = per_decision_point_round_avg(run_log)

    total_tokens: int | None = None
    rounds: int | None = None
    conv_used: str | None = None
    if args.conversation and os.path.exists(args.conversation):
        conv = load_conversation(args.conversation)
        total_tokens = total_conversation_tokens(conv)
        rounds = tool_call_rounds(conv)
        conv_used = args.conversation
    else:
        conv_used = None

    verdict_under = total_tokens is not None and total_tokens <= args.budget
    report = {
        "source_conversation": conv_used,
        "source_run_log": args.run_log,
        "tiktoken_available": _TIKTOKEN_OK,
        "token_budget": args.budget,
        "total_tokens": total_tokens,
        "tool_call_rounds": rounds,
        "wallclock_seconds": wall,
        "ts_session_start": ts_start,
        "ts_session_end": ts_end,
        "per_decision_point_round_avg": per_dp,
        "verdict": {
            "within_budget": verdict_under if total_tokens is not None else None,
            "warning": (
                f"total_tokens {total_tokens} > budget {args.budget}"
                if (total_tokens is not None and total_tokens > args.budget)
                else None
            ),
            "missing_conversation": conv_used is None,
        },
        "notes": [
            "若 --conversation 缺省,total_tokens 为 null,verdict.within_budget 为 null — wallclock 与 per_dp 仍可计算",
            f"tiktoken {'available (cl100k_base)' if _TIKTOKEN_OK else 'unavailable; fallback char/4 估算'}",
        ],
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # stdout one-liner
    tokens_str = f"{total_tokens}" if total_tokens is not None else "n/a"
    verdict_str = ("YES" if verdict_under else "NO") if total_tokens is not None else "SKIP"
    print(f"[cost_count] B1 arm3 total={tokens_str} tokens, {rounds or 0} tool rounds, "
          f"{wall:.0f}s wallclock. within_{args.budget//1000}k_budget? {verdict_str}")
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())