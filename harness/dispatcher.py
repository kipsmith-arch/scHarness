"""Tool dispatcher (loop_design.md §5.3).

Executes tool calls declared in the skill's tool_runtime plus the loop's
builtin notebook tools. Dispatch is by spec["type"]:

    - subprocess: run `python <script> <subcommand> [--arg value ...]`, then
                  parse the LAST stdout line as JSON. Contract (tool_design):
                      {"status": "ok", "data": {...}}
                      {"status": "error", "error": "...", "stderr_tail": "..."}
                  The loop passes `data` through verbatim without parsing it.
    - function:   importlib-import spec["function"] ("module.func"),
                  call func(args, state), return its dict result.
    - builtin:    loop notebook tools (write_note / retrieve_notes).

New tool types only require extending dispatch(); the skill format is untouched.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from typing import Any, Dict

from . import notebook


def run_subprocess(spec: Dict[str, Any], args: Dict[str, Any]) -> dict:
    """Execute a skill CLI script and return its stdout-JSON result."""
    cmd = [sys.executable, str(spec["script"])]
    subcommand = spec.get("subcommand")
    if subcommand:
        cmd.append(subcommand)
    allowed = spec.get("arg_names")
    if allowed:
        allowed_set = set(allowed)
        args = {k: v for k, v in (args or {}).items() if k in allowed_set}
    for key, value in (args or {}).items():
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                cmd.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            cmd.append(flag)
            cmd.extend(str(v) for v in value)
            continue
        cmd.extend([flag, str(value)])

    timeout = float(spec.get("timeout", 3600))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"timeout after {timeout}s", "cmd": " ".join(cmd)}
    except OSError as exc:
        return {"status": "error", "error": f"cannot run script: {exc}", "cmd": " ".join(cmd)}

    if result.returncode != 0:
        return {
            "status": "error",
            "error": f"exit code {result.returncode}",
            "stderr_tail": result.stderr[-2000:],
            "cmd": " ".join(cmd),
        }

    lines = result.stdout.strip().split("\n")
    if not lines:
        return {"status": "error", "error": "empty stdout", "cmd": " ".join(cmd)}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "unparseable stdout (last line is not JSON)",
            "stdout_tail": result.stdout[-2000:],
            "cmd": " ".join(cmd),
        }


def run_function(spec: Dict[str, Any], args: Dict[str, Any], state: dict) -> dict:
    """Import spec["function"] (module.func) and call func(args, state)."""
    module_path, _, func_name = spec["function"].rpartition(".")
    if not module_path:
        return {"status": "error", "error": f"invalid function spec: {spec['function']!r}"}
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
    except Exception as exc:  # ImportError / AttributeError
        return {"status": "error", "error": f"cannot load {spec['function']}: {exc}"}
    try:
        result = func(args, state)
    except Exception as exc:
        return {"status": "error", "error": f"{spec['function']} raised: {exc}"}
    if not isinstance(result, dict):
        result = {"status": "ok", "data": result}
    return result


def run_builtin(spec: Dict[str, Any], args: Dict[str, Any], state: dict) -> dict:
    """Execute loop builtin notebook tools (never skill-owned)."""
    func = notebook.NOTEBOOK_FUNCS.get(spec.get("name", ""))
    if func is None:
        return {"status": "error", "error": f"unknown builtin tool: {spec.get('name')}"}
    return func(args, state)


def dispatch(spec: Dict[str, Any], args: Dict[str, Any], state: dict) -> dict:
    """Dispatch one tool call by spec["type"]."""
    tool_type = spec.get("type")
    if tool_type == "subprocess":
        return run_subprocess(spec, args)
    if tool_type == "function":
        return run_function(spec, args, state)
    if tool_type == "builtin":
        return run_builtin(spec, args, state)
    return {"status": "error", "error": f"unknown tool type: {tool_type!r}"}
