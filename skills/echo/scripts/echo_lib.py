"""Function-type tool for the echo skill (declared in SKILL.md frontmatter).

Function tools receive (args, state) and return a dict in the standard
{status, data|error} contract. The module path "echo_lib.reverse" is resolved
against the skill's scripts/ directory by the skill loader.
"""


def reverse(args: dict, state: dict) -> dict:
    """Reverse the input text.

    Args:
        args: {"text": str}
        state: loop AgentState (unused here, present for the contract).

    Returns:
        {"status": "ok", "data": {"reversed": str}}
    """
    text = str(args.get("text", ""))
    return {"status": "ok", "data": {"reversed": text[::-1]}}
