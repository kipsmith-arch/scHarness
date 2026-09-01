"""S1 合成场景 — 构造 8 个受控用例(battery)。

为什么需要这个 (experiment_implementation.md §3.2 S1):
    真实数据 ambiguous 簇太少不足以检验 refine 决策点的能力 →
    S1 主动合并两类真值簇形成 ambiguous 簇,
    测"在已知真值下,LLM/规则能否正确决定是否 refine"。

输出:
    experiments/S1/scenarios.json     — 8 用例定义
    experiments/S1/leiden_override.csv — 用例的簇重映射(原 leiden -> 新合并 leiden)

依赖:
    - experiments/gt_cells.csv         (真值)
    - output/B1/arm3_llm/step1_prepare/obs_snapshot.csv (leiden mapping)

构造原则(§3.2 表):
    - 用例必须落在"占比 ≥ 90%"的纯簇上,合并后才能信 oracle
    - 8 用例覆盖 6 陷阱 + 正反例 + 难度梯度
    - 簇号是 SRP171040 参考,实际以 build_scenarios.py 用 gt_cells 校验后的纯 pipeline 簇为准

判定线:
    - oracle 表 + deterministic ground truth (强制 subcluster 后看子簇能否分离)
    - 比较 ② (rule_judge) vs ③ (LLM single-turn) vs ground truth
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_OBS = os.path.join(REPO_ROOT, "output", "B1", "arm3_llm", "step1_prepare", "obs_snapshot.csv")
DEFAULT_GT = os.path.join(REPO_ROOT, "experiments", "gt_cells.csv")
DEFAULT_LABEL_MAP = os.path.join(REPO_ROOT, "experiments", "label_map.json")
DEFAULT_OUT = os.path.join(REPO_ROOT, "experiments", "S1")

PURITY_THRESHOLD = 0.90  # 占比 ≥ 90% 的簇视为"纯簇",可被合并


def load_obs(path: str) -> tuple[dict[str, str], list[str]]:
    """cell -> leiden, ordered_leiden list."""
    cell2leiden: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = (r.get("cell_id") or "").strip()
            l = (r.get("leiden") or "").strip()
            if c:
                cell2leiden[c] = l
    return cell2leiden, sorted(set(cell2leiden.values()))


def load_gt(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = (r.get("cell_barcode") or "").strip()
            t = (r.get("true_type") or "").strip()
            if c:
                out[c] = t
    return out


def load_label_map(path: str) -> dict[str, str]:
    """predicted -> first true label."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    for e in doc["entries"]:
        out.setdefault(e["predicted"], e["true"])
    return out


def find_pure_clusters(cell2leiden: dict[str, str], gt: dict[str, str]) -> dict[str, str]:
    """leiden -> dominant true_type, only for clusters with purity >= PURITY_THRESHOLD."""
    cnt: dict[str, Counter] = defaultdict(Counter)
    for cell, leiden in cell2leiden.items():
        tt = gt.get(cell)
        if tt:
            cnt[leiden][tt] += 1
    pure: dict[str, str] = {}
    for leiden, c in cnt.items():
        total = sum(c.values())
        if total < 50:  # too small for a meaningful scenario
            continue
        top, n = c.most_common(1)[0]
        purity = n / total
        if purity >= PURITY_THRESHOLD:
            pure[leiden] = top
    return pure


def build_scenarios(pure_clusters: dict[str, str], label_map_pred2true: dict[str, str]) -> list[dict]:
    """Construct 8 cases (S-P1..S-H2) from §3.2 table.

    Logic:
      - For each cluster_id, we know the dominant true label.
      - We pair pure clusters of *distinct* true labels for positive cases (S-P1..S-P3, S-H1, S-H2).
      - We pair pure clusters of the *same* true label for negative cases (S-N1..S-N3).
      - For parent-child trap (S-N2) we use the `supertype` relation in label_map.

    Returns scenarios.json with per-case oracle decision + cluster IDs.

    NOTE: The specific cluster ID pairs depend on which B1 arm3_llm clusters have
    sufficient purity. We scan pure_clusters and greedily pair. If a case cannot
    be built (e.g. no pair exists), it is marked `available: false`.
    """
    by_label: dict[str, list[str]] = defaultdict(list)
    for leid, lbl in pure_clusters.items():
        by_label[lbl].append(leid)

    # Parent-child pairs (label_map supertype relations)
    parent_child = []
    with open(os.path.join(REPO_ROOT, "experiments", "label_map.json"), encoding="utf-8") as f:
        lm = json.load(f)
    for e in lm["entries"]:
        if e["relation"] == "supertype":
            parent_child.append((e["predicted"], e["true"]))

    cases = []

    def pair_take_two(label: str) -> list[str]:
        """Take two clusters of a given label; return them and remove from pool."""
        cs = by_label.get(label, [])
        if len(cs) >= 2:
            a, b = cs[0], cs[1]
            by_label[label] = cs[2:]
            return [a, b]
        return []

    # S-P1: Xylem + Root hair (unrelated)
    cs = pair_take_two("Xylem") + pair_take_two("Root hair")
    cases.append({"id": "S-P1", "type": "positive_unrelated",
                   "clusters": cs[:2], "merge_label": "xylem|root_hair",
                   "trap": "none", "oracle_decision": "ambiguous_true → route step5 → refine_effective",
                   "available": len(cs) >= 2})

    # S-P2: Phloem + Root endodermis (unrelated)
    cs = pair_take_two("Phloem") + pair_take_two("Root endodermis")
    cases.append({"id": "S-P2", "type": "positive_unrelated",
                   "clusters": cs[:2], "merge_label": "phloem|root_endodermis",
                   "trap": "none", "oracle_decision": "ambiguous_true → refine_effective",
                   "available": len(cs) >= 2})

    # S-P3: Meristematic + Stem cell niche (rare) — uses trap3 small sample
    cs = pair_take_two("Meristematic cell") + pair_take_two("Stem cell niche")
    cases.append({"id": "S-P3", "type": "positive_rare",
                   "clusters": cs[:2], "merge_label": "meristematic|stem_cell_niche",
                   "trap": "trap3_small_sample",
                   "oracle_decision": "ambiguous_true but check count_diff",
                   "available": len(cs) >= 2})

    # S-N1: Pericycle + Pericycle (same type)
    cs = pair_take_two("Pericycle")
    cases.append({"id": "S-N1", "type": "negative_same_type",
                   "clusters": cs[:2], "merge_label": "pericycle|pericycle",
                   "trap": "counter_example",
                   "oracle_decision": "first_decisive, no refine",
                   "available": len(cs) >= 2})

    # S-N2: Columella root cap + Lateral root cap (parent-child via "root cap" supertype)
    cs = pair_take_two("Columella root cap") + pair_take_two("Lateral root cap")
    cases.append({"id": "S-N2", "type": "negative_parent_child",
                   "clusters": cs[:2], "merge_label": "columella|lateral_root_cap",
                   "trap": "trap2_hierarchy",
                   "oracle_decision": "ambiguous_parent_child, pick more specific, no step5",
                   "available": len(cs) >= 2})

    # S-N3: Root cortex + Root cortex (same type, different sample — single-batch)
    cs = pair_take_two("Root cortex")
    cases.append({"id": "S-N3", "type": "negative_same_type",
                   "clusters": cs[:2], "merge_label": "root_cortex|root_cortex",
                   "trap": "trap6_single_batch",
                   "oracle_decision": "no batch_effect, no refine, first_decisive",
                   "available": len(cs) >= 2})

    # S-H1: Root cortex + Root hair (mid-difficulty)
    cs = pair_take_two("Root cortex") + pair_take_two("Root hair")
    cases.append({"id": "S-H1", "type": "positive_mid_difficulty",
                   "clusters": cs[:2], "merge_label": "root_cortex|root_hair",
                   "trap": "none", "oracle_decision": "ambiguous_true → step5",
                   "available": len(cs) >= 2})

    # S-H2: 3-way merge (super-fusion) — Root endodermis + Root cortex + Pericycle
    cs = (pair_take_two("Root endodermis") +
          pair_take_two("Root cortex") +
          pair_take_two("Pericycle"))
    cases.append({"id": "S-H2", "type": "positive_superfusion",
                   "clusters": cs[:3], "merge_label": "endodermis|cortex|pericycle",
                   "trap": "none",
                   "oracle_decision": "ambiguous_true → step5, n_subclusters ≥ 2 expected",
                   "available": len(cs) >= 3})

    return cases


def write_leiden_override(scenarios: list[dict], out_path: str):
    """Write CSV: original_leiden -> synthetic_leiden, for downstream step5 run."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "original_leiden", "synthetic_leiden"])
        for case in scenarios:
            sid = case["id"]
            synth = f"S1_{sid}"
            for orig in case["clusters"]:
                w.writerow([sid, orig, synth])


def main() -> int:
    ap = argparse.ArgumentParser(prog="build_scenarios.py",
                                 description="S1 — 构造 8 个合成场景用例")
    ap.add_argument("--obs", default=DEFAULT_OBS)
    ap.add_argument("--gt", default=DEFAULT_GT)
    ap.add_argument("--label-map", default=DEFAULT_LABEL_MAP)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args()

    if not os.path.exists(args.obs):
        print(f"[build_scenarios] ERROR: missing {args.obs}", file=sys.stderr)
        return 1
    if not os.path.exists(args.gt):
        print(f"[build_scenarios] ERROR: missing {args.gt}", file=sys.stderr)
        return 1

    cell2leiden, _ = load_obs(args.obs)
    gt = load_gt(args.gt)
    pred2true = load_label_map(args.label_map)
    pure = find_pure_clusters(cell2leiden, gt)
    print(f"[build_scenarios] {len(pure)} pure clusters (>= {PURITY_THRESHOLD*100:.0f}% purity, >= 50 cells)")

    cases = build_scenarios(pure, pred2true)

    n_avail = sum(1 for c in cases if c["available"])
    n_not = len(cases) - n_avail

    os.makedirs(args.out_dir, exist_ok=True)
    scenarios_path = os.path.join(args.out_dir, "scenarios.json")
    with open(scenarios_path, "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "source_obs": args.obs,
                "source_gt": args.gt,
                "purity_threshold": PURITY_THRESHOLD,
                "min_cells": 50,
                "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "n_pure_clusters_found": len(pure),
                "n_cases_total": len(cases),
                "n_cases_available": n_avail,
                "n_cases_unavailable": n_not,
            },
            "cases": cases,
        }, f, ensure_ascii=False, indent=2)

    override_path = os.path.join(args.out_dir, "leiden_override.csv")
    write_leiden_override([c for c in cases if c["available"]], override_path)

    print(f"[build_scenarios] {n_avail}/{len(cases)} cases available; written:")
    print(f"  {scenarios_path}")
    print(f"  {override_path}")
    for c in cases:
        flag = "OK" if c["available"] else "N/A"
        print(f"  [{flag}] {c['id']}: clusters={c['clusters']} ({c['oracle_decision']})")
    if n_not:
        print(f"\nWARNING: {n_not} cases unavailable due to insufficient pure-cluster pairs.")
        print(f"  Consider relaxing PURITY_THRESHOLD or running on a richer dataset.")
    return 0


if __name__ == "__main__":
    sys.exit(main())