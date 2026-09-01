"""B4 — Self-correction 改善率计算(Story 6.8)

读取 `self_correction_pairs.json` + 对应 run_log.jsonl,按
`design/experiment_implementation.md` §3.4 表格逐决策点套用"改善判据":

  clustering_quality : silhouette_overall.mean 上升 ∧ n_singleton 下降,
                      或末版 accept 而首版 adjust
  marker_quality     : n_markers 进入 [10, 50] 区间
  refine_effect      : 子簇 silhouette 上升,或 n_subclusters_with_distinct_type 增多
  label_confirm      : 末版相对首版更明确 (unknown → confirmed 视为改善)
  其余决策点          : 末版 decision 与首版不同 ∧ 命中 oracle "good" 表
                      (复用 `experiments/judges/rule_judge.py` 的常量定义)

输出:
  experiments/B4/improvement_rate.json
  experiments/B4/improvement_by_decision_point.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Run-str → resolved filesystem path (mirror B3_metric_usage defaults).
DEFAULT_RUNS = {
    "arm3_llm": "output/B1/arm3_llm/run_log.jsonl",
    "p5_r2": "output/p5_evals_r2/run_log.jsonl",
    "n1_on": "output/N1_on/run_log.jsonl",
}

# Decision points with measurable exec metrics (per §3.4 表格):
# Each entry: list of (field path in exec.data, expected direction)
# direction: "up" = improvement, "down" = improvement
_METRIC_BASED_DECISION_POINTS = {
    "clustering_quality": [
        ("silhouette_overall.mean", "up"),
        ("n_singleton", "down"),
    ],
    "marker_quality": [
        # n_markers 进入 [10, 50] 区间; 区间内即改善
        ("n_markers", "in_range"),
    ],
    "refine_effect": [
        ("subcluster_silhouette.mean", "up"),
        ("n_subclusters_with_distinct_type", "up"),
    ],
}

# Decision points where improvement == "末版 decision 相对首版更明确".
# We define "more definitive" transitions.
_CONFIRM_DECISION_BUCKETS = {
    "label_confirm": {
        # v_first → v_last; "末版更明确"= value moves UP this ranking
        # (more confident/clearer label).
        "rank": {
            "label_unknown": 0,  # least definitive
            "label_downgraded": 1,
            "label_confirmed": 2,  # most definitive
        },
        "improved_when_v_last_rank_gt_v_first_rank": True,
    },
    # Per `design/experiment_implementation.md` §3.1 陷阱表 (B1 §3.1):
    # candidate_gap has a 3-tier quality ranking:
    #   worst  : ambiguous_true  (LLM doesn't know which type)
    #   middle : ambiguous_parent_child / ambiguous_synonym  (LLM knows the
    #            relation but can't decide; specific subtype unknown)
    #   best   : first_decisive  (LLM picks a clear winner)
    # "改主意后变好" = 末版比首版 rank 更高。
    "candidate_gap": {
        "rank": {
            "ambiguous_true": 0,
            "ambiguous_synonym": 1,
            "ambiguous_parent_child": 1,
            "first_decisive": 2,
        },
        "improved_when_v_last_rank_gt_v_first_rank": True,
    },
    # candidate_disambiguate: refine 后能选出子类型 = 改善; 否则不变
    "candidate_disambiguate": {
        "rank": {
            "still_ambiguous": 0,
            "disambiguated_partial": 1,
            "disambiguated_full": 2,
        },
        "improved_when_v_last_rank_gt_v_first_rank": True,
    },
    # refine_effect: 子簇是否分离出两个原始真值类型
    "refine_effect_label": {
        "rank": {
            "no_refine_needed": 0,
            "refined_no_distinct": 1,
            "refined_distinct": 2,
        },
        "improved_when_v_last_rank_gt_v_first_rank": True,
    },
    # unknown_cluster: 簇从 unknown 变为可分类 = 改善
    "unknown_cluster_label": {
        "rank": {
            "label_unknown": 0,
            "label_downgraded": 1,
            "label_confirmed": 2,
        },
        "improved_when_v_last_rank_gt_v_first_rank": True,
    },
    # qc_threshold, resolution_select, batch_effect, global_quality, marker_quality:
    # These are handled by the metric-based path (exec data lookup) when available.
    # If no exec data is available (no_evidence), we fall back to oracle_heuristic.
}


def _get_nested(d: dict, dotted: str):
    """Return d[dotted.path] or None. Supports dotted keys only (no list idx)."""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _load_exec_index(run_log_path: Path) -> dict[str, dict]:
    """Index exec records by run_ref → exec record (with .data)."""
    idx: dict[str, dict] = {}
    with run_log_path.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "exec":
                run_ref = rec.get("run_id") or rec.get("run_ref")
                if run_ref:
                    idx[run_ref] = rec
    return idx


def _metric_improved(field: str, direction: str, v_first_exec: dict, v_last_exec: dict) -> bool:
    """Compare a single metric between v_first and v_last exec records."""
    a = _get_nested(v_first_exec.get("data", {}) or {}, field)
    b = _get_nested(v_last_exec.get("data", {}) or {}, field)
    if a is None or b is None:
        return False
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if direction == "up":
        return bf > af
    if direction == "down":
        return bf < af
    if direction == "in_range":
        # [10, 50]
        return 10.0 <= bf <= 50.0
    return False


def _oracle_improved(decision_point: str, v_first_decision: str, v_last_decision: str) -> bool:
    """Fallback for decision points without measurable exec metrics.

    A pair counts as improved when v_last_decision is "more aligned with oracle"
    than v_first_decision. We use the simple, conservative rule:

      - if v_first_decision == v_last_decision: not improved
      - if v_last_decision is in {accept, ok, proceed, ...} and v_first_decision
        was in {adjust, retry, ambiguous_*, ...} → improved
      - otherwise: not improved (oracle-specific mapping would need hand-coding
        per decision_point; we lean conservative to avoid false positives)

    The detailed per-decision-point oracle tables live in
    `experiments/judges/rule_judge.py` and are project_dir-coupled (write side
    effects). For B4's trajectory-level scoring we use this read-only heuristic
    and document it in the report.
    """
    if v_first_decision == v_last_decision:
        return False
    good = {
        "accept", "accepted", "ok", "proceed", "confirmed",
        "threshold_set", "markers_accept", "id_match_ok", "no_refine_needed",
        "first_decisive", "label_confirmed", "wilcoxon",  # de_method OK default
        "continue", "global_quality_ok", "marker_quality_ok",
    }
    bad = {
        "adjust", "retry", "recluster", "downgrade",
        "ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true",
        "label_unknown", "label_downgraded", "no_candidates",
        "unknown", "global_quality_bad", "marker_quality_bad",
    }
    return v_last_decision in good and v_first_decision in bad


def _confirm_improved(decision_point: str, v_first_decision: str, v_last_decision: str) -> bool:
    rule = _CONFIRM_DECISION_BUCKETS.get(decision_point)
    if not rule:
        return False
    rank = rule["rank"]
    r_first = rank.get(v_first_decision)
    r_last = rank.get(v_last_decision)
    if r_first is None or r_last is None:
        return False
    return r_last > r_first if rule["improved_when_v_last_rank_gt_v_first_rank"] else False


def _classify_pair(pair: dict, exec_idx: dict) -> str:
    """Return one of: 'improved', 'unchanged', 'no_change_in_decision',
    'no_evidence', 'unscored'.

    'unscored' for pairs we deliberately don't judge (e.g., clustering_quality
    with no exec data on either side).

    'no_change_in_decision' is for multi-version pairs where v_first ==
    v_last — those are NOT real corrections (the LLM re-ran the same
    decision for the same scope), so they should NOT enter the denominator.
    """
    dp = pair["decision_point"]
    v_first_decision = pair["v_first"]["decision"]
    v_last_decision = pair["v_last"]["decision"]

    # Decision unchanged → re-judgment of the same outcome, not a correction.
    if v_first_decision == v_last_decision and not pair.get("decision_changed"):
        return "no_change_in_decision"

    # 0) Ranking-based (label_confirm, candidate_gap, candidate_disambiguate,
    # refine_effect_label, unknown_cluster_label): explicit tier maps.
    if dp in _CONFIRM_DECISION_BUCKETS:
        return "improved" if _confirm_improved(dp, v_first_decision, v_last_decision) else "unchanged"

    # 1) Metric-based (clustering_quality, marker_quality, refine_effect)
    if dp in _METRIC_BASED_DECISION_POINTS:
        v_first_ref = pair["v_first"].get("run_ref")
        v_last_ref = pair["v_last"].get("run_ref")
        v_first_exec = exec_idx.get(v_first_ref) if v_first_ref else None
        v_last_exec = exec_idx.get(v_last_ref) if v_last_ref else None
        if not (v_first_exec and v_last_exec):
            # Fall through to oracle_heuristic below
            return _oracle_improved(dp, v_first_decision, v_last_decision) and "improved" or "unchanged"
        rules = _METRIC_BASED_DECISION_POINTS[dp]
        # For clustering_quality the spec also allows: "末版 accept 而首版 adjust"
        # → that's a decision-based shortcut.  We handle it separately below.
        metric_passed = any(
            _metric_improved(f, d, v_first_exec, v_last_exec) for f, d in rules
        )
        # decision-based shortcut for clustering_quality per spec
        if dp == "clustering_quality":
            if v_last_decision == "clustering_accept" and v_first_decision == "clustering_adjust":
                return "improved"
        return "improved" if metric_passed else "unchanged"

    # 2) Fallback: oracle-based (de_method, kg_match, batch_effect, ...)
    return "improved" if _oracle_improved(dp, v_first_decision, v_last_decision) else "unchanged"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        default="experiments/B4/self_correction_pairs.json",
    )
    parser.add_argument(
        "--runs",
        nargs="+",
        default=list(DEFAULT_RUNS.values()),
        help="Run paths to index for exec lookup (must match --runs in B4_self_correction.py).",
    )
    parser.add_argument("--out-dir", default="experiments/B4")
    args = parser.parse_args()

    pairs_path = Path(args.pairs)
    out_dir = Path(args.out_dir)
    if not pairs_path.exists():
        print(f"ERROR: {pairs_path} missing — run B4_self_correction.py first", file=sys.stderr)
        return 1
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))

    # Build exec index for each run
    exec_indices: dict[str, dict[str, dict]] = {}
    for run_str in args.runs:
        rp = Path(run_str)
        if rp.exists():
            exec_indices[run_str] = _load_exec_index(rp)
        else:
            exec_indices[run_str] = {}
            print(f"NOTE: {run_str} missing — exec lookup disabled for its pairs", file=sys.stderr)

    # Classify each pair
    enriched: list[dict] = []
    for p in pairs:
        run_str = p.get("run", "")
        exec_idx = exec_indices.get(run_str, {})
        verdict = _classify_pair(p, exec_idx)
        ep = dict(p)
        ep["verdict"] = verdict
        enriched.append(ep)

    # Aggregate by decision_point & by run
    by_dp: dict[str, dict] = {}
    by_run_dp: dict[str, dict[str, dict]] = defaultdict(dict)
    counts_total = Counter()
    counts_by_run = defaultdict(Counter)
    counts_by_dp = defaultdict(Counter)

    for p in enriched:
        v = p["verdict"]
        dp = p["decision_point"]
        run_str = p.get("run", "")
        counts_total[v] += 1
        counts_by_run[run_str][v] += 1
        counts_by_dp[dp][v] += 1
        by_run_dp[run_str].setdefault(
            dp,
            {"n_pairs": 0, "n_improved": 0, "n_unchanged": 0,
             "n_no_evidence": 0, "n_no_change_in_decision": 0},
        )
        by_run_dp[run_str][dp]["n_pairs"] += 1
        if v == "improved":
            by_run_dp[run_str][dp]["n_improved"] += 1
        elif v == "unchanged":
            by_run_dp[run_str][dp]["n_unchanged"] += 1
        elif v == "no_evidence":
            by_run_dp[run_str][dp]["n_no_evidence"] += 1
        elif v == "no_change_in_decision":
            by_run_dp[run_str][dp]["n_no_change_in_decision"] += 1

    for dp, c in counts_by_dp.items():
        n_pairs = sum(c.values())
        n_imp = c.get("improved", 0)
        n_scored_dp = n_pairs - c.get("no_evidence", 0) - c.get("no_change_in_decision", 0)
        by_dp[dp] = {
            "n_pairs": n_pairs,
            "n_scored": n_scored_dp,
            "n_improved": n_imp,
            "n_unchanged": c.get("unchanged", 0),
            "n_no_evidence": c.get("no_evidence", 0),
            "n_no_change_in_decision": c.get("no_change_in_decision", 0),
            "improvement_rate": round(n_imp / n_scored_dp, 3) if n_scored_dp else None,
            "scoring_method": (
                "metric_based" if dp in _METRIC_BASED_DECISION_POINTS
                else "ranking_based" if dp in _CONFIRM_DECISION_BUCKETS
                else "oracle_heuristic"
            ),
        }

    # Flat run × dp table
    flat_by_run_dp = {
        run_str: {
            dp: {**stats,
                 "improvement_rate": round(
                     stats["n_improved"] / max(stats["n_pairs"] - stats.get("n_no_evidence", 0)
                                                - stats.get("n_no_change_in_decision", 0), 1),
                     3,
                 ) if stats["n_pairs"] else None}
            for dp, stats in dps.items()
        }
        for run_str, dps in by_run_dp.items()
    }

    # Totals
    n_total = sum(counts_total.values())
    n_improved = counts_total.get("improved", 0)
    # Exclude "no_evidence" AND "no_change_in_decision" from rate denominator:
    # - no_evidence: can't score
    # - no_change_in_decision: re-judgment of the same outcome, not a real
    #   correction (B4 = "did the LLM change its mind for the better?")
    n_scored = (
        n_total
        - counts_total.get("no_evidence", 0)
        - counts_total.get("no_change_in_decision", 0)
    )
    overall_rate = round(n_improved / n_scored, 3) if n_scored else None

    rate_json = {
        "rules": {
            "metric_based_decision_points": list(_METRIC_BASED_DECISION_POINTS.keys()),
            "ranking_based_decision_points": list(_CONFIRM_DECISION_BUCKETS.keys()),
            "oracle_heuristic_decision_points": "de_method, kg_match, batch_effect, "
                                                "qc_threshold, resolution_select, "
                                                "unknown_cluster, global_quality",
            "primary_judgment_line": "scored_improvement_rate >= 0.50 "
                                     "(per experiment_implementation §3.4)",
            "rate_denominator_excludes": [
                "no_evidence (no exec data on either side)",
                "no_change_in_decision (multi-version same outcome, not a correction)",
            ],
        },
        "totals": {
            "n_pairs": n_total,
            "n_scored": n_scored,
            "n_improved": n_improved,
            "n_unchanged": counts_total.get("unchanged", 0),
            "n_no_evidence": counts_total.get("no_evidence", 0),
            "n_no_change_in_decision": counts_total.get("no_change_in_decision", 0),
            "improvement_rate": overall_rate,
            "primary_judgment_line_pass": (
                overall_rate >= 0.50 if overall_rate is not None else None
            ),
        },
        "by_decision_point": by_dp,
        "by_run": {run_str: dict(c) for run_str, c in counts_by_run.items()},
        "by_run_x_decision_point": flat_by_run_dp,
    }

    (out_dir / "improvement_rate.json").write_text(
        json.dumps(rate_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "improvement_by_decision_point.json").write_text(
        json.dumps(by_dp, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Stdout
    print(f"Total pairs: {n_total}")
    print(f"  scored:                {n_scored}")
    print(f"  improved:              {n_improved}")
    print(f"  no_change_in_decision: {counts_total.get('no_change_in_decision', 0)}")
    print(f"  no_evidence:           {counts_total.get('no_evidence', 0)}")
    print(f"  unchanged:             {counts_total.get('unchanged', 0)}")
    print()
    print(f"Rate (scored): {overall_rate}")
    print(f"Judgment line (>= 0.50): "
          f"{'PASS' if (overall_rate is not None and overall_rate >= 0.50) else 'FAIL'}")
    print()
    print("By decision_point:")
    for dp, stats in sorted(by_dp.items(), key=lambda kv: -kv[1]["n_pairs"]):
        rate = stats["improvement_rate"]
        rate_s = f"{rate:.3f}" if rate is not None else "n/a"
        print(f"  {dp:30s} pairs={stats['n_pairs']:3d}  scored={stats['n_scored']:3d}  "
              f"improved={stats['n_improved']:3d}  rate={rate_s}  method={stats['scoring_method']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
