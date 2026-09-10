"""Step 6 — validation (tool_design.md §5.6, atomic_operations.md stage 6).

Subcommands:
    run     — marker_expression -> violin_plot -> global_summary -> write_report
              -> write_final. [1× proc h5ad load; --backed reads only the top-3
              marker columns from raw.X, falling back to a full load on failure]
    report  — regenerate report.md from final_annotations.json. [0× h5ad]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step6_validate.py",
        description="Step 6 验证:top marker 表达验证(Cohen's d/AUC/fold_change)、小提琴图、全局汇总、报告",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    p_run = sub.add_parser("run", help="marker_expression~write_final(1× proc 加载)")
    p_run.add_argument("--top-n-markers", type=int, default=3, help="每簇验证的 top marker 数")
    p_run.add_argument("--backed", action="store_true", help="backed 模式按列读 raw.X")
    p_run.add_argument("--no-violin", action="store_true", help="跳过小提琴图生成")
    common.add_common_args(p_run)

    p_rep = sub.add_parser("report", help="从 final_annotations.json 重新生成 report.md(0 加载)")
    common.add_common_args(p_rep)
    return parser


def _load_marker_matrix(h5ad, top_markers, backed: bool):
    """Load (adata, matrix, gene_col_map, used_backed) for the marker columns.

    matrix has one column per gene in ``top_markers`` that exists in raw.var;
    gene_col_map maps that gene name to its column index in matrix.
    backed: read only the needed raw.X columns into memory; on any failure
    fall back to a full in-memory load (tool_design.md §5.6).
    """
    import scanpy as sc

    def build(raw_names, X):
        present = [g for g in top_markers if g in raw_names]
        gene_col_map = {g: j for j, g in enumerate(present)}
        idx = [raw_names.index(g) for g in present]
        return X[:, idx], gene_col_map

    if backed:
        try:
            adata = sc.read_h5ad(h5ad, backed="r")
            raw_names = list(adata.raw.var_names)
            present = [g for g in top_markers if g in raw_names]
            if present:
                # backed slicing already reads only the requested columns
                sub = adata.raw.X[:, [raw_names.index(g) for g in present]]
                if hasattr(sub, "to_memory"):
                    sub = sub.to_memory()
                gene_col_map = {g: j for j, g in enumerate(present)}
                return adata, sub, gene_col_map, True
        except Exception:
            pass
    adata = common.read_h5ad(h5ad)
    sub, gene_col_map = build(list(adata.raw.var_names), adata.raw.X)
    return adata, sub, gene_col_map, False


def _stats_for_gene(matrix, col, mask):
    """pct1/pct2/mean/effect-size for one gene column vs in/out mask."""
    vals = matrix[:, col]
    if hasattr(vals, "toarray"):
        vals = np.asarray(vals.toarray()).ravel()
    else:
        vals = np.asarray(vals).ravel()
    g_in = vals[mask]
    g_out = vals[~mask]
    if len(g_out) == 0:  # cluster == all cells (single-cluster dataset)
        pct2 = 0.0
        mean_out = 0.0
        es = {"cohen_d": 0.0, "auc": None, "fold_change": None, "logfc": None}
    else:
        es = common.effect_size(g_in, g_out)
        pct2 = float((g_out > 0).mean())
        mean_out = float(g_out.mean())
    return {
        "pct1": float((g_in > 0).mean()),
        "pct2": pct2,
        "specificity_index": float((g_in > 0).mean() - pct2),
        "mean_expr": float(g_in.mean()),
        "mean_expr_other": mean_out,
        "expression_ratio": float(g_in.mean() / max(mean_out, 1e-6)),
        "cohen_d": es["cohen_d"],
        "auc": es["auc"],
        "fold_change": es["fold_change"],
        "logfc": es["logfc"],
    }


def op_marker_expression(adata, matrix, gene_col_map, labels, refined, markers_json,
                         kg_hits, top_n, log_path, params) -> dict:
    per_cluster = {}
    label_of = {}
    for c, r in refined["clusters"].items():
        genes = markers_json["per_cluster"].get(c, {}).get("marker_genes", [])[:top_n]
        if not genes:
            genes = [mk["gene"] for mk in markers_json["per_cluster"].get(c, {}).get("markers", [])[:top_n]]
        label = r.get("first_candidate") or {}
        label_name = label.get("cell_type") if isinstance(label, dict) else None
        if r["status"] == "unknown":
            label_name = "unknown"
        label_of[c] = label_name
        mask = labels == c
        exprs = []
        for g in genes:
            if g in gene_col_map:
                exprs.append({"gene": g, **(_stats_for_gene(matrix, gene_col_map[g], mask))})
        mean_pct1 = float(np.mean([e["pct1"] for e in exprs])) if exprs else None
        mean_spec = float(np.mean([e["specificity_index"] for e in exprs])) if exprs else None
        # KG marker overlap score (recall of known markers among DE markers)
        kg_markers = set()
        for cand in (kg_hits.get("per_cluster", {}).get(c, {}).get("candidates", []) or []):
            kg_markers.update(cand.get("supporting_markers", []))
        de_genes = set(markers_json["per_cluster"].get(c, {}).get("marker_genes", []))
        overlap_score = (float(len(de_genes & kg_markers) / max(len(kg_markers), 1))
                         if kg_markers else None)
        per_cluster[c] = {
            "top_markers_expression": exprs,
            "mean_top3_pct1": mean_pct1,
            "mean_top3_specificity": mean_spec,
            "frac_top3_pct1_above_0.5": (float(np.mean([e["pct1"] > 0.5 for e in exprs]))
                                         if exprs else None),
            "marker_gene_overlap_score": overlap_score,
        }
    m = {"per_cluster": per_cluster, "labels": label_of,
         "backed_mode": params.get("backed", False),
         "n_markers_validated": int(sum(len(v["top_markers_expression"]) for v in per_cluster.values()))}
    common.exec_record(log_path, "step6_validate", "marker_expression", params, m)
    return per_cluster, label_of


def op_violin_plot(adata, markers_json, top_n, figures_dir, log_path, params) -> dict:
    import anndata as ad
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import scanpy as sc

    made = []
    for c, info in markers_json["per_cluster"].items():
        genes = [mk["gene"] for mk in info["markers"][:top_n]] or info["marker_genes"][:top_n]
        genes = [g for g in genes if g in adata.raw.var_names]
        if not genes:
            continue
        try:
            expr = np.asarray(adata.raw[:, genes].X.toarray())
            tmp = ad.AnnData(X=expr)
            tmp.obs["cluster"] = adata.obs["leiden"].astype(str).values
            tmp.var_names = genes
            sc.pl.violin(tmp, keys=genes[:3], groupby="cluster", rotation=90, show=False)
            path = os.path.join(figures_dir, f"cluster_{c}_markers.png")
            plt.gcf().savefig(path, dpi=90, bbox_inches="tight")
            plt.close("all")
            made.append(path)
        except Exception:
            pass  # violin is for humans; failure must not break the run
    m = {"n_figures": len(made), "figures_dir": figures_dir}
    common.exec_record(log_path, "step6_validate", "violin_plot", params, m)
    return m


def op_global_summary(refined, expr, annotations, markers_json, log_path, params) -> dict:
    labels = []
    for c, r in refined["clusters"].items():
        first = r.get("first_candidate") or {}
        labels.append(first.get("cell_type") if r["status"] != "unknown" and first else "unknown")
    n_clusters = len(labels)
    n_unknown = int(sum(1 for l in labels if l == "unknown"))
    unique = sorted(set(labels))
    from collections import Counter
    counts = Counter(labels)
    proportions = {k: float(v / n_clusters) for k, v in counts.items()}
    entropy = common.shannon_entropy(list(counts.values()))
    gaps = [r.get("gap_metrics", {}).get("count_ratio")
            for r in refined["clusters"].values() if r.get("gap_metrics")]
    mean_gap = float(np.mean([g for g in gaps if g is not None])) if gaps else None
    specs = [v["mean_top3_specificity"] for v in expr.values() if v.get("mean_top3_specificity") is not None]
    mean_top3_spec = float(np.mean(specs)) if specs else None
    # co-annotation matrix: which clusters share a label
    co = {}
    for l in unique:
        co[l] = [c for c, lab in zip(refined["clusters"].keys(), labels) if lab == l]
    # marker reuse across clusters
    gene_owner = {}
    for c, info in markers_json["per_cluster"].items():
        for g in info["marker_genes"]:
            gene_owner.setdefault(g, []).append(c)
    reuse = {g: owners for g, owners in gene_owner.items() if len(owners) > 1}
    m = {
        "n_clusters": n_clusters,
        "n_unknown": n_unknown,
        "unknown_rate": float(n_unknown / max(n_clusters, 1)),
        "n_unique_labels": len(unique),
        "label_diversity": float(len(unique) / max(n_clusters, 1)),
        "cell_type_proportions": proportions,
        "effective_n_types": float(entropy),
        "mean_first_second_gap": mean_gap,
        "mean_top3_specificity_across_clusters": mean_top3_spec,
        "co_annotation_matrix": co,
        "cross_cluster_marker_reuse": {"n_shared_markers": len(reuse),
                                       "max_owners": max((len(v) for v in reuse.values()), default=0)},
    }
    common.exec_record(log_path, "step6_validate", "global_summary", params, m)
    return m


def op_write_report(report_path, final, log_path, params) -> dict:
    lines = [
        "# 细胞类型注释报告",
        "",
        f"- 数据集:{final.get('_meta', {}).get('dataset_id', '?')}  组织:{final.get('_meta', {}).get('organ', '?')}",
        f"- KG 来源:{final.get('_meta', {}).get('kg_source', '?')}  注释日期:{final.get('_meta', {}).get('annotation_date', '?')}",
        f"- 簇数:{final.get('_summary', {}).get('n_clusters')}  unknown:{final.get('_summary', {}).get('n_unknown')}",
        "",
    ]
    for c in sorted(final["annotations"], key=lambda x: (0, int(x)) if str(x).isdigit() else (1, str(x))):
        a = final["annotations"][c]
        label = a.get("label") or "(judgment pending)"
        confidence = a.get("confidence") or "n/a"
        lines.append(f"## 簇 {c} — {label}(置信度 {confidence})")
        if a.get("first_candidate"):
            lines.append(f"- first: {a['first_candidate']['cell_type']} (marker_count={a['first_count']}, "
                         f"conf={a['first_candidate'].get('mean_confidence')})")
        if a.get("second_candidate"):
            lines.append(f"- second: {a['second_candidate']['cell_type']} (marker_count={a['second_count']})")
        gap = a.get("gap_metrics") or {}
        lines.append(f"- count_ratio: {gap.get('count_ratio')}  count_diff: {gap.get('count_diff')}")
        for mk in a.get("top3_expression", []):
            lines.append(f"  - {mk['gene']}: pct1={mk.get('pct1'):.2f} pct2={mk.get('pct2'):.2f} "
                         f"cohen_d={mk.get('cohen_d'):.2f} auc={mk.get('auc') if mk.get('auc') is None else round(mk['auc'], 3)}")
        if a.get("subcluster"):
            sc_ = a["subcluster"]
            lines.append(f"- refine: {sc_.get('outcome')} 子簇数={sc_.get('n_subclusters')}")
            for sub_id, sr in (sc_.get("sub_results") or {}).items():
                lines.append(f"  - 子簇 {sub_id}: first={sr.get('first_candidate')} ({sr.get('first_count')} markers)")
        lines.append("")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    m = {"report_md": report_path, "n_sections": len(final["annotations"])}
    common.exec_record(log_path, "step6_validate", "write_report", params, m)
    return m


def op_write_final(out_dir, log_path, params, payload) -> dict:
    path = os.path.join(out_dir, "final_annotations.json")
    common.write_json(path, payload)
    m = {"final_annotations_json": path,
         "n_clusters": payload.get("_summary", {}).get("n_clusters")}
    rid = common.exec_record(log_path, "step6_validate", "write_final", params, m)
    m["run_id"] = rid
    return m


def _refined_from_rank(ann_payload: dict) -> dict:
    """Passthrough when step5 was skipped: measurement fields only, no labels."""
    clusters = {}
    for c, a in (ann_payload.get("annotations") or {}).items():
        clusters[c] = {
            "status": "unknown" if a.get("status") == "no_candidates" else "passthrough",
            "first_candidate": a.get("first_candidate"),
            "second_candidate": a.get("second_candidate"),
            "first_count": a.get("first_count"),
            "second_count": a.get("second_count"),
            "gap_metrics": a.get("gap_metrics"),
        }
    return {"clusters": clusters, "counts": {"n_passthrough": len(clusters)},
            "meta": {"source": "step4_rank_passthrough"}}


def cmd_run(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step6_validate")
    figures_dir = os.path.join(out_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    log = common.run_log_path(args.project_dir)
    h5ad = args.input or os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                                      "processed.h5ad")
    refined = common.read_json(os.path.join(common.step_dir(args.project_dir, "step5_refine"),
                                            "refined_annotations.json"))
    markers = common.read_json(os.path.join(common.step_dir(args.project_dir, "step2_markers"),
                                            "markers.json"))
    kg = common.read_json(os.path.join(common.step_dir(args.project_dir, "step3c_kg"),
                                       "kg_hits.json"))
    annotations = common.read_json(os.path.join(common.step_dir(args.project_dir, "step4_rank"),
                                                "annotations.json"))
    log_records = []
    log_path = common.run_log_path(args.project_dir)
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    log_records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    has_step5_exec = any(
        r.get("type") == "exec" and str(r.get("run_id", "")).startswith("step5_refine.")
        for r in log_records
    )
    if not markers:
        return common.fail("缺少 markers.json(先运行 step2_markers run)")
    if has_step5_exec:
        if not isinstance(refined, dict) or "clusters" not in refined:
            return common.fail("step5 已执行但缺少 refined_annotations.json 的 clusters")
    else:
        if not annotations:
            return common.fail("缺少 step4_rank/annotations.json（step5 未跑，无法 passthrough）")
        refined = _refined_from_rank(annotations)
    if not os.path.exists(h5ad):
        return common.fail(f"processed.h5ad 不存在:{h5ad}")

    p = {"top_n_markers": args.top_n_markers, "backed": args.backed}
    err = common.check_positive(args, ["top_n_markers"], "int")
    if err:
        return common.fail(err)
    top_markers = sorted({g for c in markers["per_cluster"] for g in
                          markers["per_cluster"][c]["marker_genes"][:args.top_n_markers]})
    adata, matrix, gene_col_map, used_backed = _load_marker_matrix(h5ad, top_markers, args.backed)
    p["backed"] = used_backed
    labels = adata.obs["leiden"].astype(str).values

    # H6: guard against stale artifacts (step1 re-run changed leiden without
    # re-running step2..5) — fail loudly instead of emitting silent NaN.
    leiden_ids = set(labels)
    refined_ids = set(refined["clusters"].keys())
    missing = sorted(refined_ids - leiden_ids)
    if missing:
        return common.fail(
            f"refined_annotations 的簇 {missing[:10]} 不在 h5ad 的 leiden 标签中——"
            "step1 可能已重跑(聚类变了)而未重跑 step2~5,请重跑 step2~6 或回退到一致的产物。"
        )

    expr, label_of = op_marker_expression(adata, matrix, gene_col_map, labels, refined,
                                          markers, kg or {}, args.top_n_markers, log, p)
    if not args.no_violin and not used_backed:
        op_violin_plot(adata, markers, args.top_n_markers, figures_dir, log, p)

    summary = op_global_summary(refined, expr, annotations or {}, markers, log, p)

    annotations_out = {}
    for c, r in refined["clusters"].items():
        first = r.get("first_candidate") or {}
        second = r.get("second_candidate")
        gap = r.get("gap_metrics") or {}
        annotations_out[c] = {
            "status": r["status"],
            "first_candidate": first if r["status"] != "unknown" and first else None,
            "second_candidate": second,
            "first_count": r.get("first_count"),
            "second_count": r.get("second_count"),
            "gap_metrics": gap,
            "top3_expression": expr.get(c, {}).get("top_markers_expression", []),
            "mean_top3_pct1": expr.get(c, {}).get("mean_top3_pct1"),
            "mean_top3_specificity": expr.get(c, {}).get("mean_top3_specificity"),
            "marker_gene_overlap_score": expr.get(c, {}).get("marker_gene_overlap_score"),
            "subcluster": r.get("subcluster"),
        }

    from importlib.metadata import version as _pkg_version
    try:
        _scanpy_version = _pkg_version("scanpy")
    except Exception:
        _scanpy_version = "unknown"

    # H2: organ must come from step1 (persisted into qc_metrics.json), never
    # hardcoded — fall back to a CLI/None default only if the file is absent.
    qc1 = common.read_json(os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                                        "qc_metrics.json"))
    organ = (qc1 or {}).get("organ", "root")

    payload = {
        "annotations": annotations_out,
        "_summary": summary,
        "_meta": {
            "kg_source": (kg or {}).get("kg_source"),
            "kg_version": (kg or {}).get("kg_version"),
            "organ": organ,
            "annotation_date": common.now_iso()[:10],
            "scanpy_version": _scanpy_version,
            "dataset_id": os.path.basename(h5ad).replace(".h5ad", ""),
            "thresholds": {"top_n_markers": args.top_n_markers},
        },
    }
    op_write_report(os.path.join(out_dir, "report.md"), payload, log, p)
    m = op_write_final(out_dir, log, p, payload)
    return common.ok({"final_annotations_json": m["final_annotations_json"],
                      "report_md": os.path.join(out_dir, "report.md"),
                      "backed_mode": used_backed,
                      **{k: v for k, v in summary.items() if k in
                         ("n_clusters", "n_unknown", "unknown_rate", "n_unique_labels", "label_diversity")},
                      "last_exec_run_id": m["run_id"]})


def cmd_report(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step6_validate")
    log = common.run_log_path(args.project_dir)
    final = common.read_json(os.path.join(out_dir, "final_annotations.json"))
    if not final:
        return common.fail("缺少 final_annotations.json,请先运行 step6_validate run")
    p = {}
    m = op_write_report(os.path.join(out_dir, "report.md"), final, log, p)
    return common.ok({"report_md": m["report_md"]})


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(parser)
    if not args.subcommand:
        parser.print_help()
        return 2
    try:
        if args.subcommand == "report":
            result = cmd_report(args)
        else:
            result = cmd_run(args)
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"{args.subcommand} failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
