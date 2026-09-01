"""B4 — Self-correction 对提取(Story 6.8)

Sweep each run's `judgment` records, group by (decision_point, scope_json),
and surface pairs where the same (dp, scope) has ≥2 versions.

输出:
  experiments/B4/self_correction_pairs.json   # 每个 multi-version (dp, scope) 一条
  experiments/B4/multi_version_summary.json   # 每 run 一行汇总
  experiments/B4/summary.json                 # 总览

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_RUNS = [
    "output/B1/arm3_llm/run_log.jsonl",
    "output/p5_evals_r2/run_log.jsonl",
    "output/N1_on/run_log.jsonl",
]


def _safe_get_decision(rec: dict) -> str:
    """Return rec['output']['decision'] or "" if shape is unexpected.

    Tolerates:
      - output missing
      - output is null
      - output is a list (not dict) — review ECH-11
      - decision field absent
    """
    out = rec.get("output")
    if isinstance(out, dict):
        d = out.get("decision", "")
        return d if isinstance(d, str) else str(d)
    return ""


def _iter_judgments(path: Path):
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
    parser.add_argument("--runs", nargs="+", default=DEFAULT_RUNS)
    parser.add_argument("--out-dir", default="experiments/B4")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_pairs: list[dict] = []
    per_run_summary: dict[str, dict] = {}
    bad_json_total = 0

    for run_str in args.runs:
        run_path = Path(run_str)
        if not run_path.exists():
            print(f"SKIP: {run_str} (not found)", file=sys.stderr)
            per_run_summary[run_str] = {"status": "missing"}
            continue

        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        n_judgments = 0
        for _, rec in _iter_judgments(run_path):
            if rec.get("_bad_json"):
                bad_json_total += 1
                continue
            n_judgments += 1
            dp = rec.get("decision_point", "")
            scope = rec.get("scope", {}) or {}
            try:
                key = (dp, json.dumps(scope, sort_keys=True, ensure_ascii=False))
            except TypeError:
                # scope contains non-JSON-serializable values (e.g., datetime);
                # fall back to repr-based key so we don't lose the record entirely
                bad_json_total += 1
                key = (dp, repr(scope))
            groups[key].append(rec)

        multi_count = 0
        decision_changed_count = 0
        for (dp, scope_str), versions in groups.items():
            if len(versions) < 2:
                continue
            multi_count += 1
            # sort by seq ascending (fallback to ts)
            versions_sorted = sorted(
                versions, key=lambda r: (r.get("seq", 0), r.get("ts", ""))
            )
            v_first = versions_sorted[0]
            v_last = versions_sorted[-1]
            d_first = _safe_get_decision(v_first)
            d_last = _safe_get_decision(v_last)
            decision_changed = d_first != d_last
            if decision_changed:
                decision_changed_count += 1

            all_versions = [
                {
                    "run_ref": v.get("run_ref"),
                    "seq": v.get("seq"),
                    "decision": _safe_get_decision(v),
                    "ts": v.get("ts"),
                }
                for v in versions_sorted
            ]
            all_pairs.append(
                {
                    "run": run_str,
                    "decision_point": dp,
                    "scope": json.loads(scope_str) if scope_str.startswith("{") or scope_str.startswith("[") else {},
                    "n_versions": len(versions_sorted),
                    "v_first": {
                        "seq": v_first.get("seq"),
                        "decision": d_first,
                        "run_ref": v_first.get("run_ref"),
                        "ts": v_first.get("ts"),
                    },
                    "v_last": {
                        "seq": v_last.get("seq"),
                        "decision": d_last,
                        "run_ref": v_last.get("run_ref"),
                        "ts": v_last.get("ts"),
                    },
                    "decision_changed": decision_changed,
                    "all_versions": all_versions,
                }
            )

        per_run_summary[run_str] = {
            "status": "ok",
            "total_judgments": n_judgments,
            "unique_keys": len(groups),
            "multi_version_pairs": multi_count,
            "decision_changed_pairs": decision_changed_count,
        }

    summary = {
        "runs_used": [r for r in per_run_summary if per_run_summary[r].get("status") == "ok"],
        "bad_json_count": bad_json_total,
        "total_multi_version_pairs": sum(
            v.get("multi_version_pairs", 0) for v in per_run_summary.values()
        ),
        "total_decision_changed_pairs": sum(
            v.get("decision_changed_pairs", 0) for v in per_run_summary.values()
        ),
        "per_run": per_run_summary,
    }

    if not summary["runs_used"]:
        print("ERROR: no usable run_logs (all skipped) — B4 has no data", file=sys.stderr)
        # Still write the JSONs so downstream consumers see empty stats
        (out_dir / "self_correction_pairs.json").write_text("[]", encoding="utf-8")
        (out_dir / "multi_version_summary.json").write_text(
            json.dumps(per_run_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 1

    (out_dir / "self_correction_pairs.json").write_text(
        json.dumps(all_pairs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "multi_version_summary.json").write_text(
        json.dumps(per_run_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Total self-correction pairs (multi-version): {summary['total_multi_version_pairs']}")
    print(f"Total decision-changed pairs:                {summary['total_decision_changed_pairs']}")
    print()
    print("Per run:")
    for run_str, info in per_run_summary.items():
        if info.get("status") != "ok":
            print(f"  {run_str}: {info.get('status')}")
            continue
        print(
            f"  {run_str}: judgments={info['total_judgments']:3d}  "
            f"unique_keys={info['unique_keys']:3d}  "
            f"multi_version={info['multi_version_pairs']:3d}  "
            f"decision_changed={info['decision_changed_pairs']:3d}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
