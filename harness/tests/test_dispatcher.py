"""Tool dispatcher tests: subprocess / function / builtin dispatch + contracts."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness.dispatcher import dispatch
from harness.skill_loader import load_skill

ECHO_SKILL = Path(__file__).resolve().parent.parent.parent / "skills" / "echo"


@pytest.fixture(scope="module")
def echo_runtime():
    return load_skill(ECHO_SKILL).tool_runtime


@pytest.fixture
def state(tmp_path):
    return {
        "project_dir": str(tmp_path),
        "notes_path": str(tmp_path / "notes.jsonl"),
        "session_id": "sess-test",
    }


# ---------------------------------------------------------------------------
# subprocess
# ---------------------------------------------------------------------------

def test_subprocess_ok(echo_runtime, state):
    spec = echo_runtime["echo__repeat"]
    result = dispatch(spec, {"text": "hi", "times": 2, "upper": True}, state)
    assert result == {"status": "ok", "data": {"text": "HIHI", "input": "hi", "times": 2}}


def test_subprocess_drops_unknown_args(echo_runtime, state):
    spec = echo_runtime["echo__repeat"]
    result = dispatch(spec, {"text": "hi", "organ": "root", "times": 1}, state)
    assert result["status"] == "ok"
    assert result["data"]["text"] == "hi"


def test_subprocess_boolean_flag_omitted(echo_runtime, state):
    spec = echo_runtime["echo__repeat"]
    result = dispatch(spec, {"text": "hi", "upper": False}, state)
    assert result["data"]["text"] == "hi"


def test_subprocess_error_on_bad_args(echo_runtime, state):
    # times expects an int; passing "abc" makes argparse fail with exit code 2
    spec = echo_runtime["echo__repeat"]
    result = dispatch(spec, {"text": "hi", "times": "abc"}, state)
    assert result["status"] == "error"
    assert "exit code" in result["error"]


def test_subprocess_unknown_tool_name(echo_runtime, state):
    spec = dict(echo_runtime["echo__repeat"])
    spec["subcommand"] = "does_not_exist"
    result = dispatch(spec, {"text": "hi"}, state)
    assert result["status"] == "error"


def test_subprocess_unicode_stdout(tmp_path, state):
    """Scripts printing Chinese must round-trip as UTF-8 regardless of console codepage."""
    script = tmp_path / "uni.py"
    script.write_text(
        "import json\nprint(json.dumps({'status': 'ok', 'data': {'msg': '拟南芥根 ✅'}}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    spec = {"type": "subprocess", "script": str(script)}
    result = dispatch(spec, {}, state)
    assert result["status"] == "ok"
    assert result["data"]["msg"] == "拟南芥根 ✅"


def test_subprocess_parses_last_stdout_line_only(tmp_path, state):
    """Contract: only the LAST stdout line is parsed as JSON."""
    script = tmp_path / "multi.py"
    script.write_text(
        "print('progress 1')\nprint('progress 2')\n"
        "import json\nprint(json.dumps({'status': 'ok', 'data': {'done': True}}))\n",
        encoding="utf-8",
    )
    spec = {"type": "subprocess", "script": str(script)}
    result = dispatch(spec, {}, state)
    assert result == {"status": "ok", "data": {"done": True}}


def test_subprocess_non_json_stdout(tmp_path, state):
    script = tmp_path / "garbage.py"
    script.write_text("print('no json here')\n", encoding="utf-8")
    spec = {"type": "subprocess", "script": str(script)}
    result = dispatch(spec, {}, state)
    assert result["status"] == "error"
    assert "unparseable stdout" in result["error"]


def test_subprocess_timeout(tmp_path, state):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    spec = {"type": "subprocess", "script": str(script), "timeout": 0.5}
    t0 = time.time()
    result = dispatch(spec, {}, state)
    assert result["status"] == "error"
    assert "timeout" in result["error"]
    assert time.time() - t0 < 4


def test_subprocess_missing_script(state):
    spec = {"type": "subprocess", "script": "C:/does/not/exist.py"}
    result = dispatch(spec, {}, state)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# function
# ---------------------------------------------------------------------------

def test_function_ok(echo_runtime, state):
    spec = echo_runtime["echo_reverse"]
    result = dispatch(spec, {"text": "abc"}, state)
    assert result == {"status": "ok", "data": {"reversed": "cba"}}


def test_function_missing_module(state):
    spec = {"type": "function", "function": "no_such_module_xyz.func"}
    result = dispatch(spec, {}, state)
    assert result["status"] == "error"
    assert "cannot load" in result["error"]


def test_function_raising(state):
    import harness.tests.test_dispatcher as td

    spec = {"type": "function", "function": f"{td.__name__}._raise_fn"}
    result = dispatch(spec, {}, state)
    assert result["status"] == "error"
    assert "raised" in result["error"]


def _raise_fn(args, state):
    raise ValueError("boom")


def test_function_non_dict_return_wrapped(state):
    spec = {"type": "function", "function": "harness.tests.test_dispatcher._str_fn"}
    result = dispatch(spec, {}, state)
    assert result == {"status": "ok", "data": "plain string"}


def _str_fn(args, state):
    return "plain string"


# ---------------------------------------------------------------------------
# builtin
# ---------------------------------------------------------------------------

def test_builtin_write_and_retrieve(state):
    spec = {"type": "builtin", "name": "write_note"}
    result = dispatch(spec, {"content": "一条经验", "tags": ["qc"]}, state)
    assert result["status"] == "ok"
    assert result["data"]["note_id"] == "note-0001"

    r = dispatch({"type": "builtin", "name": "retrieve_notes"}, {"query": "经验", "top_k": 5}, state)
    assert r["status"] == "ok"
    assert r["data"]["notes"][0]["content"] == "一条经验"


def test_builtin_unknown_name(state):
    result = dispatch({"type": "builtin", "name": "nope"}, {}, state)
    assert result["status"] == "error"
    assert "unknown builtin" in result["error"]


# ---------------------------------------------------------------------------
# dispatch-level
# ---------------------------------------------------------------------------

def test_unknown_tool_type(state):
    result = dispatch({"type": "http", "url": "x"}, {}, state)
    assert result["status"] == "error"
    assert "unknown tool type" in result["error"]
