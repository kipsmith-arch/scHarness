"""Step 2 — marker discovery (tool_design.md §5.2, atomic_operations.md stage 2).

Subcommand:
    run  — de_rank -> pct1_pct2 -> filter_markers -> [pseudobulk_de] -> write_markers.
           [1× proc h5ad load]

Extended metrics computed in-passing: BH-FDR, per-gene AUC (rank-based),
genomic inflation factor λ, DE distributions, filter funnel, grey zone.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step2_markers.py",
        description="Step 2 Marker 发现:DE 排序(BH-FDR/AUC/λ)、pct1/pct2、过滤漏斗、pseudobulk",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    p_run = sub.add_parser("run", help="DE + pct1/pct2 + 过滤 + 写出 markers")
    p_run.add_argument("--n-genes", type=int, default=500, help="每簇 DE top-N 基因")
    p_run.add_argument("--min-pct1", type=float, default=0.5, help="marker 最小 pct1")
    p_run.add_argument("--max-pct1", type=float, default=0.9, help="marker 最大 pct1(排除管家基因)")
    p_run.add_argument("--min-pct1-pct2", type=float, default=0.25, help="最小 pct1-pct2 特异性")
    p_run.add_argument("--top-n", type=int, default=30, help="每簇最终保留 marker 数")
    p_run.add_argument("--rare-threshold", type=float, default=0.05, help="稀有簇判定阈值(细胞比例)")
    p_run.add_argument("--use-pseudobulk-for-rare", action="store_true",
                       help="稀有簇切换 pseudobulk DE")
    p_run.add_argument("--pseudobulk-min-samples", type=int, default=2,
                       help="pseudobulk 每组的样本数下限")
    p_run.add_argument("--batch-key", default="Orig.ident", help="样本列名(pseudobulk 聚合用)")
    common.add_common_args(p_run)
    return parser


# ---------------------------------------------------------------------------
# DE helpers
# ---------------------------------------------------------------------------

def _rank_genes(adata, groupby, n_genes, method="wilcoxon"):
    """Run scanpy DE; returns per-cluster tables of names/logfc/pval/pval_adj.

    ``pval_adj`` is scanpy's BH-FDR computed over ALL tested genes (the correct
    correction scope); singleton clusters (1 cell) are skipped (no DE possible).
    """
    import scanpy as sc

    sizes = adata.obs[groupby].astype(str).value_counts()
    sc.tl.rank_genes_groups(adata, groupby=groupby, method=method,
                            use_raw=True, n_genes=n_genes)
    rgg = adata.uns["rank_genes_groups"]
    groups = list(rgg["names"].dtype.names)
    tables = {}
    for g in groups:
        if int(sizes.get(g, 0)) < 2:
            continue  # no DE possible; cluster stays marker-less (C7)
        tables[str(g)] = {
            "names": [str(x) for x in rgg["names"][g]],
            "logfc": [float(x) for x in rgg["logfoldchanges"][g]],
            "pval": [float(x) for x in rgg["pvals"][g]],
            "pval_adj": [float(x) for x in rgg["pvals_adj"][g]],
        }
    return tables


def _gene_auc(raw_matrix, gene_cols, mask):
    """Rank-based AUC per gene column (in-cluster vs out-of-cluster)."""
    from scipy.stats import rankdata

    n_in = int(mask.sum())
    n_out = len(mask) - n_in
    if n_in == 0 or n_out == 0 or not gene_cols:
        return [None] * len(gene_cols)
    scores = raw_matrix[:, gene_cols].toarray()  # cells × genes
    ranks = rankdata(scores, axis=0)
    mean_rank_in = ranks[mask, :].mean(axis=0)
    aucs = (mean_rank_in - (n_in + 1) / 2.0) / n_out
    return [float(a) for a in aucs]


def _inflation_lambda(pvals):
    from scipy.stats import chi2

    pvals = np.asarray(pvals, dtype=float)
    pvals = pvals[(pvals > 0) & (pvals < 1)]
    if len(pvals) == 0:
        return None
    chi2_obs = chi2.isf(pvals, df=1)
    return float(np.median(chi2_obs) / 0.4549)


def _pct1_pct2(raw_matrix, gene_cols, mask):
    """pct1 (in-cluster expression fraction) / pct2 (rest) per gene column."""
    n_in = int(mask.sum())
    n_out = len(mask) - n_in
    if not gene_cols:
        return [None] * 0, [None] * 0
    sub_in = raw_matrix[mask][:, gene_cols]
    pct1 = np.asarray((sub_in > 0).mean(axis=0)).ravel()
    if n_out:
        sub_out = raw_matrix[~mask][:, gene_cols]
        pct2 = np.asarray((sub_out > 0).mean(axis=0)).ravel()
    else:
        pct2 = np.zeros(len(gene_cols))
    return [float(a) for a in pct1], [float(b) for b in pct2]


def _pseudobulk_de(adata, cluster_id, batch_col, min_samples):
    """Pseudobulk t-test: aggregate raw counts per sample, log1p-CPM, t-test.

    Returns dict with names/logfc/pval/pval_adj_bh (top-500) or None when a
    group has fewer than ``min_samples`` samples.
    """
    from scipy.stats import ttest_ind
    from statsmodels.stats.multitest import multipletests

    groups = adata.obs["leiden"].astype(str).values
    samples = adata.obs[batch_col].astype(str).values
    in_mask = groups == str(cluster_id)
    cluster_samples = set(samples[in_mask])
    rest_samples = set(samples[~in_mask])
    if len(cluster_samples) < min_samples or len(rest_samples) < min_samples:
        return None
    X = adata.raw.X
    genes = list(adata.raw.var_names)

    def agg(sample_set):
        rows = [i for i, s in enumerate(samples) if s in sample_set]
        sub = X[rows]
        sums = np.asarray(sub.sum(axis=0)).ravel()
        lib = sums.sum() + 1e-9
        return np.log1p(sums / lib * 1e6)

    agg_in = np.vstack([agg({s}) for s in sorted(cluster_samples)])
    agg_out = np.vstack([agg({s}) for s in sorted(rest_samples)])
    logfc = agg_in.mean(0) - agg_out.mean(0)
    pvals = np.full(len(genes), 1.0)
    for i in range(len(genes)):
        t, p = ttest_ind(agg_in[:, i], agg_out[:, i], equal_var=False)
        pvals[i] = p if not np.isnan(p) else 1.0
    bh = multipletests(pvals, method="fdr_bh")[1]
    order = np.argsort(pvals)
    top = min(500, len(genes))
    return {
        "names": [genes[i] for i in order[:top]],
        "logfc": [float(logfc[i]) for i in order[:top]],
        "pval": [float(pvals[i]) for i in order[:top]],
        "pval_adj_bh": [float(bh[i]) for i in order[:top]],
        "n_pseudobulk_samples": len(cluster_samples),
    }


# ---------------------------------------------------------------------------
# ops (each appends one exec record)
# ---------------------------------------------------------------------------

def op_de_rank(adata, log_path, params, n_genes) -> dict:
    tables = _rank_genes(adata, "leiden", n_genes)
    raw = adata.raw.X
    all_genes = list(adata.raw.var_names)
    gene_index = {g: i for i, g in enumerate(all_genes)}
    labels = adata.obs["leiden"].astype(str).values
    de = {}
    for c, t in tables.items():
        cols = [gene_index[g] for g in t["names"] if g in gene_index]
        mask = labels == c
        aucs = _gene_auc(raw, cols, mask)
        pcts = _pct1_pct2(raw, cols, mask)
        from statsmodels.stats.multitest import multipletests
        pvals = np.asarray(t["pval"], dtype=float)
        # BH must be applied over ALL tested genes; scanpy already did this and
        # returned it in ``pval_adj`` — reuse it (B1). The raw-pval recompute
        # below is only kept as a cross-check on the top-N subset.
        bh = [float(x) for x in t["pval_adj"]]
        dist_logfc = common.describe_distribution(t["logfc"])
        dist_pval = common.describe_distribution(t["pval"])
        lf = np.asarray(t["logfc"], dtype=float)
        top_gap = None
        if len(lf) >= 3:
            top_gap = {"rank1_minus_rank2": float(lf[0] - lf[1]),
                       "rank2_minus_rank3": float(lf[1] - lf[2])}
        de[c] = {
            "names": t["names"], "logfc": t["logfc"], "pval": t["pval"],
            "pval_adj_bh": [float(x) for x in bh],
            "pval_adj_scanpy": t["pval_adj"],
            "auc": aucs, "pct1": pcts[0], "pct2": pcts[1],
            "pct1_minus_pct2": [float(a - b) for a, b in zip(pcts[0], pcts[1])],
            "distributions": {
                "logfc_distribution": dist_logfc,
                "pval_distribution": dist_pval,
                "auc_distribution": common.describe_distribution([a for a in aucs if a is not None]),
            },
            "n_genes_tested": len(all_genes),
            "genomic_inflation_factor_lambda": _inflation_lambda(pvals),
            "n_significant": {
                "fdr_0.05": int(sum(1 for x in bh if x < 0.05)),
                "fdr_0.01": int(sum(1 for x in bh if x < 0.01)),
                "fdr_0.001": int(sum(1 for x in bh if x < 0.001)),
            },
            "top_marker_logfc_gap": top_gap,
            "frac_positive_logfc": float((lf > 0).mean()),
        }
    # 轨迹指标瘦身:只记 top-15 基因摘要 + 分布/统计指标(完整 DE 表是中间计算,
    # 不进轨迹 —— 设计判据:轨迹记指标,产物文件存数据)
    summary = {}
    for c, t in de.items():
        top_n = 15
        n = min(top_n, len(t["names"]))
        summary[c] = {
            "top_genes": [
                {"name": t["names"][i], "logfc": t["logfc"][i],
                 "pval_adj": t["pval_adj_bh"][i], "auc": t["auc"][i],
                 "pct1": t["pct1"][i], "pct2": t["pct2"][i]}
                for i in range(n)
            ],
            "distributions": t["distributions"],
            "n_genes_tested": t["n_genes_tested"],
            "genomic_inflation_factor_lambda": t["genomic_inflation_factor_lambda"],
            "n_significant": t["n_significant"],
            "top_marker_logfc_gap": t["top_marker_logfc_gap"],
            "frac_positive_logfc": t["frac_positive_logfc"],
        }
    m = {"de_method": "wilcoxon",
         "de_distribution": {c: v["distributions"] for c, v in de.items()},
         "per_cluster": summary}
    common.exec_record(log_path, "step2_markers", "de_rank", params, m)
    return de


def op_pct1_pct2(adata, log_path, params, de) -> dict:
    per = {}
    for c, t in de.items():
        p1 = np.asarray([x for x in t["pct1"] if x is not None], dtype=float)
        p2 = np.asarray([x for x in t["pct2"] if x is not None], dtype=float)
        spec = np.asarray([x for x in t["pct1_minus_pct2"] if x is not None], dtype=float)
        per[c] = {
            "pct1_distribution": common.describe_distribution(p1),
            "pct2_distribution": common.describe_distribution(p2),
            "specificity_distribution": common.describe_distribution(spec),
            "mean_specificity_of_top_N_markers": float(spec[:50].mean()) if len(spec) else None,
        }
    m = {"per_cluster": per}
    common.exec_record(log_path, "step2_markers", "pct1_pct2", params, m)
    return m


def op_pseudobulk_de(adata, log_path, params, pb_tables) -> dict:
    """Exec record for the pseudobulk step (tables already computed in cmd_run)."""
    info = {}
    n_rare = 0
    for c, t in pb_tables.items():
        n_rare += 1
        info[c] = {"is_rare": True, "de_method": "pseudobulk",
                   "n_pseudobulk_samples": t.get("n_pseudobulk_samples")}
    m = {"per_cluster": info, "n_rare_clusters": n_rare}
    common.exec_record(log_path, "step2_markers", "pseudobulk_de", params, m)
    return m


def op_filter_markers(adata, log_path, params, de, min_pct1, max_pct1, min_diff, top_n,
                      pb_tables, rare_threshold) -> dict:
    per_cluster = {}
    funnel_global = {}
    full_kept_markers = {}
    for c, t in de.items():
        n_before = len(t["names"])
        is_pb = c in pb_tables
        if is_pb:
            pb_names = set(pb_tables[c]["names"][:500])
            kept = np.array([g in pb_names for g in t["names"]], dtype=bool)
            grey = np.zeros(len(t["names"]), dtype=bool)
            funnel = common.filter_funnel([kept], ["pseudobulk_sig"])
            n_grey = 0
            de_method = "pseudobulk"
        else:
            p1 = np.asarray(t["pct1"], dtype=float)
            p2 = np.asarray(t["pct2"], dtype=float)
            spec = np.asarray(t["pct1_minus_pct2"], dtype=float)
            mask_min = p1 >= min_pct1
            mask_max = p1 <= max_pct1
            mask_spec = spec >= min_diff
            funnel = common.filter_funnel([mask_min, mask_max, mask_spec],
                                          ["pct1_min", "pct1_max", "specificity"])
            kept = mask_min & mask_max & mask_spec
            grey = (spec >= 0.1) & (spec < min_diff)
            n_grey = int(grey.sum())
            de_method = "wilcoxon"

        n_markers = int(kept.sum())
        markers = []
        for i, g in enumerate(t["names"]):
            status = "kept" if kept[i] else ("grey" if grey[i] else "dropped")
            markers.append({
                "gene": g, "logfc": t["logfc"][i], "pval": t["pval"][i],
                "pval_adj": t["pval_adj_bh"][i], "auc": t["auc"][i],
                "pct1": t["pct1"][i], "pct2": t["pct2"][i],
                "pct1_minus_pct2": t["pct1_minus_pct2"][i], "status": status,
            })
        kept_markers = [mk for mk in markers if mk["status"] == "kept"][:top_n]
        kept_genes = [mk["gene"] for mk in kept_markers]
        full_kept_markers[c] = kept_markers  # 完整列表(产物用,非轨迹)
        is_rare = (adata.obs["leiden"].astype(str).values == c).sum() / adata.n_obs < rare_threshold
        # 轨迹指标瘦身:markers 只记 top-5 摘要(完整列表是 markers.json 的产物数据)
        summary_markers = [
            {"gene": mk["gene"], "logfc": mk["logfc"], "pct1": mk["pct1"],
             "pct2": mk["pct2"], "pct1_minus_pct2": mk["pct1_minus_pct2"]}
            for mk in kept_markers[:5]
        ]
        per_cluster[c] = {
            "markers": summary_markers,
            "marker_genes": kept_genes,
            "n_markers": len(kept_markers),
            "n_grey_zone": n_grey,
            "n_before_filter": n_before,
            "filter_funnel": funnel,
            "filter_efficiency": float(len(kept_markers) / max(n_before, 1)),
            "grey_zone_rate": float(n_grey / max(n_before, 1)),
            "is_rare": bool(is_rare),
            "de_method": de_method,
        }
        funnel_global[c] = funnel
    m = {"per_cluster": per_cluster, "filter_funnel": funnel_global,
         "thresholds": {"min_pct1": min_pct1, "max_pct1": max_pct1,
                        "min_pct1_pct2": min_diff, "top_n": top_n}}
    common.exec_record(log_path, "step2_markers", "filter_markers", params, m)
    # 返回完整 per_cluster(供 op_write_markers 写产物 markers.csv/json)
    full = {"per_cluster": {c: dict(v) for c, v in per_cluster.items()}}
    for c in full["per_cluster"]:
        full["per_cluster"][c]["markers"] = full_kept_markers[c]
    return full


def op_write_markers(out_dir, log_path, params, markers) -> dict:
    import pandas as pd

    rows = []
    for c, info in markers["per_cluster"].items():
        for mk in info["markers"]:
            rows.append({"cluster": c, **mk})
    csv_path = os.path.join(out_dir, "markers.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path = os.path.join(out_dir, "markers.json")
    common.write_json(json_path, markers)
    m = {"markers_csv": csv_path, "markers_json": json_path,
         "n_clusters": len(markers["per_cluster"]),
         "n_markers_total": int(sum(v["n_markers"] for v in markers["per_cluster"].values()))}
    rid = common.exec_record(log_path, "step2_markers", "write_markers", params, m)
    m["run_id"] = rid
    return m


def cmd_run(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step2_markers")
    log = common.run_log_path(args.project_dir)
    h5ad = args.input or os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                                      "processed.h5ad")
    if not os.path.exists(h5ad):
        return common.fail(f"processed.h5ad 不存在:{h5ad}")
    adata = common.read_h5ad(h5ad)
    if "leiden" not in adata.obs:
        return common.fail("h5ad 缺少 leiden 列(请先运行 step1_prepare run)")
    if adata.raw is None:
        return common.fail("h5ad 缺少 raw(请确认 step1_prepare run 的归一化已执行)")

    p = {"n_genes": args.n_genes, "min_pct1": args.min_pct1, "max_pct1": args.max_pct1,
         "min_pct1_pct2": args.min_pct1_pct2, "top_n": args.top_n,
         "rare_threshold": args.rare_threshold,
         "use_pseudobulk_for_rare": args.use_pseudobulk_for_rare,
         "batch_key": args.batch_key}

    err = common.check_positive(args, ["n_genes", "top_n", "pseudobulk_min_samples"], "int")
    err = err or common.check_positive(args, ["rare_threshold"], "float")
    if err:
        return common.fail(err)
    if not (0 <= args.min_pct1 < args.max_pct1 <= 1):
        return common.fail(f"marker 阈值非法:需 0 ≤ min_pct1 < max_pct1 ≤ 1(当前 {args.min_pct1} / {args.max_pct1})")
    if not (0 <= args.min_pct1_pct2 <= 1):
        return common.fail(f"min_pct1_pct2 需在 [0,1](当前 {args.min_pct1_pct2})")

    de = op_de_rank(adata, log, p, args.n_genes)
    op_pct1_pct2(adata, log, p, de)

    # rare-cluster pseudobulk tables (conditional)
    pb_tables = {}
    if args.use_pseudobulk_for_rare:
        labels = adata.obs["leiden"].astype(str).values
        for c in de:
            if (labels == c).sum() / adata.n_obs < args.rare_threshold:
                res = _pseudobulk_de(adata, c, args.batch_key, args.pseudobulk_min_samples)
                if res is not None:
                    pb_tables[c] = res
    op_pseudobulk_de(adata, log, p, pb_tables)

    # merge pseudobulk tables into the DE view used for filtering
    de_merged = dict(de)
    for c, t in pb_tables.items():
        if c not in de_merged:
            continue
        merged = dict(de_merged[c])
        names = t["names"]
        merged["names"] = names
        merged["logfc"] = t["logfc"]
        merged["pval"] = t["pval"]
        merged["pval_adj_bh"] = t["pval_adj_bh"]
        merged["pval_adj"] = t["pval_adj_bh"]
        merged["auc"] = [None] * len(names)
        merged["pct1"] = [None] * len(names)
        merged["pct2"] = [None] * len(names)
        merged["pct1_minus_pct2"] = [None] * len(names)
        de_merged[c] = merged

    markers = op_filter_markers(adata, log, p, de_merged, args.min_pct1, args.max_pct1,
                                args.min_pct1_pct2, args.top_n, pb_tables, args.rare_threshold)
    markers["de_distribution"] = {c: de[c]["distributions"] for c in de}
    markers["pseudobulk"] = {"per_cluster": {c: {"n_pseudobulk_samples": t.get("n_pseudobulk_samples"),
                                                 "de_method": "pseudobulk"} for c, t in pb_tables.items()},
                             "n_rare_clusters": len(pb_tables)}
    m = op_write_markers(out_dir, log, p, markers)
    return common.ok({"markers_csv": m["markers_csv"], "markers_json": m["markers_json"],
                      "n_clusters": m["n_clusters"], "n_markers_total": m["n_markers_total"],
                      "last_exec_run_id": m["run_id"]})


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
