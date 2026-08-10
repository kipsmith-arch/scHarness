"""Echo CLI script — smoke-test target for the loop and the skill loader.

Subcommands:
    repeat  — return the input text as-is (optionally uppercased / repeated).

Self-description contract (implementation_plan.md §3.2, single source of
truth = argparse): running `python echo.py --dump-schema` prints, as the LAST
stdout line, JSON describing every subcommand and its args. The loader
aggregates these declarations into tool_schemas (LLM-facing) and tool_runtime
(loop-facing). Subcommand runs print the standard tool result contract:
    {"status": "ok", "data": {...}}  /  {"status": "error", ...}
"""

import argparse
import json
import sys

_TYPE_MAP = {str: "string", int: "integer", float: "number"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="echo.py", description="Echo tool for loop smoke tests")
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    p_repeat = sub.add_parser("repeat", help="把输入文本原样返回(可转大写/重复)")
    p_repeat.add_argument("--text", required=True, help="要回显的文本")
    p_repeat.add_argument("--times", type=int, default=1, help="重复次数")
    p_repeat.add_argument("--upper", action="store_true", help="转大写")

    return parser


def _arg_spec(action: argparse.Action) -> dict:
    """Introspect one argparse action into the loader's arg declaration."""
    if action.dest == "help":
        return None
    opt = action.option_strings
    name = opt[0].lstrip("-").replace("-", "_") if opt else action.dest
    if action.nargs == 0 or action.const is True or action.const is False:
        atype = "boolean"
    elif action.type is not None:
        atype = _TYPE_MAP.get(action.type, "string")
    elif action.default is not None:
        atype = _TYPE_MAP.get(type(action.default), "string")
    else:
        atype = "string"
    return {
        "name": name,
        "type": atype,
        "required": bool(action.required) or (not opt),
        "default": action.default if action.default is not None else None,
        "help": action.help or "",
    }


def dump_schema() -> None:
    """Print the self-description JSON as the last stdout line."""
    parser = build_parser()
    subparsers = parser._subparsers._group_actions[0].choices
    tools = []
    for subcommand, sp in subparsers.items():
        args = [a for a in (_arg_spec(act) for act in sp._actions) if a]
        tools.append(
            {
                "subcommand": subcommand,
                "description": sp.description or "",
                "args": args,
            }
        )
    print(json.dumps({"tools": tools}, ensure_ascii=False))


def run_repeat(args: argparse.Namespace) -> dict:
    text = args.text * args.times
    if args.upper:
        text = text.upper()
    return {"status": "ok", "data": {"text": text, "input": args.text, "times": args.times}}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        dump_schema()
        return 0
    if not args.subcommand:
        parser.print_help()
        return 2
    result = run_repeat(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
