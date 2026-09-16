"""Tests for the blastp step3b provider (SPEC-blastp-homology).

CI must not hit xener, run a real blastp binary, or download the subject zip.
Live blastp+db is skip-gated.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "cell-annotation" / "scripts" / "step3b_cross_species_map.py"
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import common  # noqa: E402
import step3b_cross_species_map as xmap  # noqa: E402
import step3b_xmap_providers.base as xmap_base  # noqa: E402
import step3b_xmap_providers.blastp as blastp  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_markers(tmp_path: Path, genes: list[str]) -> Path:
    p = tmp_path / "markers.json"
    p.write_text(json.dumps({"per_cluster": {"0": {"marker_genes": genes}}}), encoding="utf-8")
    return p


def _write_var_snapshot(path: Path, gene_ids: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["gene_id,n_cells\n"]
    lines.extend(f"{g},10\n" for g in gene_ids)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_fasta(path: Path, records: list[tuple[str, str]]) -> Path:
    chunks = []
    for header, seq in records:
        chunks.append(f">{header}\n{seq}\n")
    path.write_text("".join(chunks), encoding="utf-8")
    return path


def _make_blastdb_zip(path: Path, prefixes: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for prefix in prefixes:
            zf.writestr(f"blastdb/prot/{prefix}.pin", b"fake-pin")
            zf.writestr(f"blastdb/prot/{prefix}.phr", b"fake-phr")
            zf.writestr(f"blastdb/prot/{prefix}.psq", b"fake-psq")
    data = buf.getvalue()
    path.write_bytes(data)
    return data


def _install_fake_blastp(tmp_path: Path, tabular_by_query: dict[str, str] | None = None) -> Path:
    """Install a PATH-visible ``blastp`` that emits BLAST outfmt-6 tabular.

    ``tabular_by_query`` maps stripped query id -> extra tabular lines. Default:
    two hits per query so best-1 / multiple_hits can be asserted.
    """
    script = tmp_path / "_fake_blastp.py"
    script.write_text(
        """
import sys
from pathlib import Path

args = sys.argv[1:]
query = args[args.index("-query") + 1] if "-query" in args else None
lines = []
if query:
    header = None
    seq_ids = []
    for raw in Path(query).read_text(encoding="utf-8").splitlines():
        if raw.startswith(">"):
            header = raw[1:].split()[0]
            seq_ids.append(header)
    extra = %r
    for qid in seq_ids:
        key = qid
        for prefix in ("lcl|", "dbj|", "gb|", "ref|"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break
        if extra and key in extra:
            lines.append(extra[key].rstrip() + "\\n")
        elif extra and qid in extra:
            lines.append(extra[qid].rstrip() + "\\n")
        else:
            lines.append(f"{qid}\\tlcl|HIT1\\t95.0\\t200\\t1e-50\\n")
            lines.append(f"{qid}\\tlcl|HIT2\\t80.0\\t150\\t1e-20\\n")
sys.stdout.write("".join(lines))
"""
        % (tabular_by_query or {}),
        encoding="utf-8",
    )
    if sys.platform == "win32":
        exe = tmp_path / "blastp.cmd"
        exe.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "blastp"
        exe.write_text(f"#!/usr/bin/env python3\nimport runpy\nrunpy.run_path(r'{script}', run_name='__main__')\n", encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _with_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))


# ---------------------------------------------------------------------------
# registry / species catalog
# ---------------------------------------------------------------------------

class TestResolveProvider:
    def test_fasta_prefers_blastp_even_if_default_ensembl(self, tmp_path):
        fa = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        name, warns = xmap._resolve_provider_name("ensembl_compara", str(fa))
        assert name == "blastp"
        assert any("blastp" in w.lower() for w in warns)

    def test_env_fasta_also_prefers_blastp(self, tmp_path):
        fa = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        name, _warns = xmap._resolve_provider_name(None, str(fa))
        assert name == "blastp"

    def test_no_fasta_keeps_ensembl_default(self):
        name, warns = xmap._resolve_provider_name("ensembl_compara", None)
        assert name == "ensembl_compara"
        assert warns == []

    def test_missing_fasta_file_does_not_switch(self, tmp_path):
        name, warns = xmap._resolve_provider_name(
            "ensembl_compara", str(tmp_path / "nope.fa"),
        )
        assert name == "ensembl_compara"
        assert warns == []


class TestRegistry:
    def test_blastp_registered(self):
        assert "blastp" in xmap_base.PROVIDERS
        assert xmap_base.PROVIDERS["blastp"] is blastp.BlastpProvider

    def test_default_cli_provider_still_ensembl(self):
        assert xmap.DEFAULT_PROVIDER == "ensembl_compara"

    def test_score_type_is_percent_identity(self):
        p = xmap_base.get_provider("blastp")
        assert p.score_type == "percent_identity"
        assert p.name == "blastp"


class TestSpeciesCatalog:
    def test_catalog_has_27_prefixes(self):
        assert len(blastp.BLAST_PREFIX_TO_KG) == 27
        assert blastp.BLAST_PREFIX_TO_KG["Arabidopsis_thaliana"] == "arabidopsis_thaliana"
        assert blastp.BLAST_PREFIX_TO_KG["Populus_alba_var_pyramidalis"] == "populus_alba_var_pyramidalis"
        assert blastp.kg_to_blast_prefix("oryza_sativa") == "Oryza_sativa"

    def test_unknown_species_title_cases_segments(self):
        assert blastp.kg_to_blast_prefix("foo_bar") == "Foo_Bar"


class TestStripSeqId:
    def test_strips_lcl_prefix(self):
        assert blastp.strip_seq_id("lcl|AT1G01010") == "AT1G01010"

    def test_strips_dbj_prefix(self):
        assert blastp.strip_seq_id("dbj|Os01g0100100") == "Os01g0100100"

    def test_first_token_only(self):
        assert blastp.strip_seq_id("lcl|AT1G01010 extra annotation") == "AT1G01010"


# ---------------------------------------------------------------------------
# tabular parse
# ---------------------------------------------------------------------------

class TestParseTabular:
    def test_pident_and_strip_and_raw(self):
        text = "AT1G01010\tlcl|Os06g0650100\t85.5\t210\t1e-40\n"
        recs = blastp.parse_blast_tabular(text, ref_species="oryza_sativa")
        assert len(recs) == 1
        r = recs[0]
        assert r.ref_species == "oryza_sativa"
        assert r.ref_gene_id == "Os06g0650100"
        assert r.score == 85.5
        assert r.score_type == "percent_identity"
        assert r.mapping_type == "blastp_top_hit"
        assert r.raw["sseqid"] == "lcl|Os06g0650100"
        assert r.raw["bitscore"] == 210.0
        assert r.raw["evalue"] == "1e-40"

    def test_multiple_hits_same_query_sets_mapping_type(self):
        text = (
            "AT1G01010\tlcl|HIT1\t90.0\t200\t1e-50\n"
            "AT1G01010\tlcl|HIT2\t80.0\t150\t1e-20\n"
        )
        recs = blastp.parse_blast_tabular(text, ref_species="oryza_sativa")
        assert len(recs) == 2
        assert all(r.mapping_type == "blastp_multiple_hits" for r in recs)


# ---------------------------------------------------------------------------
# lookup_many / one process per ref
# ---------------------------------------------------------------------------

class TestLookupMany:
    def test_one_blastp_process_per_ref_not_per_gene(self, tmp_path, monkeypatch):
        _install_fake_blastp(tmp_path)
        _with_path(tmp_path, monkeypatch)
        db_dir = tmp_path / "blastdb"
        prefix = blastp.kg_to_blast_prefix("oryza_sativa")
        (db_dir / "prot").mkdir(parents=True)
        (db_dir / "prot" / f"{prefix}.pin").write_bytes(b"x")
        fasta = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA"), ("AT1G01020", "MKB")])

        calls = []
        real_run = subprocess.run

        def wrapped(cmd, *a, **kw):
            calls.append(cmd)
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(blastp.subprocess, "run", wrapped)
        p = blastp.BlastpProvider()
        mapping, err = p.lookup_many(
            "arabidopsis_thaliana",
            "oryza_sativa",
            ["AT1G01010", "AT1G01020"],
            query_fasta=str(fasta),
            blastdb_dir=str(db_dir),
        )
        assert err is None
        assert len(calls) == 1
        assert mapping["AT1G01010"][0].ref_gene_id == "HIT1"
        assert mapping["AT1G01010"][0].score == 95.0

    def test_best1_keeps_highest_pident(self, tmp_path, monkeypatch):
        _install_fake_blastp(tmp_path)
        _with_path(tmp_path, monkeypatch)
        db_dir = tmp_path / "blastdb"
        prefix = blastp.kg_to_blast_prefix("oryza_sativa")
        (db_dir / "prot").mkdir(parents=True)
        (db_dir / "prot" / f"{prefix}.pin").write_bytes(b"x")
        fasta = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        p = blastp.BlastpProvider()
        mapping, err = p.lookup_many(
            "arabidopsis_thaliana", "oryza_sativa", ["AT1G01010"],
            query_fasta=str(fasta), blastdb_dir=str(db_dir),
        )
        assert err is None
        recs = mapping["AT1G01010"]
        assert recs[0].ref_gene_id == "HIT1"
        # pre-truncation type is multiple; collapse to best-1 happens in CLI,
        # provider returns all hits so CLI can label mapping_type.
        assert len(recs) == 2

    def test_blastp_passes_num_threads(self, tmp_path, monkeypatch):
        _install_fake_blastp(tmp_path)
        _with_path(tmp_path, monkeypatch)
        db_dir = tmp_path / "blastdb"
        prefix = blastp.kg_to_blast_prefix("oryza_sativa")
        (db_dir / "prot").mkdir(parents=True)
        (db_dir / "prot" / f"{prefix}.pin").write_bytes(b"x")
        fasta = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        calls = []
        real_run = subprocess.run

        def wrapped(cmd, *a, **kw):
            calls.append(cmd)
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(blastp.subprocess, "run", wrapped)
        p = blastp.BlastpProvider()
        p.lookup_many(
            "arabidopsis_thaliana", "oryza_sativa", ["AT1G01010"],
            query_fasta=str(fasta), blastdb_dir=str(db_dir), num_threads=4,
        )
        assert calls
        cmd = [str(c) for c in calls[0]]
        assert "-num_threads" in cmd
        assert cmd[cmd.index("-num_threads") + 1] == "4"

    def test_blastp_default_process_timeout_is_30_minutes(self, tmp_path, monkeypatch):
        _install_fake_blastp(tmp_path)
        _with_path(tmp_path, monkeypatch)
        db_dir = tmp_path / "blastdb"
        prefix = blastp.kg_to_blast_prefix("oryza_sativa")
        (db_dir / "prot").mkdir(parents=True)
        (db_dir / "prot" / f"{prefix}.pin").write_bytes(b"x")
        fasta = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        timeouts = []
        real_run = subprocess.run

        def wrapped(cmd, *a, **kw):
            timeouts.append(kw.get("timeout"))
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(blastp.subprocess, "run", wrapped)
        p = blastp.BlastpProvider()
        p.lookup_many(
            "arabidopsis_thaliana", "oryza_sativa", ["AT1G01010"],
            query_fasta=str(fasta), blastdb_dir=str(db_dir),
        )
        assert timeouts == [blastp.BLASTP_TIMEOUT_DEFAULT]
        assert blastp.BLASTP_TIMEOUT_DEFAULT == 1800

    def test_thread_budget_uses_all_cpus_for_one_job(self):
        assert blastp.blast_thread_budget(n_jobs=1, cpu_count=8) == 8
        assert blastp.blast_thread_budget(n_jobs=1, cpu_count=2) == 2


# ---------------------------------------------------------------------------
# ensure_blastdb
# ---------------------------------------------------------------------------

class TestEnsureBlastdb:
    def test_skips_download_when_pin_present(self, tmp_path):
        prot = tmp_path / "prot"
        prot.mkdir()
        (prot / "Arabidopsis_thaliana.pin").write_bytes(b"ok")
        calls = []

        def download(url, dest):
            calls.append(url)
            raise AssertionError("should not download")

        path, warns = blastp.ensure_blastdb(
            str(tmp_path), "http://example.invalid/blastdb.zip", "abc",
            download_fn=download,
        )
        assert path == str(tmp_path)
        assert calls == []
        assert warns == []

    def test_get_not_head_and_sha256_reject(self, tmp_path):
        methods = []

        def download(url, dest):
            methods.append("GET")
            Path(dest).write_bytes(b"not-a-zip")

        with pytest.raises(blastp.BlastDbError, match="sha256"):
            blastp.ensure_blastdb(
                str(tmp_path / "cache"),
                "http://example.invalid/blastdb.zip",
                hashlib.sha256(b"expected").hexdigest(),
                download_fn=download,
            )
        assert methods == ["GET"]

    def test_installs_zip_layout_prot(self, tmp_path):
        zpath = tmp_path / "b.zip"
        data = _make_blastdb_zip(zpath, ["Arabidopsis_thaliana", "Oryza_sativa"])
        digest = hashlib.sha256(data).hexdigest()
        cache = tmp_path / "cache"

        def download(url, dest):
            shutil.copyfile(zpath, dest)

        path, warns = blastp.ensure_blastdb(str(cache), "http://example.invalid/x.zip", digest, download_fn=download)
        assert (Path(path) / "prot" / "Arabidopsis_thaliana.pin").is_file()
        assert (Path(path) / "prot" / "Oryza_sativa.pin").is_file()
        assert warns == []


# ---------------------------------------------------------------------------
# FASTA filter
# ---------------------------------------------------------------------------

class TestFastaFilter:
    def test_filters_against_var_snapshot_and_markers(self, tmp_path):
        var = _write_var_snapshot(tmp_path / "step1_prepare" / "var_snapshot.csv", ["AT1G01010", "AT1G01020"])
        fasta = _write_fasta(tmp_path / "q.fa", [
            ("lcl|AT1G01010", "MKA"),
            ("AT1G99999", "MKB"),  # not in var
            ("AT1G01020", "MKC"),  # in var but we intersect markers below
        ])
        out = tmp_path / "filtered.fa"
        kept, metrics, warns = xmap.filter_query_fasta(
            str(fasta), str(var), marker_genes=["AT1G01010"], out_path=str(out),
        )
        assert kept == ["AT1G01010"]
        assert metrics["n_fasta_seq"] == 3
        assert metrics["n_kept_in_var"] == 2
        assert metrics["n_kept_in_markers"] == 1
        assert any("var_names" in w or "var" in w.lower() for w in warns)
        text = out.read_text(encoding="utf-8")
        assert "AT1G01010" in text
        assert "AT1G01020" not in text


# ---------------------------------------------------------------------------
# cache key
# ---------------------------------------------------------------------------

class TestCacheKeyFasta:
    def test_blastp_cache_key_includes_fasta_digest(self, tmp_path):
        markers = _write_markers(tmp_path, ["AT1G01010"])
        fa1 = _write_fasta(tmp_path / "a.fa", [("AT1G01010", "MKA")])
        fa2 = _write_fasta(tmp_path / "b.fa", [("AT1G01010", "MKCHANGED")])
        k1 = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"], "blastp",
                             str(markers), query_fasta=str(fa1))
        k2 = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"], "blastp",
                             str(markers), query_fasta=str(fa2))
        k1b = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"], "blastp",
                              str(markers), query_fasta=str(fa1))
        assert k1 != k2
        assert k1 == k1b


# ---------------------------------------------------------------------------
# CLI: lookup_many, best-1 across species, failover, ref cap
# ---------------------------------------------------------------------------

class TestCliBlastp:
    def _env(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def test_dump_schema_exposes_query_fasta_and_blastp_choice(self):
        buf_names = None
        parser = xmap._build_parser()
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            rc = common.dump_schema(parser)
        assert rc == 0
        schema = json.loads(buf.getvalue().strip().splitlines()[-1])
        tool = schema["tools"][0]
        arg_names = {a["name"] for a in tool["args"]}
        assert "query_fasta" in arg_names
        provider_arg = next(a for a in tool["args"] if a["name"] == "provider")
        # choices are not always serialized; registry must include blastp.
        assert "blastp" in xmap_base.PROVIDERS

    def test_blastp_cli_default_timeout_is_30_minutes(self):
        assert xmap._timeout_for_provider("blastp", None) == 1800
        assert xmap._timeout_for_provider("blastp", 10) == 1800
        assert xmap._timeout_for_provider("blastp", 2) == 2
        assert xmap._timeout_for_provider("ensembl_compara", 10) == 10
        assert xmap._timeout_for_provider("ensembl_compara", None) == 10

    def test_best1_keeps_all_species(self, tmp_path):
        """Per-species best-1; two refs → two mappings, no global top-1."""
        rec_a = xmap_base.MappingRecord(
            ref_species="oryza_sativa", ref_gene_id="Os1", score=90.0,
            score_type="percent_identity", mapping_type="blastp_top_hit",
        )
        rec_b = xmap_base.MappingRecord(
            ref_species="zea_mays", ref_gene_id="Zm1", score=40.0,
            score_type="percent_identity", mapping_type="blastp_top_hit",
        )
        per_ref = {
            "oryza_sativa": {"AT1G01010": [rec_a]},
            "zea_mays": {"AT1G01010": [rec_b]},
        }
        merged = xmap.collapse_best1_across_refs(["AT1G01010"], per_ref, max_hits_per_species=1)
        rows = merged["AT1G01010"]
        species = {r["ref_species"] for r in rows}
        assert species == {"oryza_sativa", "zea_mays"}
        assert len(rows) == 2

    def test_truncate_reference_species_to_three(self):
        refs, warns = xmap._limit_reference_species(
            ["a", "b", "c", "d"], max_n=3,
        )
        assert refs == ["a", "b", "c"]
        assert any("3" in w for w in warns)

    def test_batch_query_runs_refs_serially(self, tmp_path):
        import threading
        import time

        active = 0
        max_active = 0
        lock = threading.Lock()
        order: list[str] = []

        class SlowBlast(xmap_base.BaseCrossSpeciesProvider):
            name = "slow_blast_serial"
            score_type = "percent_identity"

            def available(self, species_type):
                return True

            def lookup(self, *a, **kw):
                return [], None

            def lookup_many(self, target_species, ref_species, genes, **kw):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    order.append(ref_species)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return {g: [] for g in genes}, None

        provider = SlowBlast()
        log = str(tmp_path / "run_log.jsonl")
        Path(log).write_text("", encoding="utf-8")
        _per_ref, _stats, err = xmap.op_query_provider(
            ["AT1G01010"], "arabidopsis_thaliana",
            ["oryza_sativa", "zea_mays"],
            provider, host=None,
            min_score=0, max_hits=1,
            timeout=10, max_retries=0, concurrency=8,
            log_path=log, params={},
            extra_warnings=[],
        )
        assert err is None
        assert max_active == 1
        assert order == ["oryza_sativa", "zea_mays"]

    def test_failover_without_blastp_binary_stays_ok(self, tmp_path, monkeypatch):
        markers = _write_markers(tmp_path, ["AT1G01010"])
        _write_var_snapshot(tmp_path / "step1_prepare" / "var_snapshot.csv", ["AT1G01010"])
        fasta = _write_fasta(tmp_path / "q.fa", [("AT1G01010", "MKA")])
        monkeypatch.setenv("PATH", str(tmp_path / "empty_bin"))
        (tmp_path / "empty_bin").mkdir()
        monkeypatch.setattr(xmap, "check_dns", lambda url: False)

        class FakeEnsembl:
            name = "ensembl_compara"
            score_type = "percent_identity"

            def available(self, species_type):
                return True

            def lookup(self, *a, **kw):
                return [], None

            def lookup_many(self, *a, **kw):
                return {"AT1G01010": []}, None

        FakeEnsembl.default_host_for = staticmethod(lambda st: "https://rest.plants.ensembl.org")

        monkeypatch.setattr(xmap, "get_provider", lambda name: FakeEnsembl() if name != "blastp" else xmap_base.get_provider("blastp"))

        ns = type("A", (), {})()
        ns.provider = "blastp"
        ns.target_species = "arabidopsis_thaliana"
        ns.reference_species = ["oryza_sativa"]
        ns.species_type = "Plant"
        ns.input = str(markers)
        ns.project_dir = str(tmp_path)
        ns.min_score = 30.0
        ns.max_hits_per_gene = 1
        ns.force_refresh = True
        ns.provider_timeout = 2
        ns.provider_max_retries = 0
        ns.provider_concurrency = 1
        ns.max_genes = 0
        ns.ensembl_rest_host = None
        ns.query_fasta = str(fasta)

        result = xmap.cmd_run(ns)
        assert result["status"] == "ok"
        warnings = result["data"]["warnings"]
        assert any("ensembl" in w.lower() for w in warnings)

    def test_empty_fasta_marker_intersection_failovers(self, tmp_path, monkeypatch):
        _install_fake_blastp(tmp_path)
        _with_path(tmp_path, monkeypatch)
        markers = _write_markers(tmp_path, ["AT1G01010"])
        _write_var_snapshot(tmp_path / "step1_prepare" / "var_snapshot.csv", ["AT1G01010"])
        fasta = _write_fasta(tmp_path / "q.fa", [("NOT_A_MARKER", "MKA")])
        monkeypatch.setattr(xmap, "check_dns", lambda url: False)

        class FakeEnsembl:
            name = "ensembl_compara"
            score_type = "percent_identity"
            default_host_for = staticmethod(lambda st: "https://rest.plants.ensembl.org")

            def available(self, species_type):
                return True

            def lookup(self, *a, **kw):
                return [], None

        monkeypatch.setattr(
            xmap, "get_provider",
            lambda name: FakeEnsembl() if name != "blastp" else xmap_base.get_provider("blastp"),
        )
        ns = type("A", (), {})()
        ns.provider = "blastp"
        ns.target_species = "arabidopsis_thaliana"
        ns.reference_species = ["oryza_sativa"]
        ns.species_type = "Plant"
        ns.input = str(markers)
        ns.project_dir = str(tmp_path)
        ns.min_score = 30.0
        ns.max_hits_per_gene = 1
        ns.force_refresh = True
        ns.provider_timeout = 2
        ns.provider_max_retries = 0
        ns.provider_concurrency = 1
        ns.max_genes = 0
        ns.ensembl_rest_host = None
        ns.query_fasta = str(fasta)
        result = xmap.cmd_run(ns)
        assert result["status"] == "ok"
        assert any("ensembl" in w.lower() for w in result["data"]["warnings"])


def _live_blast_ready() -> bool:
    pin = Path(os.environ.get(
        "CELL_ANNOTATION_BLASTDB_DIR",
        str(Path.home() / ".cache" / "annot-harness" / "cell-annotation" / "blastdb"),
    )) / "prot" / "Arabidopsis_thaliana.pin"
    return shutil.which("blastp") is not None and pin.is_file()


@pytest.mark.skipif(not _live_blast_ready(), reason="real blastp + subject db not present")
def test_live_blastp_optional(tmp_path):
    markers = _write_markers(tmp_path, ["AT1G01010"])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    fasta = os.environ.get("CELL_ANNOTATION_QUERY_FASTA")
    if not fasta or not Path(fasta).is_file():
        pytest.skip("CELL_ANNOTATION_QUERY_FASTA not set")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run",
         "--provider", "blastp",
         "--target-species", "arabidopsis_thaliana",
         "--reference-species", "oryza_sativa",
         "--query-fasta", fasta,
         "--input", str(markers),
         "--project-dir", str(tmp_path),
         "--max-genes", "1",
         "--force-refresh"],
        capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode == 0
    envelope = json.loads(proc.stdout.strip().splitlines()[-1])
    assert envelope["status"] == "ok"
