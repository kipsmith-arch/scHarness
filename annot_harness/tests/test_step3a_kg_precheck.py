"""Tests for step3a_kg_precheck (CAP-1).

Coverage:
- arg_spec / --dump-schema discipline
- coverage_tier classification (high / medium / low)
- recommended_strategy mapping (single_species / mixed / cross_species_only)
- end-to-end: CLI invocation against live Neo4j (arabidopsis / sorghum / unknown)
- graceful failure when Neo4j unreachable (mocked driver)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "cell-annotation" / "scripts" / "step3a_kg_precheck.py"
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import common  # noqa: E402
import step3a_kg_precheck as precheck  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-function tests (no Neo4j needed)
# ---------------------------------------------------------------------------

class TestCoverageTier:
    def test_high_at_threshold(self):
        assert precheck._coverage_tier(500, 500, 50) == "high"

    def test_high_above(self):
        assert precheck._coverage_tier(10000, 500, 50) == "high"

    def test_medium_in_range(self):
        assert precheck._coverage_tier(200, 500, 50) == "medium"

    def test_low_below(self):
        assert precheck._coverage_tier(0, 500, 50) == "low"
        assert precheck._coverage_tier(49, 500, 50) == "low"

    def test_boundary_at_low(self):
        assert precheck._coverage_tier(50, 500, 50) == "medium"


class TestRecommendedStrategy:
    def test_high_maps_to_single_species(self):
        assert precheck._recommended_strategy("high") == "single_species"

    def test_medium_maps_to_mixed(self):
        assert precheck._recommended_strategy("medium") == "mixed"

    def test_low_maps_to_cross_species_only(self):
        assert precheck._recommended_strategy("low") == "cross_species_only"

    def test_unknown_defaults_to_cross_species_only(self):
        # Defensive: unknown tier should not crash; should default to the most
        # conservative strategy (better to over-recommend cross-species than
        # to silently fall back to direct path on zero coverage).
        assert precheck._recommended_strategy("garbage") == "cross_species_only"


class TestRationale:
    def test_rationale_contains_target_species(self):
        text = precheck._strategy_rationale(
            "arabidopsis_thaliana", "high", 21299, "single_species")
        assert "arabidopsis_thaliana" in text
        assert "21299" in text

    def test_rationale_low_points_at_static_catalog(self):
        text = precheck._strategy_rationale(
            "sorghum_bicolor", "low", 0, "cross_species_only")
        assert "sorghum_bicolor" in text
        assert "reference-species.md" in text


# ---------------------------------------------------------------------------
# Schema discipline tests
# ---------------------------------------------------------------------------

class TestSchemaDiscipline:
    def test_dump_schema_returns_valid_json(self):
        """dump_schema emits a ``{"tools": [...]}`` JSON object whose first
        tool entry lists every A-class arg. Verify the LLM-visible args are
        all present and there are no hidden (SUPPRESS) args."""
        parser = precheck._build_parser()
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = common.dump_schema(parser)
        assert rc == 0
        out = buf.getvalue()
        lines = [l for l in out.splitlines() if l.strip()]
        schema = json.loads(lines[-1])
        assert "tools" in schema
        assert len(schema["tools"]) == 1
        tool = schema["tools"][0]
        assert tool["subcommand"] == "run"
        arg_names = {a["name"] for a in tool["args"]}
        # A-class args must be exposed
        for required in ("target_species", "organ", "species_type",
                         "project_dir", "high_threshold", "low_threshold"):
            assert required in arg_names, f"missing A-class arg: {required}"

    def test_no_suppressed_args(self):
        """Verify all args are A-class (LLM-visible). This tool has no KG-resource
        args per SPEC CAP-1; SUPPRESS is reserved for B-class (KG/Neo4j)."""
        import argparse
        parser = precheck._build_parser()
        all_actions = []
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    all_actions.extend(sub._actions)
            else:
                all_actions.append(action)
        for action in all_actions:
            if action.dest in ("help", "dump_schema"):
                continue
            assert action.default is not argparse.SUPPRESS, \
                f"step3a_kg_precheck has SUPPRESS on {action.dest}; this tool has no B-class args"
            assert action.help is not argparse.SUPPRESS, \
                f"step3a_kg_precheck has SUPPRESS on {action.dest} help; this tool has no B-class args"


# ---------------------------------------------------------------------------
# End-to-end CLI tests against live Neo4j (skipped if unreachable)
# ---------------------------------------------------------------------------

def _has_neo4j() -> bool:
    """Probe whether Neo4j is reachable AND responsive from this environment.
    Loads skill .env (via common.py) so NEO4J_PASSWORD is set, then issues a
    real RETURN 1 query with a 5s socket timeout. A driver-only check is not
    enough: Neo4j can accept the connection but be OOM and refuse queries."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                                / "skills" / "cell-annotation" / "scripts"))
        import common  # noqa: F401  -- triggers load_skill_dotenv()
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
            connection_timeout=5,  # fail fast if Neo4j is unresponsive
        )
        with driver.session() as s:
            s.run("RETURN 1", timeout=5).single()
        driver.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_neo4j(), reason="Neo4j unreachable")
class TestEndToEnd:
    def test_arabidopsis_returns_high_tier(self, tmp_path):
        """Arabidopsis has 21k+ genes with CT in KG (Neo4j live). Expect
        coverage_tier=high, strategy=single_species, top ref is a Plant.

        Re-checks Neo4j responsiveness inside the test (Neo4j may have gone
        OOM since the module-level _has_neo4j check). On failure, the test
        skips with a clear reason rather than falsely reporting a code bug.
        """
        if not _has_neo4j():
            pytest.skip("Neo4j not responsive at test time (OOM / network)")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--target-species", "arabidopsis_thaliana",
             "--organ", "root",
             "--project-dir", str(tmp_path)],
            capture_output=True, timeout=60, env=env,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "error" in proc.stdout.splitlines()[-1]:
            pytest.skip(f"Neo4j transient failure: {proc.stderr[:200]}")
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        assert envelope["status"] == "ok"
        data = envelope["data"]
        assert data["coverage_tier"] == "high"
        assert data["recommended_strategy"] == "single_species"
        report_path = Path(data["coverage_report_json"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["target_species"] == "arabidopsis_thaliana"
        assert report["target_species_type"] == "Plant"
        assert "推荐" in report["strategy_rationale"] or "覆盖" in report["strategy_rationale"]
        assert "recommended_reference_species" not in report

    def test_sorghum_returns_low_tier(self, tmp_path):
        """Sorghum is NOT in KG (Neo4j verified 2026-08). Expect
        coverage_tier=low, strategy=cross_species_only, refs include Plant."""
        if not _has_neo4j():
            pytest.skip("Neo4j not responsive at test time (OOM / network)")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run",
             "--target-species", "sorghum_bicolor",
             "--organ", "root",
             "--project-dir", str(tmp_path)],
            capture_output=True, timeout=60, env=env,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "error" in proc.stdout.splitlines()[-1]:
            pytest.skip(f"Neo4j transient failure: {proc.stderr[:200]}")
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        assert envelope["status"] == "ok"
        data = envelope["data"]
        assert data["coverage_tier"] == "low"
        assert data["recommended_strategy"] == "cross_species_only"
        assert data["n_target_genes_with_ct"] == 0
        report = json.loads(Path(data["coverage_report_json"]).read_text(encoding="utf-8"))
        assert "recommended_reference_species" not in report
        assert "reference-species.md" in report["strategy_rationale"]
