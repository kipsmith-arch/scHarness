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

    Mirrors ``python-dotenv.dotenv_values`` semantics, but:
        - loads from a project-relative path (not the cwd of the caller)
        - skips when ``SC_HARNESS_SKIP_DOTENV=1`` is already set in the shell
        - never raises — a missing ``python-dotenv`` or unreadable file just
          returns ``[]``; the rest of the codebase keeps its hardcoded
          defaults (tool_design.md §10: "硬编码默认值").

    Args:
        override: When True, ``.env`` values overwrite pre-existing
            ``os.environ`` entries. Default False — process / shell env wins
            over the file, matching conventional dotenv semantics and letting
            CI / Docker inject values without being clobbered by stale
            on-disk ``.env`` files.

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
    # the shell, parent process, or a prior loader pass and are authoritative
    # over anything we'll read from disk. The snapshot covers all keys (not
    # just RECOGNIZED_KEYS) so arbitrary user-set env vars also win over the
    # dotenv files. RECOGNIZED_KEYS is only used for *reporting* which keys
    # were populated from disk.
    pre_existing: set[str] = set(os.environ)

    # Pass 1: load the *first* dotenv file (``.env.example``) as a seed for
    # keys not yet in os.environ. This gives a fresh clone sensible defaults
    # while still respecting anything the shell already exported.
    seeded_from_template: set[str] = set()
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
            if key in os.environ:
                continue  # shell wins, template is just a fallback
            os.environ[key] = value
            seeded_from_template.add(key)
            if key in RECOGNIZED_KEYS:
                populated.append(key)

    # Pass 2: load any subsequent file (``.env``). Priority within this pass:
    #   1. shell env (pre_existing)    — ALWAYS wins
    #   2. .env (current file)          — wins over template seed
    #   3. .env.example template seed   — only place the value can be set if
    #                                     .env did not mention this key
    # The check ``key not in pre_existing`` is what prevents ``.env`` from
    # clobbering a value the shell already set, even if pass 1 also seeded
    # the same key from .env.example after the shell wrote.
    for path in paths[1:]:
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
                continue  # shell env is authoritative; do not touch
            os.environ[key] = value
            seeded_from_template.discard(key)
            if key in RECOGNIZED_KEYS:
                populated.append(key)

    # ``override=True`` is an explicit opt-in for tests / power users; flip the
    # final state to honor the user's request without affecting pass logic.
    if override:
        # Re-read every dotenv file once more with override semantics so the
        # file beats even the shell env. This is the test escape hatch.
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