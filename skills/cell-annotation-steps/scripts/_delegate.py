"""Run the matching script in skills/cell-annotation/scripts.

Wrappers exist so this skill loads the same commands without registering
write_judgment. The implementation stays in one place.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def delegate(script_name: str) -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "cell-annotation"
        / "scripts"
        / script_name
    )
    if not src.is_file():
        sys.stderr.write(f"missing implementation: {src}\n")
        raise SystemExit(1)
    runpy.run_path(str(src), run_name="__main__")
