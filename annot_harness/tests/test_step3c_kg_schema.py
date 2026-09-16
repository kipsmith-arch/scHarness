"""Tests for the A-class / B-class arg discipline in step3c_kg.

The cell-annotation skill classifies CLI args into:

    A. Task / biological-decision args — LLM-visible in the tool schema
       (organ, species, etc.). I/O paths (--project-dir, --input) are also
       A-class because decision-point loops may need to override them
       (e.g. qc_threshold → re-run step1 with new threshold in a fresh dir).
    B. KG-resource / Neo4j-credential args — hidden via argparse.SUPPRESS so
       the LLM is not nudged to set them; CLI / env still overrides at runtime
       via ``common.env_or_default``.

step3c_kg is the reference implementation of this split. These tests pin
the contract so future refactors don't regress (e.g. re-exposing --password
to the LLM, or hiding --organ from the LLM).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PROJECT_ROOT / "skills" / "cell-annotation" / "scripts" / "step3c_kg.py"


def _dump_schema() -> dict:
    """Run ``python step3c_kg.py --dump-schema`` and return the parsed payload."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dump-schema"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, f"--dump-schema failed:\n{proc.stderr}"
    last_line = proc.stdout.strip().split("\n")[-1]
    return json.loads(last_line)


def _by_subcommand(payload: dict) -> dict:
    return {t["subcommand"]: t for t in payload["tools"]}


# ---------------------------------------------------------------------------
# A-class contract: these MUST remain visible to the LLM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "organ", "species", "species_type", "strict_organ",  # biological decision
        "ortholog_map",  # SOP-3 map consumed by step3c_kg query
        "project_dir", "input",  # I/O paths exposed to LLM (decision-point-driven overrides)
    ],
)
def test_query_A_class_visible(name: str):
    payload = _dump_schema()
    query = _by_subcommand(payload)["query"]
    arg_names = {a["name"] for a in query["args"]}
    assert name in arg_names, (
        f"--{name.replace('_', '-')} is A-class and must remain in the tool schema; "
        f"got args={arg_names}"
    )


# ---------------------------------------------------------------------------
# B-class contract: only KG-resource / Neo4j-credential flags are hidden.
# --project-dir / --input are A-class (LLM-visible) since they may be overridden
# by decision points (e.g. qc_threshold loop → re-run step1 with new threshold).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "min_confidence",   # KG service tuning
        "max_ancestor_hops",  # KG query tuning
        "uri", "user", "password",  # Neo4j credentials
    ],
)
def test_query_B_class_hidden(name: str):
    payload = _dump_schema()
    query = _by_subcommand(payload)["query"]
    arg_names = {a["name"] for a in query["args"]}
    assert name not in arg_names, (
        f"--{name.replace('_', '-')} is B-class (KG-resource / Neo4j credential) and "
        f"must NOT appear in the LLM-facing tool schema; got args={arg_names}"
    )


# ---------------------------------------------------------------------------
# Loader coupling: arg_spec must drop SUPPRESS args from the schema
# ---------------------------------------------------------------------------

def test_arg_spec_drops_suppress_default():
    """Sanity-check ``common.arg_spec``: an action with default=SUPPRESS returns None."""
    import argparse
    sys.path.insert(0, str(PROJECT_ROOT / "skills" / "cell-annotation" / "scripts"))
    import common  # noqa: E402

    parser = argparse.ArgumentParser()
    parser.add_argument("--hidden", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--visible", default="x", help="shown")
    hidden = next(a for a in parser._actions if a.dest == "hidden")
    visible = next(a for a in parser._actions if a.dest == "visible")
    assert common.arg_spec(hidden) is None
    assert common.arg_spec(visible) == {
        "name": "visible",
        "type": "string",
        "required": False,
        "default": "x",
        "help": "shown",
    }


def test_arg_spec_drops_suppress_help_only():
    """Help=SUPPRESS alone is enough to hide the arg (we treat either signal as authoritative)."""
    import argparse
    sys.path.insert(0, str(PROJECT_ROOT / "skills" / "cell-annotation" / "scripts"))
    import common  # noqa: E402

    parser = argparse.ArgumentParser()
    parser.add_argument("--only-help-suppressed", default=0, help=argparse.SUPPRESS)
    action = next(a for a in parser._actions if a.dest == "only_help_suppressed")
    assert common.arg_spec(action) is None


# ---------------------------------------------------------------------------
# Runtime contract: B-class flags still work on the CLI (env_or_default)
# ---------------------------------------------------------------------------

def test_query_missing_organ_fails_fast():
    """--organ is A-class and mandatory; missing it must return a clear error."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "query"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0  # emit() returns 0 even on error envelopes
    payload = json.loads(proc.stdout.strip().split("\n")[-1])
    assert payload["status"] == "error"
    # Either organ-missing fail-fast or markers-missing (when running outside
    # a populated project dir). Both are valid fail-fast paths.
    assert "organ" in payload["error"].lower() or "markers" in payload["error"].lower()