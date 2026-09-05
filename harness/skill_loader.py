"""Standard skill loader (implementation_plan.md §3.2).

A standard skill package looks like:

    skills/<name>/
    ├── SKILL.md          YAML frontmatter (name, description [, functions]) + body
    └── scripts/          one .py per CLI script, each supports --dump-schema

Derivation rules — the single source of truth is each script's argparse:
    - SKILL.md frontmatter -> name / description (metadata)
    - SKILL.md body        -> system_prompt (concatenated with loop base_prompt)
    - scripts/*.py         -> run `python <script> --dump-schema`, which prints
                              JSON describing every subcommand and its args; the
                              loader aggregates them into:
                                  tool_schemas  OpenAI function-calling format
                                                (LLM-facing)
                                  tool_runtime  name -> {type, script, subcommand}
                                                (loop-facing)
    - optional frontmatter `functions:` list declares type="function" tools
      (module.func paths resolved against the skill's scripts/ dir).

Errors are raised with clear messages for malformed packages: missing
SKILL.md, missing frontmatter, or no scripts/ with any tool.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# map our schema types -> JSON schema types
_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

_SCHEMA_FLAG = "--dump-schema"


class SkillError(Exception):
    """Raised when a skill package is malformed or cannot be loaded."""


@dataclass
class Skill:
    """Loaded standard skill: the three interfaces the loop needs."""

    name: str
    description: str
    system_prompt: str
    tool_schemas: List[dict] = field(default_factory=list)   # OpenAI function-calling
    tool_runtime: Dict[str, dict] = field(default_factory=dict)  # name -> spec
    skill_dir: Path = None

    @property
    def tool_names(self) -> List[str]:
        return list(self.tool_runtime.keys())


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse YAML frontmatter delimited by --- lines. Empty dict if absent."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError("SKILL.md frontmatter must be a YAML mapping")
    return data


def _build_function_tool(decl: Dict[str, Any], scripts_dir: Path) -> tuple:
    """Build (schema, runtime) for a frontmatter-declared function tool."""
    name = decl.get("name")
    if not name:
        raise SkillError("functions[] entry missing 'name'")
    func_path = decl.get("function")
    if not func_path:
        raise SkillError(f"function tool {name!r} missing 'function' (module.func)")
    parameters = decl.get("parameters") or {"type": "object", "properties": {}}
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": decl.get("description", ""),
            "parameters": parameters,
        },
    }
    runtime = {"type": "function", "function": func_path}
    # resolve module paths against the skill's scripts/ dir
    module = func_path.rpartition(".")[0]
    if module and str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    return schema, runtime


# ---------------------------------------------------------------------------
# scripts -> tools (via --dump-schema)
# ---------------------------------------------------------------------------

def _run_dump_schema(script: Path) -> List[dict]:
    """Run `python <script> --dump-schema`, return the normalized tool list.

    Non-tool scripts (library modules with no CLI) are skipped silently: they
    either exit 0 with empty stdout, or are rejected by argparse with exit
    code 2 / "unrecognized arguments". Any other failure raises SkillError.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(script), _SCHEMA_FLAG],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError(f"cannot run {script.name} --dump-schema: {exc}") from exc

    stderr_tail = result.stderr[-500:]
    if result.returncode == 0 and not result.stdout.strip():
        # library module executed cleanly without printing -> not a tool script
        return []
    if result.returncode == 2 and "unrecognized arguments" in result.stderr:
        # argparse rejected --dump-schema -> not a tool script
        return []
    if result.returncode != 0:
        raise SkillError(
            f"{script.name} --dump-schema failed (exit {result.returncode}): "
            f"{stderr_tail}"
        )
    lines = result.stdout.strip().split("\n")
    if not lines:
        raise SkillError(f"{script.name} --dump-schema produced empty stdout")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise SkillError(
            f"{script.name} --dump-schema: last stdout line is not JSON: {exc}"
        ) from exc

    # normalize: {"tools": [...]} | [...] | single tool dict
    if isinstance(payload, dict) and "tools" in payload:
        tools = payload["tools"]
    elif isinstance(payload, list):
        tools = payload
    elif isinstance(payload, dict):
        tools = [payload]
    else:
        raise SkillError(f"{script.name} --dump-schema: unexpected payload {payload!r}")

    for tool in tools:
        if not isinstance(tool, dict) or "subcommand" not in tool:
            raise SkillError(
                f"{script.name} --dump-schema: each tool needs a 'subcommand' key"
            )
    return tools


def _tool_name(script_stem: str, subcommand: str) -> str:
    # function-calling names must match ^[a-zA-Z0-9_-]+$ — dots are rejected;
    # double underscore keeps stem and subcommand unambiguous (both use single _).
    return f"{script_stem}__{subcommand}"


def _build_subprocess_schema(script_stem: str, tool: dict) -> dict:
    """Convert one {subcommand, description, args[]} declaration to OpenAI FC."""
    properties = {}
    required = []
    for arg in tool.get("args") or []:
        name = arg["name"]
        properties[name] = {
            "type": _TYPE_MAP.get(arg.get("type", "string"), "string"),
            "description": arg.get("help") or "",
        }
        if arg.get("required"):
            required.append(name)
    parameters = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": _tool_name(script_stem, tool["subcommand"]),
            "description": tool.get("description") or "",
            "parameters": parameters,
        },
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def load_skill(skill_dir) -> Skill:
    """Load a standard skill package and derive the three interfaces.

    Args:
        skill_dir: Path to the skill directory (contains SKILL.md and scripts/).

    Returns:
        Skill instance with system_prompt / tool_schemas / tool_runtime.

    Raises:
        SkillError: malformed package (missing SKILL.md / frontmatter / scripts).
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise SkillError(f"skill directory not found: {skill_dir}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise SkillError(
            f"skill {skill_dir.name!r} is missing SKILL.md "
            "(standard anatomy requires SKILL.md with YAML frontmatter)"
        )
    md_text = skill_md.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(md_text)
    if not frontmatter:
        raise SkillError(
            f"skill {skill_dir.name!r}: SKILL.md has no YAML frontmatter "
            "(need at least 'name' and 'description')"
        )
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        raise SkillError(
            f"skill {skill_dir.name!r}: frontmatter requires 'name' and 'description'"
        )
    body = md_text.split("---", 2)[2].strip() if md_text.count("---") >= 2 else ""

    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        raise SkillError(
            f"skill {skill_dir.name!r} has no scripts/ directory "
            "(standard anatomy requires scripts/ with CLI tools)"
        )
    script_files = sorted(scripts_dir.glob("*.py"))
    if not script_files:
        raise SkillError(f"skill {skill_dir.name!r}: scripts/ contains no *.py files")

    tool_schemas: List[dict] = []
    tool_runtime: Dict[str, dict] = {}
    errors = []
    for script in script_files:
        try:
            tools = _run_dump_schema(script)
        except SkillError as exc:
            errors.append(str(exc))
            continue
        for tool in tools:
            subcommand = tool["subcommand"]
            tool_full_name = _tool_name(script.stem, subcommand)
            tool_schemas.append(_build_subprocess_schema(script.stem, tool))
            tool_runtime[tool_full_name] = {
                "type": "subprocess",
                "script": str(script),
                "subcommand": subcommand,
                "arg_names": [a["name"] for a in (tool.get("args") or []) if a.get("name")],
            }
    if errors:
        raise SkillError(
            f"skill {skill_dir.name!r}: failed to derive tools from scripts:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    if not tool_runtime:
        raise SkillError(
            f"skill {skill_dir.name!r}: scripts/ declared no tools via --dump-schema"
        )

    # optional function-type tools from frontmatter
    for decl in frontmatter.get("functions") or []:
        schema, runtime = _build_function_tool(decl, scripts_dir)
        tool_schemas.append(schema)
        tool_runtime[schema["function"]["name"]] = runtime

    return Skill(
        name=name,
        description=description,
        system_prompt=body,
        tool_schemas=tool_schemas,
        tool_runtime=tool_runtime,
        skill_dir=skill_dir,
    )


def summarize_skill(skill: Skill) -> str:
    """Human-readable summary of the derived interfaces (for --dump-skill)."""
    lines = [
        f"name: {skill.name}",
        f"description: {skill.description}",
        f"system_prompt: {len(skill.system_prompt)} chars, {len(skill.system_prompt.splitlines())} lines",
        "",
        f"tools ({len(skill.tool_schemas)}):",
    ]
    for schema in skill.tool_schemas:
        fn = schema["function"]
        params = fn.get("parameters", {})
        props = list(params.get("properties", {}).keys())
        req = params.get("required", [])
        lines.append(
            f"  - {fn['name']}  (type={skill.tool_runtime[fn['name']]['type']}, "
            f"args={props}, required={req})\n      {fn.get('description', '')}"
        )
    return "\n".join(lines)
