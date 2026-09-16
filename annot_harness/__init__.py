"""annotHarness loop package (细胞注释 harness).

Generic, domain-agnostic agent loop (loop_design.md):
- skill_loader  : load a standard skill package -> system_prompt / tool_schemas / tool_runtime
- loop          : LangGraph LLM <-> tool loop
- dispatcher    : execute tools (subprocess / function / builtin)
- dag           : domain-agnostic DAG nodes / topo sort / retry cap
- scripted_driver : ①② DAG walker (shares dispatcher.dispatch with the loop)
- notebook      : loop built-in cross-session memory (write_note / retrieve_notes)
- session       : run_session() entry point + CLI
- conversation  : conversation.jsonl read/write
- config        : project-root .env loader (auto-imported so that any
                  ``from annot_harness.X import ...`` triggers the dotenv load)
"""

# Importing ``annot_harness.config`` here triggers the project-root .env /
# .env.example load exactly once, before any submodule evaluates. Skill
# scripts and the eval tooling do NOT import from annot_harness; they rely on
# subprocess inheritance from the parent loop for harness-owned env vars
# (LLM gateway, RAG toggles) and load their own skill-level .env via
# scripts/common.py for skill-owned config (e.g. cell-annotation's Neo4j).
from . import config  # noqa: F401

__all__ = [
    "config",
    "skill_loader",
    "loop",
    "dispatcher",
    "dag",
    "scripted_driver",
    "notebook",
    "session",
    "conversation",
]
