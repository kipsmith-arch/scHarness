"""B1 scripted driver — run the 47-op pipeline end-to-end via subprocess.

Why this exists (experiment_implementation.md §3.1):
    Three arms (default / rule / llm) must share the same substrate: identical
    raw h5ad, identical DAG (47 ops), identical ``dispatcher.dispatch``, identical
    ``run_log.jsonl`` format. The only thing that differs across arms is the
    *judgment layer* — driven separately by ``experiments/judges/*``.

This driver is the substrate: it walks step1 → step7 in DAG order, invoking each
script via subprocess so shell-level re-use mirrors what the LLM arm sees (and so
the exec record / stdout JSON contract is identical). Judgment writing is
*not* this driver's concern — call ``experiments/judges/{default,rule}_judge.py``
after the driver completes (they read run_log.jsonl + per-step JSON outputs
and emit judgment records).

The LLM arm skips this driver entirely: it runs through ``harness.session`` so
that LLM-driven judgments are produced organically.

Usage:
    python experiments/scripted_driver.py \\
        --project-dir experiments/B1/arm1_default --raw dataset/h5ad/SRP171040.h5ad
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback

# repo_root is the parent of the experiments/ package
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
SKILL_SCRIPTS = os.path.join(REPO_ROOT, "skills", "cell-annotation", "scripts")

# DAG order: each entry is (script, subcommand, extra_args_list).
# Marker / KG / refine / validate / diagnose need either pre-existing artifacts
# (run from a project_dir that already has step1 outputs) or a raw h5ad.
PIPELINE_STEPS = [
    ("step1_prepare.py", "run", []),
    ("step2_markers.py", "run", []),
    ("step3_kg.py", "query", []),
    ("step3_kg.py", "test-connection", []),
    ("step4_judge.py", "run", []),
    ("step5_refine.py", "run", []),
    ("step6_validate.py", "run", []),
    ("step6_validate.py", "report", []),
    ("step7_diagnose.py", "run", []),
]


def run_step(script: str, sub: str, project_dir: str, raw: str | None,
             extra_args: list[str]) -> dict:
    """Invoke one pipeline script and return its status line.

    Each script prints its final ``{"status": ..., "data": ...}`` JSON as the
    last stdout line (per the harness/tool contract); we parse that line and
    propagate non-ok status to the caller.
    """
    cmd = [
        sys.executable,
        os.path.join(SKILL_SCRIPTS, script),
        sub,
        "--project-dir", project_dir,
    ]
    if raw:
        cmd += ["--input", raw]
    cmd += extra_args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    ok = proc.returncode == 0
    return {
        "script": script,
        "sub": sub,
        "returncode": proc.returncode,
        "stdout_tail": last,
        "stderr_tail": (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "",
        "ok": ok,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="scripted_driver.py",
        description="B1 substrate: run all 47 pipeline ops end-to-end via subprocess.",
    )
    ap.add_argument("--project-dir", required=True,
                    help="Working directory (will contain run_log.jsonl + per-step JSONs)")
    ap.add_argument("--raw", default=None,
                    help="Raw h5ad path. If omitted, step1 will fail; only useful when "
                         "step1 has already been run (project_dir already has processed.h5ad)")
    ap.add_argument("--skip", nargs="*", default=(),
                    help="Subcommands to skip, e.g. --skip 'step1_prepare.run' "
                         "if step1 outputs are already present.")
    args = ap.parse_args()

    os.makedirs(args.project_dir, exist_ok=True)
    log_path = os.path.join(args.project_dir, "run_log.jsonl")
    if not os.path.exists(log_path):
        # initialise empty log so append_log picks seq=1 for the first session_start
        # that the LLM/rule judge will write later.
        open(log_path, "w", encoding="utf-8").close()

    results = []
    fail = None
    for script, sub, extra in PIPELINE_STEPS:
        marker = f"{os.path.splitext(script)[0]}.{sub}"
        if marker in args.skip:
            print(f"[driver] skip {marker}")
            continue
        print(f"[driver] {marker} ...", flush=True)
        r = run_step(script, sub, args.project_dir, args.raw, extra)
        results.append(r)
        if not r["ok"]:
            fail = r
            print(f"[driver] FAIL {marker}: rc={r['returncode']} stderr={r['stderr_tail']}")
            break
        print(f"[driver] ok   {marker}")

    if fail:
        print(f"[driver] ABORT at {fail['script']}.{fail['sub']}", file=sys.stderr)
        return 1
    print(f"[driver] all {len(results)} steps complete in {args.project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())