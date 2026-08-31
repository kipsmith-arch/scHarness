"""Step 2.5 — cross-species gene mapping (SPEC CAP-2, provider-abstraction refactor).

Refactored 2026-08-24: was ``step2_ortholog.py`` (Ensembl Compara hardcoded).
Now generic ``step2_cross_species_map.py`` that dispatches to a registered
provider via ``--provider``. Default provider is ``ensembl_compara``.

Subcommands:
    run — read markers.json, call the chosen provider for each marker
          against one or more reference species, write
          ``cross_species_map.json`` with target_gene -> list of
          MappingRecord (provider-neutral). 0 h5ad, pure JSON + provider.

Per SPEC cross-species-routing CAP-2 (revised):
- Inputs: --provider, --target-species, --reference-species (repeatable),
          --species-type, --input (markers.json path), --project-dir,
          --min-score (default 30, normalized 0~100),
          --max-hits-per-gene (default 3),
          --provider-timeout, --provider-max-retries, --provider-concurrency,
          --force-refresh, --max-genes
- Output: step2_cross_species_map/cross_species_map.json (+ cache file)
- Cache key: (target_species, sorted_ref_species, provider_name, md5(markers.json))
- Provider unreachable -> warn, return empty map, never block pipeline.

Adding a new provider: write a class in step2_cross_species_map/
implementing BaseCrossSpeciesProvider; register it with @register_provider.
No changes to this CLI, no changes to LLM-facing args, no changes to step3_kg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  -- also triggers load_skill_dotenv()

from step2_xmap_providers import PROVIDERS, get_provider  # noqa: E402
from step2_xmap_providers.base import MappingRecord  # noqa: E402
from step2_xmap_providers.ensembl_compara import check_dns  # noqa: E402


DEFAULT_PROVIDER = "ensembl_compara"
DEFAULT_MIN_SCORE = 30.0
DEFAULT_MAX_HITS_PER_GENE = 3
DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RETRIES = 4
DEFAULT_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="step2_cross_species_map.py",
        description="Step 2.5 cross-species gene mapping via pluggable provider (0 h5ad)",
    )
    p.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="subcommand")

    p_r = sub.add_parser("run", help="map target markers to reference species via provider")
    # Provider selection (was hardcoded before refactor)
    p_r.add_argument("--provider", default=DEFAULT_PROVIDER,
                     choices=sorted(PROVIDERS.keys()),
                     help=f"mapping provider (default {DEFAULT_PROVIDER}; see step2_cross_species_map/)")
    # A-class (LLM-visible): biological decisions + I/O paths
    p_r.add_argument("--target-species", required=True,
                     help="target species in Ensembl/KG format (lower_underscore)")
    p_r.add_argument("--reference-species", action="append", required=True,
                     help="reference species (repeat for multiple). Required, at least one.")
    p_r.add_argument("--species-type", default="Plant",
                     help="species type for provider routing (Plant / Animal / ...)")
    p_r.add_argument("--input", required=True,
                     help="path to step2_markers/markers.json")
    p_r.add_argument("--project-dir", default="output",
                     help="project directory (default: output)")
    # Score / hit filtering (provider-neutral names)
    p_r.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                     help=f"drop records with score < this (provider-neutral 0~100, default {DEFAULT_MIN_SCORE})")
    p_r.add_argument("--max-hits-per-gene", type=int, default=DEFAULT_MAX_HITS_PER_GENE,
                     help=f"cap per-gene results, take top N by score (default {DEFAULT_MAX_HITS_PER_GENE})")
    p_r.add_argument("--force-refresh", action="store_true",
                     help="ignore cache and re-call the provider even if a cached map exists")
    # Provider-specific tuning (passed through to provider.lookup)
    p_r.add_argument("--provider-timeout", type=int, default=DEFAULT_TIMEOUT,
                     help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT})")
    p_r.add_argument("--provider-max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                     help=f"max retries per request (default {DEFAULT_MAX_RETRIES})")
    p_r.add_argument("--provider-concurrency", type=int, default=DEFAULT_CONCURRENCY,
                     help=f"parallel workers (default {DEFAULT_CONCURRENCY})")
    p_r.add_argument("--max-genes", type=int, default=0,
                     help="cap input marker genes for smoke testing (0 = no cap)")
    # Provider-specific override (only honored by providers that need it)
    p_r.add_argument("--ensembl-rest-host", default=None,
                     help=argparse.SUPPRESS)  # legacy alias; CLI pass-through
    return p


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(target_species: str, reference_species: list[str],
               provider_name: str, markers_path: str) -> str:
    refs_joined = ",".join(sorted(reference_species))
    md5 = hashlib.md5()
    try:
        with open(markers_path, "rb") as f:
            md5.update(f.read())
    except OSError:
        md5.update(b"<unreadable>")
    digest = md5.hexdigest()[:8]
    return f"cross_species_map__{target_species}__{refs_joined}__{provider_name}__{digest}.json"


def _cache_dir(project_dir: str) -> str:
    d = os.path.join(project_dir, "step2_cross_species_map", "cache")
    os.makedirs(d, exist_ok=True)
    return d


def _try_cache_load(cache_path: str) -> dict | None:
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_save(cache_path: str, payload: dict) -> None:
    common.write_json(cache_path, payload)


# ---------------------------------------------------------------------------
# DNS / host resolution (provider-aware; future providers without REST may
# skip this — but it's harmless to call)
# ---------------------------------------------------------------------------

def _ensure_provider_available(provider, species_type: str) -> list[str]:
    """Return a list of warnings if the provider is not configured for this
    species division. Empty list means OK."""
    warnings: list[str] = []
    if not provider.available(species_type):
        warnings.append(
            f"provider {provider.name!r} 不支持 species_type={species_type!r};"
            f"查询可能失败或返 404"
        )
    return warnings


def _ensure_host_reachable(provider, species_type: str, override_host: str | None) -> tuple[str | None, list[str]]:
    """For REST-based providers, try DNS on the canonical host and fall back
    to a known-reachable host (vertebrates) if the canonical one fails. For
    non-REST providers (e.g. future local BLAST), this is a no-op.

    Returns (host_to_use_or_None, warnings). ``None`` means "provider doesn't
    need a network host (e.g. local DB); skip DNS precheck".
    """
    warnings: list[str] = []
    if override_host:
        return override_host, warnings
    # Providers may expose a default_host_for(species_type) classmethod; if so,
    # use it. Otherwise skip DNS precheck (provider handles its own resolution).
    default_host_fn = getattr(type(provider), "default_host_for", None)
    if default_host_fn is None:
        return None, warnings
    canonical = default_host_fn(species_type)
    if check_dns(canonical):
        return canonical, warnings
    warnings.append(f"默认 provider 端点 {canonical} DNS 解析失败,尝试备选")
    fallback = "https://rest.ensembl.org"
    if canonical != fallback and check_dns(fallback):
        warnings.append(f"fallback 到 {fallback} (vertebrates);跨 division 查询可能返 400/404")
        return fallback, warnings
    warnings.append(f"所有 provider 端点都不可达,使用 {canonical} (预期请求失败)")
    return canonical, warnings


# ---------------------------------------------------------------------------
# ops
# ---------------------------------------------------------------------------

def op_collect_marker_genes(markers_path: str, log_path: str, params: dict) -> tuple[list[str], int]:
    payload = common.read_json(markers_path)
    if not payload or "per_cluster" not in payload:
        raise ValueError(f"{markers_path} 缺少 per_cluster 块,无法提取 marker genes")
    genes: set[str] = set()
    for cluster_id, cluster in payload["per_cluster"].items():
        for g in cluster.get("marker_genes", []):
            if g:
                genes.add(g)
    genes_sorted = sorted(genes)
    m = {"n_input_marker_genes": len(genes_sorted), "source_markers_json": markers_path}
    common.exec_record(log_path, "step2_cross_species_map", "collect_marker_genes", params, m)
    return genes_sorted, len(payload.get("per_cluster", {}))


def op_query_provider(genes: list[str], target_species: str, reference_species: list[str],
                      provider, host: str | None,
                      min_score: float, max_hits: int,
                      timeout: int, max_retries: int, concurrency: int,
                      log_path: str, params: dict) -> tuple[dict, dict]:
    """Call the chosen provider for each (gene, reference_species) pair.

    Returns:
        per_ref: {ref_species: {gene: [MappingRecord, ...]}}
        stats: per_ref request counts, error counts.
    """
    per_ref: dict[str, dict[str, list[MappingRecord]]] = {ref: {} for ref in reference_species}
    stats: dict = {
        "n_requests": 0,
        "n_errors": 0,
        "n_429": 0,
        "n_404": 0,
        "n_400": 0,
        "n_other_errors": 0,
    }

    def _one(symbol: str, ref: str) -> tuple[str, str, list[MappingRecord] | None]:
        records, err = provider.lookup(
            target_species, ref, symbol,
            timeout=timeout, max_retries=max_retries, host=host,
        )
        return symbol, ref, records

    tasks = [(g, ref) for ref in reference_species for g in genes]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = [ex.submit(_one, g, r) for g, r in tasks]
        for fut in as_completed(futs):
            sym, ref, records = fut.result()
            stats["n_requests"] += 1
            if records is None:
                stats["n_errors"] += 1
                per_ref[ref][sym] = []
                continue
            # Filter by min_score and truncate by max_hits
            kept = [r for r in records if r.score >= min_score]
            kept.sort(key=lambda r: -r.score)
            per_ref[ref][sym] = kept[:max_hits]

    common.exec_record(log_path, "step2_cross_species_map", "query_provider", params, stats)
    return per_ref, stats


def op_write_output(out_dir: str, log_path: str, params: dict, payload: dict) -> dict:
    json_path = os.path.join(out_dir, "cross_species_map.json")
    common.write_json(json_path, payload)
    m = {"cross_species_map_json": json_path,
         "n_target_genes": payload["summary"]["n_input_genes"],
         "n_mapped": payload["summary"]["n_mapped"]}
    common.exec_record(log_path, "step2_cross_species_map", "write_output", params, m)
    return m


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def cmd_run(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    out_dir = common.step_dir(project_dir, "step2_cross_species_map")
    log = common.run_log_path(project_dir)
    species_type = args.species_type or "Plant"
    reference_species = list(dict.fromkeys(args.reference_species))  # dedupe, keep order

    if not reference_species:
        return common.fail("至少需要一个 --reference-species")

    # Provider instantiation
    try:
        provider = get_provider(args.provider)
    except ValueError as e:
        return common.fail(str(e))

    # Host precheck (provider-aware)
    host, host_warnings = _ensure_host_reachable(provider, species_type, args.ensembl_rest_host)
    provider_warnings = _ensure_provider_available(provider, species_type)

    params = {
        "provider": provider.name,
        "target_species": args.target_species,
        "reference_species": reference_species,
        "species_type": species_type,
        "min_score": args.min_score,
        "max_hits_per_gene": args.max_hits_per_gene,
        "host": host,
        "force_refresh": args.force_refresh,
    }

    # Cache check
    cache_path = os.path.join(_cache_dir(project_dir),
                              _cache_key(args.target_species, reference_species,
                                         provider.name, args.input))
    if not args.force_refresh:
        cached = _try_cache_load(cache_path)
        if cached and cached.get("target_species") == args.target_species \
                and sorted(cached.get("reference_species", [])) == sorted(reference_species) \
                and cached.get("provider") == provider.name:
            cached["cache_hit"] = True
            cached.setdefault("warnings", [])
            cached["warnings"] = list(cached["warnings"]) + host_warnings + provider_warnings
            write_meta = op_write_output(out_dir, log, params, cached)
            return common.ok({
                "cross_species_map_json": write_meta["cross_species_map_json"],
                "cache_hit": True,
                "n_target_genes": cached["summary"]["n_input_genes"],
                "n_mapped": cached["summary"]["n_mapped"],
                "hit_rate": cached["summary"]["hit_rate"],
                "warnings": cached.get("warnings", []),
            })

    # Live provider calls
    genes, n_clusters = op_collect_marker_genes(args.input, log, params)
    if args.max_genes > 0:
        genes = genes[:args.max_genes]

    if not genes:
        empty = _empty_payload(args, provider, reference_species, species_type, host,
                               n_clusters=0, host_warnings=host_warnings + provider_warnings,
                               extra_warning="markers.json 中无 marker_genes")
        write_meta = op_write_output(out_dir, log, params, empty)
        _cache_save(cache_path, empty)
        return common.ok({**write_meta, "cache_hit": False, "hit_rate": 0.0,
                          "warnings": empty["warnings"]})

    t0 = time.time()
    per_ref, stats = op_query_provider(
        genes, args.target_species, reference_species, provider, host,
        args.min_score, args.max_hits_per_gene,
        args.provider_timeout, args.provider_max_retries, args.provider_concurrency,
        log, params)
    elapsed = round(time.time() - t0, 3)

    # Build cross_species_map keyed by target_gene -> list of mappings (across refs).
    cross_species_map: dict[str, list] = {}
    unmapped: list[str] = []
    all_scores: list[float] = []
    mapping_type_counts: dict[str, int] = {}
    score_type_counts: dict[str, int] = {}

    for gene in genes:
        merged: dict[tuple[str, str], MappingRecord] = {}
        for ref, ref_map in per_ref.items():
            for rec in ref_map.get(gene, []):
                key = (rec.ref_species, rec.ref_gene_id)
                if key not in merged or rec.score > merged[key].score:
                    merged[key] = rec
        if not merged:
            unmapped.append(gene)
            cross_species_map[gene] = []
            continue
        records = sorted(merged.values(), key=lambda r: -r.score)[:args.max_hits_per_gene]
        cross_species_map[gene] = [r.to_json() for r in records]
        for r in records:
            all_scores.append(r.score)
            mapping_type_counts[r.mapping_type] = mapping_type_counts.get(r.mapping_type, 0) + 1
            score_type_counts[r.score_type] = score_type_counts.get(r.score_type, 0) + 1

    n_mapped = sum(1 for g in genes if cross_species_map.get(g))
    n_ref_per_target = []
    for g in genes:
        if cross_species_map.get(g):
            n_ref_per_target.append(len(set(r["ref_species"] for r in cross_species_map[g])))
    n_ref_per_target_mean = (sum(n_ref_per_target) / len(n_ref_per_target)) if n_ref_per_target else 0.0

    score_dist = common.describe_distribution(all_scores) if all_scores else None

    warnings = list(host_warnings) + list(provider_warnings)
    n_total = len(reference_species) * len(genes)
    if stats["n_errors"] >= n_total and n_total:
        warnings.append(f"provider 全部请求失败 (errors={stats['n_errors']}/{n_total})")
    elif stats["n_errors"] > 0 and stats["n_errors"] >= n_total * 0.5:
        warnings.append(f"provider 多数请求失败 (errors={stats['n_errors']}/{n_total})")

    payload = {
        "provider": provider.name,
        "target_species": args.target_species,
        "reference_species": reference_species,
        "species_type": species_type,
        "host_used": host,
        "computed_at": common.now_iso(),
        "cache_hit": False,
        "cross_species_map": cross_species_map,
        "unmapped_genes": unmapped,
        "summary": {
            "n_input_genes": len(genes),
            "n_clusters_in_source": n_clusters,
            "n_mapped": n_mapped,
            "n_unmapped": len(unmapped),
            "hit_rate": round(n_mapped / max(len(genes), 1), 4),
            "score_distribution": score_dist,
            "score_type_distribution": score_type_counts,
            "mapping_type_distribution": mapping_type_counts,
            "n_ref_genes_per_target_mean": round(n_ref_per_target_mean, 4),
            "provider_request_count": stats["n_requests"],
            "provider_elapsed_seconds": elapsed,
            "provider_errors": {"total": stats["n_errors"],
                                "400": stats["n_400"], "404": stats["n_404"],
                                "429": stats["n_429"], "other": stats["n_other_errors"]},
            "min_score_applied": args.min_score,
            "max_hits_per_gene_applied": args.max_hits_per_gene,
            "max_genes_applied": args.max_genes,
            "concurrency_applied": args.provider_concurrency,
        },
        "warnings": warnings,
    }

    _cache_save(cache_path, payload)
    write_meta = op_write_output(out_dir, log, params, payload)
    return common.ok({**write_meta, "cache_hit": False,
                      "hit_rate": payload["summary"]["hit_rate"],
                      "warnings": warnings})


def _empty_payload(args, provider, reference_species, species_type, host,
                   n_clusters: int, host_warnings: list[str],
                   extra_warning: str | None = None) -> dict:
    warnings = list(host_warnings)
    if extra_warning:
        warnings.append(extra_warning)
    return {
        "provider": provider.name,
        "target_species": args.target_species,
        "reference_species": reference_species,
        "species_type": species_type,
        "host_used": host,
        "computed_at": common.now_iso(),
        "cache_hit": False,
        "cross_species_map": {},
        "unmapped_genes": [],
        "summary": {"n_input_genes": 0, "n_clusters_in_source": n_clusters,
                    "n_mapped": 0, "n_unmapped": 0, "hit_rate": 0.0,
                    "score_distribution": None, "score_type_distribution": {},
                    "mapping_type_distribution": {}, "n_ref_genes_per_target_mean": 0.0,
                    "provider_request_count": 0, "provider_elapsed_seconds": 0.0,
                    "provider_errors": {"total": 0, "400": 0, "404": 0,
                                        "429": 0, "other": 0},
                    "min_score_applied": args.min_score,
                    "max_hits_per_gene_applied": args.max_hits_per_gene,
                    "max_genes_applied": args.max_genes,
                    "concurrency_applied": args.provider_concurrency},
        "warnings": warnings,
    }


def main(argv=None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(p)
    if not args.subcommand:
        p.print_help()
        return 2
    try:
        if args.subcommand == "run":
            result = cmd_run(args)
        else:
            result = common.fail(f"未知子命令:{args.subcommand}")
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"{args.subcommand} failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
