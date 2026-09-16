"""Local BLASTP provider for step3b_cross_species_map (SPEC-blastp-homology).

Subject libraries are a pre-built BLASTDB v5 protein zip (~27 species). Query
sequences come from a user FASTA. This provider never infers cell types —
it only emits MappingRecord rows for step3c_kg --ortholog-map.

Download happens only when this provider is selected and the local cache is
missing or fails checksum. Ensembl / skill-load paths must not call
``ensure_blastdb``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, ClassVar

from .base import BaseCrossSpeciesProvider, MappingRecord, register_provider


DEFAULT_BLASTDB_DIR = str(
    Path.home() / ".cache" / "annot-harness" / "cell-annotation" / "blastdb"
)
DEFAULT_BLASTDB_URL = "https://xener.dcs.cloud/api/public/download?file=blastdb.zip"
# Pin of the current zip (42,152,397 bytes, 2025-11-04 build). Override via
# CELL_ANNOTATION_BLASTDB_SHA256 when the zip is replaced.
DEFAULT_BLASTDB_SHA256 = "8dd83c925f8f18d7e3f2cf626ce780548085321ed501805247932c5b56dab1c3"
# One blastp process per reference species; 10s is the Ensembl REST default and
# is far too short for a filtered proteome query.
BLASTP_TIMEOUT_DEFAULT = 1800

# Zip interior prefixes (2025-11-04 build) → KG / CLI lower_underscore ids.
BLAST_PREFIX_TO_KG: dict[str, str] = {
    "Arabidopsis_thaliana": "arabidopsis_thaliana",
    "Brassica_rapa": "brassica_rapa",
    "Oryza_sativa": "oryza_sativa",
    "Zea_mays": "zea_mays",
    "Triticum_aestivum": "triticum_aestivum",
    "Glycine_max": "glycine_max",
    "Medicago_truncatula": "medicago_truncatula",
    "Solanum_lycopersicum": "solanum_lycopersicum",
    "Nicotiana_tabacum": "nicotiana_tabacum",
    "Nicotiana_attenuata": "nicotiana_attenuata",
    "Fragaria_vesca": "fragaria_vesca",
    "Manihot_esculenta": "manihot_esculenta",
    "Gossypium_hirsutum": "gossypium_hirsutum",
    "Bombax_ceiba": "bombax_ceiba",
    "Catharanthus_roseus": "catharanthus_roseus",
    "Populus_alba_var_pyramidalis": "populus_alba_var_pyramidalis",
    "Homo_sapiens": "homo_sapiens",
    "Mus_musculus": "mus_musculus",
    "Danio_rerio": "danio_rerio",
    "Oryzias_latipes": "oryzias_latipes",
    "Oreochromis_niloticus": "oreochromis_niloticus",
    "Oncorhynchus_mykiss": "oncorhynchus_mykiss",
    "Gasterosteus_aculeatus": "gasterosteus_aculeatus",
    "Astyanax_mexicanus": "astyanax_mexicanus",
    "Mastacembelus_armatus": "mastacembelus_armatus",
    "Monopterus_albus": "monopterus_albus",
    "Nothobranchius_furzeri": "nothobranchius_furzeri",
}
KG_TO_BLAST_PREFIX: dict[str, str] = {v: k for k, v in BLAST_PREFIX_TO_KG.items()}

_NCBI_PREFIX = re.compile(r"^(?:[A-Za-z]{2,3}\|)+")


class BlastDbError(Exception):
    """Subject-library download / checksum / extract failure."""


def strip_seq_id(raw: str) -> str:
    """Take the first FASTA/BLAST token and drop NCBI-style ``db|`` prefixes."""
    token = (raw or "").strip().split()[0] if raw else ""
    token = _NCBI_PREFIX.sub("", token)
    if "|" in token:
        parts = [p for p in token.split("|") if p]
        token = parts[0] if parts else token
    return token


def kg_to_blast_prefix(kg_id: str) -> str:
    """Map KG/CLI ``arabidopsis_thaliana`` to the BLAST filename prefix.

    Known 27 prefixes are an explicit table (binomial, not title-case-each).
    Unknown ids fall back to capitalizing each underscore segment.
    """
    if kg_id in KG_TO_BLAST_PREFIX:
        return KG_TO_BLAST_PREFIX[kg_id]
    return "_".join(p[:1].upper() + p[1:] if p else p for p in kg_id.split("_"))


def species_in_blast_catalog(kg_id: str) -> bool:
    return kg_id in KG_TO_BLAST_PREFIX


def blast_thread_budget(n_jobs: int = 1, cpu_count: int | None = None) -> int:
    """Threads for one ``blastp`` process (``-num_threads``).

    Reference species are queried serially, so the default is all CPUs on
    the single in-flight job. ``CELL_ANNOTATION_BLAST_THREADS`` overrides
    when ``cpu_count`` is not passed explicitly.
    """
    if cpu_count is None:
        override = os.environ.get("CELL_ANNOTATION_BLAST_THREADS")
        if override:
            try:
                return max(1, int(override))
            except ValueError:
                pass
        cpu_count = os.cpu_count() or 1
    return max(1, int(cpu_count) // max(1, n_jobs))


def blastdb_config() -> dict[str, str]:
    return {
        "dir": os.environ.get("CELL_ANNOTATION_BLASTDB_DIR") or DEFAULT_BLASTDB_DIR,
        "url": os.environ.get("CELL_ANNOTATION_BLASTDB_URL") or DEFAULT_BLASTDB_URL,
        "sha256": (os.environ.get("CELL_ANNOTATION_BLASTDB_SHA256") or DEFAULT_BLASTDB_SHA256).lower(),
    }


def _lock_dir(path: str) -> str:
    return path.rstrip("\\/") + ".lock"


def _acquire_dir_lock(lock_path: str, timeout: float = 300.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            os.mkdir(lock_path)
            return
        except FileExistsError:
            if time.time() >= deadline:
                raise BlastDbError(f"timed out waiting for blastdb lock {lock_path}")
            time.sleep(0.2)


def _release_dir_lock(lock_path: str) -> None:
    try:
        os.rmdir(lock_path)
    except OSError:
        pass


def _default_download(url: str, dest: str) -> None:
    """GET the zip. The hosting API returns 404 on HEAD, so probing must GET."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sentinel_pin(cache_dir: str) -> str:
    return os.path.join(cache_dir, "prot", "Arabidopsis_thaliana.pin")


def ensure_blastdb(
    cache_dir: str,
    url: str,
    expected_sha256: str,
    download_fn: Callable[[str, str], None] | None = None,
) -> tuple[str, list[str]]:
    """Ensure ``{cache_dir}/prot/*.pin`` exists; download+verify the zip if not.

    Zip interior layout is ``blastdb/prot/{Genus_species}.*``. After extract the
    cache root equals that ``blastdb/`` so callers see ``prot/`` underneath.
    """
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.isfile(_sentinel_pin(cache_dir)):
        return cache_dir, []

    if not expected_sha256:
        raise BlastDbError("CELL_ANNOTATION_BLASTDB_SHA256 is empty; refuse to install unverified zip")

    download = download_fn or _default_download
    lock_path = _lock_dir(cache_dir)
    _acquire_dir_lock(lock_path)
    try:
        if os.path.isfile(_sentinel_pin(cache_dir)):
            return cache_dir, []
        fd, tmp_zip = tempfile.mkstemp(prefix="blastdb-", suffix=".zip", dir=cache_dir)
        os.close(fd)
        try:
            download(url, tmp_zip)
            digest = _sha256_file(tmp_zip)
            if digest.lower() != expected_sha256.lower():
                raise BlastDbError(
                    f"sha256 mismatch for blastdb zip: got {digest}, expected {expected_sha256}"
                )
            extract_root = tempfile.mkdtemp(prefix="blastdb-extract-", dir=cache_dir)
            try:
                with zipfile.ZipFile(tmp_zip) as zf:
                    zf.extractall(extract_root)
                prot_src = _find_prot_dir(extract_root)
                dest_prot = os.path.join(cache_dir, "prot")
                if os.path.exists(dest_prot):
                    shutil.rmtree(dest_prot)
                shutil.move(prot_src, dest_prot)
            finally:
                shutil.rmtree(extract_root, ignore_errors=True)
        finally:
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
        if not os.path.isfile(_sentinel_pin(cache_dir)):
            raise BlastDbError("zip extracted but Arabidopsis_thaliana.pin is missing")
        return cache_dir, []
    finally:
        _release_dir_lock(lock_path)


def _find_prot_dir(extract_root: str) -> str:
    direct = os.path.join(extract_root, "blastdb", "prot")
    if os.path.isdir(direct):
        return direct
    nested = os.path.join(extract_root, "prot")
    if os.path.isdir(nested):
        return nested
    for dirpath, dirnames, _files in os.walk(extract_root):
        if os.path.basename(dirpath) == "prot" and any(f.endswith(".pin") for f in _files):
            return dirpath
    raise BlastDbError("zip does not contain blastdb/prot/")


def parse_blast_tabular(text: str, ref_species: str) -> list[MappingRecord]:
    """Parse ``-outfmt '6 qseqid sseqid pident bitscore evalue'`` into records.

    ``mapping_type`` is ``blastp_top_hit`` if that query had one row, else
    ``blastp_multiple_hits`` (before any best-1 truncation).
    """
    grouped: dict[str, list[tuple[str, str, float, float, str]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        qseqid, sseqid, pident_s, bitscore_s, evalue = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            pident = float(pident_s)
            bitscore = float(bitscore_s)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(qseqid, []).append((qseqid, sseqid, pident, bitscore, evalue))

    records: list[MappingRecord] = []
    for qseqid, rows in grouped.items():
        mapping_type = "blastp_top_hit" if len(rows) == 1 else "blastp_multiple_hits"
        for _q, sseqid, pident, bitscore, evalue in rows:
            records.append(
                MappingRecord(
                    ref_species=ref_species,
                    ref_gene_id=strip_seq_id(sseqid),
                    score=pident,
                    score_type="percent_identity",
                    mapping_type=mapping_type,
                    confidence=None,
                    raw={
                        "qseqid": qseqid,
                        "sseqid": sseqid,
                        "bitscore": bitscore,
                        "evalue": evalue,
                    },
                )
            )
    return records


@register_provider
class BlastpProvider(BaseCrossSpeciesProvider):
    name: ClassVar[str] = "blastp"
    score_type: ClassVar[str] = "percent_identity"

    def available(self, species_type: str) -> bool:
        return shutil.which("blastp") is not None

    def lookup(
        self,
        target_species: str,
        ref_species: str,
        gene: str,
        *,
        timeout: int = BLASTP_TIMEOUT_DEFAULT,
        max_retries: int = 4,
        **opts,
    ) -> tuple[list[MappingRecord] | None, str | None]:
        mapping, err = self.lookup_many(
            target_species, ref_species, [gene], timeout=timeout, **opts,
        )
        if err:
            return None, err
        return (mapping or {}).get(gene, []), None

    def lookup_many(
        self,
        target_species: str,
        ref_species: str,
        genes: list[str],
        *,
        timeout: int = BLASTP_TIMEOUT_DEFAULT,
        max_retries: int = 0,
        query_fasta: str | None = None,
        blastdb_dir: str | None = None,
        **opts,
    ) -> tuple[dict[str, list[MappingRecord]] | None, str | None]:
        """One ``blastp`` process per reference species against the filtered FASTA."""
        if not query_fasta or not os.path.isfile(query_fasta):
            return None, "query FASTA missing or unreadable"
        if not shutil.which("blastp"):
            return None, "blastp not on PATH"
        db_root = blastdb_dir or blastdb_config()["dir"]
        prefix = kg_to_blast_prefix(ref_species)
        db_path = os.path.join(db_root, "prot", prefix)
        if not os.path.isfile(db_path + ".pin"):
            empty = {g: [] for g in genes}
            return empty, None
        try:
            tabular = self._run_blastp(
                query_fasta, db_path, timeout=timeout,
                num_threads=int(opts["num_threads"]) if opts.get("num_threads") else blast_thread_budget(1),
            )
        except BlastDbError as exc:
            return None, str(exc)
        records = parse_blast_tabular(tabular, ref_species=ref_species)
        by_gene: dict[str, list[MappingRecord]] = {g: [] for g in genes}
        gene_set = set(genes)
        for rec in records:
            qid = strip_seq_id(str(rec.raw.get("qseqid") or ""))
            if qid in gene_set:
                by_gene[qid].append(rec)
            elif rec.raw.get("qseqid") in by_gene:
                by_gene[str(rec.raw["qseqid"])].append(rec)
        return by_gene, None

    def _run_blastp(self, query_fasta: str, db_path: str, timeout: int,
                    num_threads: int = 1) -> str:
        blastp_bin = shutil.which("blastp") or "blastp"
        threads = max(1, int(num_threads))
        cmd = [
            blastp_bin,
            "-query", query_fasta,
            "-db", db_path,
            "-num_threads", str(threads),
            "-outfmt", "6 qseqid sseqid pident bitscore evalue",
            "-max_target_seqs", "5",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(timeout, 1),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BlastDbError(f"blastp failed to start: {exc}") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            raise BlastDbError(f"blastp process failed: {err}")
        return proc.stdout or ""
