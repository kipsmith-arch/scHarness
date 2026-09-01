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
    # marker_quality: per design §3.4 "n_markers 进入 [10, 50] 区间";
    # but the actual run_log.jsonl shape emits "n_markers_per_cluster_avg"
    # (averaged across clusters) or "n_clusters_in_range_10_50" (count of
    # clusters with n_markers in [10, 50]). We treat the latter as the
    # actionable proxy — "more clusters in the healthy range" = improvement.
    "marker_quality": [
        ("n_clusters_in_range_10_50", "up"),
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
    # NOTE: `refine_effect` and `unknown_cluster` have their own ranking schemes
    # below (added during review iteration 1). `refine_effect_label` /
    # `unknown_cluster_label` were originally planned here but they don't match
    # the canonical decision_point keys in trajectory_schema.py — those entries
    # are removed; we handle refine_effect via metric_based path (exec data)
    # and unknown_cluster via oracle_heuristic.
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

    We use a CONSERVATIVE read-only heuristic that maps each (decision_point,
    decision) pair to an explicit tier per `design/experiment_implementation.md`
    §3.4 oracle-style guidance. Only pairs with **strict directionality**
    (lower tier → higher tier) count as improved.

    Why not import `experiments/judges/rule_judge.py` directly: that module is
    per-cluster with project_dir-coupled write side effects (writes to
    `run_log.jsonl`). For B4's offline trajectory analysis we need a pure
    read-only classifier; the simplified tier table below is documented and
    deterministic, satisfying `experiment_implementation.md §3.4` "在脚本中
    显式定义, 不靠 LLM 自评".

    Tier convention: 0 = worst, 1 = neutral, 2 = best. A pair improves iff
    v_last_decision tier > v_first_decision tier.
    """
    if v_first_decision == v_last_decision:
        return False

    # Per-decision-point tier table. Each tuple is (decision -> tier).
    # Unknown decisions default to tier 1 (neutral).
    DECISION_TIERS: dict[str, dict[str, int]] = {
        # qc_threshold: setting an explicit threshold (any non-default value)
        # is better than default; specific "loosen"/"tighten" distinctions
        # would require experiment-specific calibration we don't have here.
        "qc_threshold": {
            "threshold_default": 1,
            "threshold_set": 2,
        },
        # resolution_select: any explicit resolution choice > default
        "resolution_select": {
            "resolution_default": 1,
            "resolution_chosen": 2,
        },
        # de_method: wilcoxon is the safe default; pseudobulk switches happen
        # only for rare clusters (improvement is when default wilcoxon is
        # EXPLICITLY confirmed for the dataset, OR a switch to pseudobulk is
        # decided for rare clusters). Without per-experiment calibration we
        # cannot tell which direction is "better", so all direction-changing
        # pairs score as unchanged (NOT improved). See F28 in review findings.
        "de_method": {
            "wilcoxon": 1,
            "pseudobulk_rare": 1,
            "pseudobulk_all": 1,
        },
        # kg_match: id_match_ok > id_match_no
        "kg_match": {
            "id_match_no": 0,
            "id_match_partial": 1,
            "id_match_ok": 2,
        },
        # batch_effect: detected is informational; correction > detection-only
        "batch_effect": {
            "batch_not_detected": 1,
            "batch_detected": 1,
            "batch_corrected": 2,
        },
        # global_quality: ok > marginal > bad
        "global_quality": {
            "global_quality_bad": 0,
            "global_quality_marginal": 1,
            "global_quality_ok": 2,
        },
        # clustering_quality: accept > adjust (handled by metric path normally;
        # fallback here for exec data missing)
        "clustering_quality": {
            "clustering_adjust": 0,
            "clustering_accept": 2,
        },
        # refine_effect: handled by metric_based normally; fallback
        "refine_effect": {
            "refine_skipped": 0,
            "refine_ineffective": 0,
            "refine_effective": 2,
        },
        # unknown_cluster: clarified (specific type) > flagged (unknown)
        "unknown_cluster": {
            "unknown_cluster_yes": 0,  # still unknown after consideration
            "unknown_cluster_flagged": 1,  # flagged for review
            "unknown_cluster_clarified": 2,  # resolved to a known type
        },
    }

    tiers = DECISION_TIERS.get(decision_point, {})
    if not tiers:
        # Unknown decision_point: cannot judge, return False (conservative)
        return False
    tier_first = tiers.get(v_first_decision, 1)
    tier_last = tiers.get(v_last_decision, 1)
    return tier_last > tier_first


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
            # No exec data on either side → "no_evidence"; do NOT fall through
            # to oracle (the metric-based dp was specifically chosen because
            # we expect measurable metrics).
            return "no_evidence"
        rules = _METRIC_BASED_DECISION_POINTS[dp]
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

    # Flat run × dp table. Use None (not 0.0) for empty bins to match the
    # convention used by `by_decision_point` below (review F20/ECH-22).
    flat_by_run_dp = {
        run_str: {
            dp: (lambda s: {
                **s,
                "n_scored": s["n_pairs"] - s.get("n_no_evidence", 0)
                            - s.get("n_no_change_in_decision", 0),
                "improvement_rate": round(s["n_improved"] / (
                    s["n_pairs"] - s.get("n_no_evidence", 0)
                    - s.get("n_no_change_in_decision", 0)
                ), 3) if (
                    s["n_pairs"] - s.get("n_no_evidence", 0)
                    - s.get("n_no_change_in_decision", 0)
                ) > 0 else None,
            })(stats)
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
    # Unconditional: of ALL multi-version pairs, what fraction improved?
    unconditional_rate = round(n_improved / n_total, 3) if n_total else None
    # Conditional: of pairs WHERE the LLM changed its mind, what fraction improved?
    overall_rate = round(n_improved / n_scored, 3) if n_scored else None

    rate_json = {
        "rules": {
            "metric_based_decision_points": list(_METRIC_BASED_DECISION_POINTS.keys()),
            "ranking_based_decision_points": list(_CONFIRM_DECISION_BUCKETS.keys()),
            "oracle_heuristic_decision_points": [
                "de_method", "kg_match", "batch_effect",
                "qc_threshold", "resolution_select",
                "unknown_cluster", "global_quality",
                "clustering_quality", "refine_effect",  # fallbacks
            ],
            "primary_judgment_line": "scored_improvement_rate >= 0.50 "
                                     "(per experiment_implementation §3.4)",
            "rate_denominator_excludes": [
                "no_evidence (no exec data on either side)",
                "no_change_in_decision (multi-version same outcome, not a correction)",
            ],
            "rate_definitions": {
                "conditional_rate (improvement_rate)": "improved / (improved + unchanged). "
                                                       "Among pairs where the LLM changed "
                                                       "its mind, what fraction improved?",
                "unconditional_rate": "improved / total_pairs. "
                                      "Of all multi-version pairs, what fraction "
                                      "improved (treating no-change as not-improved)?",
            },
        },
        "totals": {
            "n_pairs": n_total,
            "n_scored": n_scored,
            "n_improved": n_improved,
            "n_unchanged": counts_total.get("unchanged", 0),
            "n_no_evidence": counts_total.get("no_evidence", 0),
            "n_no_change_in_decision": counts_total.get("no_change_in_decision", 0),
            "improvement_rate": overall_rate,
            "unconditional_rate": unconditional_rate,
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
