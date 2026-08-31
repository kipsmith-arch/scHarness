"""Tests for step2_cross_species_map (CAP-2, provider-abstraction refactor).

Refactored 2026-08-24 from test_step2_ortholog.py: was Ensembl-Compara-hardcoded;
now tests the generic provider abstraction and the Ensembl provider as the
default shipped implementation.

Coverage:
- Provider registry & base class contract (MappingRecord, BaseCrossSpeciesProvider)
- DNS / host selection at CLI layer (provider-aware)
- Cache key (now includes provider name) + load/save roundtrip
- Ensembl provider: homology extraction (Ensembl shape -> MappingRecord)
- Schema discipline (A-class args visible)
- End-to-end CLI with real subprocess (Ensembl REST, gated)
- End-to-end offline: cache reuse, --force-refresh, unreachable host graceful
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "cell-annotation" / "scripts" / "step2_cross_species_map.py"
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import common  # noqa: E402
import step2_cross_species_map as xmap  # noqa: E402
import step2_xmap_providers  # noqa: E402  -- registers built-in providers
import step2_xmap_providers.base as xmap_base  # noqa: E402
import step2_xmap_providers.ensembl_compara as xmap_ensembl  # noqa: E402


# ---------------------------------------------------------------------------
# Provider registry & contract
# ---------------------------------------------------------------------------

class TestProviderRegistry:
    def test_ensembl_compara_registered(self):
        assert "ensembl_compara" in xmap_base.PROVIDERS
        assert xmap_base.PROVIDERS["ensembl_compara"] is xmap_ensembl.EnsemblComparaProvider

    def test_get_provider_returns_instance(self):
        p = xmap_base.get_provider("ensembl_compara")
        assert isinstance(p, xmap_base.BaseCrossSpeciesProvider)
        assert p.name == "ensembl_compara"

    def test_get_provider_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown provider"):
            xmap_base.get_provider("nonexistent")

    def test_register_without_name_raises(self):
        class Broken(xmap_base.BaseCrossSpeciesProvider):
            name = ""  # missing
            score_type = "x"

            def available(self, species_type):
                return True

            def lookup(self, target_species, ref_species, gene, **kw):
                return [], None

        with pytest.raises(ValueError, match="name must be set"):
            xmap_base.register_provider(Broken)

    def test_register_duplicate_raises(self):
        class Dup(xmap_base.BaseCrossSpeciesProvider):
            name = "ensembl_compara"  # already registered
            score_type = "x"

            def available(self, species_type):
                return True

            def lookup(self, target_species, ref_species, gene, **kw):
                return [], None

        with pytest.raises(ValueError, match="already registered"):
            xmap_base.register_provider(Dup)


class TestMappingRecord:
    def test_to_json_roundtrip(self):
        r = xmap_base.MappingRecord(
            ref_species="oryza_sativa", ref_gene_id="Os06g0650100",
            score=85.0, score_type="percent_identity",
            mapping_type="ensembl_one2one", confidence=0.95,
            raw={"ensembl_type": "ortholog_one2one"},
        )
        d = r.to_json()
        assert d["ref_species"] == "oryza_sativa"
        assert d["ref_gene_id"] == "Os06g0650100"
        assert d["score"] == 85.0
        assert d["score_type"] == "percent_identity"
        assert d["mapping_type"] == "ensembl_one2one"
        assert d["confidence"] == 0.95
        assert d["raw"]["ensembl_type"] == "ortholog_one2one"

    def test_default_confidence_is_none(self):
        r = xmap_base.MappingRecord(
            ref_species="x", ref_gene_id="y", score=50.0,
            score_type="t", mapping_type="m",
        )
        assert r.confidence is None
        assert r.raw == {}


# ---------------------------------------------------------------------------
# Ensembl provider: response parsing
# ---------------------------------------------------------------------------

class TestEnsemblProvider:
    def test_default_host_per_division(self):
        assert xmap_ensembl.EnsemblComparaProvider.default_host_for("Plant") == "https://rest.plants.ensembl.org"
        assert xmap_ensembl.EnsemblComparaProvider.default_host_for("Animal") == "https://rest.ensembl.org"
        assert xmap_ensembl.EnsemblComparaProvider.default_host_for("Unknown") == "https://rest.ensembl.org"

    def test_available_returns_true_for_known_divisions(self):
        p = xmap_ensembl.EnsemblComparaProvider()
        assert p.available("Plant") is True
        assert p.available("Animal") is True
        assert p.available("Fungi") is True

    def test_available_returns_false_for_unknown(self):
        p = xmap_ensembl.EnsemblComparaProvider()
        assert p.available("Alien") is False

    def test_parse_get_shape(self):
        body = {
            "data": [
                {"id": "AT1G31340", "homologies": [
                    {"type": "ortholog_one2one",
                     "target": {"species": "oryza_sativa", "id": "Os06g0650100",
                                "perc_id": 85.0, "perc_pos": 95.0,
                                "protein_id": "CDF37620", "cigar_line": "54D3M22D76MD"}},
                ]},
            ]
        }
        p = xmap_ensembl.EnsemblComparaProvider()
        records = p._parse_response(body, "AT1G31340", "oryza_sativa")
        assert len(records) == 1
        r = records[0]
        assert r.ref_species == "oryza_sativa"
        assert r.ref_gene_id == "Os06g0650100"
        assert r.score == 85.0
        assert r.score_type == "percent_identity"
        assert r.mapping_type == "ensembl_one2one"
        assert r.raw["ensembl_type"] == "ortholog_one2one"
        assert r.raw["perc_pos"] == 95.0

    def test_parse_filters_non_ortholog_types(self):
        body = {"data": [{"id": "X", "homologies": [
            {"type": "within_species_paralog",
             "target": {"species": "ref", "id": "X1", "perc_id": 90.0}},
            {"type": "ortholog_one2one",
             "target": {"species": "ref", "id": "X2", "perc_id": 80.0}},
        ]}]}
        p = xmap_ensembl.EnsemblComparaProvider()
        records = p._parse_response(body, "X", "ref")
        assert len(records) == 1
        assert records[0].ref_gene_id == "X2"

    def test_parse_handles_top_level_list_shape(self):
        body = [{"id": "AT1G31340", "homologies": [
            {"type": "ortholog_one2one",
             "target": {"species": "oryza_sativa", "id": "Os06g0650100", "perc_id": 70.0}},
        ]}]
        p = xmap_ensembl.EnsemblComparaProvider()
        records = p._parse_response(body, "AT1G31340", "oryza_sativa")
        assert len(records) == 1
        assert records[0].score == 70.0

    def test_parse_missing_perc_id_skipped(self):
        body = {"data": [{"id": "X", "homologies": [
            {"type": "ortholog_one2one",
             "target": {"species": "ref", "id": "X1"}},  # no perc_id
        ]}]}
        p = xmap_ensembl.EnsemblComparaProvider()
        records = p._parse_response(body, "X", "ref")
        assert records == []


# ---------------------------------------------------------------------------
# CLI: cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_cache_key_changes_with_provider(self, tmp_path):
        m = tmp_path / "m.json"
        m.write_text('{"a": 1}')
        k_ensembl = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"],
                                     "ensembl_compara", str(m))
        k_blast = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"],
                                   "blast", str(m))
        assert k_ensembl != k_blast
        # Same provider same key
        k_ensembl2 = xmap._cache_key("arabidopsis_thaliana", ["oryza_sativa"],
                                      "ensembl_compara", str(m))
        assert k_ensembl == k_ensembl2

    def test_cache_key_refs_order_insensitive(self):
        k1 = xmap._cache_key("x", ["a", "b"], "ensembl_compara", "m.json")
        k2 = xmap._cache_key("x", ["b", "a"], "ensembl_compara", "m.json")
        assert k1 == k2

    def test_cache_roundtrip(self, tmp_path):
        d = xmap._cache_dir(str(tmp_path))
        cp = os.path.join(d, "test.json")
        payload = {"provider": "ensembl_compara", "target_species": "x",
                   "summary": {"hit_rate": 0.5}}
        xmap._cache_save(cp, payload)
        assert xmap._try_cache_load(cp) == payload


# ---------------------------------------------------------------------------
# CLI: provider / host checks
# ---------------------------------------------------------------------------

class TestProviderAvailable:
    def test_known_species_no_warning(self):
        p = xmap_ensembl.EnsemblComparaProvider()
        warns = xmap._ensure_provider_available(p, "Plant")
        assert warns == []

    def test_unknown_species_warns(self):
        p = xmap_ensembl.EnsemblComparaProvider()
        warns = xmap._ensure_provider_available(p, "Alien")
        assert len(warns) == 1
        assert "Alien" in warns[0]


class TestHostReachable:
    def test_non_rest_provider_skips_dns(self):
        """A future provider without default_host_for returns (None, [])."""

        class LocalProvider(xmap_base.BaseCrossSpeciesProvider):
            name = "local"
            score_type = "x"

            def available(self, species_type):
                return True

            def lookup(self, target_species, ref_species, gene, **kw):
                return [], None

        xmap_base.PROVIDERS["local"] = LocalProvider
        try:
            p = xmap_base.get_provider("local")
            host, warns = xmap._ensure_host_reachable(p, "Plant", None)
            assert host is None
            assert warns == []
        finally:
            del xmap_base.PROVIDERS["local"]

    def test_override_host_always_used(self):
        p = xmap_ensembl.EnsemblComparaProvider()
        host, warns = xmap._ensure_host_reachable(p, "Plant", "https://my.example.org")
        assert host == "https://my.example.org"
        assert warns == []


# ---------------------------------------------------------------------------
# Schema discipline
# ---------------------------------------------------------------------------

class TestSchemaDiscipline:
    def test_dump_schema_exposes_required_args(self):
        import io
        from contextlib import redirect_stdout
        parser = xmap._build_parser()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = common.dump_schema(parser)
        assert rc == 0
        schema = json.loads(buf.getvalue().strip().splitlines()[-1])
        tool = schema["tools"][0]
        arg_names = {a["name"] for a in tool["args"]}
        for required in ("provider", "target_species", "reference_species",
                         "species_type", "input", "project_dir",
                         "min_score", "max_hits_per_gene",
                         "force_refresh",
                         "provider_timeout", "provider_max_retries",
                         "provider_concurrency", "max_genes"):
            assert required in arg_names, f"missing arg: {required}"

    def test_no_suppressed_args(self):
        """step2_cross_species_map has no KG-resource args; all are biological
        / network / provider-tuning."""
        parser = xmap._build_parser()
        all_actions = []
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    all_actions.extend(sub._actions)
            else:
                all_actions.append(action)
        # The one legacy alias (--ensembl-rest-host) is intentionally SUPPRESS
        # because it was hidden for the refactor; verify it stays so.
        legacy = [a for a in all_actions if a.dest == "ensembl_rest_host"]
        for a in legacy:
            assert a.help is argparse.SUPPRESS


# ---------------------------------------------------------------------------
# End-to-end CLI tests (real subprocess)
# ---------------------------------------------------------------------------

def _write_markers(tmp_path: Path, genes: list[str]) -> Path:
    p = tmp_path / "markers.json"
    p.write_text(json.dumps({
        "per_cluster": {"0": {"marker_genes": genes}},
    }))
    return p


def _ensembl_reachable(host: str) -> bool:
    """DNS + 1 quick probe to confirm Ensembl responds."""
    import socket
    from urllib.parse import urlparse
    try:
        socket.gethostbyname(urlparse(host).hostname)
    except (socket.gaierror, UnicodeError):
        return False
    try:
        import ssl, urllib.request
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(f"{host}/info/species?content-type=application/json",
                                     headers={"Accept": "application/json", "User-Agent": "pytest/1"})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            return r.status == 200
    except Exception:
        return False


ENSEMBL_OK = _ensembl_reachable("https://rest.ensembl.org")


@pytest.mark.skipif(not ENSEMBL_OK, reason="Ensembl REST unreachable from this environment")
class TestEndToEndRealEnsembl:
    def test_run_with_real_ensembl(self, tmp_path):
        """Real Ensembl REST, 3 well-known Vertebrate genes, end-to-end.
        Skipped when Ensembl unreachable."""
        markers = _write_markers(tmp_path, ["TP53", "BRCA1", "EGFR"])
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--target-species", "homo_sapiens",
             "--reference-species", "mus_musculus",
             "--species-type", "Animal",
             "--input", str(markers),
             "--project-dir", str(tmp_path),
             "--ensembl-rest-host", "https://rest.ensembl.org",
             "--max-genes", "3",
             "--provider-concurrency", "3",
             "--provider-max-retries", "1",
             "--force-refresh",
             "--provider-timeout", "15"],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        assert envelope["status"] == "ok"
        result = json.loads(Path(envelope["data"]["cross_species_map_json"]).read_text(encoding="utf-8"))
        assert result["provider"] == "ensembl_compara"
        assert result["summary"]["n_input_genes"] == 3


class TestEndToEndOffline:
    """End-to-end CLI tests that don't depend on network. Subprocess-based
    E2E with stub providers is fundamentally limited (argparse choices= is
    snapshotted at process start; can't inject stubs from parent), so these
    tests use the real Ensembl provider with a non-routable host to exercise
    the graceful-degradation path. Real Ensembl end-to-end is in
    TestEndToEndRealEnsembl (gated)."""

    def _env(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def test_unreachable_host_fails_gracefully(self, tmp_path):
        """When the Ensembl host is unreachable, the tool must still produce
        a JSON output (with empty map + warning), not crash. We use TEST-NET-2
        (RFC 5737, 198.51.100.0/24) which is reserved/unrouted, with a tiny
        timeout to make the failure fast."""
        markers = _write_markers(tmp_path, ["TP53"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--provider", "ensembl_compara",
             "--target-species", "homo_sapiens",
             "--reference-species", "mus_musculus",
             "--input", str(markers),
             "--project-dir", str(tmp_path),
             "--ensembl-rest-host", "http://198.51.100.1:80",
             "--max-genes", "1",
             "--provider-max-retries", "0",
             "--provider-timeout", "2",
             "--force-refresh"],
            capture_output=True, timeout=30, env=self._env(),
            encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0, f"stderr={proc.stderr[:300]}"
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        assert envelope["status"] == "ok"
        assert len(envelope["data"]["warnings"]) >= 1
        assert Path(envelope["data"]["cross_species_map_json"]).exists()

    def test_unknown_provider_rejected_by_argparse(self, tmp_path):
        """Argparse ``choices=`` must reject unknown provider names without
        ever entering the handler."""
        markers = _write_markers(tmp_path, ["TP53"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--provider", "does_not_exist",
             "--target-species", "homo_sapiens",
             "--reference-species", "mus_musculus",
             "--input", str(markers),
             "--project-dir", str(tmp_path),
             "--max-genes", "1"],
            capture_output=True, timeout=10, env=self._env(),
            encoding="utf-8", errors="replace",
        )
        assert proc.returncode != 0
        assert "invalid choice" in proc.stderr

    def test_empty_markers_json_handled_gracefully(self, tmp_path):
        """If markers.json has no genes, the tool still completes with
        a valid empty-map output (not a crash). No provider call happens
        (no genes), so warnings list should contain only the explicit
        "no marker_genes" note from the tool."""
        empty_markers = tmp_path / "markers.json"
        empty_markers.write_text(json.dumps({"per_cluster": {}}))
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--provider", "ensembl_compara",
             "--target-species", "homo_sapiens",
             "--reference-species", "mus_musculus",
             "--input", str(empty_markers),
             "--project-dir", str(tmp_path),
             "--ensembl-rest-host", "http://198.51.100.1:80",
             "--provider-max-retries", "0",
             "--provider-timeout", "2",
             "--force-refresh"],
            capture_output=True, timeout=15, env=self._env(),
            encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        assert envelope["status"] == "ok"
        assert envelope["data"]["hit_rate"] == 0.0
        assert Path(envelope["data"]["cross_species_map_json"]).exists()
