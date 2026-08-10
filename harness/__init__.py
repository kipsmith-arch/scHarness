"""scHarness loop package.

Generic, domain-agnostic agent loop (loop_design.md):
- skill_loader  : load a standard skill package -> system_prompt / tool_schemas / tool_runtime
- loop          : LangGraph LLM <-> tool loop
- dispatcher    : execute tools (subprocess / function / builtin)
- notebook      : loop built-in cross-session memory (write_note / retrieve_notes)
- session       : run_session() entry point + CLI
- conversation  : conversation.jsonl read/write
"""

__all__ = [
    "skill_loader",
    "loop",
    "dispatcher",
    "notebook",
    "session",
    "conversation",
]
