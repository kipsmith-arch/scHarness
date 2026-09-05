"""Step 7 — diagnostics (tool_design.md §5.7, atomic_operations.md stage 7).

Subcommand:
    run  — hit_rate -> candidate_count -> first_second -> batch_entropy
            -> metadata_check -> cross_cluster -> write report.
           [0× h5ad — reads obs_snapshot.csv + step JSONs only]

Per-cluster batch entropy comes from obs_snapshot.csv, not from the h5ad, so
this step performs zero AnnData loads.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

_REQUIRED_META = ["kg_source", "kg_version", "organ", "annotation_date", "scanpy_version"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step7_diagnose.py",
        description="Step 7 诊断:命中率/候选数/first-second/批次熵(读 obs_snapshot)/元数据/跨簇(0 次 h5ad 加载)",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")
    p_run = sub.add_parser("run", help="hit_rate~cross_cluster + 报告")
    p_run.add_argument("--batch-key", default=None, help="批次列名(缺省自动识别)")
    common.add_common_args(p_run)
    return parser


def op_hit_rate(kg_hits, log_path, params) -> dict:
    per = {}
    for c, info in kg_hits.get("per_cluster", {}).items():
        per[c] = float(info.get("n_markers_hit", 0) / max(info.get("n_markers", 1), 1))
    m = {"per_cluster_hit_rate": per,
         "mean_hit_rate": float(np.mean(list(per.values()))) if per else None}
    common.exec_record(log_path, "step7_diagnose", "hit_rate", params, m)
    return per


def op_candidate_count(kg_hits, log_path, params) -> dict:
    per = {c: len(info.get("candidates", [])) for c, info in kg_hits.get("per_cluster", {}).items()}
    m = {"per_cluster_candidate_count": per}
    common.exec_record(log_path, "step7_diagnose", "candidate_count", params, m)
    return per


def op_first_second(annotations, log_path, params) -> dict:
    per = {}
    for c, a in annotations.get("annotations", {}).items():
        per[c] = {
            "first_count": a.get("first_count"),
            "second_count": a.get("second_count"),
            "top_strictly_ahead": bool(a.get("first_count", 0) > a.get("second_count", 0)),
        }
    m = {"per_cluster_first_second": per}
    common.exec_record(log_path, "step7_diagnose", "first_second", params, m)
    return per


def op_batch_entropy(obs_snapshot_path, batch_key, log_path, params) -> dict:
    if not os.path.exists(obs_snapshot_path):
        return common.fail(f"obs_snapshot.csv 不存在:{obs_snapshot_path}(请先运行 step1_prepare run)")
    obs = pd.read_csv(obs_snapshot_path, index_col=0)
    if "leiden" not in obs.columns:
        return common.fail("obs_snapshot 缺少 leiden 列")
    try:
        key = common.resolve_batch_key(obs.columns, batch_key)
    except ValueError as exc:
        return common.fail(str(exc))
    bm = common.batch_mixing(obs["leiden"].astype(str).values, obs[key].astype(str).values)
    bm["batch_key_used"] = key
    m = {"per_cluster_batch_entropy": {c: v["entropy"] for c, v in bm["per_cluster"].items()},
         "per_cluster_max_batch_fraction": {c: v["max_batch_fraction"] for c, v in bm["per_cluster"].items()},
         "per_cluster_batch_nunique": {c: v["nunique"] for c, v in bm["per_cluster"].items()},
         "batch_key_used": key,
         "batch_cluster_chi_square": bm["chi_square"],
         "batch_cluster_chi_square_p": bm["chi_square_p"],
         "overall_batch_mixing_index": bm["overall_batch_mixing_index"]}
    common.exec_record(log_path, "step7_diagnose", "batch_entropy", params, m)
    return m


def op_metadata_check(final, log_path, params) -> dict:
    meta = (final or {}).get("_meta", {})
    missing = [f for f in _REQUIRED_META if f not in meta or meta.get(f) is None]
    m = {"metadata_missing": missing, "required_fields": _REQUIRED_META}
    common.exec_record(log_path, "step7_diagnose", "metadata_check", params, m)
    return m


def op_cross_cluster(final, markers_json, qc_metrics, hit_rate, first_second,
                     batch_entropy, log_path, params) -> dict:
    summary = (final or {}).get("_summary", {})
    annotations = (final or {}).get("annotations", {})
    n_clusters = len(annotations)
    n_unique = summary.get("n_unique_labels")
    label_counts = list((summary.get("cell_type_proportions") or {}).values())
    # cross-cluster marker overlap matrix (Jaccard over kept marker sets)
    sets = {c: set(v.get("marker_genes", [])) for c, v in markers_json.get("per_cluster", {}).items()}
    po = common.pairwise_overlap(sets)
    # mean first-count gap (global)
    gaps = [fs["first_count"] - fs["second_count"] for fs in first_second.values()
            if fs.get("first_count") is not None]
    # cluster purity proxy from step1 silhouette
    sil = ((qc_metrics or {}).get("clustering", {}) or {}).get("silhouette", {})
    m = {
        "label_uniqueness": (float(n_unique / max(n_clusters, 1)) if n_unique is not None else None),
        "annotation_entropy": common.shannon_entropy(label_counts) if label_counts else None,
        "cluster_purity_proxy": sil.get("mean"),
        "mean_first_count_gap": float(np.mean(gaps)) if gaps else None,
        "cross_cluster_marker_overlap_matrix": po.get("overlap_matrix", {}),
        "mean_cross_cluster_marker_overlap": po.get("mean_overlap"),
        "batch_cluster_independence_chi_square": batch_entropy.get("batch_cluster_chi_square"),
        "mean_hit_rate": float(np.mean(list(hit_rate.values()))) if hit_rate else None,
        "n_clusters_no_candidates": int(sum(1 for a in annotations.values()
                                            if not a.get("first_candidate"))),
    }
    rid = common.exec_record(log_path, "step7_diagnose", "cross_cluster", params, m)
    m["run_id"] = rid
    return m


def cmd_run(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step7_diagnose")
    log = common.run_log_path(args.project_dir)
    kg = common.read_json(os.path.join(common.step_dir(args.project_dir, "step3_kg"),
                                       "kg_hits.json"))
    ann = common.read_json(os.path.join(common.step_dir(args.project_dir, "step4_rank"),
                                        "annotations.json"))
    final = common.read_json(os.path.join(common.step_dir(args.project_dir, "step6_validate"),
                                          "final_annotations.json"))
    markers = common.read_json(os.path.join(common.step_dir(args.project_dir, "step2_markers"),
                                            "markers.json"))
    qc = common.read_json(os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                                       "qc_metrics.json"))
    obs_snap = os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                            "obs_snapshot.csv")
    if not kg or not ann:
        return common.fail("缺少 kg_hits.json / annotations.json(先运行 step3/step4)")

    p = {"batch_key": args.batch_key or ""}
    hr = op_hit_rate(kg, log, p)
    cc = op_candidate_count(kg, log, p)
    fs = op_first_second(ann, log, p)
    be_res = op_batch_entropy(obs_snap, args.batch_key, log, p)
    if isinstance(be_res, dict) and be_res.get("status") == "error":
        return be_res
    mc = op_metadata_check(final, log, p)
    xc = op_cross_cluster(final, markers or {}, qc, hr, fs, be_res, log, p)

    # report generation is folded into the cross_cluster op (step7 defines 6 ops)
    lines = ["# 诊断报告", ""]
    rows = []
    for c in sorted(hr, key=lambda x: (0, int(x)) if str(x).isdigit() else (1, str(x))):
        rows.append((c, hr[c], cc.get(c), fs.get(c, {}), be_res["per_cluster_batch_entropy"].get(c)))
    lines.append("| 簇 | KG 命中率 | 候选数 | first | second | strictly_ahead | 批次熵 |")
    lines.append("|---|---|---|---|---|---|---|")
    for c, hrc, ccc, fsc, be in rows:
        lines.append(f"| {c} | {hrc:.2f} | {ccc} | {fsc.get('first_count')} | "
                     f"{fsc.get('second_count')} | {fsc.get('top_strictly_ahead')} | "
                     f"{be:.2f} |")
    lines.append("")
    lines.append("## 跨簇指标")
    for k, v in xc.items():
        if isinstance(v, dict):
            continue
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 元数据检查")
    lines.append(f"- 缺失字段: {mc['metadata_missing'] or '无'}")
    report_path = os.path.join(out_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "per_cluster_hit_rate": hr,
        "per_cluster_candidate_count": cc,
        "per_cluster_first_second": fs,
        "per_cluster_batch_entropy": be_res["per_cluster_batch_entropy"],
        "per_cluster_max_batch_fraction": be_res["per_cluster_max_batch_fraction"],
        "batch_key_used": be_res["batch_key_used"],
        "metadata_missing": mc["metadata_missing"],
        "cross_cluster": xc,
        "meta": {"date": common.now_iso()[:10]},
    }
    common.write_json(os.path.join(out_dir, "step7_diagnose.json"), payload)
    return common.ok({"step7_diagnose_json": os.path.join(out_dir, "step7_diagnose.json"),
                      "report_md": report_path,
                      "h5ad_loads": 0,
                      "last_exec_run_id": xc.get("run_id")})


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(parser)
    if not args.subcommand:
        parser.print_help()
        return 2
    try:
        result = cmd_run(args)
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"run failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
