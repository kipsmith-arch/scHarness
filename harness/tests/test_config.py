"""Tests for ``harness.config`` — the project-root .env loader.

These tests are isolated from the rest of the harness: they do NOT import
``harness.config`` at module top (the package __init__ would otherwise auto-
load .env on first import and make test ordering nondeterministic). Each test
re-imports the module with the desired environment under monkeypatch.

The tests use dummy keys (``CFG_TEST_*``) that are not in ``RECOGNIZED_KEYS``
— the loader should treat any key written in .env the same way regardless of
whether it is in the recognized set, so this proves the dotenv-loading logic
without coupling tests to any specific config schema.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Inlined mirror of harness.config.RECOGNIZED_KEYS — we cannot import it from
# the module itself because that import would trigger the eager load_dotenv
# bootstrap and pollute os.environ before the test can control it.
_RECOGNIZED_KEYS = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "OPENAI_MAX_RETRIES",
    "RAG_NOTES_DIR", "RAG_EMBEDDING",
)
# Dummy keys for tests (not in RECOGNIZED_KEYS by design — exercises the
# loader's behavior for arbitrary .env entries).
_DUMMY = "CFG_TEST_KEY"


def _drop_harness_modules() -> None:
    for name in [n for n in list(sys.modules) if n == "harness" or n.startswith("harness.")]:
        sys.modules.pop(name, None)


def _write_env(body: str) -> tuple[Path, str | None]:
    env_path = PROJECT_ROOT / ".env"
    backup = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    env_path.write_text(body, encoding="utf-8")
    return env_path, backup


def _restore_env(env_path: Path, backup: str | None) -> None:
    if backup is None:
        env_path.unlink(missing_ok=True)
    else:
        env_path.write_text(backup, encoding="utf-8")


def _reload_config(monkeypatch, env_overrides: dict | None = None,
                   skip_dotenv: bool = False) -> None:
    """Drop the cached module and re-import with controlled environment.

    Also seeds the requested env overrides (and clears recognized keys) so
    the next import sees a pristine environment.
    """
    # Clear the package init side-effect too: drop all harness modules.
    # This must run BEFORE any `import harness.config`, otherwise the module
    # gets cached and a second import won't re-run the bootstrap.
    _drop_harness_modules()

    # Wipe any previously-loaded recognized keys (only those; leave non-config env alone).
    for key in _RECOGNIZED_KEYS:
        monkeypatch.delenv(key, raising=False)

    if env_overrides:
        for k, v in env_overrides.items():
            monkeypatch.setenv(k, v)
    if skip_dotenv:
        monkeypatch.setenv("SC_HARNESS_SKIP_DOTENV", "1")
    else:
        monkeypatch.delenv("SC_HARNESS_SKIP_DOTENV", raising=False)

    # This import is now the FIRST time the module is loaded since we
    # cleared sys.modules, so the bootstrap runs under our controlled env.
    import harness.config  # noqa: F401, E402  (triggers auto-load)


def test_load_dotenv_picks_up_arbitrary_keys(monkeypatch):
    """.env populates any key it contains into os.environ (regardless of RECOGNIZED_KEYS)."""
    env_path, backup = _write_env(
        f"{_DUMMY}=from-dotenv\nANOTHER_DUMMY=also-loaded\n"
    )
    try:
        _drop_harness_modules()
        monkeypatch.delenv(_DUMMY, raising=False)
        monkeypatch.delenv("ANOTHER_DUMMY", raising=False)
        import harness.config  # noqa: E402
        import os
        assert os.environ[_DUMMY] == "from-dotenv"
        assert os.environ["ANOTHER_DUMMY"] == "also-loaded"
    finally:
        _restore_env(env_path, backup)


def test_load_dotenv_file_wins_over_existing_env(monkeypatch):
    """.env wins over shell env (revised priority 2026-09).

    Rationale: this project treats ``.env`` as the canonical configuration;
    a stale shell export must not silently shadow an updated ``.env`` entry.
    The ``pre_existing`` snapshot is still used to fill *gaps* — shell env
    provides a key if and only if neither ``.env`` nor ``.env.example``
    mentions it. This test verifies that ``.env`` overwrites shell.
    """
    env_path, backup = _write_env(f"{_DUMMY}=from-dotenv\n")
    try:
        _drop_harness_modules()
        monkeypatch.setenv(_DUMMY, "from-shell")
        import harness.config  # noqa: E402  -- bootstrap runs now
        import os
        assert os.environ[_DUMMY] == "from-dotenv"
    finally:
        _restore_env(env_path, backup)


def test_load_dotenv_shell_fills_gap_when_file_missing(monkeypatch):
    """When .env does not mention a key, shell env still provides it.

    The revised priority chain is ``.env > shell > .env.example > default``.
    Shell does not *shadow* ``.env``; it *fills gaps* that ``.env`` left.
    """
    env_path, backup = _write_env("ANOTHER_DUMMY=from-dotenv\n")
    try:
        _drop_harness_modules()
        monkeypatch.delenv("ANOTHER_DUMMY", raising=False)
        monkeypatch.setenv(_DUMMY, "from-shell")  # not in .env
        import harness.config  # noqa: E402
        import os
        assert os.environ[_DUMMY] == "from-shell"
        assert os.environ["ANOTHER_DUMMY"] == "from-dotenv"
    finally:
        _restore_env(env_path, backup)


def test_load_dotenv_override_true_overwrites(monkeypatch):
    """When override=True, .env wins over shell env (explicit opt-in).

    Since the default behaviour already has ``.env`` winning, override=True
    is now mostly a redundant test escape hatch — preserved for callers that
    want belt-and-suspenders. The contract test stays for compatibility.
    """
    env_path, backup = _write_env(f"{_DUMMY}=from-dotenv\n")
    try:
        _drop_harness_modules()
        monkeypatch.setenv(_DUMMY, "from-shell")
        import harness.config  # noqa: E402
        harness.config.load_dotenv(override=True)
        import os
        assert os.environ[_DUMMY] == "from-dotenv"
    finally:
        _restore_env(env_path, backup)


def test_skip_dotenv_disables_loading(monkeypatch):
    """SC_HARNESS_SKIP_DOTENV=1 keeps os.environ clean of .env values."""
    env_path, backup = _write_env(f"{_DUMMY}=from-dotenv\n")
    try:
        monkeypatch.delenv(_DUMMY, raising=False)
        _reload_config(monkeypatch, skip_dotenv=True)
        import os
        assert _DUMMY not in os.environ
    finally:
        _restore_env(env_path, backup)


def test_load_dotenv_missing_python_dotenv(monkeypatch):
    """When python-dotenv is not installed, the loader is a silent no-op."""
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("dotenv."):
            raise ImportError("simulated: dotenv not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    env_path, backup = _write_env(f"{_DUMMY}=from-dotenv\n")
    try:
        monkeypatch.delenv(_DUMMY, raising=False)
        _reload_config(monkeypatch)
        import os
        assert _DUMMY not in os.environ  # loader failed silently
    finally:
        _restore_env(env_path, backup)


def test_harness_package_init_triggers_dotenv(monkeypatch):
    """Importing any submodule of harness loads .env exactly once (side-effect of __init__)."""
    env_path, backup = _write_env(f"{_DUMMY}=from-package-init\n")
    try:
        monkeypatch.delenv(_DUMMY, raising=False)
        _drop_harness_modules()
        import harness.notebook  # noqa: F401  -- deep import, no direct config ref
        import os
        assert os.environ[_DUMMY] == "from-package-init"
    finally:
        _restore_env(env_path, backup)


def test_get_helper_is_os_environ_get(monkeypatch):
    """harness.config.get(key, default) is a thin wrapper around os.environ.get."""
    monkeypatch.setenv(_DUMMY, "via-get")
    _drop_harness_modules()
    import harness.config  # noqa: E402
    assert harness.config.get(_DUMMY) == "via-get"
    assert harness.config.get(_DUMMY, "fallback") == "via-get"
    assert harness.config.get("UNSET_KEY_XYZ", "fallback") == "fallback"
    assert harness.config.get("UNSET_KEY_XYZ") is None