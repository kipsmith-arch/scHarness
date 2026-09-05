"""Step 1 — data preparation (tool_design.md §5.1, atomic_operations.md stage 1).

Subcommands:
    metrics    — load + QC variables + QC distributions + QC plot (ops 01-04).
                  [1× raw h5ad load]
    run        — full pipeline: load_data -> write_output, all 16 ops in ONE
                  h5ad load, computing every Step-1 metric in-passing.
                  [1× raw h5ad load]
    recluster  — re-run Leiden clustering on processed.h5ad (ops 12,13,14,16).
                  [1× proc h5ad load]

Plant-specialized QC: ``pct_counts_chloroplast`` (ATCG) is computed and
filtered alongside ``pct_counts_mt``. Every atomic op appends an ``exec``
record to run_log.jsonl (run_id = step1_prepare.{op}#{attempt}).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step1_prepare.py",
        description="Step 1 数据准备:QC、过滤、双峰、归一化、HVG、PCA、聚类、UMAP、批次评估",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    def add_qc_args(sp):
        sp.add_argument("--organ", default="root", help="组织(默认 root)")
        sp.add_argument("--batch-key", default="Orig.ident", help="批次列名(默认 Orig.ident)")
        sp.add_argument("--mt-pattern", default="^(ATMG|MT-)", help="线粒体基因前缀正则")
        sp.add_argument("--cp-pattern", default="^ATCG", help="叶绿体基因前缀正则(植物)")
        sp.add_argument("--seed", type=int, default=0, help="随机种子")

    p_metrics = sub.add_parser("metrics", help="加载 + QC 变量 + 分布统计 + QC 图(ops 01-04)")
    add_qc_args(p_metrics)

    p_run = sub.add_parser("run", help="完整流水线:load_data~write_output(16 op 单次加载)")
    add_qc_args(p_run)
    p_run.add_argument("--min-genes", type=int, default=300, help="细胞最小检测基因数")
    p_run.add_argument("--max-mt-pct", type=float, default=15.0, help="线粒体占比上限")
    p_run.add_argument("--max-chloroplast-pct", type=float, default=15.0, help="叶绿体占比上限(植物)")
    p_run.add_argument("--min-cells", type=int, default=3, help="基因最小表达细胞数")
    p_run.add_argument("--expected-doublet-rate", type=float, default=0.06, help="scrublet 期望双峰率")
    p_run.add_argument("--target-sum", type=float, default=1e4, help="归一化 target_sum")
    p_run.add_argument("--n-top-genes", type=int, default=2000, help="HVG 数量")
    p_run.add_argument("--hvg-batch-key", default=None, help="分批选 HVG 的批次列(可选)")
    p_run.add_argument("--n-comps", type=int, default=50, help="PCA 主成分数")
    p_run.add_argument("--n-neighbors", type=int, default=15, help="kNN 邻居数")
    p_run.add_argument("--n-pcs", type=int, default=30, help="kNN 使用的 PC 数")
    p_run.add_argument("--resolution-list", default="0.4,0.6,0.8,1.0,1.2", help="Leiden 分辨率列表(逗号分隔)")
    p_run.add_argument("--target-resolution", default=None, help="选中的分辨率(必须由判断层显式给出,禁止 knee 静默选定)")

    p_re = sub.add_parser("recluster", help="对 processed.h5ad 重聚类(ops 12-14,16)")
    add_qc_args(p_re)
    p_re.add_argument("--resolution-list", default="0.4,0.6,0.8,1.0,1.2", help="Leiden 分辨率列表")
    p_re.add_argument("--target-resolution", default=None, help="选中的分辨率(必须由判断层显式给出,禁止 knee 静默选定)")
    p_re.add_argument("--n-neighbors", type=int, default=15, help="kNN 邻居数")
    p_re.add_argument("--n-pcs", type=int, default=30, help="kNN 使用的 PC 数")

    for sp in (p_metrics, p_run, p_re):
        common.add_common_args(sp)
    return parser


# ---------------------------------------------------------------------------
# op implementations (each returns the metrics dict for its exec record)
# ---------------------------------------------------------------------------

def _mark_organelle_genes(adata, mt_pattern: str, cp_pattern: str) -> None:
    """Mark mitochondrial / chloroplast genes into var columns."""
    mt_re = re.compile(mt_pattern)
    cp_re = re.compile(cp_pattern)
    adata.var["mt"] = [bool(mt_re.match(g)) for g in adata.var_names]
    adata.var["chloroplast"] = [bool(cp_re.match(g)) for g in adata.var_names]


def _qc_vars(adata, qc_vars, mt_pattern: str, cp_pattern: str) -> dict:
    """Per-QC-variable distribution summaries + gini (total_counts)."""
    import scanpy as sc

    _mark_organelle_genes(adata, mt_pattern, cp_pattern)
    sc.pp.calculate_qc_metrics(adata, qc_vars=qc_vars, inplace=True)
    out = {}
    for v in ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]:
        if v in adata.obs:
            dist = common.describe_distribution(adata.obs[v].values)
            if v == "total_counts" and dist is not None:
                dist["gini"] = common.gini_coefficient(adata.obs[v].values)
            out[v] = dist
    return out


def op_load_data(adata, log_path, params) -> dict:
    m = {"n_cells": int(adata.n_obs), "n_genes": int(adata.n_vars),
         "raw_available": adata.raw is not None}
    common.exec_record(log_path, "step1_prepare", "load_data", params, m)
    return m


def op_compute_qc(adata, log_path, params, qc_vars, mt_pattern, cp_pattern) -> dict:
    m = {"n_cells": int(adata.n_obs), "n_genes": int(adata.n_vars),
         "qc_variables": _qc_vars(adata, qc_vars, mt_pattern, cp_pattern),
         "n_mt_genes": int(adata.var["mt"].sum()) if "mt" in adata.var else None,
         "n_chloroplast_genes": int(adata.var["chloroplast"].sum()) if "chloroplast" in adata.var else None}
    common.exec_record(log_path, "step1_prepare", "compute_qc", params, m)
    return m


def op_qc_distribution(adata, log_path, params) -> dict:
    from scipy.stats import norm, kstest

    dists = {}
    for v in ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]:
        if v not in adata.obs:
            continue
        vals = adata.obs[v].values.astype(float)
        d = common.describe_distribution(vals)
        if d is None:
            continue
        # valley detection for bimodal distributions
        valley = None
        if d["bimodality_coefficient"] is not None and d["bimodality_coefficient"] > 0.555:
            counts = np.array(d["histogram"])
            edges = common.histogram_bin_edges(d)
            if edges is not None and len(counts) >= 4:
                i_max = int(np.argmax(counts))
                # find the min count between the two largest peaks
                order = np.argsort(counts)[::-1]
                i_second = order[1] if order[1] != i_max else order[2] if len(order) > 2 else i_max
                lo, hi = sorted([i_max, i_second])
                seg = counts[lo:hi + 1]
                if len(seg) >= 2:
                    i_valley = lo + int(np.argmin(seg))
                    valley = float((edges[i_valley] + edges[i_valley + 1]) / 2)
        d["valley_detection"] = valley
        d["tail_fraction"] = float(d["percentiles"]["p99"] - d["percentiles"]["p90"])
        try:
            z = (vals - vals.mean()) / (vals.std() + 1e-9)
            d["ks_statistic_vs_normal"] = float(kstest(z, norm.cdf).statistic)
        except Exception:
            d["ks_statistic_vs_normal"] = None
        dists[v] = d
    m = {"pre_filter_distributions": dists}
    common.exec_record(log_path, "step1_prepare", "qc_distribution", params, m)
    return m


def op_qc_plot(adata, out_dir, log_path, params) -> dict:
    """Histogram PNG of the QC variables (for humans; LLM reads the JSON)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        axes = axes.ravel()
        for i, v in enumerate(["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]):
            if v in adata.obs and i < len(axes):
                axes[i].hist(adata.obs[v].values.astype(float), bins=50)
                axes[i].set_title(v)
        fig.tight_layout()
        path = os.path.join(out_dir, "qc_distributions.png")
        fig.savefig(path, dpi=100)
        plt.close(fig)
        m = {"plot_path": path}
    except Exception as exc:
        m = {"plot_path": None, "error": str(exc)}
    common.exec_record(log_path, "step1_prepare", "qc_plot", params, m)
    return m


def op_filter_cells(adata, log_path, params, min_genes, max_mt_pct, max_cp_pct) -> dict:
    from scipy.stats import ks_2samp

    n_before = adata.n_obs
    obs_before = adata.obs.copy()
    masks = []
    labels = []
    if min_genes:
        masks.append(adata.obs["n_genes_by_counts"].values >= min_genes)
        labels.append("min_genes")
    if max_mt_pct is not None:
        masks.append(adata.obs["pct_counts_mt"].values < max_mt_pct)
        labels.append("max_mt_pct")
    if max_cp_pct is not None:
        masks.append(adata.obs["pct_counts_chloroplast"].values < max_cp_pct)
        labels.append("max_chloroplast_pct")
    funnel = common.filter_funnel(masks, labels)
    keep = np.ones(n_before, dtype=bool)
    for msk in masks:
        keep &= msk
    adata._inplace_subset_obs(keep)

    # distribution shift between pre/post filter
    shift = {}
    for v in ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]:
        if v not in adata.obs:
            continue
        pre = obs_before[v].values.astype(float)
        post = adata.obs[v].values.astype(float)
        d_pre, d_post = common.describe_distribution(pre), common.describe_distribution(post)
        if d_pre is None or d_post is None:
            continue
        shift[v] = {
            "delta_median": float(d_post["median"] - d_pre["median"]),
            "delta_iqr": float(d_post["iqr"] - d_pre["iqr"]),
            "delta_skewness": float((d_post["skewness"] or 0) - (d_pre["skewness"] or 0)),
            "ks_statistic": float(ks_2samp(pre, post).statistic),
        }
    m = {
        "n_cells_before": n_before,
        "n_cells_after": int(adata.n_obs),
        "frac_cells_lost": float(1 - adata.n_obs / max(n_before, 1)),
        "funnel": funnel,
        "distribution_shift": shift,
        "post_filter_distributions": {
            v: common.describe_distribution(adata.obs[v].values)
            for v in ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]
            if v in adata.obs
        },
    }
    common.exec_record(log_path, "step1_prepare", "filter_cells", params, m)
    return m


def op_filter_genes(adata, log_path, params, min_cells, drop_organelles=True) -> dict:
    n_before = adata.n_vars
    X = adata.X
    n_cells_expr = np.asarray((X > 0).sum(axis=0)).ravel() if hasattr(X, "toarray") else (X > 0).sum(axis=0)
    breadth = (n_cells_expr / adata.n_obs).astype(float)

    keep = n_cells_expr >= min_cells
    if drop_organelles:
        keep &= ~(adata.var["mt"].values | adata.var["chloroplast"].values)
    n_mt_total = int(adata.var["mt"].sum()) if drop_organelles else 0
    n_cp_total = int(adata.var["chloroplast"].sum()) if drop_organelles else 0
    adata._inplace_subset_var(keep)

    n_after = adata.n_vars
    m = {
        "n_genes_before": n_before,
        "n_genes_after": int(n_after),
        "frac_genes_lost": float(1 - n_after / max(n_before, 1)),
        "n_mt_removed": n_mt_total,
        "n_cp_removed": n_cp_total,
        "n_genes_removed_by_min_cells": int((n_cells_expr < min_cells).sum()),
        "expression_breadth_distribution": common.describe_distribution(breadth),
        "n_genes_in_lt_1pct_cells": int((breadth < 0.01).sum()),
    }
    common.exec_record(log_path, "step1_prepare", "filter_genes", params, m)
    return m


def op_detect_doublets(adata, log_path, params, expected_doublet_rate) -> dict:
    import scrublet

    m = {"de_method": "scrublet", "n_before": int(adata.n_obs)}
    try:
        scr = scrublet.Scrublet(adata.X, expected_doublet_rate=expected_doublet_rate)
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):  # scrublet prints summary lines
            doublet_scores, predicted = scr.scrub_doublets(verbose=False)
            try:
                scr.call_doublets()
                predicted = scr.predicted_doublets_
                threshold = float(scr.threshold_)
            except Exception:
                threshold = None
        adata.obs["doublet_score"] = doublet_scores
        adata.obs["predicted_doublet"] = np.asarray(predicted).astype(bool)
        keep = ~adata.obs["predicted_doublet"].values
        n_detected = int((~keep).sum())
        adata._inplace_subset_obs(keep)
        score_dist = common.describe_distribution(doublet_scores)
        m.update({
            "n_doublets_detected": n_detected,
            "frac_doublets": float(n_detected / max(m["n_before"], 1)),
            "doublet_score_distribution": score_dist,
            "doublet_score_bimodality": score_dist.get("bimodality_coefficient") if score_dist else None,
            "implied_threshold": threshold,
            "n_after": int(adata.n_obs),
        })
    except Exception as exc:
        # scrublet failure must not silently corrupt the run: keep cells, log it.
        if "doublet_score" not in adata.obs:
            adata.obs["doublet_score"] = np.nan
            adata.obs["predicted_doublet"] = False
        m.update({"error": str(exc), "n_doublets_detected": 0, "frac_doublets": 0.0,
                  "implied_threshold": None, "n_after": int(adata.n_obs)})
    common.exec_record(log_path, "step1_prepare", "detect_doublets", params, m)
    return m


def op_normalize(adata, log_path, params, target_sum) -> dict:
    import scanpy as sc

    library_sizes = np.asarray(adata.X.sum(axis=1)).ravel()
    m = {"median_library_size_before": float(np.median(library_sizes)),
         "normalization_target": float(target_sum)}
    adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=target_sum)
    sc.pp.log1p(adata)
    adata.raw = adata.copy()
    m["frac_zero_after_norm"] = float(1 - adata.X.nnz / max(adata.X.shape[0] * adata.X.shape[1], 1))
    m["post_norm_mean_expression_distribution"] = common.describe_distribution(
        np.asarray(adata.X.mean(axis=0)).ravel())
    common.exec_record(log_path, "step1_prepare", "normalize", params, m)
    return m


def op_select_hvg(adata, log_path, params, n_top_genes, hvg_batch_key) -> dict:
    import scanpy as sc

    n_before = adata.n_vars
    kwargs = {}
    if hvg_batch_key:
        kwargs["batch_key"] = hvg_batch_key
    # compute on the full var table first so the non-HVG dispersion gap is
    # measurable, then subset in place
    sc.pp.highly_variable_genes(adata, flavor="seurat", n_top_genes=n_top_genes,
                                subset=False, **kwargs)
    disp_all = adata.var["dispersions_norm"].values
    hv_mask = adata.var["highly_variable"].values.astype(bool)
    gap = None
    if disp_all.size and hv_mask.any() and (~hv_mask).any():
        gap = float(np.median(disp_all[hv_mask]) - np.median(disp_all[~hv_mask]))
    adata._inplace_subset_var(hv_mask)
    n_after = adata.n_vars
    disp = adata.var["dispersions_norm"].values if "dispersions_norm" in adata.var else None
    means = adata.var["means"].values if "means" in adata.var else None
    m = {
        "n_genes_before_hvg": n_before,
        "n_genes_after": int(n_after),
        "frac_hvg_of_total": float(n_after / max(n_before, 1)),
        "hvg_dispersion_distribution": common.describe_distribution(disp) if disp is not None else None,
        "hvg_mean_expression_distribution": common.describe_distribution(means) if means is not None else None,
        "hvg_nongvg_dispersion_gap": gap,
        "n_batch_specific_hvg": (int(adata.var["highly_variable_nbatches"].sum())
                                 if hvg_batch_key and "highly_variable_nbatches" in adata.var else None),
    }
    common.exec_record(log_path, "step1_prepare", "select_hvg", params, m)
    return m


def op_pca(adata, log_path, params, n_comps) -> dict:
    import scanpy as sc

    sc.pp.scale(adata, max_value=10)
    n_comp = min(n_comps, adata.n_obs, adata.n_vars)
    sc.pp.pca(adata, n_comps=n_comp, svd_solver="arpack" if n_comp < min(adata.n_obs, adata.n_vars) else "full")
    m = common.variance_explained(adata)
    m["n_comps_requested"] = n_comps
    common.exec_record(log_path, "step1_prepare", "pca", params, m)
    return m


def op_knn_graph(adata, log_path, params, n_neighbors, n_pcs) -> dict:
    import scanpy as sc
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs)
    A = adata.obsp["connectivities"]
    n_comp = int(connected_components(A, directed=False)[0])
    degrees = np.asarray(A.sum(axis=1)).ravel()
    density = float(A.nnz / max(A.shape[0] * A.shape[1], 1))
    m = {
        "n_connected_components": n_comp,
        "graph_density": density,
        "mean_degree": float(degrees.mean()),
        "median_degree": float(np.median(degrees)),
        "degree_distribution": common.describe_distribution(degrees),
        "frac_isolated_nodes": float((degrees == 0).mean()),
    }
    try:
        import networkx as nx
        G = nx.from_scipy_sparse_array(A)
        m["avg_clustering_coefficient"] = float(nx.average_clustering(G))
    except Exception:
        m["avg_clustering_coefficient"] = None
    common.exec_record(log_path, "step1_prepare", "knn_graph", params, m)
    return m


def op_leiden_cluster(adata, log_path, params, res_list, seed) -> dict:
    import scanpy as sc

    cluster_counts = {}
    quality = {}
    for r in res_list:
        key = f"leiden_{r}"
        sc.tl.leiden(adata, resolution=r, key_added=key, random_state=seed, flavor="igraph")
        cluster_counts[str(r)] = int(adata.obs[key].nunique())
        quality[str(r)] = common.cluster_quality(adata, adata.obs[key].values, max_silhouette_samples=10000)
    m = {
        "resolution_cluster_counts": cluster_counts,
        "per_resolution_quality": quality,
    }
    common.exec_record(log_path, "step1_prepare", "leiden_cluster", params, m)
    return m


def op_choose_resolution(adata, log_path, params, res_list, target) -> dict:
    counts = {str(r): int(adata.obs[f"leiden_{r}"].nunique()) for r in res_list if f"leiden_{r}" in adata.obs}
    res_strs = [str(r) for r in res_list if str(r) in counts]
    if target is None or str(target).strip() == "":
        raise ValueError("choose_resolution 需要显式 --target-resolution，禁止 knee 静默选定")
    target = str(target)
    if target not in counts:
        raise ValueError(f"--target-resolution {target} 不在已聚类列表 {res_strs} 中")
    adata.obs["leiden"] = adata.obs[f"leiden_{target}"].astype(str).values
    stab = common.resolution_stability(adata, res_list, chosen=str(target))
    m = {"resolution_chosen": str(target), "auto_knee_not_applicable": False, **stab,
         "n_clusters": int(adata.obs["leiden"].nunique())}
    common.exec_record(log_path, "step1_prepare", "choose_resolution", params, m)
    return m


def op_umap(adata, log_path, params, seed) -> dict:
    import scanpy as sc
    from sklearn.manifold import trustworthiness

    sc.tl.umap(adata, random_state=seed)
    X_high = adata.obsm["X_pca"]
    X_low = adata.obsm["X_umap"]
    rng = np.random.RandomState(seed)
    n = adata.n_obs
    if n < 3:
        m = {"trustworthiness": None, "continuity": None, "n_too_few_cells": n}
        common.exec_record(log_path, "step1_prepare", "umap", params, m)
        return m
    idx = rng.choice(n, min(n, 5000), replace=False) if n > 5000 else np.arange(n)
    n_trust = min(15, len(idx) - 1)
    tw = float(trustworthiness(X_high[idx], X_low[idx], n_neighbors=n_trust))
    # continuity: fraction of high-dim kNN that stay kNN in low dim (symmetric-ish)
    from sklearn.neighbors import NearestNeighbors
    n_knn = min(16, len(idx) - 1)
    knn_h = NearestNeighbors(n_neighbors=n_knn + 1).fit(X_high[idx]).kneighbors(X_high[idx])[1][:, 1:]
    knn_l = NearestNeighbors(n_neighbors=n_knn + 1).fit(X_low[idx]).kneighbors(X_low[idx])[1][:, 1:]
    continuity = float(np.mean([len(set(h) & set(l)) / n_knn for h, l in zip(knn_h, knn_l)]))

    labels = adata.obs["leiden"].values
    intra, inter = [], []
    for c in sorted(set(labels)):
        mask = labels == c
        pts = X_low[mask]
        if len(pts) < 2:
            continue
        from scipy.spatial.distance import pdist
        intra.append(float(pdist(pts).mean()))
        other = X_low[~mask]
        if len(other):
            from sklearn.metrics import pairwise_distances
            inter.append(float(pairwise_distances(pts, other).mean()))
    sep = float(np.mean(inter) / np.mean(intra)) if intra and inter else None

    # overlapping-cluster proxy: bounding-box overlap in UMAP space
    n_overlap = 0
    bboxes = {}
    for c in sorted(set(labels)):
        pts = X_low[labels == c]
        bboxes[c] = (pts.min(0), pts.max(0))
    cs = list(bboxes)
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            a, b = bboxes[cs[i]], bboxes[cs[j]]
            if all(a[0][k] <= b[1][k] and b[0][k] <= a[1][k] for k in range(2)):
                n_overlap += 1

    m = {
        "trustworthiness": tw,
        "continuity": continuity,
        "mean_intra_cluster_distance_umap": float(np.mean(intra)) if intra else None,
        "mean_inter_cluster_distance_umap": float(np.mean(inter)) if inter else None,
        "umap_separation_ratio": sep,
        "n_overlapping_clusters_umap": n_overlap,
        "umap_coordinate_range": {
            "UMAP1": [float(X_low[:, 0].min()), float(X_low[:, 0].max())],
            "UMAP2": [float(X_low[:, 1].min()), float(X_low[:, 1].max())],
        },
        "trustworthiness_sampled": int(len(idx)),
    }
    common.exec_record(log_path, "step1_prepare", "umap", params, m)
    return m


def op_batch_mixing(adata, log_path, params, batch_key) -> dict:
    import scanpy as sc

    batch = adata.obs[batch_key].astype(str).values
    leiden = adata.obs["leiden"].astype(str).values
    m = common.batch_mixing(leiden, batch)
    m["batch_key_used"] = batch_key
    # Moran's I of batch one-hot on the kNN graph
    try:
        morans = []
        for b in sorted(set(batch)):
            onehot = (batch == b).astype(float)
            morans.append(float(sc.metrics.morans_i(adata, vals=onehot)))
        m["batch_graph_autocorr_morans_i"] = {
            "per_batch": dict(zip(sorted(set(batch)), morans)),
            "mean_abs": float(np.mean(np.abs(morans))),
        }
    except Exception as exc:
        m["batch_graph_autocorr_morans_i"] = {"error": str(exc)}
    common.exec_record(log_path, "step1_prepare", "batch_mixing", params, m)
    return m


def op_write_output(adata, out_dir, log_path, params, metrics_all) -> dict:
    import pandas as pd

    h5ad_path = os.path.join(out_dir, "processed.h5ad")
    obs_path = os.path.join(out_dir, "obs_snapshot.csv")
    var_path = os.path.join(out_dir, "var_snapshot.csv")
    json_path = os.path.join(out_dir, "qc_metrics.json")

    adata.write(h5ad_path)
    obs_df = adata.obs.copy()
    obs_df.index.name = "cell_id"
    obs_df.to_csv(obs_path)
    var_df = adata.var.copy()
    var_df.index.name = "gene_id"
    var_df.to_csv(var_path)
    common.write_json(json_path, metrics_all)

    m = {
        "processed_h5ad": h5ad_path,
        "obs_snapshot": obs_path,
        "var_snapshot": var_path,
        "qc_metrics": json_path,
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "n_clusters": int(adata.obs["leiden"].nunique()),
    }
    rid = common.exec_record(log_path, "step1_prepare", "write_output", params, m)
    m["run_id"] = rid
    return m


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_metrics(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step1_prepare")
    log = common.run_log_path(args.project_dir)
    if not args.input:
        return common.fail("metrics 需要 --input(原始 h5ad 路径)")
    adata = common.read_h5ad(args.input)
    p = {"organ": args.organ, "mt_pattern": args.mt_pattern, "cp_pattern": args.cp_pattern}
    op_load_data(adata, log, p)
    op_compute_qc(adata, log, p, ["mt", "chloroplast"], args.mt_pattern, args.cp_pattern)
    op_qc_distribution(adata, log, p)
    op_qc_plot(adata, out_dir, log, p)
    metrics = {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "distributions": {v: common.describe_distribution(adata.obs[v].values)
                          for v in ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_chloroplast"]
                          if v in adata.obs},
    }
    common.write_json(os.path.join(out_dir, "qc_metrics.json"), metrics)
    return common.ok({
        "n_cells": metrics["n_cells"],
        "n_genes": metrics["n_genes"],
        "qc_metrics_json": os.path.join(out_dir, "qc_metrics.json"),
        "distributions": metrics["distributions"],
    })


def cmd_run(args) -> dict:
    if not getattr(args, "target_resolution", None):
        return common.fail(
            "step1_prepare run 需要显式 --target-resolution（由判断层或 ① 默认给出），禁止 knee 静默选定"
        )
    out_dir = common.step_dir(args.project_dir, "step1_prepare")
    log = common.run_log_path(args.project_dir)
    if not args.input:
        return common.fail("run 需要 --input(原始 h5ad 路径)")
    res_list = common.parse_float_list(args.resolution_list)
    if not res_list:
        return common.fail("--resolution-list 解析为空")
    err = common.check_positive(args, ["min_genes", "min_cells", "n_top_genes", "n_comps",
                                       "n_neighbors", "n_pcs"], "int")
    err = err or common.check_positive(args, ["expected_doublet_rate"], "float")
    if err:
        return common.fail(err)
    try:
        re.compile(args.mt_pattern)
        re.compile(args.cp_pattern)
        if not args.mt_pattern or not args.cp_pattern:
            return common.fail("--mt-pattern/--cp-pattern 不能为空")
    except re.error as exc:
        return common.fail(f"非法正则 --mt-pattern/--cp-pattern: {exc}")

    adata = common.read_h5ad(args.input)
    if hasattr(adata.X, "nnz") and adata.X.nnz == 0 or (not hasattr(adata.X, "nnz") and np.asarray(adata.X).sum() == 0):
        return common.fail("表达矩阵全为零,数据异常,拒绝继续")
    p = {
        "organ": args.organ, "batch_key": args.batch_key,
        "mt_pattern": args.mt_pattern, "cp_pattern": args.cp_pattern,
        "min_genes": args.min_genes, "max_mt_pct": args.max_mt_pct,
        "max_chloroplast_pct": args.max_chloroplast_pct, "min_cells": args.min_cells,
        "expected_doublet_rate": args.expected_doublet_rate, "target_sum": args.target_sum,
        "n_top_genes": args.n_top_genes, "n_comps": args.n_comps,
        "n_neighbors": args.n_neighbors, "n_pcs": args.n_pcs,
        "resolution_list": args.resolution_list, "target_resolution": args.target_resolution,
        "seed": args.seed,
    }
    metrics_all: dict = {}

    op_load_data(adata, log, {"input": args.input})
    m = op_compute_qc(adata, log, p, ["mt", "chloroplast"], args.mt_pattern, args.cp_pattern)
    metrics_all["qc_variables"] = m["qc_variables"]
    metrics_all["n_cells_before"] = m["n_cells"]
    metrics_all["n_genes_before"] = m["n_genes"]
    metrics_all["n_mt_genes"] = m["n_mt_genes"]
    metrics_all["n_chloroplast_genes"] = m["n_chloroplast_genes"]
    metrics_all["organ"] = args.organ  # persisted for step6 _meta (H2)

    m = op_qc_distribution(adata, log, p)
    metrics_all["pre_filter_distributions"] = m["pre_filter_distributions"]

    m = op_qc_plot(adata, out_dir, log, p)
    metrics_all["qc_plot"] = m

    m = op_filter_cells(adata, log, p, args.min_genes, args.max_mt_pct,
                        args.max_chloroplast_pct)
    metrics_all["cell_filtering"] = m
    metrics_all["post_filter_distributions"] = m["post_filter_distributions"]
    if adata.n_obs == 0:
        return common.fail("过滤后细胞数为 0:阈值过严(min_genes/max_mt_pct/max_chloroplast_pct)")

    m = op_filter_genes(adata, log, p, args.min_cells)
    metrics_all["gene_filtering"] = m
    if adata.n_vars == 0:
        return common.fail("过滤后基因数为 0:min_cells 阈值过严")

    m = op_detect_doublets(adata, log, p, args.expected_doublet_rate)
    metrics_all["doublet"] = m

    m = op_normalize(adata, log, p, args.target_sum)
    metrics_all["normalization"] = m

    m = op_select_hvg(adata, log, p, args.n_top_genes, args.hvg_batch_key)
    metrics_all["hvg"] = m

    m = op_pca(adata, log, p, args.n_comps)
    metrics_all["pca"] = m

    m = op_knn_graph(adata, log, p, args.n_neighbors, args.n_pcs)
    metrics_all["knn_graph"] = m

    m = op_leiden_cluster(adata, log, p, res_list, args.seed)
    metrics_all["clustering"] = {"resolution_cluster_counts": m["resolution_cluster_counts"],
                                 "per_resolution_quality": m["per_resolution_quality"]}

    m = op_choose_resolution(adata, log, p, res_list, args.target_resolution)
    metrics_all["resolution_stability"] = {k: v for k, v in m.items() if k != "resolution_chosen"}
    metrics_all["clustering"]["resolution_chosen"] = m["resolution_chosen"]
    metrics_all["clustering"]["n_clusters"] = m["n_clusters"]
    chosen_q = m["resolution_chosen"]
    q = m["per_resolution_quality"] if "per_resolution_quality" in m else None
    # promote chosen-resolution quality block
    chosen_quality = metrics_all["clustering"]["per_resolution_quality"].get(chosen_q, {})
    metrics_all["clustering"]["silhouette"] = chosen_quality.get("silhouette_overall")
    metrics_all["clustering"]["silhouette_per_cluster"] = chosen_quality.get("silhouette_per_cluster")
    metrics_all["clustering"]["n_clusters_negative_mean_silhouette"] = chosen_quality.get(
        "n_clusters_negative_mean_silhouette")
    metrics_all["clustering"]["modularity"] = chosen_quality.get("modularity")
    metrics_all["clustering"]["cluster_size_distribution"] = chosen_quality.get("cluster_size_distribution")
    metrics_all["clustering"]["frac_largest_cluster"] = chosen_quality.get("frac_largest_cluster")
    metrics_all["clustering"]["n_rare_clusters"] = chosen_quality.get("n_rare_clusters")
    metrics_all["clustering"]["n_singleton_clusters"] = chosen_quality.get("n_singleton_clusters")

    m = op_umap(adata, log, p, args.seed)
    metrics_all["umap"] = m

    m = op_batch_mixing(adata, log, p, common.resolve_batch_key(adata.obs.columns, args.batch_key))
    metrics_all["batch_mixing"] = m

    m = op_write_output(adata, out_dir, log, p, metrics_all)
    metrics_all["write_output"] = m

    cl = metrics_all.get("clustering") or {}
    rs = metrics_all.get("resolution_stability") or {}
    bm = metrics_all.get("batch_mixing") or {}
    return common.ok({
        "n_cells": m["n_cells"], "n_genes": m["n_genes"], "n_clusters": m["n_clusters"],
        "processed_h5ad": m["processed_h5ad"], "obs_snapshot": m["obs_snapshot"],
        "var_snapshot": m["var_snapshot"], "qc_metrics_json": m["qc_metrics"],
        "last_exec_run_id": m["run_id"],
        "judge_view": {
            "resolution_chosen": cl.get("resolution_chosen"),
            "n_clusters": cl.get("n_clusters"),
            "resolution_cluster_counts": cl.get("resolution_cluster_counts"),
            "silhouette_overall": cl.get("silhouette"),
            "n_clusters_negative_mean_silhouette": cl.get("n_clusters_negative_mean_silhouette"),
            "modularity": cl.get("modularity"),
            "cluster_size_distribution": cl.get("cluster_size_distribution"),
            "frac_largest_cluster": cl.get("frac_largest_cluster"),
            "n_rare_clusters": cl.get("n_rare_clusters"),
            "n_singleton_clusters": cl.get("n_singleton_clusters"),
            "n_clusters_derivative": rs.get("n_clusters_derivative"),
            "adjacent_ari": rs.get("adjacent_ari"),
            "stability_at_chosen_resolution": rs.get("stability_at_chosen_resolution"),
            "batch_key_used": bm.get("batch_key_used"),
            "batch_graph_autocorr_morans_i": bm.get("batch_graph_autocorr_morans_i"),
            "per_cluster_batch_entropy": bm.get("per_cluster_batch_entropy"),
            "per_cluster_max_batch_fraction": bm.get("per_cluster_max_batch_fraction"),
            "per_cluster_batch_nunique": bm.get("per_cluster_batch_nunique"),
        },
    })


def cmd_recluster(args) -> dict:
    if not getattr(args, "target_resolution", None):
        return common.fail(
            "step1_prepare recluster 需要显式 --target-resolution（由判断层给出），禁止 knee 静默选定"
        )
    out_dir = common.step_dir(args.project_dir, "step1_prepare")
    log = common.run_log_path(args.project_dir)
    h5ad = args.input or os.path.join(out_dir, "processed.h5ad")
    if not os.path.exists(h5ad):
        return common.fail(f"processed.h5ad 不存在:{h5ad}")
    res_list = common.parse_float_list(args.resolution_list)
    if not res_list:
        return common.fail("--resolution-list 解析为空")
    err = common.check_positive(args, ["n_neighbors", "n_pcs"], "int")
    if err:
        return common.fail(err)
    adata = common.read_h5ad(h5ad)
    p = {"resolution_list": args.resolution_list, "target_resolution": args.target_resolution,
         "n_neighbors": args.n_neighbors, "n_pcs": args.n_pcs, "seed": args.seed,
         "organ": args.organ}
    m = op_leiden_cluster(adata, log, p, res_list, args.seed)
    m2 = op_choose_resolution(adata, log, p, res_list, args.target_resolution)
    m3 = op_umap(adata, log, p, args.seed)
    # B3 fix: never clobber existing qc_metrics.json — read, update the two
    # affected blocks, write back; then persist the h5ad + sidecars.
    qc = common.read_json(os.path.join(out_dir, "qc_metrics.json")) or {}
    chosen = m2["resolution_chosen"]
    quality = m["per_resolution_quality"].get(chosen, {})
    qc["clustering"] = qc.get("clustering", {})
    qc["clustering"].update({
        "resolution_chosen": chosen,
        "n_clusters": m2["n_clusters"],
        "resolution_cluster_counts": m["resolution_cluster_counts"],
        "per_resolution_quality": m["per_resolution_quality"],
        "silhouette": quality.get("silhouette_overall"),
        "silhouette_per_cluster": quality.get("silhouette_per_cluster"),
        "n_clusters_negative_mean_silhouette": quality.get("n_clusters_negative_mean_silhouette"),
        "modularity": quality.get("modularity"),
        "cluster_size_distribution": quality.get("cluster_size_distribution"),
    })
    qc["resolution_stability"] = {k: v for k, v in m2.items() if k != "resolution_chosen"}
    common.write_json(os.path.join(out_dir, "qc_metrics.json"), qc)
    m4 = op_write_output(adata, out_dir, log, p, qc)
    return common.ok({"n_clusters": m2["n_clusters"], "resolution_chosen": chosen,
                      "processed_h5ad": m4["processed_h5ad"],
                      "last_exec_run_id": m4["run_id"]})


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(parser)
    if not args.subcommand:
        parser.print_help()
        return 2
    handlers = {"metrics": cmd_metrics, "run": cmd_run, "recluster": cmd_recluster}
    try:
        result = handlers[args.subcommand](args)
    except Exception as exc:  # contract: error JSON as last stdout line
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"{args.subcommand} failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
