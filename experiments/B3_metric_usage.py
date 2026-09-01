"""B3 — 指标引用频次统计(Story 6.8)

Sweep all judgment records across the three LLM session run_logs and emit:

  experiments/B3/metric_usage_by_decision.json   # {decision_point: {path: count}}
  experiments/B3/path_frequency.json             # {path: total_count} 全量降序
  experiments/B3/summary.json                    # {total_judgments, total_unique_paths,
                                                  #   per_run_counts, bad_json_count}

Stdlib only. 写死 3 个 LLM run 的路径(避免误扫 arm1/arm2 scripted run).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Hard-coded LLM session paths per spec. Do NOT enumerate output/ at large.
DEFAULT_RUNS = [
    "output/B1/arm3_llm/run_log.jsonl",
    "output/p5_evals_r2/run_log.jsonl",
    "output/N1_on/run_log.jsonl",
]


def _iter_judgments(path: Path):
    """Yield (line_no, record) for each judgment record in a run_log.jsonl.

    Tolerates JSONDecodeError — emits (line_no, {"_bad_json": True}) instead
    of raising, so the caller can decide how to surface it.
    """
    with path.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                yield line_no, {"_bad_json": True, "_error": str(exc)}
                continue
            if rec.get("type") == "judgment":
                yield line_no, rec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        nargs="+",
        default=DEFAULT_RUNS,
        help="run_log.jsonl paths to sweep (default: the 3 LLM sessions).",
    )
    parser.add_argument(
        "--out",
        default="experiments/B3",
        help="Output directory (default: experiments/B3).",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # per_decision_point[dp][path] = count
    per_decision_point: dict[str, Counter] = defaultdict(Counter)
    path_frequency: Counter = Counter()
    per_run_counts: dict[str, int] = {}
    bad_json_total = 0
    total_judgments = 0
    used_runs: list[str] = []

    for run_str in args.runs:
        run_path = Path(run_str)
        if not run_path.exists():
            print(f"SKIP: {run_str} (not found)", file=sys.stderr)
            continue
        used_runs.append(run_str)
        n = 0
        bad = 0
        for _, rec in _iter_judgments(run_path):
            if rec.get("_bad_json"):
                bad += 1
                continue
            n += 1
            dp = rec.get("decision_point")
            if not dp:
                continue
            for inp in rec.get("inputs", []) or []:
                p = inp.get("path")
                if not p:
                    continue
                per_decision_point[dp][p] += 1
                path_frequency[p] += 1
        per_run_counts[run_str] = n
        total_judgments += n
        bad_json_total += bad
        if bad:
            print(f"WARN: {run_str} had {bad} bad JSON line(s), skipped", file=sys.stderr)

    if not used_runs:
        print("ERROR: no usable run_logs (all skipped)", file=sys.stderr)
        return 1

    # Sort & dump
    metric_usage = {
        dp: dict(counter.most_common())
        for dp, counter in sorted(per_decision_point.items())
    }
    path_freq_sorted = dict(path_frequency.most_common())

    (out_dir / "metric_usage_by_decision.json").write_text(
        json.dumps(metric_usage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "path_frequency.json").write_text(
        json.dumps(path_freq_sorted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "runs_used": used_runs,
        "total_judgments": total_judgments,
        "total_unique_paths": len(path_frequency),
        "per_run_counts": per_run_counts,
        "decision_points": sorted(per_decision_point.keys()),
        "bad_json_count": bad_json_total,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Concise stdout summary
    print(f"TOTAL judgments: {total_judgments}")
    print(f"TOTAL unique input paths: {len(path_frequency)}")
    print(f"Distinct decision_points: {len(per_decision_point)}")
    print(f"Runs used: {len(used_runs)} (bad json lines skipped: {bad_json_total})")
    print(f"Outputs:")
    for fname in ("metric_usage_by_decision.json", "path_frequency.json", "summary.json"):
        print(f"  {out_dir / fname}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
