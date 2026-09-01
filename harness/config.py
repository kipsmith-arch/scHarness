"""Single source of truth for HARNESS-LEVEL configuration (tool_design.md §10).

Scope: this module owns the **agent loop's** own configuration only — the
OpenAI-compatible LLM gateway (API key / base URL / model / retries) and the
notebook / RAG toggles. Skill-level configuration (e.g. cell-annotation's
Neo4j connection and KG mapping file) lives in ``<skill>/.env`` and is loaded
by the skill's own ``scripts/common.py``; the harness must not know what
external services a skill depends on (loop_design.md: "domain-agnostic").

Why a dedicated module:
    - ``.env`` is gitignored and therefore invisible to a fresh clone; this
      module makes the *set* of recognized keys discoverable (see
      ``.env.example`` for the template + ``CONFIGURATION_REFERENCE.md`` for
      the canonical schema).
    - The skill stays environment-agnostic (``tool_design.md §10``: "skill 本身
      保持环境无关"); skills read ``os.environ`` via their own loader, they
      do not import from ``harness.config``.
    - Loading happens once at import time; later mutations of ``os.environ``
      (e.g. ``experiments/judges/rule_judge.py``'s save / restore of
      ``PROJECT_DIR``) take effect immediately without re-running the loader.

Priority chain (tool_design.md §10): ``CLI flag > env var > hardcoded default``.
This module only mediates the *env var* step; CLI overrides are still applied
by each script's argparse layer above this loader.

Disable loading (tests / CI): set ``SC_HARNESS_SKIP_DOTENV=1`` in the environment
*before* importing anything from ``harness``; this skips ``load_dotenv`` and
makes the codebase behave exactly like the pre-config-file version (only
vars explicitly set in the shell are visible).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

# ---- recognized config keys (the schema) --------------------------------
# Listed in the order they appear in docs/CONFIGURATION_REFERENCE.md §2.
# These are configuration for the HARNESS itself (the agent loop). Skill-level
# configuration (e.g. cell-annotation's NEO4J_* / KG_*) is NOT here — each
# skill owns its own .env loaded by its own scripts/common.py.
#
# Type annotations drive the docstring only; no runtime coercion happens here
# (each call site does its own parsing — same shape as the pre-loader code).
RECOGNIZED_KEYS: tuple[str, ...] = (
    # OpenAI-compatible LLM gateway
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_MAX_RETRIES",
    # Notebook / RAG
    "RAG_NOTES_DIR",
    "RAG_EMBEDDING",
)

# ---- loader ------------------------------------------------------------

def _project_root() -> Path:
    """Repo root = parent of the ``harness`` package."""
    return Path(__file__).resolve().parent.parent


def _env_files_in_order() -> Iterable[Path]:
    """Dotenv files to consider.

    Two files, two roles:
        - ``.env.example`` (tracked template): seeds defaults so a fresh clone
          boots without manual setup.
        - ``.env`` (gitignored, per-user secrets): user-provided overrides.

    Both are read by ``load_dotenv``; see that function's docstring for the
    two-pass loading order that makes the priority: shell env > ``.env`` >
    ``.env.example``.
    """
    root = _project_root()
    yield root / ".env.example"
    yield root / ".env"


def load_dotenv(override: bool = False) -> list[str]:
    """Load project-root ``.env`` into ``os.environ``.

    Project-specific priority chain (revised 2026-09 — see docs/CONFIGURATION_REFERENCE.md §3.0):
        .env  >  shell env  >  .env.example  >  hardcoded defaults

    Rationale: this is a single-researcher project with one canonical ``.env``
    file that captures the user's chosen credentials / endpoints. Shell env
    leaking from older sessions (or from a CI inject that the user forgot
    about) must NOT silently shadow ``.env`` — if the user updates ``.env``
    they expect it to take effect on the next Python start.

    Mirrors ``python-dotenv.dotenv_values`` semantics, but:
        - loads from a project-relative path (not the cwd of the caller)
        - skips when ``SC_HARNESS_SKIP_DOTENV=1`` is already set in the shell
        - never raises — a missing ``python-dotenv`` or unreadable file just
          returns ``[]``; the rest of the codebase keeps its hardcoded
          defaults (tool_design.md §10: "硬编码默认值").

    Args:
        override: When True, ``.env`` (and only ``.env``) becomes the
            authoritative source — even shell env is overwritten. Default
            False: ``.env`` still wins over shell env (priority is
            ``.env > shell > .env.example > default``), but shell env
            fills keys ``.env`` did not mention. In practice override=True
            is rarely needed.

    Returns:
        List of keys actually populated from the dotenv files (only the
        recognized keys; unknown keys are still loaded into ``os.environ`` so
        power users can stash extras, but they are not reported here).
    """
    if os.environ.get("SC_HARNESS_SKIP_DOTENV") == "1":
        return []
    try:
        from dotenv import dotenv_values
    except ImportError:
        # python-dotenv missing — silently fall through to hardcoded defaults
        return []

    populated: list[str] = []
    paths = list(_env_files_in_order())
    if not paths:
        return populated

    # Snapshot EVERY key already in os.environ at entry — these came from
    # the shell, parent process, or a prior loader pass. Under the revised
    # priority chain (``file > shell``), the snapshot is only used to skip
    # re-loading a shell-set key that ``.env`` did NOT mention (so shell
    # still fills gaps, just does not shadow ``.env``).
    pre_existing: set[str] = set(os.environ)

    # Pass 1: load ``.env`` (per-user secrets). PROJECT-AUTHORITATIVE:
    # overwrites any same-name key already in os.environ (typically a stale
    # shell export). This is the inversion from the previous behaviour.
    for path in reversed(paths[1:]):  # .env (file with secrets) — reversed order is harmless since there is at most one
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
            os.environ[key] = value  # file wins — overwrite shell if present
            if key in RECOGNIZED_KEYS:
                populated.append(key)

    # Pass 2: load ``.env.example`` (tracked template) as a fallback for
    # keys the shell did not set AND ``.env`` did not mention. It cannot
    # shadow either — we only set when neither source has touched the key.
    for path in paths[:1]:
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
            if key in pre_existing:
                # shell set this; ``.env`` may or may not have overridden
                # it above. Either way, template must NOT overwrite either.
                continue
            if key in os.environ:
                # ``.env`` already set this key in pass 1; template is just
                # a fallback, skip.
                continue
            os.environ[key] = value
            if key in RECOGNIZED_KEYS:
                populated.append(key)

    # ``override=True`` is an explicit opt-in: file beats even keys ``.env``
    # already wrote (e.g. tests that need pristine shell env overridden).
    # In normal use this is unnecessary because ``.env`` already wins over
    # shell; the hook is preserved for backward compatibility.
    if override:
        for path in paths:
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
                if key in RECOGNIZED_KEYS:
                    populated.append(key)
    return populated


def get(key: str, default: str | None = None) -> str | None:
    """Read a config value via ``os.environ`` — thin convenience wrapper.

    Provided so call sites that prefer an explicit accessor (vs. raw
    ``os.environ.get``) can be spotted by grep. Behaviorally identical to
    ``os.environ.get(key, default)``; returns ``None`` when unset.
    """
    return os.environ.get(key, default)


# ---- eager bootstrap ----------------------------------------------------
# Importing this module (directly or transitively, e.g. ``harness.session``)
# auto-loads the dotenv files exactly once. Side effects are limited to
# ``os.environ`` mutation, so importing is otherwise a no-op.
_LOADED_AT_IMPORT = False
if not _LOADED_AT_IMPORT:
    load_dotenv()
    _LOADED_AT_IMPORT = True


__all__ = ["RECOGNIZED_KEYS", "load_dotenv", "get"]