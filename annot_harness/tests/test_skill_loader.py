"""Standard skill loader tests: derivation rules + malformed-package errors."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from annot_harness.skill_loader import SkillError, load_skill, summarize_skill

ECHO_SKILL = Path(__file__).resolve().parent.parent.parent / "skills" / "echo"

# ---------------------------------------------------------------------------
# helpers: build a minimal skill package in tmp_path
# ---------------------------------------------------------------------------

ECHO_SCRIPT = """\
import argparse, json

def build_parser():
    parser = argparse.ArgumentParser(prog="echo.py")
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")
    p = sub.add_parser("repeat", help="echo text back")
    p.add_argument("--text", required=True, help="text to echo")
    p.add_argument("--times", type=int, default=1, help="repeat count")
    p.add_argument("--upper", action="store_true", help="uppercase")
    return parser

def dump_schema():
    import sys
    sys.stdout.write(json.dumps({"tools": [{"subcommand": "repeat", "description": "echo text back",
        "args": [
            {"name": "text", "type": "string", "required": True, "default": None, "help": "text to echo"},
            {"name": "times", "type": "integer", "required": False, "default": 1, "help": "repeat count"},
            {"name": "upper", "type": "boolean", "required": False, "default": False, "help": "uppercase"},
        ]}]}, ensure_ascii=False))

if __name__ == "__main__":
    import sys
    if "--dump-schema" in sys.argv:
        dump_schema()
    else:
        args = build_parser().parse_args()
        text = args.text * args.times
        if args.upper:
            text = text.upper()
        print(json.dumps({"status": "ok", "data": {"text": text}}, ensure_ascii=False))
"""


def make_skill(tmp_path, name="mini", frontmatter=None, script=ECHO_SCRIPT, script_name="mini.py"):
    """Create a standard-format skill package and return its directory."""
    skill_dir = tmp_path / name
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    if frontmatter is None:
        frontmatter = "name: mini\ndescription: 最小测试技能\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}---\n# body\n测试 body 内容\n", encoding="utf-8"
    )
    (scripts / script_name).write_text(script, encoding="utf-8")
    return skill_dir


# ---------------------------------------------------------------------------
# derivation
# ---------------------------------------------------------------------------

def test_load_echo_skill_from_repo():
    skill = load_skill(ECHO_SKILL)
    assert skill.name == "echo"
    assert "回声" in skill.description
    assert "Echo 技能" in skill.system_prompt
    names = {s["function"]["name"] for s in skill.tool_schemas}
    assert names == {"echo__repeat", "echo_reverse"}
    assert set(skill.tool_runtime) == names
    assert skill.tool_runtime["echo__repeat"]["type"] == "subprocess"
    assert skill.tool_runtime["echo_reverse"]["type"] == "function"
    assert skill.tool_runtime["echo__repeat"]["subcommand"] == "repeat"


def test_derived_subprocess_schema_shape(tmp_path):
    skill = load_skill(make_skill(tmp_path))
    schema = next(s for s in skill.tool_schemas if s["function"]["name"] == "mini__repeat")
    fn = schema["function"]
    assert fn["name"] == "mini__repeat"
    assert fn["parameters"]["type"] == "object"
    props = fn["parameters"]["properties"]
    assert props["text"]["type"] == "string"
    assert props["times"]["type"] == "integer"
    assert props["upper"]["type"] == "boolean"
    assert fn["parameters"]["required"] == ["text"]


def test_tool_names_match_function_calling_pattern(tmp_path):
    """OpenAI/DeepSeek reject names not matching ^[a-zA-Z0-9_-]+$."""
    skill = load_skill(make_skill(tmp_path))
    pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    for name in skill.tool_runtime:
        assert pattern.match(name), f"tool name {name!r} invalid"
    assert "mini__repeat" in skill.tool_runtime  # double-underscore separator


def test_frontmatter_functions_declare_function_tools(tmp_path):
    fm = (
        "name: mini\ndescription: 带函数工具\n"
        "functions:\n"
        "  - name: mini_reverse\n"
        "    description: 反转文本\n"
        "    function: echo_lib.reverse\n"
        "    parameters:\n"
        "      type: object\n"
        "      properties:\n"
        "        text: {type: string, description: 文本}\n"
        "      required: [text]\n"
    )
    skill = load_skill(make_skill(tmp_path, frontmatter=fm))
    rt = skill.tool_runtime["mini_reverse"]
    assert rt["type"] == "function"
    assert rt["function"] == "echo_lib.reverse"
    schema = next(s for s in skill.tool_schemas if s["function"]["name"] == "mini_reverse")
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_optional_scripts_are_skipped(tmp_path):
    """Library modules without a CLI must not break tool derivation."""
    skill_dir = make_skill(tmp_path)
    (skill_dir / "scripts" / "echo_lib.py").write_text(
        "def reverse(args, state):\n    return {'status': 'ok', 'data': {'reversed': args['text'][::-1]}}\n",
        encoding="utf-8",
    )
    skill = load_skill(skill_dir)
    assert "mini__repeat" in skill.tool_runtime
    assert len(skill.tool_runtime) == 1


# ---------------------------------------------------------------------------
# malformed packages -> clear SkillError
# ---------------------------------------------------------------------------

def test_missing_skill_md(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    with pytest.raises(SkillError, match="missing SKILL.md"):
        load_skill(d)


def test_missing_frontmatter(tmp_path):
    d = tmp_path / "x"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("# 没有 frontmatter\n", encoding="utf-8")
    with pytest.raises(SkillError, match="no YAML frontmatter"):
        load_skill(d)


def test_frontmatter_missing_required_keys(tmp_path):
    d = tmp_path / "x"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: only_name\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SkillError, match="'name' and 'description'"):
        load_skill(d)


def test_missing_scripts_dir(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SkillError, match="no scripts/"):
        load_skill(d)


def test_empty_scripts_dir(tmp_path):
    d = tmp_path / "x"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SkillError, match="no \\*.py files"):
        load_skill(d)


def test_dump_schema_broken_script_raises(tmp_path):
    script = "print('this is not json')\n"  # exits 0, prints garbage
    d = make_skill(tmp_path, script=script)
    with pytest.raises(SkillError, match="not JSON"):
        load_skill(d)


def test_summarize_skill_smoke():
    skill = load_skill(ECHO_SKILL)
    text = summarize_skill(skill)
    assert "name: echo" in text
    assert "echo__repeat" in text
    assert "echo_reverse" in text
