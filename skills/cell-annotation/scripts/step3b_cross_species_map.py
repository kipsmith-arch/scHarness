"""Step 3 — cross-species gene mapping to raise KG hit rate (SPEC CAP-2).

Belongs to SOP-3 (query the knowledge graph), not SOP-2 (find markers).
Marker lists are only the input; the purpose of mapping is to look up
reference-species genes that the KG actually covers.

Was ``step2_ortholog.py`` then ``step2_cross_species_map.py`` then
``step3_cross_species_map.py``; renamed 2026-09-08 to ``step3b_`` so
SOP-3 order is visible in the filename (3a precheck → 3b map → 3c query).
Dispatches to a registered provider via ``--provider``. Default provider
is ``ensembl_compara``.

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
- Output: step3b_cross_species_map/cross_species_map.json (+ cache file)
- Cache key: (target_species, sorted_ref_species, provider_name, md5(markers.json))
- Provider unreachable -> warn, return empty map, never block pipeline.

Adding a new provider: write a class in step3b_xmap_providers/
implementing BaseCrossSpeciesProvider; register it with @register_provider.
No changes to this CLI, no changes to LLM-facing args, no changes to step3c_kg.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  -- also triggers load_skill_dotenv()

from step3b_xmap_providers import PROVIDERS, get_provider  # noqa: E402
from step3b_xmap_providers.base import (  # noqa: E402
    BaseCrossSpeciesProvider,
    MappingRecord,
)
from step3b_xmap_providers.ensembl_compara import check_dns  # noqa: E402
from step3b_xmap_providers import blastp as blastp_mod  # noqa: E402


DEFAULT_PROVIDER = "ensembl_compara"
DEFAULT_MIN_SCORE = 30.0
DEFAULT_MAX_HITS_PER_GENE = 1
MAX_REFERENCE_SPECIES = 3
DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RETRIES = 4
DEFAULT_CONCURRENCY = 8
BLASTP_TIMEOUT_DEFAULT = blastp_mod.BLASTP_TIMEOUT_DEFAULT


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="step3b_cross_species_map.py",
        description="Step 2.5 cross-species gene mapping via pluggable provider (0 h5ad)",
    )
    p.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="subcommand")

    p_r = sub.add_parser("run", help="map target markers to reference species via provider")
    # Provider selection (was hardcoded before refactor)
    p_r.add_argument("--provider", default=DEFAULT_PROVIDER,
                     choices=sorted(PROVIDERS.keys()),
                     help=f"mapping provider (default {DEFAULT_PROVIDER}; "
                          f"if --query-fasta is set, blastp is preferred)")
    p_r.add_argument("--query-fasta", default=None,
                     help="query protein FASTA. When this file exists, blastp is used "
                          "even if --provider is still ensembl_compara. "
                          "Also CELL_ANNOTATION_QUERY_FASTA.")
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
                     help=f"cap per (gene, reference-species) hits by score (default {DEFAULT_MAX_HITS_PER_GENE}; best-1)")
    p_r.add_argument("--force-refresh", action="store_true",
                     help="ignore cache and re-call the provider even if a cached map exists")
    # Provider-specific tuning (passed through to provider.lookup)
    p_r.add_argument("--provider-timeout", type=int, default=DEFAULT_TIMEOUT,
                     help=(f"per-request timeout in seconds (Ensembl default {DEFAULT_TIMEOUT}; "
                           f"blastp default {BLASTP_TIMEOUT_DEFAULT} = 30 min unless this flag "
                           f"is set to a value other than {DEFAULT_TIMEOUT})"))
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


def _timeout_for_provider(provider_name: str, requested: int | None) -> int:
    """Ensembl REST stays at 10s; blastp uses 30 min unless CLI overrides.

    argparse default is still 10 so Ensembl / dump-schema do not change. That
    sentinel is remapped for blastp; any other explicit value is honored.
    """
    if requested is None:
        requested = DEFAULT_TIMEOUT
    if provider_name == "blastp" and int(requested) == DEFAULT_TIMEOUT:
        return BLASTP_TIMEOUT_DEFAULT
    return int(requested)


def _flatten_species_list(values) -> list[str]:
    """Accept argparse append, dispatcher lists, or comma-separated strings."""
    out: list[str] = []
    for item in values or []:
        for part in str(item).replace(";", ",").split(","):
            name = part.strip()
            if name and name not in out:
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(target_species: str, reference_species: list[str],
               provider_name: str, markers_path: str,
               query_fasta: str | None = None) -> str:
    refs_joined = ",".join(sorted(reference_species))
    md5 = hashlib.md5()
    try:
        with open(markers_path, "rb") as f:
            md5.update(f.read())
    except OSError:
        md5.update(b"<unreadable>")
    if query_fasta:
        md5.update(b"|fasta:")
        try:
            with open(query_fasta, "rb") as f:
                md5.update(hashlib.md5(f.read()).digest())
        except OSError:
            md5.update(b"<unreadable-fasta>")
    digest = md5.hexdigest()[:8]
    return f"cross_species_map__{target_species}__{refs_joined}__{provider_name}__{digest}.json"


def _limit_reference_species(values: list[str], max_n: int = MAX_REFERENCE_SPECIES) -> tuple[list[str], list[str]]:
    if len(values) <= max_n:
        return values, []
    kept = values[:max_n]
    return kept, [f"--reference-species 超过 {max_n} 个,截断为先传入的 {max_n} 个: {kept}"]


def _resolve_provider_name(requested: str | None, query_fasta: str | None) -> tuple[str, list[str]]:
    """Pick the mapping backend.

    A readable query FASTA prefers ``blastp`` even if ``--provider`` is still
    the argparse default ``ensembl_compara`` (LLMs often echo schema defaults).
    No FASTA → requested name, or ``ensembl_compara``.
    """
    requested = (requested or "").strip() or DEFAULT_PROVIDER
    fasta_ok = bool(query_fasta and os.path.isfile(query_fasta))
    if fasta_ok:
        note = "已提供 query FASTA,优先使用 blastp"
        if requested != "blastp":
            note += f"（忽略 --provider {requested}）"
        return "blastp", [note]
    return requested, []


def _load_var_names(var_snapshot_path: str) -> set[str]:
    names: set[str] = set()
    with open(var_snapshot_path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if row and row[0]:
                names.add(row[0])
    return names


def _iter_fasta(path: str):
    header = None
    seq_chunks: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(seq_chunks)
                header = line[1:]
                seq_chunks = []
            else:
                seq_chunks.append(line)
        if header is not None:
            yield header, "".join(seq_chunks)


def filter_query_fasta(
    fasta_path: str,
    var_snapshot_path: str,
    marker_genes: list[str],
    out_path: str,
) -> tuple[list[str], dict, list[str]]:
    """Keep FASTA records in var_names ∩ marker_genes. Never loads h5ad."""
    warnings: list[str] = []
    marker_set = set(marker_genes)
    var_names = _load_var_names(var_snapshot_path)
    n_fasta = 0
    n_in_var = 0
    n_dropped_var = 0
    kept_ids: list[str] = []
    kept_recs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for header, seq in _iter_fasta(fasta_path):
        n_fasta += 1
        gid = blastp_mod.strip_seq_id(header)
        if gid not in var_names:
            n_dropped_var += 1
            continue
        n_in_var += 1
        if gid not in marker_set or gid in seen:
            continue
        seen.add(gid)
        kept_ids.append(gid)
        kept_recs.append((gid, seq))
    if n_dropped_var:
        warnings.append(f"query FASTA 丢弃 {n_dropped_var} 条不在 var_names 中的序列")
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for gid, seq in kept_recs:
            f.write(f">{gid}\n{seq}\n")
    metrics = {
        "n_fasta_seq": n_fasta,
        "n_kept_in_var": n_in_var,
        "n_kept_in_markers": len(kept_ids),
    }
    return kept_ids, metrics, warnings


def collapse_best1_across_refs(
    genes: list[str],
    per_ref: dict[str, dict[str, list[MappingRecord]]],
    max_hits_per_species: int = 1,
) -> dict[str, list]:
    """Keep up to N hits per reference species; keep every species (no global top-N)."""
    out: dict[str, list] = {}
    for gene in genes:
        rows: list[MappingRecord] = []
        for _ref, ref_map in per_ref.items():
            recs = sorted(ref_map.get(gene, []), key=lambda r: -r.score)[:max_hits_per_species]
            rows.extend(recs)
        rows.sort(key=lambda r: -r.score)
        out[gene] = [r.to_json() for r in rows]
    return out


def _cache_dir(project_dir: str) -> str:
    d = os.path.join(project_dir, "step3b_cross_species_map", "cache")
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
    """For REST-based providers, try DNS on the canonical host.

    Does **not** fall back across Ensembl divisions (Plant → vertebrates REST
    would query the wrong database for thousands of genes). Unreachable
    canonical host → ``host=None`` + warning; caller writes an empty map.

    Returns (host_to_use_or_None, warnings). ``None`` with empty warnings means
    the provider does not need a network host.
    """
    warnings: list[str] = []
    if override_host:
        return override_host, warnings
    default_host_fn = getattr(type(provider), "default_host_for", None)
    if default_host_fn is None:
        return None, warnings
    canonical = default_host_fn(species_type)
    if check_dns(canonical):
        return canonical, warnings
    warnings.append(
        f"默认 provider 端点 {canonical} DNS 解析失败,不跨 division fallback;"
        f"返回空映射"
    )
    return None, warnings


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
    common.exec_record(log_path, "step3b_cross_species_map", "collect_marker_genes", params, m)
    return genes_sorted, len(payload.get("per_cluster", {}))


def _uses_batch_lookup(provider) -> bool:
    return type(provider).lookup_many is not BaseCrossSpeciesProvider.lookup_many


def op_query_provider(genes: list[str], target_species: str, reference_species: list[str],
                      provider, host: str | None,
                      min_score: float, max_hits: int,
                      timeout: int, max_retries: int, concurrency: int,
                      log_path: str, params: dict,
                      query_fasta: str | None = None,
                      blastdb_dir: str | None = None,
                      extra_warnings: list[str] | None = None) -> tuple[dict, dict, str | None]:
    """Call the chosen provider.

    BLAST overrides ``lookup_many`` (one process per ref, refs in series;
    ``-num_threads`` uses the CPU budget). Ensembl keeps the per-gene thread
    pool so REST concurrency is unchanged.

    Returns ``(per_ref, stats, failover_error)``. ``failover_error`` is set when
    the batch provider cannot run (process non-zero / db unreadable).
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
    extra_warnings = extra_warnings if extra_warnings is not None else []

    if _uses_batch_lookup(provider):
        # BLAST: one process per reference species, sequential across refs.
        # Parallelism is inside blastp via -num_threads (all CPUs on the current job).
        num_threads = blastp_mod.blast_thread_budget(1)
        for ref in reference_species:
            if getattr(provider, "name", "") == "blastp" and not blastp_mod.species_in_blast_catalog(ref):
                extra_warnings.append(
                    f"参考物种 {ref} 不在 BLAST 27 库中,该 ref 空映射"
                )
                per_ref[ref] = {g: [] for g in genes}
                continue
            mapping, err = provider.lookup_many(
                target_species, ref, genes,
                timeout=timeout, max_retries=max_retries, host=host,
                query_fasta=query_fasta, blastdb_dir=blastdb_dir,
                num_threads=num_threads,
            )
            stats["n_requests"] += 1
            if err or mapping is None:
                stats["n_errors"] += 1
                common.exec_record(log_path, "step3b_cross_species_map", "query_provider", params, stats)
                return per_ref, stats, err or "lookup_many returned no mapping"
            for gene in genes:
                recs = mapping.get(gene, []) or []
                kept = [r for r in recs if r.score >= min_score]
                kept.sort(key=lambda r: -r.score)
                per_ref[ref][gene] = kept[:max_hits]
        common.exec_record(log_path, "step3b_cross_species_map", "query_provider", params, stats)
        return per_ref, stats, None

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
            kept = [r for r in records if r.score >= min_score]
            kept.sort(key=lambda r: -r.score)
            per_ref[ref][sym] = kept[:max_hits]

    common.exec_record(log_path, "step3b_cross_species_map", "query_provider", params, stats)
    return per_ref, stats, None


def op_write_output(out_dir: str, log_path: str, params: dict, payload: dict) -> dict:
    json_path = os.path.join(out_dir, "cross_species_map.json")
    common.write_json(json_path, payload)
    m = {"cross_species_map_json": json_path,
         "n_target_genes": payload["summary"]["n_input_genes"],
         "n_mapped": payload["summary"]["n_mapped"]}
    common.exec_record(log_path, "step3b_cross_species_map", "write_output", params, m)
    return m


def _blastp_prepare(args, project_dir: str, out_dir: str, log: str,
                    params: dict, genes: list[str], extra_warnings: list[str]) -> str | None:
    """Return a failover reason, or None if BLAST can run.

    On success sets ``params['filtered_fasta']`` and ``params['blastdb_dir']``.
    """
    import shutil

    if shutil.which("blastp") is None:
        return "PATH 无 blastp"
    query_fasta = getattr(args, "query_fasta", None) or os.environ.get("CELL_ANNOTATION_QUERY_FASTA") or None
    if not query_fasta or not os.path.isfile(query_fasta):
        return "无可用 query FASTA"
    var_path = os.path.join(project_dir, "step1_prepare", "var_snapshot.csv")
    if not os.path.isfile(var_path):
        return f"缺少 var_snapshot.csv ({var_path})"
    filtered_path = os.path.join(out_dir, "query.filtered.fa")
    kept, metrics, warns = filter_query_fasta(query_fasta, var_path, genes, filtered_path)
    extra_warnings.extend(warns)
    common.exec_record(log, "step3b_cross_species_map", "filter_query_fasta", params, metrics)
    if not kept:
        return "FASTA ∩ marker ∩ var_names 为空"
    cfg = blastp_mod.blastdb_config()
    try:
        blastp_mod.ensure_blastdb(cfg["dir"], cfg["url"], cfg["sha256"])
    except blastp_mod.BlastDbError as exc:
        return f"BLAST 库下载/校验失败: {exc}"
    params["filtered_fasta"] = filtered_path
    params["blastdb_dir"] = cfg["dir"]
    return None


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def cmd_run(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    out_dir = common.step_dir(project_dir, "step3b_cross_species_map")
    log = common.run_log_path(project_dir)
    species_type = args.species_type or "Plant"
    reference_species = _flatten_species_list(args.reference_species)
    extra_warnings: list[str] = []
    reference_species, trunc_warns = _limit_reference_species(reference_species)
    extra_warnings.extend(trunc_warns)

    if not reference_species:
        return common.fail("至少需要一个 --reference-species")

    query_fasta = getattr(args, "query_fasta", None) or os.environ.get("CELL_ANNOTATION_QUERY_FASTA") or None
    if query_fasta == "":
        query_fasta = None
    provider_name, auto_warns = _resolve_provider_name(getattr(args, "provider", None), query_fasta)
    extra_warnings.extend(auto_warns)
    try:
        provider = get_provider(provider_name)
    except ValueError as e:
        return common.fail(str(e))

    filtered_fasta = None
    blastdb_dir = None
    cache_fasta = None

    params = {
        "provider": provider.name,
        "target_species": args.target_species,
        "reference_species": reference_species,
        "species_type": species_type,
        "min_score": args.min_score,
        "max_hits_per_gene": args.max_hits_per_gene,
        "host": None,
        "force_refresh": args.force_refresh,
    }

    genes, n_clusters = op_collect_marker_genes(args.input, log, params)
    if args.max_genes > 0:
        genes = genes[:args.max_genes]

    if provider.name == "blastp":
        failover_reason = _blastp_prepare(
            args, project_dir, out_dir, log, params, genes, extra_warnings,
        )
        if failover_reason is None:
            filtered_fasta = params.get("filtered_fasta")
            blastdb_dir = params.get("blastdb_dir")
            cache_fasta = query_fasta
        else:
            extra_warnings.append(
                f"BLAST 不可用({failover_reason}),同组 ref 改道 ensembl_compara"
            )
            provider = get_provider("ensembl_compara")
            params["provider"] = provider.name

    host, host_warnings = _ensure_host_reachable(provider, species_type, args.ensembl_rest_host)
    provider_warnings = _ensure_provider_available(provider, species_type)
    params["host"] = host
    all_pre_warnings = extra_warnings + host_warnings + provider_warnings

    cache_path = os.path.join(
        _cache_dir(project_dir),
        _cache_key(args.target_species, reference_species, provider.name, args.input,
                   query_fasta=cache_fasta),
    )
    if not args.force_refresh:
        cached = _try_cache_load(cache_path)
        if cached and cached.get("target_species") == args.target_species \
                and sorted(cached.get("reference_species", [])) == sorted(reference_species) \
                and cached.get("provider") == provider.name:
            cached["cache_hit"] = True
            cached.setdefault("warnings", [])
            cached["warnings"] = list(cached["warnings"]) + all_pre_warnings
            write_meta = op_write_output(out_dir, log, params, cached)
            return common.ok({
                "cross_species_map_json": write_meta["cross_species_map_json"],
                "cache_hit": True,
                "n_target_genes": cached["summary"]["n_input_genes"],
                "n_mapped": cached["summary"]["n_mapped"],
                "hit_rate": cached["summary"]["hit_rate"],
                "warnings": cached.get("warnings", []),
            })

    needs_host = getattr(type(provider), "default_host_for", None) is not None
    if needs_host and host is None:
        empty = _empty_payload(
            args, provider, reference_species, species_type, host,
            n_clusters=n_clusters,
            host_warnings=all_pre_warnings,
            extra_warning="provider REST 不可达,跳过逐基因查询",
        )
        write_meta = op_write_output(out_dir, log, params, empty)
        _cache_save(cache_path, empty)
        return common.ok({
            "cross_species_map_json": write_meta["cross_species_map_json"],
            "cache_hit": False,
            "n_target_genes": empty["summary"]["n_input_genes"],
            "n_mapped": 0,
            "hit_rate": 0.0,
            "warnings": empty["warnings"],
        })

    if not genes:
        empty = _empty_payload(args, provider, reference_species, species_type, host,
                               n_clusters=0, host_warnings=all_pre_warnings,
                               extra_warning="markers.json 中无 marker_genes")
        write_meta = op_write_output(out_dir, log, params, empty)
        _cache_save(cache_path, empty)
        return common.ok({**write_meta, "cache_hit": False, "hit_rate": 0.0,
                          "warnings": empty["warnings"]})

    t0 = time.time()
    per_ref, stats, batch_err = op_query_provider(
        genes, args.target_species, reference_species, provider, host,
        args.min_score, args.max_hits_per_gene,
        _timeout_for_provider(provider.name, args.provider_timeout),
        args.provider_max_retries, args.provider_concurrency,
        log, params, query_fasta=filtered_fasta, blastdb_dir=blastdb_dir,
        extra_warnings=extra_warnings)
    if batch_err and provider.name == "blastp":
        extra_warnings.append(
            f"BLAST 不可用({batch_err}),同组 ref 改道 ensembl_compara"
        )
        provider = get_provider("ensembl_compara")
        params["provider"] = provider.name
        cache_fasta = None
        filtered_fasta = None
        blastdb_dir = None
        host, host_warnings = _ensure_host_reachable(provider, species_type, args.ensembl_rest_host)
        provider_warnings = _ensure_provider_available(provider, species_type)
        params["host"] = host
        all_pre_warnings = extra_warnings + host_warnings + provider_warnings
        cache_path = os.path.join(
            _cache_dir(project_dir),
            _cache_key(args.target_species, reference_species, provider.name, args.input),
        )
        if needs_host := (getattr(type(provider), "default_host_for", None) is not None):
            if host is None:
                empty = _empty_payload(
                    args, provider, reference_species, species_type, host,
                    n_clusters=n_clusters, host_warnings=all_pre_warnings,
                    extra_warning="provider REST 不可达,跳过逐基因查询",
                )
                write_meta = op_write_output(out_dir, log, params, empty)
                _cache_save(cache_path, empty)
                return common.ok({
                    "cross_species_map_json": write_meta["cross_species_map_json"],
                    "cache_hit": False, "n_target_genes": empty["summary"]["n_input_genes"],
                    "n_mapped": 0, "hit_rate": 0.0, "warnings": empty["warnings"],
                })
        per_ref, stats, _batch_err = op_query_provider(
            genes, args.target_species, reference_species, provider, host,
            args.min_score, args.max_hits_per_gene,
            _timeout_for_provider(provider.name, args.provider_timeout),
            args.provider_max_retries, args.provider_concurrency,
            log, params)
    elapsed = round(time.time() - t0, 3)

    cross_species_map = collapse_best1_across_refs(genes, per_ref, args.max_hits_per_gene)
    common.exec_record(
        log, "step3b_cross_species_map", "collapse_best1", params,
        {"n_genes": len(genes), "max_hits_per_species": args.max_hits_per_gene},
    )
    unmapped: list[str] = []
    all_scores: list[float] = []
    mapping_type_counts: dict[str, int] = {}
    score_type_counts: dict[str, int] = {}
    for gene in genes:
        records = cross_species_map.get(gene) or []
        if not records:
            unmapped.append(gene)
            continue
        for r in records:
            all_scores.append(r["score"])
            mapping_type_counts[r["mapping_type"]] = mapping_type_counts.get(r["mapping_type"], 0) + 1
            score_type_counts[r["score_type"]] = score_type_counts.get(r["score_type"], 0) + 1

    n_mapped = sum(1 for g in genes if cross_species_map.get(g))
    n_ref_per_target = []
    for g in genes:
        if cross_species_map.get(g):
            n_ref_per_target.append(len(set(r["ref_species"] for r in cross_species_map[g])))
    n_ref_per_target_mean = (sum(n_ref_per_target) / len(n_ref_per_target)) if n_ref_per_target else 0.0

    score_dist = common.describe_distribution(all_scores) if all_scores else None

    warnings = list(extra_warnings) + list(host_warnings) + list(provider_warnings)
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
