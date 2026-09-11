"""Tests for the cell-annotation skill's Neo4j credential loader.

The loader is implemented as ``load_skill_dotenv`` inside
``skills/cell-annotation/scripts/common.py``. Scope: Neo4j connection
credentials plus BLAST subject-library / query-FASTA paths.
KG query tunables (``--species-type``, ``--min-confidence``,
``--max-ancestor-hops``) are LLM-facing argparse parameters.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = PROJECT_ROOT / "skills" / "cell-annotation"
SKILL_SCRIPTS_DIR = SKILL_DIR / "scripts"
SKILL_ENV = SKILL_DIR / ".env"
SKILL_ENV_EXAMPLE = SKILL_DIR / ".env.example"


@pytest.fixture(autouse=True)
def _add_skill_scripts_to_path():
    """Add the skill's scripts/ to sys.path so ``import common`` resolves."""
    p = str(SKILL_SCRIPTS_DIR)
    added = p not in sys.path
    if added:
        sys.path.insert(0, p)
    yield
    if added:
        try:
            sys.path.remove(p)
        except ValueError:
            pass


# Recognized cell-annotation env vars (mirror of common.SKILL_DOTENV_KEYS).
# Inlined so the tests do not depend on importing common (which would trigger
# the loader side-effect and pollute env).
SKILL_KEYS = (
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
    "CELL_ANNOTATION_BLASTDB_DIR", "CELL_ANNOTATION_BLASTDB_URL",
    "CELL_ANNOTATION_BLASTDB_SHA256", "CELL_ANNOTATION_QUERY_FASTA",
)
SKIP_VAR = "CELL_ANNOTATION_SKIP_DOTENV"


def _drop_skill_modules() -> None:
    targets = [n for n in list(sys.modules) if n == "common" or n.endswith(".common")]
    for name in targets:
        sys.modules.pop(name, None)


def _write_skill_env(body: str) -> tuple[Path, str | None, Path, str | None]:
    env_backup = SKILL_ENV.read_text(encoding="utf-8") if SKILL_ENV.exists() else None
    ex_backup = SKILL_ENV_EXAMPLE.read_text(encoding="utf-8") if SKILL_ENV_EXAMPLE.exists() else None
    SKILL_ENV.write_text(body, encoding="utf-8")
    return SKILL_ENV, env_backup, SKILL_ENV_EXAMPLE, ex_backup


def _restore_skill_env(env_backup: str | None, ex_backup: str | None) -> None:
    if env_backup is None:
        SKILL_ENV.unlink(missing_ok=True)
    else:
        SKILL_ENV.write_text(env_backup, encoding="utf-8")
    if ex_backup is None:
        SKILL_ENV_EXAMPLE.unlink(missing_ok=True)
    else:
        SKILL_ENV_EXAMPLE.write_text(ex_backup, encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_skill_env(monkeypatch):
    """Wipe cell-annotation env before each test and restore after."""
    for k in SKILL_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv(SKIP_VAR, raising=False)
    _drop_skill_modules()
    yield
    _drop_skill_modules()


def test_load_skill_dotenv_seeds_from_env_example(monkeypatch):
    """.env.example seeds Neo4j defaults when no ``.env`` is present.

    We temporarily hide any user ``.env`` (the autouse ``_isolate_skill_env``
    fixture only wipes the shell env, not the on-disk file) so the loader is
    forced to read ``.env.example``. ``_restore_skill_env`` puts it back.
    """
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("")  # empty .env
    try:
        _drop_skill_modules()
        import common  # noqa: F401  -- triggers load_skill_dotenv()
        import os
        assert os.environ.get("NEO4J_URI") == "bolt://localhost:7687"
        assert os.environ.get("NEO4J_USER") == "neo4j"
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_load_skill_dotenv_does_not_overwrite_shell_env(monkeypatch):
    """Shell env wins over .env (standard dotenv semantics)."""
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.setenv("NEO4J_URI", "bolt://from-shell")
        import common  # noqa: F401
        import os
        assert os.environ["NEO4J_URI"] == "bolt://from-shell"
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_load_skill_dotenv_env_overrides_env_example(monkeypatch):
    """.env upgrades values seeded by .env.example, but only if shell didn't set them."""
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.delenv("NEO4J_URI", raising=False)
        import common  # noqa: F401
        import os
        assert os.environ["NEO4J_URI"] == "bolt://from-env"
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_load_skill_dotenv_override_true(monkeypatch):
    """override=True makes .env beat even shell env (test escape hatch)."""
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.setenv("NEO4J_URI", "bolt://from-shell")
        import common  # noqa: F401
        common.load_skill_dotenv(override=True)
        import os
        assert os.environ["NEO4J_URI"] == "bolt://from-env"
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_load_skill_dotenv_skip_disables(monkeypatch):
    """CELL_ANNOTATION_SKIP_DOTENV=1 prevents the loader from running."""
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.setenv(SKIP_VAR, "1")
        monkeypatch.delenv("NEO4J_URI", raising=False)
        import common  # noqa: F401
        import os
        assert "NEO4J_URI" not in os.environ
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_load_skill_dotenv_missing_python_dotenv(monkeypatch):
    """Missing python-dotenv: silent no-op (matches harness/config.py)."""
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("dotenv."):
            raise ImportError("simulated: dotenv not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.delenv("NEO4J_URI", raising=False)
        import common  # noqa: F401
        import os
        assert "NEO4J_URI" not in os.environ  # loader failed silently
    finally:
        _restore_skill_env(env_backup, ex_backup)


def test_import_common_triggers_load(monkeypatch):
    """Importing common (the way every step script does) loads dotenv automatically."""
    env_path, env_backup, ex_path, ex_backup = _write_skill_env("NEO4J_URI=bolt://from-env\n")
    try:
        _drop_skill_modules()
        monkeypatch.delenv("NEO4J_URI", raising=False)
        import common  # noqa: F401
        import os
        assert os.environ["NEO4J_URI"] == "bolt://from-env"
    finally:
        _restore_skill_env(env_backup, ex_backup)