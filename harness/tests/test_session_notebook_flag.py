"""NB_ON / NB_OFF for harness.session (spec-b1-three-arm-rerun). No LLM, no h5ad."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from harness.loop import LOOP_BASE_PROMPT, LOOP_BASE_PROMPT_NO_NOTEBOOK, NOTEBOOK_TOOL_SCHEMAS
from harness.session import (
    build_session_messages,
    merge_session_tools,
    merged_tool_names,
    session_base_prompt,
)


def _fake_skill():
    return SimpleNamespace(
        tool_schemas=[{"type": "function", "function": {"name": "echo__repeat"}}],
        tool_runtime={"echo__repeat": {"type": "subprocess"}},
    )


def _schema_names(schemas) -> set[str]:
    names = set()
    for item in schemas:
        fn = item.get("function") or {}
        if fn.get("name"):
            names.add(fn["name"])
    return names


def test_nb_off_excludes_notebook_tools():
    schemas, runtime = merge_session_tools(_fake_skill(), notebook=False)
    names = _schema_names(schemas)
    assert "echo__repeat" in names
    assert "write_note" not in names
    assert "retrieve_notes" not in names
    assert "write_note" not in runtime
    assert "retrieve_notes" not in runtime


def test_nb_on_includes_notebook_tools():
    schemas, runtime = merge_session_tools(_fake_skill(), notebook=True)
    names = _schema_names(schemas)
    notebook_names = {s["function"]["name"] for s in NOTEBOOK_TOOL_SCHEMAS}
    assert notebook_names <= names
    assert "echo__repeat" in names
    assert "write_note" in runtime
    assert "retrieve_notes" in runtime


def test_nb_off_prompt_omits_notebook_guidance():
    off = session_base_prompt(False)
    on = session_base_prompt(True)
    assert on == LOOP_BASE_PROMPT
    assert "write_note" not in off
    assert "retrieve_notes" not in off
    assert "笔记本" not in off
    assert "write_note" in on
    assert "笔记本" in on


def test_default_merge_is_notebook_on():
    schemas_default, _ = merge_session_tools(_fake_skill())
    schemas_on, _ = merge_session_tools(_fake_skill(), notebook=True)
    assert _schema_names(schemas_default) == _schema_names(schemas_on)


def test_resume_restamps_system_prompt_when_notebook_off(tmp_path):
    skill = SimpleNamespace(system_prompt="SKILL_PROMPT", tool_schemas=[], tool_runtime={})
    conv = tmp_path / "conversation.jsonl"
    conv.write_text(
        '{"role":"system","content":"旧笔记本指引 write_note"}\n'
        '{"role":"user","content":"继续"}\n',
        encoding="utf-8",
    )
    msgs = build_session_messages(
        skill, "ignored", notebook=False, resume=True, conversation_path=str(conv),
    )
    assert isinstance(msgs[0], SystemMessage)
    assert "write_note" not in msgs[0].content
    assert "笔记本" not in msgs[0].content
    assert "SKILL_PROMPT" in msgs[0].content
    assert LOOP_BASE_PROMPT_NO_NOTEBOOK.split("规则:")[0].strip() in msgs[0].content
    assert isinstance(msgs[1], HumanMessage)


def test_resume_empty_conversation_starts_fresh(tmp_path):
    skill = SimpleNamespace(system_prompt="SKILL_PROMPT", tool_schemas=[], tool_runtime={})
    conv = tmp_path / "conversation.jsonl"
    conv.write_text("", encoding="utf-8")
    msgs = build_session_messages(
        skill, "新任务", notebook=False, resume=True, conversation_path=str(conv),
    )
    assert len(msgs) == 2
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[1].content == "新任务"
    assert "笔记本" not in msgs[0].content


def test_dump_skill_no_notebook_omits_notes(capsys, tmp_path):
    from harness.session import main

    echo = Path(__file__).resolve().parent.parent.parent / "skills" / "echo"
    rc = main([
        "--skill", str(echo),
        "--project-dir", str(tmp_path),
        "--dump-skill",
        "--no-notebook",
    ])
    assert rc == 0
    merged = capsys.readouterr().out.split("session_merged_tools:")[-1]
    assert "write_note" not in merged
    assert "retrieve_notes" not in merged


def test_dump_skill_default_lists_notes(capsys, tmp_path):
    from harness.session import main

    echo = Path(__file__).resolve().parent.parent.parent / "skills" / "echo"
    rc = main(["--skill", str(echo), "--project-dir", str(tmp_path), "--dump-skill"])
    assert rc == 0
    merged = capsys.readouterr().out.split("session_merged_tools:")[-1]
    assert "write_note" in merged
    assert "retrieve_notes" in merged
    assert set(merged_tool_names(_fake_skill(), True)) >= {"write_note", "retrieve_notes"}
