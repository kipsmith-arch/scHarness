"""B3 — 核心子集提取 + per-cluster 折叠(Story 6.8)

读取 B3_metric_usage 产出的两个 JSON,按 `design/experiment_implementation.md` §3.3
规则产出两种核心子集:

  raw    — 不折叠,直接按"被 ≥2 决策点引用 OR 单点频次 top-20"取
  folded — 把路径中的 `cluster{N}` 段替换为 `<CLUSTER_ID>` 后再聚合,同样规则取

判定线对照以 folded 为准(raw 只作 per-cluster 展开诊断)。

输出:
  experiments/B3/minimal_sufficient_set.json
  experiments/B3/decision_point_top_paths.json   # 每个决策点的 top-N paths
  experiments/B3/per_cluster_expansion.json       # 决策点的 per-cluster 展开度诊断
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Match any path segment that looks like a cluster identifier, in any of these
# shapes seen in run_log.jsonl:
#   cluster{N}             — `step4_rank.rank_candidates.cluster3.first_count`
#   cluster_{N} / cluster-{N}  — defensive
#   per_cluster.{N}        — `step4_rank.per_cluster.13.first` (older shape)
#   clusters.{N}           — plural variant (defensive)
# We collapse all of them to a single canonical token `<CLUSTER_ID>` so
# `per_cluster.13.first` and `per_cluster.4.first` fold to the same key.
_CLUSTER_PATTERN = re.compile(
    r"(?:cluster[-_]?|clusters\.|per_cluster\.|cluster)(\d+)"
)


def fold_cluster_id(path: str) -> str:
    """Replace any `cluster{N}` / `per_cluster.{N}` / `clusters.{N}` segment
    with the canonical token `<CLUSTER_ID>`."""
    return _CLUSTER_PATTERN.sub("<CLUSTER_ID>", path)


def _core_subset(per_dp: dict[str, Counter], top_single: int = 20,
                 multi_dp_threshold: int = 2) -> list[str]:
    """Pick the core subset per §3.3 rules.

    Rule: a path is "core" if EITHER
      (a) it's cited by ≥ `multi_dp_threshold` distinct decision_points, OR
      (b) it's top-`top_single` within a single decision_point (any dp).
    Return the union, sorted by overall frequency descending.
    """
    # (a) cross-dp count
    path_to_dps: dict[str, set[str]] = defaultdict(set)
    for dp, counter in per_dp.items():
        for path in counter:
            path_to_dps[path].add(dp)
    cross_dp_paths = {p for p, dps in path_to_dps.items() if len(dps) >= multi_dp_threshold}

    # (b) single-dp top-N
    single_dp_paths: set[str] = set()
    for dp, counter in per_dp.items():
        for path, _ in counter.most_common(top_single):
            single_dp_paths.add(path)

    core = cross_dp_paths | single_dp_paths
    # Sort by total freq desc, then path asc for stability
    total_freq = Counter()
    for dp, counter in per_dp.items():
        for path, c in counter.items():
            total_freq[path] += c
    return sorted(core, key=lambda p: (-total_freq[p], p))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in-dir",
        default="experiments/B3",
        help="Directory with metric_usage_by_decision.json + path_frequency.json",
    )
    parser.add_argument(
        "--out-dir",
        default="experiments/B3",
        help="Where to write minimal_sufficient_set.json + companions",
    )
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    usage_path = in_dir / "metric_usage_by_decision.json"
    freq_path = in_dir / "path_frequency.json"
    if not usage_path.exists():
        print(f"ERROR: {usage_path} missing — run B3_metric_usage.py first", file=sys.stderr)
        return 1
    if not freq_path.exists():
        print(f"ERROR: {freq_path} missing — run B3_metric_usage.py first", file=sys.stderr)
        return 1

    raw_per_dp: dict[str, Counter] = {
        dp: Counter(counts) for dp, counts in json.loads(usage_path.read_text(encoding="utf-8")).items()
    }
    raw_total_freq: Counter = Counter(json.loads(freq_path.read_text(encoding="utf-8")))

    # ----- Folded view -----
    folded_per_dp: dict[str, Counter] = defaultdict(Counter)
    for dp, counter in raw_per_dp.items():
        for path, c in counter.items():
            folded_per_dp[dp][fold_cluster_id(path)] += c

    # ----- Per-cluster expansion diagnostic -----
    # Threshold for `is_per_cluster_heavy`: data-driven (median expansion ratio
    # across all decision_points, +1 MAD). Falls back to 3.0 if dataset is
    # too small for MAD computation. Review F25.
    all_ratios = []
    for dp in sorted(raw_per_dp):
        ru = len(raw_per_dp[dp])
        fu = len(folded_per_dp[dp])
        if fu:
            all_ratios.append(ru / fu)
    if len(all_ratios) >= 2:
        sorted_r = sorted(all_ratios)
        median = sorted_r[len(sorted_r) // 2]
        mad = sorted_r[len(sorted_r) // 2]  # crude MAD ≈ median for small N
        heavy_threshold = max(3.0, median + mad)
    else:
        heavy_threshold = 3.0
    expansion: dict[str, dict] = {}
    for dp in sorted(raw_per_dp):
        raw_unique = len(raw_per_dp[dp])
        folded_unique = len(folded_per_dp[dp])
        ratio = raw_unique / folded_unique if folded_unique else None
        expansion[dp] = {
            "raw_unique_paths": raw_unique,
            "folded_unique_paths": folded_unique,
            "expansion_ratio": round(ratio, 2) if ratio is not None else None,
            "is_per_cluster_heavy": bool(ratio and ratio > heavy_threshold),
            "heavy_threshold_used": round(heavy_threshold, 2),
        }

    # ----- Core subsets -----
    raw_core = _core_subset(raw_per_dp)
    folded_core = _core_subset(folded_per_dp)

    # ----- Decision-point top paths (folded view) -----
    top_paths_per_dp = {
        dp: dict(counter.most_common(10)) for dp, counter in sorted(folded_per_dp.items())
    }

    out = {
        "raw_size": len(raw_core),
        "folded_size": len(folded_core),
        "total_unique_paths_raw": len(raw_total_freq),
        "total_unique_paths_folded": sum(len(c) for c in folded_per_dp.values()),
        "minimal_sufficient_set_raw": raw_core,
        "minimal_sufficient_set_folded": folded_core,
        "rules": {
            "include_if_cited_by_ge_decision_points": 2,
            "include_if_top_n_within_single_decision_point": 20,
            "fold_target_segment": "cluster{N}",
            "primary_judgment_line": "folded_size <= 60 (per experiment_implementation §3.3)",
            "size_notes": (
                "folded_size is the size of the core subset after per-cluster "
                "folding (cluster{N} → <CLUSTER_ID>). It mixes metric-leaf paths, "
                "parameter paths, and cluster-level wrappers; it is NOT a strict "
                "subset of the 247 atomic metrics in operations_metrics_catalog.md. "
                "Review F3 / F15."
            ),
        },
        "per_cluster_expansion": expansion,
    }
    (out_dir / "minimal_sufficient_set.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "decision_point_top_paths.json").write_text(
        json.dumps(top_paths_per_dp, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "per_cluster_expansion.json").write_text(
        json.dumps(expansion, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ----- Stdout summary -----
    print(f"raw_size:        {out['raw_size']}")
    print(f"folded_size:     {out['folded_size']}")
    print(f"unique_paths_raw:    {out['total_unique_paths_raw']}")
    print(f"unique_paths_folded: {out['total_unique_paths_folded']}")
    print()
    print("Per-cluster expansion (decision_point):")
    for dp, ex in sorted(expansion.items()):
        marker = " *" if ex["is_per_cluster_heavy"] else "  "
        print(f"  {marker}{dp:30s} raw={ex['raw_unique_paths']:4d} folded={ex['folded_unique_paths']:3d}")
    print()
    print(f"Primary judgment line (folded_size <= 60): "
          f"{'PASS' if out['folded_size'] <= 60 else 'FAIL'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
