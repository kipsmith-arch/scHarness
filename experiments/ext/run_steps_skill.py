"""Run skills/cell-annotation-steps with the LLM on Arabidopsis root.

Same loader, model, temperature, and notebook setting as B1 arm 3.
The user message only names the data. The procedure comes from the skill.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RAW = ROOT / "dataset" / "h5ad" / "SRP171040.h5ad"
PROJECT = ROOT / "output" / "steps_arabidopsis"
SKILL = ROOT / "skills" / "cell-annotation-steps"
TOOL_TIMEOUT = 14400
MAX_TURNS = 80


def task_text() -> str:
    return """请对拟南芥根单细胞 RNA-seq 做细胞类型注释。

数据与目录：
- raw h5ad: dataset/h5ad/SRP171040.h5ad
- project-dir: output/steps_arabidopsis
- organism: Arabidopsis thaliana
- organ: root

请开始执行。
"""


def preflight() -> None:
    import annot_harness.config  # noqa: F401
    import subprocess

    if not RAW.is_file():
        raise SystemExit(f"missing raw h5ad: {RAW}")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing")
    tmp = tempfile.mkdtemp(prefix="steps-preflight-")
    try:
        cmd = [
            sys.executable,
            str(ROOT / "skills" / "cell-annotation" / "scripts" / "step3c_kg.py"),
            "test-connection",
            "--project-dir",
            tmp,
        ]
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0 or '"status": "ok"' not in (proc.stdout or "") and '"status":"ok"' not in (proc.stdout or ""):
            tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-800:]
            raise SystemExit(f"Neo4j preflight failed:\n{tail}")
        print("neo4j preflight ok", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    from annot_harness.session import run_session
    from annot_harness.skill_loader import load_skill

    os.chdir(ROOT)
    os.environ["RAG_EMBEDDING"] = "off"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    preflight()
    PROJECT.mkdir(parents=True, exist_ok=True)
    (PROJECT / "task.txt").write_text(task_text(), encoding="utf-8")
    skill = load_skill(str(SKILL))
    for spec in skill.tool_runtime.values():
        spec["timeout"] = max(float(spec.get("timeout") or 0), TOOL_TIMEOUT)
    print(
        f"session skill={skill.name} project={PROJECT} "
        f"model={os.environ.get('OPENAI_MODEL')} max_turns={MAX_TURNS} notebook=False",
        flush=True,
    )
    final_state = run_session(
        skill,
        str(PROJECT),
        task_text(),
        max_turns=MAX_TURNS,
        notebook=False,
    )
    last = final_state["messages"][-1]
    print("=" * 60, flush=True)
    print("final answer:", flush=True)
    print(getattr(last, "content", ""), flush=True)
    print("=" * 60, flush=True)
    print(f"conversation={PROJECT / 'conversation.jsonl'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
