"""conversation.jsonl read/write (loop_design.md §6).

The loop's only trajectory responsibility is writing the complete message list
to conversation.jsonl at session end. Messages are serialized to NDJSON, one
record per line, and can be loaded back to resume an interrupted session.

Line format:
    {"role": "system", "content": "..."}
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": null, "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "tool_call_id": "call_1", "content": "{...}"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


def _role_of(msg: BaseMessage) -> str:
    if isinstance(msg, SystemMessage):
        return "system"
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, AIMessage):
        return "assistant"
    if isinstance(msg, ToolMessage):
        return "tool"
    return msg.type


def serialize_message(msg: BaseMessage) -> dict:
    """Convert a LangChain message to a plain JSON-able dict."""
    record = {"role": _role_of(msg), "content": msg.content}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        record["tool_calls"] = [
            {"id": tc["id"], "name": tc["name"], "args": tc["args"]}
            for tc in tool_calls
        ]
    if isinstance(msg, ToolMessage):
        record["tool_call_id"] = msg.tool_call_id
    return record


def save_conversation(path: Union[str, Path], messages: List[BaseMessage], mode: str = "w") -> Path:
    """Write the full message list to conversation.jsonl (NDJSON).

    Args:
        path: Output file path (normally <project-dir>/conversation.jsonl).
        messages: Full message list.
        mode: "w" overwrites (fresh session), "a" appends (resumed session).

    Returns:
        The resolved output path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(serialize_message(msg), ensure_ascii=False) + "\n")
    return path


def load_conversation(path: Union[str, Path]) -> List[BaseMessage]:
    """Load a conversation.jsonl back into LangChain message objects.

    Used for resuming an interrupted session (loop_design.md §7.3).
    """
    path = Path(path)
    messages: List[BaseMessage] = []
    if not path.exists():
        return messages
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        role = rec["role"]
        content = rec.get("content")
        if role == "system":
            messages.append(SystemMessage(content=content))
        elif role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            tool_calls = rec.get("tool_calls")
            # langchain-core 1.5+ rejects content=None; guard hand-written nulls
            content = content if content is not None else ""
            if tool_calls:
                messages.append(AIMessage(content=content, tool_calls=tool_calls))
            else:
                messages.append(AIMessage(content=content))
        elif role == "tool":
            messages.append(ToolMessage(content=content, tool_call_id=rec["tool_call_id"]))
    return messages
