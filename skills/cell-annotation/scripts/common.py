"""Shared utilities for the cell-annotation pipeline scripts.

Design sources:
    - tool_design.md  §6   (generic metric functions)
    - trajectory_design.md §9  (append_log / next_run_id)
    - atomic_operations.md  (op semantics)
    - operations_metrics_catalog.md (metric paths)

Every CLI script in this package:
    1. declares its subcommands + args purely via argparse (single source of truth),
    2. supports ``--dump-schema`` which prints, as the LAST stdout line, the JSON
       tool declaration consumed by harness/skill_loader.py,
    3. prints the standard tool result contract ``{"status": "ok", "data": {...}}``
       or ``{"status": "error", ...}`` as the LAST stdout line,
    4. appends one ``exec`` record to ``<project-dir>/run_log.jsonl`` after every
       atomic operation (run_id = ``{step}.{op}#{attempt}``).

Module naming: keep ``import common`` working when scripts are invoked directly.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

# Skill scripts run as subprocesses from harness/dispatcher.py. The parent
# loop process has already imported ``harness.config`` (via the package
# ``__init__``), which populated ``os.environ`` with every key from the
# project-root ``.env``; subprocesses inherit that environment automatically.
#
# This module therefore does NOT import ``harness.config`` itself — that
# would couple the skill to the harness package layout (tool_design.md §10:
# "skill 本身保持环境无关"). When invoked directly outside the loop (e.g.
# ``python skills/cell-annotation/scripts/step1_prepare.py metrics``) the
# caller is responsible for sourcing ``.env`` first; see docs/CONFIGURATION_REFERENCE.md
# §2 for the recommended pattern.

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

STEP_DIRS = {
    "step1_prepare": "step1_prepare",
    "step2_markers": "step2_markers",
    "step3_kg": "step3_kg",
    "step4_rank": "step4_rank",
    "step5_refine": "step5_refine",
    "step6_validate": "step6_validate",
    "step7_diagnose": "step7_diagnose",
}

# First attempt #1 + at most 5 retries #2–#6. #7 is refused (script + driver).
MAX_ATTEMPT = 6


class AttemptCapExceeded(RuntimeError):
    """Raised when the next run_id would be #{7} or higher."""

    def __init__(self, step: str, op: str, next_attempt: int):
        self.step = step
        self.op = op
        self.next_attempt = next_attempt
        super().__init__(
            f"retry cap: {step}.{op} already has {MAX_ATTEMPT} attempts; "
            f"refusing #{next_attempt}"
        )


def step_dir(project_dir: str, step: str) -> str:
    """Directory holding one step's data files (created on demand)."""
    if step == "step4_judge":
        raise ValueError("step4_judge 已改名为 step4_rank；拒绝读写 step4_judge/")
    d = os.path.join(project_dir, STEP_DIRS.get(step, step))
    os.makedirs(d, exist_ok=True)
    return d


def run_log_path(project_dir: str) -> str:
    return os.path.join(project_dir, "run_log.jsonl")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json(path: str, default: Any = None) -> Any:
    """Read a JSON file; return ``default`` when missing/invalid (default None)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str, obj: Any) -> None:
    """Write an object as pretty JSON (utf-8, ensure_ascii=False)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_h5ad(path: str):
    """Read an h5ad with the repo's safety conventions (dataset/init.py).

    - rebuild ``raw`` when saved by an old scanpy (``_index`` in raw.var)
    - reject numeric var_names (must be gene symbols / locus ids)
    """
    import anndata as ad
    import scanpy as sc

    adata = sc.read_h5ad(path)
    raw_available = hasattr(adata, "raw") and adata.raw is not None
    if raw_available and "_index" in adata.raw.var.columns:
        raw_var = adata.raw.var.set_index("_index").copy()
        raw_var.index = raw_var.index.astype(str)
        raw_var.index.name = None
        adata.raw = ad.AnnData(X=adata.raw.X, var=raw_var)

    if raw_available:
        try:
            adata.raw.var_names.astype(float)
            raise TypeError("adata.raw.var_names isn't gene names!")
        except ValueError:
            pass
    else:
        try:
            adata.var_names.astype(float)
            raise TypeError("adata.var_names isn't gene names!")
        except ValueError:
            pass
    return adata


# ---------------------------------------------------------------------------
# run_log (trajectory_design.md §9)
# ---------------------------------------------------------------------------

def append_log(log_path: str, record: dict) -> None:
    """Append one record to run_log.jsonl, auto-filling ``ts`` and ``seq``."""
    record["ts"] = now_iso()
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            record["seq"] = sum(1 for _ in f) + 1
    else:
        record["seq"] = 1
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def exec_record(log_path: str, step: str, op: str, parameters: dict, metrics: dict) -> str:
    """Append an ``exec`` record; returns the run_id used.

    run_id = ``{step}.{op}#{attempt}`` — attempt continues from the log's max.
    """
    run_id = next_run_id(log_path, step, op)
    append_log(log_path, {
        "type": "exec",
        "run_id": run_id,
        "parameters": parameters or {},
        "metrics": metrics or {},
    })
    return run_id


def next_run_id(log_path: str, step: str, op: str) -> str:
    """Compute the next run_id ``{step}.{op}#{attempt}`` (append-only resume)."""
    prefix = f"{step}.{op}#"
    attempts: List[int] = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = r.get("run_id", "")
                if rid.startswith(prefix):
                    try:
                        attempts.append(int(rid.split("#")[1]))
                    except (ValueError, IndexError):
                        continue
    attempt = (max(attempts) + 1) if attempts else 1
    if attempt > MAX_ATTEMPT:
        raise AttemptCapExceeded(step, op, attempt)
    return f"{step}.{op}#{attempt}"


def current_metrics(log_path: str, step_op_prefix: str) -> Optional[dict]:
    """Return the metrics of the highest-seq exec record for a step.op prefix.

    Utility mirroring trajectory_design.md §4.3 ("current value = highest seq");
    kept for LLM-facing scripts / future exporters.
    """
    best = None
    if not os.path.exists(log_path):
        return None
    prefix = f"{step_op_prefix}#"
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "exec" and r.get("run_id", "").startswith(prefix):
                if best is None or r["seq"] > best["seq"]:
                    best = r
    return best["metrics"] if best else None


# ---------------------------------------------------------------------------
# distribution / statistics helpers (tool_design.md §6)
# ---------------------------------------------------------------------------

def gini_coefficient(values: Sequence[float]) -> Optional[float]:
    """Gini coefficient (0 = uniform, 1 = maximally unequal)."""
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return None
    v = np.sort(v)
    n = len(v)
    if v.sum() == 0:
        return 0.0
    # relative mean absolute difference formula (sign-correct)
    return float((2 * np.sum(np.arange(1, n + 1) * v) - (n + 1) * v.sum()) / (n * v.sum()))


def shannon_entropy(counts: Sequence[float], base: float = 2.0) -> float:
    """Shannon entropy of a count distribution (0 when single category)."""
    counts = np.asarray(counts, dtype=float)
    counts = counts[counts > 0]
    if counts.size == 0:
        return 0.0
    p = counts / counts.sum()
    return float(-(p * np.log(p) / np.log(base)).sum())


def _finite_or_none(x) -> Optional[float]:
    """Return None for NaN/Inf (JSON-safe), else the float value."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _compact_float(x) -> Optional[float]:
    """JSON-safe float with 6 significant figures (cuts run_log bulk)."""
    f = _finite_or_none(x)
    if f is None:
        return None
    return float(f"{f:.6g}")


def histogram_bin_edges(dist: dict) -> Optional[np.ndarray]:
    """Equal-width edges implied by min/max and len(histogram). None if unusable."""
    counts = dist.get("histogram")
    vmin, vmax = dist.get("min"), dist.get("max")
    if not isinstance(counts, list) or len(counts) < 1:
        return None
    if vmin is None or vmax is None:
        return None
    return np.linspace(float(vmin), float(vmax), len(counts) + 1)


def describe_distribution(values, n_bins: int = 20) -> Optional[dict]:
    """One-call distribution summary (tool_design.md §6.1).

    Shape fields sit together: percentiles (p1..p99) plus histogram (n_bins
    equal-width counts from min to max). Edges are not stored — reconstruct
    with ``histogram_bin_edges``. Floats are 6 significant figures.
    """
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) == 0:
        return None
    from scipy.stats import skew, kurtosis

    pct_keys = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    counts, _edges = np.histogram(values, bins=n_bins)
    mean = float(values.mean())
    std = float(values.std())
    p25, p75 = np.percentile(values, [25, 75])
    sk = _compact_float(skew(values))
    kt = _compact_float(kurtosis(values))  # excess kurtosis
    bimod = ((sk ** 2) + 1) / (kt + 3) if (sk is not None and kt is not None and (kt + 3) > 0) else None
    return {
        "min": _compact_float(values.min()),
        "max": _compact_float(values.max()),
        "mean": _compact_float(mean),
        "std": _compact_float(std),
        "median": _compact_float(np.median(values)),
        "iqr": _compact_float(p75 - p25),
        "cv": _compact_float(std / mean) if mean != 0 else None,
        "skewness": sk,
        "kurtosis": kt,
        "bimodality_coefficient": _compact_float(bimod),
        "percentiles": {f"p{k}": _compact_float(np.percentile(values, k)) for k in pct_keys},
        "histogram": [int(c) for c in counts],
    }


def filter_funnel(masks: Sequence[np.ndarray], labels: Sequence[str]) -> dict:
    """Record how many items pass each filter stage (tool_design.md §6.2).

    ``n_passed`` is measured on the original population; ``n_lost`` is
    sequential (lost at this stage given all previous stages passed).
    """
    masks = [np.asarray(m, dtype=bool) for m in masks]
    if not masks:
        return {"n_before": 0, "n_after": 0, "frac_retained": 0.0, "stages": {},
                "n_lost_multiple_criteria": 0}
    n_before = len(masks[0])
    combined = np.ones(n_before, dtype=bool)
    stages: Dict[str, dict] = {}
    for mask, label in zip(masks, labels):
        n_pass = int(mask.sum())
        n_lost = int((combined & ~mask).sum())
        stages[label] = {"n_passed": n_pass, "n_lost": n_lost}
        combined &= mask
    n_after = int(combined.sum())
    fails = np.stack([~m for m in masks], axis=1)
    n_lost_multiple = int((fails.sum(axis=1) >= 2).sum())
    return {
        "n_before": n_before,
        "n_after": n_after,
        "frac_retained": n_after / max(n_before, 1),
        "stages": stages,
        "n_lost_multiple_criteria": n_lost_multiple,
    }


def effect_size(group_in, group_out) -> dict:
    """Effect-size metrics for in-cluster vs out-cluster expression (tool §6.3)."""
    from sklearn.metrics import roc_auc_score

    group_in = np.asarray(group_in, dtype=float)
    group_out = np.asarray(group_out, dtype=float)
    mean_in = float(group_in.mean())
    mean_out = float(group_out.mean())
    std_in = float(group_in.std())
    std_out = float(group_out.std())
    pooled_std = float(np.sqrt((std_in**2 + std_out**2) / 2))
    cohen_d = (mean_in - mean_out) / pooled_std if pooled_std > 0 else 0.0
    labels = np.concatenate([np.ones(len(group_in)), np.zeros(len(group_out))])
    scores = np.concatenate([group_in, group_out])
    try:
        auc = float(roc_auc_score(labels, scores))
    except ValueError:  # single class present
        auc = None
    eps = 1e-6
    fold_change = mean_in / max(mean_out, eps)
    logfc = float(np.log2((mean_in + 1) / (mean_out + 1)))
    return {
        "mean_in": mean_in,
        "mean_out": mean_out,
        "cohen_d": float(cohen_d),
        "auc": auc,
        "fold_change": float(fold_change),
        "logfc": logfc,
    }


def pairwise_overlap(sets: Dict[str, set], labels: Optional[Sequence[str]] = None) -> dict:
    """Pairwise overlap metrics over a collection of sets (tool_design.md §6.4).

    Returns mean/max overlap (intersection / min size), mean Jaccard, per-pair
    details, an NxN overlap matrix and per-set unique-marker fractions.
    """
    items = list(sets.items())
    keys = [k for k, _ in items]
    if len(keys) < 2:
        return {
            "mean_overlap": 0.0,
            "max_overlap": 0.0,
            "mean_jaccard": 0.0,
            "pairs": [],
            "overlap_matrix": {k: {k2: 1.0 if k == k2 else 0.0 for k2 in keys} for k in keys},
            "frac_unique_markers": {k: 1.0 for k in keys},
        }
    pairs = []
    overlaps: List[float] = []
    jaccards: List[float] = []
    matrix = {k: {k2: 0.0 for k2 in keys} for k in keys}
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i == j:
                matrix[a][b] = 1.0
                continue
            sa, sb = sets[a], sets[b]
            inter = len(sa & sb)
            overlap = inter / max(min(len(sa), len(sb)), 1)
            jaccard = inter / max(len(sa | sb), 1)
            matrix[a][b] = float(overlap)
            if j < i:
                continue
            pairs.append({
                "pair": f"{a}-{b}",
                "overlap": float(overlap),
                "jaccard": float(jaccard),
            })
            overlaps.append(overlap)
            jaccards.append(jaccard)
    unique_frac = {
        k: float(1.0 - (len({g for k2 in keys if k2 != k for g in sets[k] & sets[k2]}) / max(len(sets[k]), 1)))
        for k in keys
    }
    return {
        "mean_overlap": float(np.mean(overlaps)) if overlaps else 0.0,
        "max_overlap": float(max(overlaps)) if overlaps else 0.0,
        "mean_jaccard": float(np.mean(jaccards)) if jaccards else 0.0,
        "pairs": pairs,
        "overlap_matrix": matrix,
        "frac_unique_markers": unique_frac,
    }


def batch_mixing(cluster_labels, batch_labels) -> dict:
    """Per-cluster batch mixing metrics + chi-square independence test (tool §6.5)."""
    from collections import Counter
    from scipy.stats import chi2_contingency

    clusters = sorted(set(cluster_labels))
    batches = sorted(set(batch_labels))
    n_batches = max(len(batches), 1)
    per_cluster = {}
    contingency = []
    for c in clusters:
        mask = np.asarray(cluster_labels) == c
        batch_counts = Counter(np.asarray(batch_labels)[mask])
        total = sum(batch_counts.values())
        entropy = shannon_entropy(list(batch_counts.values()), base=2.0)
        max_frac = (max(batch_counts.values()) / total) if total > 0 else 0.0
        per_cluster[str(c)] = {
            "entropy": float(entropy),
            "nunique": len(batch_counts),
            "max_batch_fraction": float(max_frac),
        }
        contingency.append([batch_counts.get(b, 0) for b in batches])
    chi2_stat = chi2_p = None
    if len(contingency) > 1 and len(batches) > 1:
        try:
            chi2, p, _, _ = chi2_contingency(contingency)
            chi2_stat, chi2_p = float(chi2), float(p)
        except Exception:
            pass
    entropies = [v["entropy"] for v in per_cluster.values()]
    max_entropy = math.log2(n_batches) if n_batches > 1 else 1.0
    overall_index = float(np.mean(entropies) / max_entropy) if entropies and max_entropy else None
    return {
        "per_cluster": per_cluster,
        "chi_square": chi2_stat,
        "chi_square_p": chi2_p,
        "overall_batch_mixing_index": overall_index,
        "batch_key_used": None,  # filled by caller when known
    }


def _modularity(A, labels) -> Optional[float]:
    """Weighted Newman modularity of ``labels`` on adjacency ``A`` (csr)."""
    import scipy.sparse as sp

    A = sp.csr_matrix(A)
    k = np.asarray(A.sum(axis=1)).ravel()
    m = float(k.sum())
    if m <= 0:
        return None
    labels = np.asarray(labels)
    q = 0.0
    for c in sorted(set(labels)):
        idx = np.where(labels == c)[0]
        e_c = A[idx][:, idx].sum()
        k_c = k[idx].sum()
        q += e_c / (2 * m) - (k_c / (2 * m)) ** 2
    return float(q)


def cluster_quality(adata, labels, X_pca=None, max_silhouette_samples: int = 10000) -> dict:
    """Clustering quality: silhouette, size distribution, DB/CH, WCSS/BCSS, modularity."""
    from sklearn.metrics import (
        calinski_harabasz_score,
        davies_bouldin_score,
        silhouette_samples,
    )

    if X_pca is None:
        X_pca = adata.obsm["X_pca"]
    X_pca = np.asarray(X_pca)
    labels = np.asarray(labels)
    unique_labels = sorted(set(labels))
    n = len(labels)

    # silhouette / DB / CH need >= 2 clusters and >= 2 samples; degrade gracefully
    if len(unique_labels) < 2 or n < 2:
        return {
            "silhouette_overall": None,
            "silhouette_per_cluster": {},
            "silhouette_sampled": False,
            "n_clusters_negative_mean_silhouette": 0,
            "cluster_size_distribution": None,
            "frac_largest_cluster": None,
            "frac_smallest_cluster": None,
            "n_rare_clusters": 0,
            "n_singleton_clusters": int(sum(1 for c in unique_labels if (labels == c).sum() <= 2)),
            "davies_bouldin": None,
            "calinski_harabasz": None,
            "wcss": None,
            "bcss": None,
            "wcss_bcss_ratio": None,
            "modularity": None,
            "n_clusters_too_few": len(unique_labels),
        }

    if n > max_silhouette_samples:
        rng = np.random.RandomState(0)
        idx = rng.choice(n, max_silhouette_samples, replace=False)
        sil_samples = silhouette_samples(X_pca[idx], labels[idx])
        sampled = True
        sil_labels = labels[idx]  # labels aligned with sil_samples
    else:
        sil_samples = silhouette_samples(X_pca, labels)
        sampled = False
        sil_labels = labels

    per_cluster = {}
    for c in unique_labels:
        mask = sil_labels == c
        if not mask.any():
            continue
        per_cluster[str(c)] = {
            "mean": float(np.mean(sil_samples[mask])),
            "median": float(np.median(sil_samples[mask])),
            "p25": float(np.percentile(sil_samples[mask], 25)),
            "p75": float(np.percentile(sil_samples[mask], 75)),
        }
    n_negative = int(sum(1 for c in unique_labels
                         if per_cluster.get(str(c)) and per_cluster[str(c)]["mean"] < 0))

    sizes = [int(np.sum(labels == c)) for c in unique_labels]
    size_dist = describe_distribution(sizes)
    if size_dist is not None:
        size_dist["gini"] = gini_coefficient(sizes)

    # cluster compactness / separation on X_pca
    db = ch = None
    wcss = bcss = None
    try:
        if len(unique_labels) > 1:
            db = float(davies_bouldin_score(X_pca, labels))
            ch = float(calinski_harabasz_score(X_pca, labels))
            centers = np.vstack([X_pca[labels == c].mean(axis=0) for c in unique_labels])
            global_center = X_pca.mean(axis=0)
            wcss = float(sum(((X_pca[labels == c] - centers[i]) ** 2).sum() for i, c in enumerate(unique_labels)))
            bcss = float(sum(len(idx) * ((centers[i] - global_center) ** 2).sum() for i, idx in
                             [ (i, np.where(labels == c)[0]) for i, c in enumerate(unique_labels)]))
    except Exception:
        pass

    modularity = None
    try:
        if "connectivities" in adata.obsp:
            modularity = _modularity(adata.obsp["connectivities"], labels)
    except Exception:
        pass

    return {
        "silhouette_overall": {
            "mean": float(np.mean(sil_samples)),
            "std": float(np.std(sil_samples)),
        },
        "silhouette_per_cluster": per_cluster,
        "silhouette_sampled": sampled,
        "n_clusters_negative_mean_silhouette": n_negative,
        "cluster_size_distribution": size_dist,
        "frac_largest_cluster": float(max(sizes) / n) if sizes else None,
        "frac_smallest_cluster": float(min(sizes) / n) if sizes else None,
        "n_rare_clusters": int(sum(1 for s in sizes if s / n < 0.05)),
        "n_singleton_clusters": int(sum(1 for s in sizes if s <= 2)),
        "davies_bouldin": db,
        "calinski_harabasz": ch,
        "wcss": wcss,
        "bcss": bcss,
        "wcss_bcss_ratio": (wcss / bcss if wcss is not None and bcss else None),
        "modularity": modularity,
    }


def variance_explained(adata) -> dict:
    """PCA variance explained (tool_design.md §6.7) + knee detection."""
    pca = adata.uns.get("pca", {})
    var_ratio = pca.get("variance_ratio", [])
    if var_ratio is None:
        var_ratio = []
    var_ratio = [float(v) for v in var_ratio]
    cumulative = []
    s = 0.0
    for v in var_ratio:
        s += v
        cumulative.append(s)

    def n_pcs_for(target: float) -> Optional[int]:
        for i, c in enumerate(cumulative):
            if c >= target:
                return i + 1
        return len(cumulative) if cumulative else None

    knee = None
    if len(var_ratio) >= 2:
        gaps = [var_ratio[i] - var_ratio[i + 1] for i in range(len(var_ratio) - 1)]
        knee = int(np.argmax(gaps) + 2)  # index of the PC after the largest drop (1-based)

    top_loadings = {}
    try:
        loadings = pca.get("variance_loadings")
        if loadings is not None:
            var_names = list(adata.var_names)
            for pc in ("PC1", "PC2"):
                i = 0 if pc == "PC1" else 1
                if i < loadings.shape[1]:
                    order = np.argsort(np.abs(loadings[:, i]))[::-1][:10]
                    top_loadings[pc] = [str(var_names[j]) for j in order]
    except Exception:
        pass

    return {
        "per_pc": var_ratio,
        "cumulative": cumulative,
        "n_pcs_for_50pct": n_pcs_for(0.5),
        "n_pcs_for_80pct": n_pcs_for(0.8),
        "n_pcs_for_90pct": n_pcs_for(0.9),
        "variance_explained_knee": knee,
        "top_loading_genes": top_loadings,
    }


def resolution_stability(adata, res_list, chosen: Optional[str] = None) -> dict:
    """Adjacent-resolution ARI/NMI + cluster persistence (tool_design.md §6.8)."""
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    res_list = [str(r) for r in res_list]
    results: Dict[str, Any] = {"adjacent_ari": {}, "adjacent_nmi": {}}
    for i in range(len(res_list) - 1):
        r1, r2 = res_list[i], res_list[i + 1]
        if f"leiden_{r1}" not in adata.obs or f"leiden_{r2}" not in adata.obs:
            continue
        l1 = adata.obs[f"leiden_{r1}"].astype(str).values
        l2 = adata.obs[f"leiden_{r2}"].astype(str).values
        results["adjacent_ari"][f"{r1}-{r2}"] = float(adjusted_rand_score(l1, l2))
        results["adjacent_nmi"][f"{r1}-{r2}"] = float(normalized_mutual_info_score(l1, l2))

    # cluster persistence: chosen-res clusters that survive (Jaccard >= 0.7) in
    # every other resolution.
    persistence: Dict[str, float] = {}
    if chosen is not None and f"leiden_{chosen}" in adata.obs:
        base = adata.obs[f"leiden_{chosen}"].astype(str).values
        base_clusters = sorted(set(base))
        for c in base_clusters:
            mask = base == c
            cells = set(np.where(mask)[0])
            survives = []
            for r in res_list:
                if r == str(chosen) or f"leiden_{r}" not in adata.obs:
                    continue
                other = adata.obs[f"leiden_{r}"].astype(str).values
                best = 0.0
                for c2 in sorted(set(other)):
                    inter = len(cells & set(np.where(other == c2)[0]))
                    j = inter / max(len(cells) + len(set(np.where(other == c2)[0])) - inter, 1)
                    best = max(best, j)
                survives.append(best)
            persistence[str(c)] = float(np.mean(survives)) if survives else None
    n_stable = int(sum(1 for v in persistence.values() if v is not None and v >= 0.7))
    stability_at_chosen = None
    if chosen is not None:
        keys = [k for k in results["adjacent_ari"] if str(chosen) in k.split("-")]
        if keys:
            stability_at_chosen = float(np.mean([results["adjacent_ari"][k] for k in keys]))

    cluster_counts = {r: int(adata.obs[f"leiden_{r}"].nunique()) for r in res_list
                      if f"leiden_{r}" in adata.obs}
    counts_arr = [cluster_counts[r] for r in res_list if r in cluster_counts]
    derivative = None
    if len(counts_arr) >= 2:
        derivative = [float(counts_arr[i] - counts_arr[i - 1]) for i in range(1, len(counts_arr))]
    knee = None
    if len(counts_arr) >= 3:
        # largest drop in cluster count between adjacent resolutions;
        # returns the resolution string AFTER the drop (matching choose_resolution)
        drops = [counts_arr[i] - counts_arr[i + 1] for i in range(len(counts_arr) - 1)]
        if max(drops) > 0:
            knee = res_list[int(np.argmax(drops)) + 1]

    return {
        "adjacent_ari": results["adjacent_ari"],
        "adjacent_nmi": results["adjacent_nmi"],
        "cluster_persistence": persistence,
        "n_stable_clusters": n_stable,
        "stability_at_chosen_resolution": stability_at_chosen,
        "resolution_cluster_counts": cluster_counts,
        "n_clusters_derivative": derivative,
        "resolution_knee": knee,
    }


def candidate_autocorr(cluster_adata, candidate1_markers: Sequence[str],
                       candidate2_markers: Sequence[str]) -> dict:
    """Moran's I / Geary's C of candidate-preference score on a kNN graph.

    Requires the kNN graph to exist on ``cluster_adata`` (obsp connectivities).
    Returns the preference-score distribution for the LLM to judge bimodality.
    """
    import scanpy as sc

    c1 = [g for g in candidate1_markers if g in cluster_adata.var_names or
          (cluster_adata.raw is not None and g in cluster_adata.raw.var_names)]
    c2 = [g for g in candidate2_markers if g in cluster_adata.var_names or
          (cluster_adata.raw is not None and g in cluster_adata.raw.var_names)]
    result: Dict[str, Any] = {}
    try:
        if c1:
            sc.tl.score_genes(cluster_adata, c1, score_name="cand1_score", use_raw=True)
        if c2:
            sc.tl.score_genes(cluster_adata, c2, score_name="cand2_score", use_raw=True)
        s1 = cluster_adata.obs["cand1_score"].values if "cand1_score" in cluster_adata.obs else np.zeros(cluster_adata.n_obs)
        s2 = cluster_adata.obs["cand2_score"].values if "cand2_score" in cluster_adata.obs else np.zeros(cluster_adata.n_obs)
        x = s1 - s2
        moran = float(sc.metrics.morans_i(cluster_adata, vals=x))
        geary = float(sc.metrics.gearys_c(cluster_adata, vals=x))
        dist = describe_distribution(x)
        result = {
            "morans_i": moran,
            "gearys_c": geary,
            "score_distribution": dist,
            "score_bimodality_coefficient": dist.get("bimodality_coefficient") if dist else None,
            "cand1_score_mean": float(s1.mean()),
            "cand2_score_mean": float(s2.mean()),
            "n_cand1_markers_available": len(c1),
            "n_cand2_markers_available": len(c2),
        }
    except Exception as exc:  # score_genes can fail on degenerate inputs
        result = {"error": str(exc), "morans_i": None, "gearys_c": None}
    return result


# ---------------------------------------------------------------------------
# --dump-schema contract (implementation_plan.md §3.2, see skills/echo)
# ---------------------------------------------------------------------------

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def arg_spec(action: argparse.Action) -> Optional[dict]:
    """Introspect one argparse action into the loader's arg declaration.

    Returns ``None`` (i.e. drop the arg from the schema) when the action
    declares itself hidden via ``argparse.SUPPRESS`` as either default or help.
    This is how skill scripts signal "B-class / environment arg — don't show
    to the LLM". Loader sees no entry, so the LLM has no way to set it; the
    runtime layer (``env_or_default``) still resolves CLI override > env > default.
    """
    if action.dest == "help":
        return None
    # SUPPRESS on default OR help means "hide from tool schema entirely".
    if action.default is argparse.SUPPRESS or action.help is argparse.SUPPRESS:
        return None
    opt = action.option_strings
    name = opt[0].lstrip("-").replace("-", "_") if opt else action.dest
    if action.nargs == 0 or action.const is True or action.const is False:
        atype = "boolean"
    elif action.type is not None:
        atype = _TYPE_MAP.get(action.type, "string")
    elif action.default is not None:
        atype = _TYPE_MAP.get(type(action.default), "string")
    else:
        atype = "string"
    return {
        "name": name,
        "type": atype,
        "required": bool(action.required) or (not opt),
        "default": action.default if action.default is not None else None,
        "help": action.help or "",
    }


def dump_schema(parser: argparse.ArgumentParser) -> int:
    """Print the self-description JSON as the last stdout line."""
    subparsers = parser._subparsers._group_actions[0].choices
    tools = []
    for subcommand, sp in subparsers.items():
        args = [a for a in (arg_spec(act) for act in sp._actions) if a]
        tools.append({
            "subcommand": subcommand,
            "description": sp.description or "",
            "args": args,
        })
    print(json.dumps({"tools": tools}, ensure_ascii=False))
    return 0


def emit(result: dict) -> int:
    """Print the tool result contract as the last stdout line.

    allow_nan=False + no default=str: non-finite / non-serializable content
    fails loudly instead of emitting an invalid or silently-mangled JSON line.
    """
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


def ok(data: Any) -> dict:
    return {"status": "ok", "data": data}


def fail(message: str, **extra) -> dict:
    return {"status": "error", "error": message, **extra}


# ---------------------------------------------------------------------------
# CLI / env helpers (tool_design.md §10: CLI flags > env > defaults)
# ---------------------------------------------------------------------------

def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-dir", default="output",
                        help="项目目录(含 run_log.jsonl 与各 step 数据目录)")
    parser.add_argument("--input", default=None,
                        help="输入 h5ad 路径(缺省时按 step 约定自动寻找)")


def add_neo4j_args(parser: argparse.ArgumentParser) -> None:
    """Declare the three Neo4j CLI flags.

    Flags default to ``argparse.SUPPRESS`` so they are omitted from the
    ``--dump-schema`` tool declaration (loader does not see them, hence the
    LLM is not nudged to set them). At runtime, ``neo4j_config`` resolves
    CLI value > env var > built-in default via ``env_or_default``.

    Note: add_neo4j_args is currently called only by step3_kg. If other
    skills start needing Neo4j, keep this in common.py (skill-agnostic).
    """
    parser.add_argument("--uri", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--user", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--password", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def env_or_default(args, name: str, env_keys: tuple = (), default=None, cast=None):
    """Read a CLI arg that may use ``argparse.SUPPRESS`` with env > fallback.

    Resolution order (first non-SUPPRESS wins):
        1. ``getattr(args, name, SUPPRESS)`` — the CLI flag, if the LLM/operator passed it
        2. ``os.environ[k]`` for each k in ``env_keys`` (in order) — if set and non-empty
        3. ``default`` — the code-level fallback

    Args:
        args:        argparse.Namespace from parse_args().
        name:        attribute name (e.g. "min_confidence", matches the dest of --min-confidence).
        env_keys:    tuple of environment variable names to try (in order). Pass () to skip env.
        default:     value returned when CLI and env both yield SUPPRESS / unset.
        cast:        optional callable (e.g. int, float) applied to the resolved value
                     **except** when the value came from CLI (already typed by argparse).

    Returns:
        The resolved value. Never returns argparse.SUPPRESS.

    Use case: an arg declared as ``default=argparse.SUPPRESS, help=argparse.SUPPRESS``
    is hidden from the tool schema (so it doesn't clutter the LLM's tool
    description), but operators can still override on the CLI or via env.
    """
    raw = getattr(args, name, argparse.SUPPRESS)
    if raw is not argparse.SUPPRESS:
        # argparse already typed the CLI value; respect it as-is.
        return raw
    for k in env_keys:
        v = os.environ.get(k)
        if v is not None and v != "":
            return cast(v) if cast is not None else v
    return cast(default) if (cast is not None and default is not None) else default


def neo4j_config(args) -> dict:
    """Resolve Neo4j credentials: CLI flags > env vars > defaults. Never hardcode passwords."""
    uri = env_or_default(args, "uri", ("NEO4J_URI",), "bolt://localhost:7687")
    user = env_or_default(args, "user", ("NEO4J_USER",), "neo4j")
    password = env_or_default(args, "password", ("NEO4J_PASSWORD",), None)
    return {"uri": uri, "user": user, "password": password}


def parse_float_list(value: Optional[str]) -> List[float]:
    """Parse '0.4,0.6,0.8' -> [0.4, 0.6, 0.8]."""
    if not value:
        return []
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def check_positive(args, names, kind: str = "int") -> Optional[str]:
    """Validate numeric CLI args are strictly positive; returns error text or None."""
    for n in names:
        v = getattr(args, n, None)
        if v is None:
            continue
        if kind == "int" and not isinstance(v, bool) and int(v) <= 0:
            return f"--{n.replace('_', '-')} 必须为正整数(当前 {v})"
        if kind == "float" and not isinstance(v, bool) and float(v) <= 0:
            return f"--{n.replace('_', '-')} 必须为正数(当前 {v})"
    return None


def flatten(values: Iterable[Any]) -> List[Any]:
    out = []
    for v in values:
        out.extend(v)
    return out


def resolve_batch_key(obs_columns, batch_key: Optional[str], default: str = "Orig.ident") -> str:
    """Pick the batch column: requested key, else a categorical/object column.

    Column names are matched case-insensitively (Seurat ``Orig.ident`` vs
    ``orig.ident``). Raises ValueError when no suitable column exists (never
    silently picks a numeric QC column as a batch label).
    """
    lower = {str(c).lower(): c for c in obs_columns}

    def _hit(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        if name in obs_columns:
            return name
        return lower.get(str(name).lower())

    for candidate in (batch_key, default, "Dataset", "sample", "batch", "Libraries"):
        found = _hit(candidate)
        if found:
            return found
    raise ValueError(
        f"找不到批次列:未指定 --batch-key,且 obs 中没有 {default!r}/Dataset/sample/batch/Libraries 列。"
        f"可用列:{list(obs_columns)[:20]}"
    )


# ---------------------------------------------------------------------------
# Skill-level dotenv loader (mirrors the pattern of harness/config.py but
# scoped to the cell-annotation skill's own .env file).
#
# Why here (and not a separate _skill_dotenv.py): the dotenv loader has
# exactly one caller (this module) and one consumer (cell-annotation skill
# scripts). Inlining keeps the file count low and the side-effect visible at
# the bottom of the same file that downstream readers will already be in.
#
# SCOPE: only Neo4j connection credentials live in SKILL_DOTENV_KEYS. The
# cell-annotation skill owns Neo4j access (it connects from step3_kg and
# scripts/build_label_map.py); the harness does not know Neo4j exists.
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent  # .../skills/cell-annotation/
SKILL_ENV_EXAMPLE = SKILL_DIR / ".env.example"
SKILL_ENV = SKILL_DIR / ".env"
SKILL_DOTENV_KEYS: tuple[str, ...] = (
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
)
SKIP_SKILL_DOTENV_VAR = "CELL_ANNOTATION_SKIP_DOTENV"


def load_skill_dotenv(override: bool = False) -> list[str]:
    """Load the cell-annotation skill's own ``.env`` into ``os.environ``.

    Files read, in increasing priority (later wins):
        - ``<skill_dir>/.env.example`` (tracked template) — seeds defaults
        - ``<skill_dir>/.env`` (gitignored, per-user secrets) — overrides

    Within a single file, shell env still wins unless ``override=True``;
    this mirrors ``harness/config.py:load_dotenv``.

    Returns:
        List of keys actually populated from disk (only SKILL_DOTENV_KEYS).
        Unknown keys are still loaded into ``os.environ`` so users can stash
        extras, but they are not reported.

    Disable loading (tests / CI): set ``CELL_ANNOTATION_SKIP_DOTENV=1`` in
    the environment *before* importing ``common``.
    """
    if os.environ.get(SKIP_SKILL_DOTENV_VAR) == "1":
        return []
    try:
        from dotenv import dotenv_values
    except ImportError:
        return []

    pre_existing: set[str] = set(os.environ)
    populated: list[str] = []

    # Pass 1: .env.example seeds unset keys.
    seeded_from_template: set[str] = set()
    if SKILL_ENV_EXAMPLE.is_file():
        try:
            values = dotenv_values(SKILL_ENV_EXAMPLE, interpolate=False)
        except Exception:
            values = None
        if values:
            for key, value in values.items():
                if value is None or not value:
                    continue
                if key in os.environ:
                    continue
                os.environ[key] = value
                seeded_from_template.add(key)
                if key in SKILL_DOTENV_KEYS:
                    populated.append(key)

    # Pass 2: .env overrides template-seeded values; shell env wins.
    if SKILL_ENV.is_file():
        try:
            values = dotenv_values(SKILL_ENV, interpolate=False)
        except Exception:
            values = None
        if values:
            for key, value in values.items():
                if value is None or not value:
                    continue
                if key in pre_existing:
                    continue
                os.environ[key] = value
                seeded_from_template.discard(key)
                if key in SKILL_DOTENV_KEYS:
                    populated.append(key)

    if override:
        for path in (SKILL_ENV_EXAMPLE, SKILL_ENV):
            if not path.is_file():
                continue
            try:
                values = dotenv_values(path, interpolate=False)
            except Exception:
                continue
            if not values:
                continue
            for key, value in values.items():
                if value is None or not value:
                    continue
                os.environ[key] = value
                if key in SKILL_DOTENV_KEYS:
                    populated.append(key)
    return populated


# Eager bootstrap: any script that imports this module gets the skill's
# environment loaded once, at import time. The load is idempotent.
load_skill_dotenv()


# ---------------------------------------------------------------------------
# Cross-species species name normalization (SPEC cross-species-routing CAP-2)
# ---------------------------------------------------------------------------
# Ensembl Compara REST returns target.species in Ensembl's namespace
# (Plant: lower_underscore like ``arabidopsis_thaliana``; Vertebrate:
# lower_underscore like ``homo_sapiens``). Neo4j KG stores Species in a
# mix of formats: Plant uses lower_underscore; Animal uses TitleCase with
# spaces (``Human``, ``Mus musculus``). When an ortholog from Ensembl needs
# to be looked up in the KG, normalize via the alias table below.
#
# Verified 2026-08 via Neo4j live query; Plant entries not listed because
# they already match Ensembl format.

SPECIES_NAME_ALIASES: dict[tuple[str, str], str] = {
    # (Ensembl name, species_type) -> KG Species string
    ("homo_sapiens", "Animal"): "Human",
    ("mus_musculus", "Animal"): "Mus musculus",
    ("danio_rerio", "Animal"): "Danio rerio",
    ("monopterus_albus", "Animal"): "Monopterus albus",
    ("oreochromis_niloticus", "Animal"): "Oreochromis niloticus",
    ("gasterosteus_aculeatus", "Animal"): "Gasterosteus aculeatus",
    ("astyanax_mexicanus", "Animal"): "Astyanax mexicanus",
    ("nothobranchius_furzeri", "Animal"): "Nothobranchius furzeri",
    ("oncorhynchus_mykiss", "Animal"): "Oncorhynchus mykiss",
    ("mastacembelus_armatus", "Animal"): "Mastacembelus armatus",
    ("oryzias_latipes", "Animal"): "Oryzias latipes",
}


def normalize_species_name(name: str, species_type: str) -> str:
    """Translate Ensembl-formatted species name to KG Species string.

    ``name`` is whatever the Ensembl endpoint returned in ``target.species``
    (lower_underscore, e.g. ``homo_sapiens``). ``species_type`` is one of
    ``Plant`` / ``Animal`` / ``Fungi`` / ``Metazoa`` / ``Protists`` / ``Bacteria``
    (defaults to ``Plant`` if unknown — Plant names match Ensembl verbatim).

    Returns the KG string to use in Cypher queries (``WHERE g.Species = ...``).
    Falls back to ``name`` unchanged if no alias is registered, assuming the
    KG and Ensembl naming conventions already match (true for Plant).
    """
    if not name:
        return name
    return SPECIES_NAME_ALIASES.get((name, species_type), name)


# Ensembl REST base hosts per species division. Plant has its own deployment
# (rest.plants.ensembl.org); vertebrates share rest.ensembl.org. step2_ortholog
# routes by species_type; this is the single source of truth.
ENSEMBL_REST_HOSTS: dict[str, str] = {
    "Plant": "https://rest.plants.ensembl.org",
    "Animal": "https://rest.ensembl.org",
    "Fungi": "https://rest.fungi.ensembl.org",
    "Metazoa": "https://rest.metazoa.ensembl.org",
    "Protists": "https://rest.protists.ensembl.org",
    "Bacteria": "https://rest.bacteria.ensembl.org",
}


def ensembl_rest_host(species_type: str) -> str:
    """Return the Ensembl REST base host for the given species division.

    Defaults to ``https://rest.ensembl.org`` (vertebrates) when species_type
    is unknown — safer than failing, because a wrong host typically 404s on
    the first marker which the caller can then handle as ``unmapped``.
    """
    return ENSEMBL_REST_HOSTS.get(species_type, "https://rest.ensembl.org")

