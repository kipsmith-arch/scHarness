"""conversation.jsonl serialization round-trip tests."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from annot_harness.conversation import load_conversation, save_conversation, serialize_message


def test_serialize_system_and_user():
    sys_msg = SystemMessage(content="base prompt\n\nskill prompt")
    user_msg = HumanMessage(content="请注释数据集")
    assert serialize_message(sys_msg) == {"role": "system", "content": "base prompt\n\nskill prompt"}
    assert serialize_message(user_msg) == {"role": "user", "content": "请注释数据集"}


def test_serialize_assistant_with_tool_calls():
    msg = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "echo__repeat", "args": {"text": "hi"}}],
    )
    rec = serialize_message(msg)
    assert rec["role"] == "assistant"
    assert rec["tool_calls"] == [{"id": "call_1", "name": "echo__repeat", "args": {"text": "hi"}}]


def test_serialize_tool_message():
    msg = ToolMessage(content='{"status": "ok", "data": {"text": "hi"}}', tool_call_id="call_1")
    rec = serialize_message(msg)
    assert rec["role"] == "tool"
    assert rec["tool_call_id"] == "call_1"


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "conversation.jsonl"
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="task"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "echo__repeat", "args": {"text": "hi"}}]),
        ToolMessage(content='{"status": "ok", "data": {"text": "hi"}}', tool_call_id="call_1"),
        AIMessage(content="完成"),
    ]
    save_conversation(path, messages)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    # NDJSON: every line parses independently
    for line in lines:
        json.loads(line)

    loaded = load_conversation(path)
    assert [type(m).__name__ for m in loaded] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "AIMessage",
    ]
    assert loaded[2].tool_calls[0]["id"] == "call_1"
    assert loaded[3].tool_call_id == "call_1"
    assert loaded[4].content == "完成"


def test_unicode_content_round_trip(tmp_path):
    """Chinese content must survive NDJSON write/read byte-for-byte."""
    path = tmp_path / "conversation.jsonl"
    original = "拟南芥根 QC:看线粒体与叶绿体基因占比 ✅"
    save_conversation(path, [SystemMessage(content=original)])
    assert load_conversation(path)[0].content == original


def test_load_handles_null_assistant_content(tmp_path):
    """Hand-written jsonl may carry null content on tool-call messages."""
    path = tmp_path / "conversation.jsonl"
    path.write_text(
        '{"role": "assistant", "content": null, "tool_calls": [{"id": "c1", "name": "t", "args": {}}]}\n',
        encoding="utf-8",
    )
    loaded = load_conversation(path)
    assert len(loaded) == 1
    assert loaded[0].content == ""
    assert loaded[0].tool_calls[0]["id"] == "c1"


def test_resume_appends_without_losing_history(tmp_path):
    path = tmp_path / "conversation.jsonl"
    first = [SystemMessage(content="sys"), HumanMessage(content="第一轮")]
    save_conversation(path, first)
    resumed = load_conversation(path) + [HumanMessage(content="第二轮")]
    save_conversation(path, resumed, mode="w")
    loaded = load_conversation(path)
    assert [m.content for m in loaded] == ["sys", "第一轮", "第二轮"]


def test_load_missing_file_returns_empty(tmp_path):
    assert load_conversation(tmp_path / "nope.jsonl") == []
