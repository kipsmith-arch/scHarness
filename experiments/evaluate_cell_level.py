"""B1 cell-level evaluation — GT-pin + KG-hierarchy accuracy.

``final_annotations.json`` 的 ``label`` / ``confidence`` / ``status`` 必须由判断层写入;
本脚本不再从 ``run_log`` 做第二套覆盖。缺这三项则非零退出。

Cell-level semantics (``design/eval_design.md``):
    predicted → alias → node A; this cell's GT → pin → node B;
    relation from ontology_relation ancestors (or alias exact/synonym if KG skipped).
    strict_correct = relation in {exact, synonym}
    relaxed        = relation weight only (exact/synonym=1, subtype/supertype=0.5)
    label_unknown  = raw "unknown" → relation=unmatched
    label_downgraded keeps the predicted label; low confidence is reported as
    ``low_conf_rate``, not multiplied into accuracy.

Usage:
    python experiments/evaluate_cell_level.py \\
        --arms arm1=output/B1/arm1_default arm2=output/B1/arm2_rule arm3=output/B1/arm3_llm \\
        --gt-csv experiments/gt_cells.csv \\
        --gt-ontology experiments/gt_ontology.json \\
        --aliases experiments/kg_term_aliases.json \\
        --out output/B1/eval/evaluation_report.json

Scoring spec: ``design/eval_design.md``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ontology_eval import (  # noqa: E402
    OntologyScorer,
    fetch_hierarchy,
    load_aliases,
    load_gt_ontology,
)

STRICT_HIT = {"exact", "synonym"}
PARTIAL_HIT = {"subtype", "supertype"}
WEIGHT = {"exact": 1.0, "synonym": 1.0, "subtype": 0.5, "supertype": 0.5, "unrelated": 0.0, "unmatched": 0.0}
# Recorded on per_cell for diagnostics; not used in strict / relaxed / macro-F1.
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 1.0, "low": 0.5}
SKIP_SKILL_DOTENV = "CELL_ANNOTATION_SKIP_DOTENV"


def load_obs_snapshot(path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "leiden" not in (reader.fieldnames or []):
            raise SystemExit(f"error: {path} 缺少 leiden 列")
        for r in reader:
            cell = (r.get("cell_id") or "").strip()
            leiden = (r.get("leiden") or "").strip()
            if cell:
                mapping[cell] = leiden
    return mapping


def load_final_annotations(path: str) -> dict[str, dict]:
    """leiden -> {label, confidence, status, ...}. Requires judge-written fields."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    ann = doc.get("annotations", doc)
    out: dict[str, dict] = {}
    required = ("label", "confidence", "status")
    for k, v in ann.items():
        if not isinstance(v, dict):
            raise SystemExit(
                f"error: {path} cluster {k} 缺少判断层写入的 label/confidence/status"
                "（pipeline 不再写入这些字段）"
            )
        missing = [f for f in required if not v.get(f)]
        if missing:
            raise SystemExit(
                f"error: {path} cluster {k} 缺少判断层写入的 {missing}"
                "（pipeline 不再写入 label/confidence/status）"
            )
        out[str(k)] = v
    if not out:
        raise SystemExit(
            f"error: {path} 没有任何带判断层 label/confidence/status 的簇"
        )
    return out


def load_gt(path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            cell = (r.get("cell_barcode") or "").strip()
            tt = (r.get("true_type") or "").strip()
            if cell:
                mapping[cell] = tt
    return mapping


def evaluate_arm(name: str, project_dir: str, obs_path: str, ann_path: str,
                 scorer: OntologyScorer, gt: dict) -> dict:
    """Return a per-arm evaluation report (accuracy from GT-pin + hierarchy)."""
    obs = load_obs_snapshot(obs_path)
    ann = load_final_annotations(ann_path)

    per_cell: list[dict] = []
    unmatched_terms: Counter = Counter()
    unknown_leiden: set[str] = set()
    for cell, true in gt.items():
        leiden = obs.get(cell)
        if leiden is None:
            continue
        ent = ann.get(str(leiden))
        if ent is None:
            unknown_leiden.add(str(leiden))
            raw, conf, status = "unknown", "low", "unknown"
        else:
            raw = ent.get("label") or "unknown"
            conf = ent.get("confidence") or "medium"
            status = ent.get("status") or "decisive"
        relation = scorer.relation(raw, true)
        if relation == "unmatched" and raw != "unknown":
            unmatched_terms[raw] += 1
        rel_w = WEIGHT.get(relation, 0.0)
        conf_w = CONFIDENCE_WEIGHT.get(conf, 0.5)
        cell_w = rel_w
        is_strict = relation in STRICT_HIT
        per_cell.append({
            "cell": cell, "true": true, "leiden": leiden, "raw": raw,
            "confidence": conf, "status": status, "relation": relation,
            "node_a": scorer.resolve_predicted(raw),
            "node_b": scorer.resolve_gt(true),
            "mapped_true": scorer.mapped_true(raw),
            "relation_weight": rel_w, "confidence_weight": conf_w, "cell_weight": cell_w,
            "is_strict_correct": is_strict,
        })

    n = len(per_cell)
    if n == 0:
        raise SystemExit(f"error: {name} 无可评估细胞(obs 与 gt 无交集)")

    strict_hits = sum(1 for c in per_cell if c["is_strict_correct"])
    score_sum = sum(c["cell_weight"] for c in per_cell)
    strict_acc = strict_hits / n
    relaxed_acc = score_sum / n

    true_labels = sorted(set(c["true"] for c in per_cell))
    tp = {t: 0.0 for t in true_labels}
    fp = {t: 0.0 for t in true_labels}
    fn = {t: 0.0 for t in true_labels}
    for c in per_cell:
        if c["relation"] in STRICT_HIT or c["relation"] in PARTIAL_HIT:
            tp[c["true"]] += c["cell_weight"]
        else:
            fn[c["true"]] += 1.0
            mapped_true = c.get("mapped_true")
            if mapped_true and mapped_true in fp and mapped_true != c["true"]:
                fp[mapped_true] += 1.0
    f1s: dict[str, float] = {}
    for t in true_labels:
        p = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        r = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1s[t] = 2 * p * r / (p + r) if (p + r) else 0.0
    macro_f1 = sum(f1s.values()) / len(f1s) if f1s else 0.0

    cluster_true: dict[str, Counter] = {}
    cluster_per_arm = {}
    for c in per_cell:
        cluster_true.setdefault(c["leiden"], Counter())[c["true"]] += 1
    last_ent: dict = {}
    for leiden, cnt in sorted(cluster_true.items()):
        total = sum(cnt.values())
        top_true, top_n = cnt.most_common(1)[0]
        hits = [c for c in per_cell if c["leiden"] == leiden]
        strict_count = sum(1 for c in hits if c["is_strict_correct"])
        last_ent = ann.get(str(leiden), {})
        cluster_per_arm[leiden] = {
            "leiden": leiden, "n_cells": total, "predicted": hits[0]["raw"],
            "confidence": hits[0]["confidence"], "relation": hits[0]["relation"],
            "gap_metrics": last_ent.get("gap_metrics", {}),
            "top3_expression": last_ent.get("top3_expression", []),
            "subcluster": last_ent.get("subcluster"),
            "top_true": top_true, "top_true_n": top_n, "purity": top_n / total,
            "strict_correct_cells": strict_count,
        }
    mean_purity = sum(v["purity"] for v in cluster_per_arm.values()) / max(len(cluster_per_arm), 1)

    conf_dist = Counter(c["confidence"] for c in per_cell)
    n_low = conf_dist.get("low", 0)

    return {
        "arm": name,
        "project_dir": project_dir,
        "n_cells_evaluated": n,
        "strict_accuracy": round(strict_acc, 4),
        "relaxed_accuracy": round(relaxed_acc, 4),
        "macro_f1_soft": round(macro_f1, 4),
        "mean_cluster_purity": round(mean_purity, 4),
        "n_clusters": len(cluster_per_arm),
        "low_conf_rate": round(n_low / n, 4),
        "confidence_distribution": dict(conf_dist),
        "unmatched_terms": dict(unmatched_terms),
        "unknown_leiden": sorted(unknown_leiden),
        "kg_hierarchy": "skipped" if scorer.hierarchy.skipped else "used",
        "per_cluster": cluster_per_arm,
        "per_cell": per_cell,
        "f1_per_type": {k: round(v, 4) for k, v in f1s.items()},
    }


def _load_skill_neo4j_env() -> None:
    """Load skill Neo4j keys: .env.example seeds, .env overrides, shell wins."""
    if os.environ.get(SKIP_SKILL_DOTENV) == "1":
        return
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    skill_dir = REPO_ROOT / "skills" / "cell-annotation"
    pre_existing = set(os.environ)
    example = skill_dir / ".env.example"
    if example.is_file():
        try:
            values = dotenv_values(example, interpolate=False)
        except Exception:
            values = None
        if values:
            for key, value in values.items():
                if not key or value is None or value == "":
                    continue
                if key in os.environ:
                    continue
                os.environ[key] = value
    env_path = skill_dir / ".env"
    if env_path.is_file():
        try:
            values = dotenv_values(env_path, interpolate=False)
        except Exception:
            values = None
        if values:
            for key, value in values.items():
                if not key or value is None or value == "":
                    continue
                if key in pre_existing:
                    continue
                os.environ[key] = value


def _collect_query_names(pins: dict[str, str], aliases: dict[str, str],
                         predicted: list[str]) -> list[str]:
    names = set(pins.values()) | set(aliases.values()) | set(aliases.keys())
    names.update(predicted)
    return sorted({n.strip() for n in names if n and str(n).strip()})


def _arm_paths(spec: str) -> tuple[str, str, str, str]:
    if "=" not in spec:
        raise SystemExit(f"--arms 格式应为 name=path,收到: {spec}")
    name, pdir = spec.split("=", 1)
    obs_path = os.path.join(pdir, "obs_snapshot.csv")
    if not os.path.exists(obs_path):
        obs_path = os.path.join(pdir, "step1_prepare", "obs_snapshot.csv")
    ann_path = os.path.join(pdir, "step6_validate", "final_annotations.json")
    if not os.path.exists(ann_path):
        raise SystemExit(f"missing: {ann_path}")
    return name, pdir, obs_path, ann_path


def build_scorer(pins: dict[str, str], aliases: dict[str, str],
                 predicted: list[str], max_hops: int) -> OntologyScorer:
    _load_skill_neo4j_env()
    names = _collect_query_names(pins, aliases, predicted)
    hierarchy = fetch_hierarchy(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD"),
        names,
        max_hops=max_hops,
    )
    return OntologyScorer(pins=pins, aliases=aliases, hierarchy=hierarchy)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="evaluate_cell_level.py",
        description="B1 多臂细胞级评估(strict/relaxed = GT 钉节点 + 图谱层次)",
    )
    ap.add_argument("--arms", nargs="+", required=True,
                    help="格式 <name>=<project_dir>(可多个),如 arm1=output/B1/arm1_default")
    ap.add_argument("--gt-csv", default="experiments/gt_cells.csv")
    ap.add_argument("--gt-ontology", default="experiments/gt_ontology.json",
                    help="GT 字符串 → Ontology.Name 钉表")
    ap.add_argument("--aliases", default="experiments/kg_term_aliases.json",
                    help="预测词变体 → 规范 Ontology.Name")
    ap.add_argument("--max-ancestor-hops", type=int, default=3)
    ap.add_argument("--label-map", default=None,
                    help="已停用;传入则报错,改为 --gt-ontology / --aliases")
    ap.add_argument("--out", default=None, help="评估报告 JSON 路径")
    args = ap.parse_args()

    if args.label_map:
        raise SystemExit(
            "error: --label-map pair 表已停用。改用 --gt-ontology 与 --aliases"
            "（见 _bmad-output/implementation-artifacts/label-map-ontology-eval.md）"
        )

    pins, pin_meta = load_gt_ontology(args.gt_ontology)
    aliases, alias_meta = load_aliases(args.aliases)
    gt = load_gt(args.gt_csv)
    if pin_meta.get("verified") is not True:
        print("[evaluate_cell_level] WARNING: gt_ontology 未定案", file=sys.stderr)

    predicted: list[str] = []
    arm_specs: list[tuple[str, str, str, str]] = []
    for spec in args.arms:
        name, pdir, obs_path, ann_path = _arm_paths(spec)
        arm_specs.append((name, pdir, obs_path, ann_path))
        for ent in load_final_annotations(ann_path).values():
            predicted.append(ent.get("label") or "unknown")

    scorer = build_scorer(pins, aliases, predicted, args.max_ancestor_hops)
    if scorer.hierarchy.skipped:
        print("[evaluate_cell_level] kg_hierarchy: skipped (offline alias exact/synonym only)",
              file=sys.stderr)

    arms: list[dict] = []
    for name, pdir, obs_path, ann_path in arm_specs:
        arms.append(evaluate_arm(name, pdir, obs_path, ann_path, scorer, gt))

    summary_table = []
    for r in arms:
        summary_table.append({
            "arm": r["arm"],
            "n_cells": r["n_cells_evaluated"],
            "strict_accuracy": r["strict_accuracy"],
            "relaxed_accuracy": r["relaxed_accuracy"],
            "macro_f1_soft": r["macro_f1_soft"],
            "mean_cluster_purity": r["mean_cluster_purity"],
            "low_conf_rate": r["low_conf_rate"],
            "confidence_distribution": r["confidence_distribution"],
            "n_clusters": r["n_clusters"],
            "kg_hierarchy": r["kg_hierarchy"],
        })

    report = {
        "gt_ontology_verified": pin_meta.get("verified") is True,
        "aliases_verified": alias_meta.get("verified") is True,
        "kg_hierarchy": "skipped" if scorer.hierarchy.skipped else "used",
        "weights": {"relation": WEIGHT,
                    "confidence_diagnostic_only": CONFIDENCE_WEIGHT,
                    "strict_requires": "relation in {exact, synonym}",
                    "relaxed": "relation weight; confidence not multiplied"},
        "arms_summary": summary_table,
        "arms": [
            {k: v for k, v in r.items() if k != "per_cell"} for r in arms
        ],
    }
    per_cell_payload = {r["arm"]: r["per_cell"] for r in arms}
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        pc_path = args.out.replace(".json", ".per_cell.json")
        with open(pc_path, "w", encoding="utf-8") as f:
            json.dump(per_cell_payload, f, ensure_ascii=False)

    print(f"[evaluate_cell_level] gt_ontology_verified={pin_meta.get('verified') is True} "
          f"kg_hierarchy={report['kg_hierarchy']}")
    print(f"{'arm':<12} {'cells':>6} {'strict':>8} {'relaxed':>9} {'macroF1':>9} {'purity':>8} {'low_conf':>10}")
    for r in arms:
        low = r["confidence_distribution"].get("low", 0)
        print(f"{r['arm']:<12} {r['n_cells_evaluated']:>6} "
              f"{r['strict_accuracy']:>8.4f} {r['relaxed_accuracy']:>9.4f} "
              f"{r['macro_f1_soft']:>9.4f} {r['mean_cluster_purity']:>8.4f} "
              f"{low:>10}")
    print(f"  report: {args.out or '<stdout only>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
