"""Session entry point (loop_design.md §5.4, file structure §8).

run_session() ties everything together:
    load skill -> merge loop base_prompt + notebook tools -> run the LangGraph
    loop -> save conversation.jsonl.

Also provides a CLI for smoke tests and evals:
    python -m harness.session --skill skills/echo --project-dir output/echo_test \
        --task "echo hello" --model deepseek-v4-flash
"""

from __future__ import annotations

import argparse
import io
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .conversation import load_conversation, save_conversation
from .loop import LOOP_BASE_PROMPT, NOTEBOOK_TOOL_RUNTIME, NOTEBOOK_TOOL_SCHEMAS, build_workflow
from .notebook import init_embedder
from .skill_loader import Skill, load_skill, summarize_skill

DEFAULT_MODEL = "gpt-4o-mini"


def generate_session_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"sess-{stamp}-{random.randint(0, 9999):04d}"


def build_llm(llm_config: Optional[dict] = None) -> ChatOpenAI:
    """Build ChatOpenAI from llm_config, falling back to env vars.

    Priority: llm_config > OPENAI_MODEL > DEFAULT_MODEL.
    API key / base URL fall back to OPENAI_API_KEY / OPENAI_BASE_URL.
    """
    cfg = llm_config or {}
    model = (
        cfg.get("model")
        or os.environ.get("OPENAI_MODEL")
        or DEFAULT_MODEL
    )
    kwargs: Dict = {
        "model": model,
        "temperature": cfg.get("temperature", 0.0),
        # 对不稳定网关(如 dcsapi 概率性断连)提高重试:llm_config > OPENAI_MAX_RETRIES > 6
        "max_retries": int(cfg.get("max_retries") or os.environ.get("OPENAI_MAX_RETRIES", "6")),
    }
    if cfg.get("api_key"):
        kwargs["api_key"] = cfg["api_key"]
    elif os.environ.get("OPENAI_API_KEY"):
        kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]
    elif os.environ.get("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return ChatOpenAI(**kwargs)


def run_session(
    skill: Skill,
    project_dir: str,
    task_message: str,
    llm_config: Optional[dict] = None,
    session_id: Optional[str] = None,
    conversation_path: Optional[str] = None,
    max_turns: int = 100,
    resume: bool = False,
) -> dict:
    """Run one agent session with a loaded skill.

    Args:
        skill: Skill instance from skill_loader.load_skill().
        project_dir: Working directory for outputs (conversation.jsonl,
            notes.jsonl unless RAG_NOTES_DIR is set).
        task_message: User task text.
        llm_config: Optional dict {model, api_key, base_url, temperature}.
        session_id: Optional explicit id; auto-generated otherwise.
        conversation_path: Override conversation.jsonl path.
        max_turns: LangGraph recursion limit.
        resume: If True and conversation.jsonl exists, load history and continue.

    Returns:
        Final LangGraph state (contains messages, session_id, ...).
    """
    init_embedder()
    project_dir = str(project_dir)
    conversation_path = conversation_path or os.path.join(project_dir, "conversation.jsonl")
    notes_path = os.environ.get("RAG_NOTES_DIR") or os.path.join(project_dir, "notes.jsonl")
    session_id = session_id or generate_session_id()

    llm = build_llm(llm_config)
    app = build_workflow(llm)

    if resume and os.path.exists(conversation_path):
        messages = load_conversation(conversation_path)
    else:
        messages = [
            SystemMessage(content=f"{LOOP_BASE_PROMPT}\n\n{skill.system_prompt}"),
            HumanMessage(content=task_message),
        ]

    tool_schemas = skill.tool_schemas + NOTEBOOK_TOOL_SCHEMAS
    tool_runtime = {**skill.tool_runtime, **NOTEBOOK_TOOL_RUNTIME}

    state = {
        "messages": messages,
        "base_prompt": LOOP_BASE_PROMPT,
        "system_prompt": skill.system_prompt,
        "tool_schemas": tool_schemas,
        "tool_runtime": tool_runtime,
        "project_dir": project_dir,
        "notes_path": notes_path,
        "session_id": session_id,
    }

    final_state = app.invoke(state, config={"recursion_limit": max_turns})
    save_conversation(conversation_path, final_state["messages"])
    return final_state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    # tolerate LLM output that the console codepage cannot encode (e.g. emoji)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding=sys.stdout.encoding or "utf-8", errors="replace"
            )

    parser = argparse.ArgumentParser(description="Run an agent session with a standard skill.")
    parser.add_argument("--skill", required=True, help="Path to the skill directory")
    parser.add_argument("--project-dir", required=True, help="Output directory for this run")
    parser.add_argument("--task", default="请简单回复:回声测试通过", help="User task message")
    parser.add_argument("--model", default=None, help="Model name (default: $OPENAI_MODEL or gpt-4o-mini)")
    parser.add_argument("--max-turns", type=int, default=100, help="Recursion limit")
    parser.add_argument("--resume", action="store_true", help="Resume from existing conversation.jsonl")
    parser.add_argument(
        "--dump-skill",
        action="store_true",
        help="Print the loader-derived interfaces (system_prompt / tool_schemas / tool_runtime) and exit",
    )
    args = parser.parse_args(argv)

    try:
        skill = load_skill(args.skill)
    except Exception as exc:
        print(f"[skill_loader] ERROR: {exc}")
        return 1

    if args.dump_skill:
        print(summarize_skill(skill))
        return 0

    print(f"[session] skill={skill.name} model={args.model or os.environ.get('OPENAI_MODEL') or DEFAULT_MODEL}")
    llm_config = {"model": args.model} if args.model else None
    final_state = run_session(
        skill,
        args.project_dir,
        args.task,
        llm_config=llm_config,
        max_turns=args.max_turns,
        resume=args.resume,
    )

    last = final_state["messages"][-1]
    print("=" * 60)
    print("final answer:")
    print(last.content)
    print("=" * 60)
    print(f"[session] conversation saved to {os.path.join(args.project_dir, 'conversation.jsonl')}")
    print(f"[session] session_id = {final_state['session_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
