"""Step 5 — refinement (tool_design.md §5.5, atomic_operations.md stage 5).

Subcommand:
    run  — candidate_autocorr -> subcluster -> subcluster_de -> subcluster_kg
            -> marker_overlap -> type_membership -> unknown_overlap
            -> write_refined. [1× proc h5ad load]

Ambiguous clusters listed by the judgment layer (``--clusters``) are
sub-clustered (SOP-5A: <min_cells cells -> skipped). Pipeline never infers
the list from first/second counts.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from step3c_kg import _rank_candidates  # reuse the single candidate-ranking impl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step5_refine.py",
        description="Step 5 细化:候选自相关预判、子聚类、子簇 DE/KG 重查、marker 重叠、类型归属",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")
    p_run = sub.add_parser("run", help="candidate_autocorr~write_refined(1× proc 加载)")
    p_run.add_argument("--clusters", default=None,
                      help="判断层给出的待细化簇 id(逗号分隔);缺省拒绝全量自路由")
    p_run.add_argument("--subcluster-resolution", type=float, default=0.5, help="子聚类分辨率")
    p_run.add_argument("--min-cells", type=int, default=100, help="可细分的最小父簇细胞数(SOP-5A)")
    p_run.add_argument("--subcluster-n-pcs", type=int, default=15, help="子聚类 PCA 主成分数")
    p_run.add_argument("--subcluster-n-neighbors", type=int, default=15, help="子聚类 kNN 邻居数")
    p_run.add_argument("--sub-de-n-genes", type=int, default=50, help="子簇 DE top-N")
    p_run.add_argument("--sub-top-n", type=int, default=10, help="每子簇保留 marker 数")
    p_run.add_argument("--min-pct1", type=float, default=0.5, help="子簇 marker 最小 pct1")
    p_run.add_argument("--max-pct1", type=float, default=0.9, help="子簇 marker 最大 pct1")
    p_run.add_argument("--min-pct1-pct2", type=float, default=0.25, help="子簇最小 pct1-pct2")
    common.add_common_args(p_run)
    return parser


def _subset(adata, mask, label_col="leiden"):
    import anndata as ad

    sub = adata[mask].copy()
    if adata.raw is not None:
        # raw may be anndata._core.raw.Raw (after h5ad round-trip); rebuild as AnnData
        sub.raw = ad.AnnData(X=adata.raw.X[mask], var=adata.raw.var)
    if label_col in adata.obs:
        sub.obs[label_col] = adata.obs[label_col].values[mask]
    return sub


def _subcluster_leiden(sub, resolution, n_pcs, n_neighbors):
    """Re-cluster a subset: PCA within subset -> neighbors -> Leiden."""
    import scanpy as sc

    n_comp = min(n_pcs, sub.n_obs - 1, sub.n_vars)
    sc.pp.pca(sub, n_comps=n_comp, svd_solver="arpack" if n_comp < min(sub.n_obs, sub.n_vars) else "full")
    sc.pp.neighbors(sub, n_neighbors=min(n_neighbors, sub.n_obs - 1), n_pcs=n_comp)
    sc.tl.leiden(sub, resolution=resolution, key_added="sub_leiden", random_state=0, flavor="igraph")
    return sub


def _sub_de_markers(sub, n_genes, top_n, min_pct1, max_pct1, min_diff):
    """Wilcoxon DE within the subset + pct1/pct2 filter -> {sub_cluster: [genes]}."""
    import scanpy as sc
    from scipy.stats import rankdata

    sc.tl.rank_genes_groups(sub, groupby="sub_leiden", method="wilcoxon",
                            use_raw=True, n_genes=n_genes)
    rgg = sub.uns["rank_genes_groups"]
    raw = sub.raw.X
    all_genes = list(sub.raw.var_names)
    gene_index = {g: i for i, g in enumerate(all_genes)}
    labels = sub.obs["sub_leiden"].astype(str).values
    sub_markers = {}
    for g in rgg["names"].dtype.names:
        names = [str(x) for x in rgg["names"][g]]
        cols = [gene_index[nm] for nm in names if nm in gene_index]
        mask = labels == g
        aligned_names = [nm for nm in names if nm in gene_index]
        if cols:
            pct1 = np.asarray((raw[mask][:, cols] > 0).mean(axis=0)).ravel()
            pct2 = (np.asarray((raw[~mask][:, cols] > 0).mean(axis=0)).ravel()
                    if (~mask).any() else np.zeros(len(cols)))
        else:
            pct1, pct2 = np.zeros(0), np.zeros(0)
        spec = pct1 - pct2
        keep = (pct1 >= min_pct1) & (pct1 <= max_pct1) & (spec >= min_diff)
        kept = [aligned_names[i] for i in range(len(aligned_names))
                if i < len(keep) and keep[i]][:top_n]
        sub_markers[str(g)] = kept
    return sub_markers


def _parse_clusters(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def op_candidate_autocorr(adata, annotations, kg_hits, log_path, params, n_neighbors,
                          n_pcs, min_cells, target_ids=None) -> dict:
    """Score-genes + Moran's I for judgment-selected clusters."""
    import scanpy as sc

    target_ids = set(str(c) for c in target_ids) if target_ids is not None else None
    results = {}
    for c, a in annotations.items():
        if target_ids is not None and str(c) not in target_ids:
            results[c] = {"status": "not_selected"}
            continue
        if a.get("status") != "has_candidates":
            results[c] = {"status": "no_candidates"}
            continue
        c1 = a.get("first_supporting_markers") or []
        c2 = a.get("second_supporting_markers") or []
        mask = adata.obs["leiden"].astype(str).values == c
        n_cells = int(mask.sum())
        if n_cells < min_cells:
            # consistent with subcluster's skip gate (M3)
            results[c] = {"status": "too_small", "n_cells": n_cells, "skipped_by_min_cells": True}
            continue
        sub = _subset(adata, mask)
        try:
            sc.pp.neighbors(sub, n_neighbors=min(n_neighbors, sub.n_obs - 1),
                            n_pcs=min(n_pcs, sub.n_obs - 1))
        except Exception as exc:
            results[c] = {"status": "error", "error": str(exc)}
            continue
        results[c] = common.candidate_autocorr(sub, c1, c2)
        results[c]["status"] = "ambiguous"
        results[c]["n_cells"] = n_cells
    m = {"per_cluster": results,
         "n_ambiguous": int(sum(1 for v in results.values() if v.get("status") == "ambiguous"))}
    common.exec_record(log_path, "step5_refine", "candidate_autocorr", params, m)
    return results


def op_subcluster(adata, annotations, log_path, params, res, min_cells, n_pcs, n_neighbors,
                  target_ids=None) -> dict:
    target_ids = set(str(c) for c in target_ids) if target_ids is not None else None
    subs = {}
    outcomes = {}
    for c, a in annotations.items():
        if target_ids is not None and str(c) not in target_ids:
            continue
        if a.get("status") != "has_candidates":
            continue
        mask = adata.obs["leiden"].astype(str).values == c
        n_cells = int(mask.sum())
        if n_cells < min_cells:
            outcomes[c] = {"outcome": "skipped", "reason": "n_cells_below_min",
                           "n_cells": n_cells}
            continue
        sub = _subset(adata, mask)
        try:
            sub = _subcluster_leiden(sub, res, n_pcs, n_neighbors)
        except Exception as exc:
            outcomes[c] = {"outcome": "skipped", "reason": f"error: {exc}", "n_cells": n_cells}
            continue
        n_sub = int(sub.obs["sub_leiden"].nunique())
        sizes = sub.obs["sub_leiden"].value_counts().sort_index()
        if n_sub < 2:
            # homogeneous parent: no real split (M4) — skip DE/KG downstream
            outcomes[c] = {
                "outcome": "analyzed_nosplit", "n_cells": n_cells,
                "n_subclusters": n_sub,
                "sub_cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
                "reason": "subclustering produced a single cluster",
            }
            continue
        subs[c] = sub
        outcomes[c] = {
            "outcome": "analyzed", "n_cells": n_cells,
            "n_subclusters": n_sub,
            "sub_cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        }
    m = {"per_cluster": outcomes,
         "n_analyzed": int(sum(1 for v in outcomes.values() if v["outcome"] == "analyzed")),
         "n_skipped": int(sum(1 for v in outcomes.values() if v["outcome"] == "skipped"))}
    common.exec_record(log_path, "step5_refine", "subcluster", params, m)
    return subs, outcomes


def op_subcluster_de(subs, log_path, params, n_genes, top_n, min_pct1, max_pct1, min_diff) -> dict:
    sub_markers = {}
    details = {}
    for c, sub in subs.items():
        try:
            markers = _sub_de_markers(sub, n_genes, top_n, min_pct1, max_pct1, min_diff)
        except Exception as exc:
            markers = {}
            details[c] = {"n_subclusters": 0, "error": str(exc)}
            sub_markers[c] = markers
            continue
        sub_markers[c] = markers
        details[c] = {"n_subclusters": len(markers),
                      "markers_per_subcluster": {k: len(v) for k, v in markers.items()}}
    m = {"per_cluster": details}
    common.exec_record(log_path, "step5_refine", "subcluster_de", params, m)
    return sub_markers


def op_subcluster_kg(sub_markers, gene_to_cts, log_path, params, target) -> dict:
    sub_results = {}
    for c, markers in sub_markers.items():
        per = _rank_candidates(markers, gene_to_cts, target)
        sub_results[c] = {}
        for sub_id, info in per.items():
            cands = info["candidates"]
            first = cands[0] if cands else None
            second = cands[1] if len(cands) > 1 else None
            sub_results[c][sub_id] = {
                "first_candidate": first["cell_type"] if first else None,
                "second_candidate": second["cell_type"] if second else None,
                "first_count": first["marker_count"] if first else 0,
                "second_count": second["marker_count"] if second else 0,
                "first_confidence": first.get("mean_confidence") if first else None,
                "markers": markers.get(sub_id, []),
                "n_candidates": len(cands),
            }
    m = {"n_parents_with_subresults": len(sub_results),
         "n_subclusters": int(sum(len(v) for v in sub_results.values()))}
    common.exec_record(log_path, "step5_refine", "subcluster_kg", params, m)
    return sub_results


def op_marker_overlap(sub_markers, log_path, params) -> dict:
    per = {}
    for c, markers in sub_markers.items():
        per[c] = common.pairwise_overlap({k: set(v) for k, v in markers.items()})
    m = {"per_cluster": per}
    common.exec_record(log_path, "step5_refine", "marker_overlap", params, m)
    return per


def op_type_membership(sub_results, kg_hits, annotations, log_path, params) -> dict:
    membership = {}
    for c, subs in sub_results.items():
        parent_types = {x["cell_type"] for x in kg_hits.get("per_cluster", {}).get(c, {}).get("candidates", [])}
        if annotations.get(c, {}).get("first_candidate"):
            parent_types.add(annotations[c]["first_candidate"]["cell_type"])
        if annotations.get(c, {}).get("second_candidate"):
            parent_types.add(annotations[c]["second_candidate"]["cell_type"])
        for sub_id, r in subs.items():
            membership[f"{c}.{sub_id}"] = {
                "types_in_parent_candidates": bool(r["first_candidate"] in parent_types) if r["first_candidate"] else None,
                "first_candidate": r["first_candidate"],
                "parent_candidate_types": sorted(parent_types),
            }
    m = {"per_subcluster": membership,
         "n_in_range": int(sum(1 for v in membership.values() if v["types_in_parent_candidates"] is True)),
         "n_out_of_range": int(sum(1 for v in membership.values() if v["types_in_parent_candidates"] is False)),
         "n_none": int(sum(1 for v in membership.values() if v["types_in_parent_candidates"] is None))}
    common.exec_record(log_path, "step5_refine", "type_membership", params, m)
    return membership


def op_unknown_overlap(markers_json, annotations, log_path, params) -> dict:
    unknown = [c for c, a in annotations.items() if a.get("status") == "no_candidates"]
    sets = {c: set(markers_json["per_cluster"][c]["marker_genes"]) for c in unknown
            if c in markers_json.get("per_cluster", {})}
    overlap = common.pairwise_overlap(sets)
    m = {
        "unknown_overlap_summary": {
            "unknown_clusters": unknown,
            "n_unknown_clusters": len(unknown),
            "frac_unknown": float(len(unknown) / max(len(annotations), 1)),
            "avg_overlap": overlap["mean_overlap"],
            "pairs": overlap["pairs"],
            "jaccard_per_pair": [{"pair": p["pair"], "jaccard": p["jaccard"]} for p in overlap["pairs"]],
        }
    }
    common.exec_record(log_path, "step5_refine", "unknown_overlap", params, m)
    return m


def subcluster_delivery(barcodes, sub_ids, sub_results: dict) -> dict:
    """Keep each subcluster's already computed name, and which cells belong to it.

    ``sub_results`` is ``{sub_id: {first_candidate: cell type or None}}``.
    Cells with no candidate are recorded as ``unknown`` rather than dropped.
    """
    cell_subcluster: dict[str, str] = {}
    for bc, sid in zip(barcodes, sub_ids):
        cell_subcluster[str(bc)] = str(sid)
    labels: dict[str, str] = {}
    for sid in sorted(set(cell_subcluster.values())):
        info = (sub_results or {}).get(sid) or {}
        name = info.get("first_candidate")
        labels[sid] = name if name else "unknown"
    return {"labels": labels, "cell_subcluster": cell_subcluster}


def op_write_refined(out_dir, log_path, params, payload) -> dict:
    path = os.path.join(out_dir, "refined_annotations.json")
    common.write_json(path, payload)
    m = {"refined_annotations_json": path,
         **payload.get("counts", {})}
    rid = common.exec_record(log_path, "step5_refine", "write_refined", params, m)
    m["run_id"] = rid
    return m


def cmd_run(args) -> dict:
    out_dir = common.step_dir(args.project_dir, "step5_refine")
    log = common.run_log_path(args.project_dir)
    h5ad = args.input or os.path.join(common.step_dir(args.project_dir, "step1_prepare"),
                                      "processed.h5ad")
    ann = common.read_json(os.path.join(common.step_dir(args.project_dir, "step4_rank"),
                                        "annotations.json"))
    kg = common.read_json(os.path.join(common.step_dir(args.project_dir, "step3c_kg"),
                                       "kg_hits.json"))
    markers = common.read_json(os.path.join(common.step_dir(args.project_dir, "step2_markers"),
                                            "markers.json"))
    if not ann or "annotations" not in ann:
        return common.fail("缺少 step4_rank/annotations.json,请先运行 step4_rank run")
    if not kg or not markers:
        return common.fail("缺少 step3c_kg/kg_hits.json 或 step2_markers/markers.json")
    target_ids = _parse_clusters(getattr(args, "clusters", None))
    if not target_ids:
        return common.fail("step5_refine 需要判断层给出的 --clusters，拒绝全量自路由")
    unknown = [c for c in target_ids if c not in ann["annotations"]]
    if unknown:
        return common.fail(f"--clusters 含 annotations 中不存在的簇: {unknown}")
    if not os.path.exists(h5ad):
        return common.fail(f"processed.h5ad 不存在:{h5ad}")
    # Target organ fail-fast:在 h5ad 加载前检查 kg_hits.json 的 query_config.organ,
    # 避免后续 op_* 调用产生的孤立 exec 记录(参见 step5_fail_late 改动)。
    query_config = kg.get("query_config")
    if not isinstance(query_config, dict) or not query_config.get("organ"):
        return common.fail("kg_hits.json 缺少 query_config.organ,无法确定目标 organ;请重新运行 step3c_kg query")
    target_organ = query_config["organ"]
    adata = common.read_h5ad(h5ad)
    if adata.raw is None:
        return common.fail("h5ad 缺少 raw(请先运行 step1_prepare run 的归一化)")
    annotations = ann["annotations"]

    p = {"subcluster_resolution": args.subcluster_resolution, "min_cells": args.min_cells,
         "subcluster_n_pcs": args.subcluster_n_pcs,
         "subcluster_n_neighbors": args.subcluster_n_neighbors,
         "sub_de_n_genes": args.sub_de_n_genes, "sub_top_n": args.sub_top_n,
         "min_pct1": args.min_pct1, "max_pct1": args.max_pct1,
         "min_pct1_pct2": args.min_pct1_pct2,
         "clusters": ",".join(target_ids)}
    err = common.check_positive(args, ["min_cells", "subcluster_n_pcs",
                                       "subcluster_n_neighbors", "sub_de_n_genes",
                                       "sub_top_n"], "int")
    if err:
        return common.fail(err)
    if not (0 <= args.min_pct1 < args.max_pct1 <= 1) or not (0 <= args.min_pct1_pct2 <= 1):
        return common.fail("子簇 marker 阈值非法:需 0 ≤ min_pct1 < max_pct1 ≤ 1 且 0 ≤ min_pct1_pct2 ≤ 1")

    autocorr = op_candidate_autocorr(adata, annotations, kg, log, p,
                                     args.subcluster_n_neighbors, args.subcluster_n_pcs,
                                     args.min_cells, target_ids)
    subs, outcomes = op_subcluster(adata, annotations, log, p, args.subcluster_resolution,
                                   args.min_cells, args.subcluster_n_pcs,
                                   args.subcluster_n_neighbors, target_ids)
    sub_markers = op_subcluster_de(subs, log, p, args.sub_de_n_genes, args.sub_top_n,
                                   args.min_pct1, args.max_pct1, args.min_pct1_pct2)
    gene_to_cts = kg.get("gene_to_cts", {})
    # target_organ 由 cmd_run 提前从 kg["query_config"]["organ"] 读取并 fail-fast
    sub_results = op_subcluster_kg(sub_markers, gene_to_cts, log, p, target_organ)
    overlap = op_marker_overlap(sub_markers, log, p)
    membership = op_type_membership(sub_results, kg, annotations, log, p)
    unknown = op_unknown_overlap(markers, annotations, log, p)

    # assemble refined annotations
    clusters = {}
    counts = {"n_passthrough": 0, "n_analyzed": 0, "n_skipped": 0, "n_unknown": 0}
    target_set = set(str(c) for c in target_ids)
    for c, a in annotations.items():
        entry = {
            "status": "unknown" if a.get("status") == "no_candidates" else "passthrough",
            "first_candidate": a.get("first_candidate"),
            "second_candidate": a.get("second_candidate"),
            "first_count": a.get("first_count"),
            "second_count": a.get("second_count"),
            "gap_metrics": a.get("gap_metrics"),
            "candidate_autocorr": autocorr.get(c),
        }
        if a.get("status") == "no_candidates":
            counts["n_unknown"] += 1
        elif str(c) not in target_set:
            counts["n_passthrough"] += 1
        else:
            outcome = outcomes.get(c, {}).get("outcome")
            entry["status"] = "analyzed" if outcome in ("analyzed", "analyzed_nosplit") else "skipped"
            entry["subcluster"] = {
                "outcome": outcome,
                "reason": outcomes.get(c, {}).get("reason"),
                "n_subclusters": outcomes.get(c, {}).get("n_subclusters"),
                "sub_cluster_sizes": outcomes.get(c, {}).get("sub_cluster_sizes"),
                "sub_markers": sub_markers.get(c),
                "sub_results": sub_results.get(c),
                "overlap_metrics": overlap.get(c),
                "type_membership": {k: v for k, v in membership.items() if k.startswith(f"{c}.")},
            }
            if c in subs:
                delivery = subcluster_delivery(
                    list(subs[c].obs_names),
                    subs[c].obs["sub_leiden"].astype(str).tolist(),
                    sub_results.get(c, {}),
                )
                entry["subcluster"]["labels"] = delivery["labels"]
                entry["subcluster"]["cell_subcluster"] = delivery["cell_subcluster"]
            if entry["status"] == "analyzed":
                counts["n_analyzed"] += 1
            else:
                counts["n_skipped"] += 1
        clusters[c] = entry

    payload = {
        "clusters": clusters,
        "counts": counts,
        "unknown_overlap_summary": unknown.get("unknown_overlap_summary", {}),
        "meta": {"date": common.now_iso()[:10],
                 "subcluster_resolution": args.subcluster_resolution,
                 "min_cells": args.min_cells},
    }
    m = op_write_refined(out_dir, log, p, payload)
    return common.ok({"refined_annotations_json": m["refined_annotations_json"],
                      **counts,
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
