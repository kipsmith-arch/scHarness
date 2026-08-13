"""B1 judges — produce per-cluster annotations + write judgment records.

Each judge is invoked *after* ``experiments/scripted_driver.py`` has produced
the per-step JSON artifacts (markers.json, kg_hits.json, refined_annotations.json,
final_annotations.json from step6 default). The judge then:

1. Reads ``run_log.jsonl`` for the relevant exec metrics (cluster-level per dp)
2. Decides, per cluster, what the label / confidence / status *should be* given
   the arm's policy (default vs. rule)
3. Writes per-cluster ``write_judgment__add`` records to run_log (trajectory)
4. Rewrites ``step6_validate/final_annotations.json`` so that the *effective*
   annotation differs across arms (this is what ``evaluate_cell_level.py``
   compares — not the judgment records themselves).

Shared helpers live here; the two arms (default, rule) are thin policy layers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "..", "skills", "cell-annotation", "scripts"))


def read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_run_log(project_dir: str) -> list[dict]:
    """Parse ``run_log.jsonl`` into a list of records (skip blank lines)."""
    path = os.path.join(project_dir, "run_log.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def latest_exec_metrics(run_log: list[dict], step_op: str) -> dict | None:
    """Highest-seq exec record's metrics for ``{step}.{op}#*``."""
    prefix = f"{step_op}#"
    best = None
    for r in run_log:
        if r.get("type") == "exec" and r.get("run_id", "").startswith(prefix):
            if best is None or r.get("seq", 0) > best.get("seq", 0):
                best = r
    return best["metrics"] if best else None


def write_judgment(project_dir: str, decision_point: str, decision: str,
                   scope_type: str, cluster_id: str | None,
                   run_ref: str, reasoning: str, confidence: str = "high",
                   action: str = "") -> dict:
    """Invoke ``write_judgment.py add`` as the LLM/tool would. Returns the parsed status line."""
    cmd = [
        sys.executable, os.path.join(SKILL_SCRIPTS, "write_judgment.py"),
        "add",
        "--project-dir", project_dir,
        "--decision-point", decision_point,
        "--decision", decision,
        "--scope-type", scope_type,
        "--run-ref", run_ref,
        "--inputs", "[]",
        "--confidence", confidence,
        "--action", action or f"arm-policy: {decision}",
        "--reasoning", reasoning,
    ]
    if cluster_id is not None:
        cmd += ["--cluster-id", str(cluster_id)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {"status": "error", "returncode": proc.returncode, "stdout_tail": last,
                "stderr_tail": (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else ""}


def rewrite_final_annotations(project_dir: str, per_cluster: dict[str, dict]) -> str:
    """Replace final_annotations.json with arm-derived per-cluster labels.

    ``per_cluster``: {cluster_id: {label, confidence, status, ...}}.
    Returns the path written.
    """
    final_path = os.path.join(project_dir, "step6_validate", "final_annotations.json")
    payload = read_json(final_path) or {"annotations": {}, "_summary": {}}
    base_anns = {str(c): v for c, v in payload.get("annotations", {}).items()}
    merged: dict[str, dict] = {}
    for c, v in per_cluster.items():
        key = str(c)
        prev = base_anns.get(key, {"cluster_id": key})
        merged[key] = {**prev, **v}
    for c, v in base_anns.items():
        if c not in merged:
            merged[c] = v
    payload["annotations"] = merged
    n_clusters = len(payload["annotations"])
    n_unknown = sum(1 for a in payload["annotations"].values() if a.get("status") == "unknown")
    unique = sorted({a.get("label") for a in payload["annotations"].values() if a.get("label")})
    payload["_summary"] = {
        **payload.get("_summary", {}),
        "n_clusters": n_clusters,
        "n_unknown": n_unknown,
        "unknown_rate": (n_unknown / n_clusters) if n_clusters else 0.0,
        "n_unique_labels": len(unique),
    }
    write_json(final_path, payload)
    return final_path


def final_annotations_clusters(project_dir: str) -> dict[str, dict]:
    """Return ``{cluster_id: annotation_entry}`` from final_annotations.json."""
    payload = read_json(os.path.join(project_dir, "step6_validate", "final_annotations.json"))
    if not payload:
        return {}
    return {str(c): v for c, v in payload.get("annotations", {}).items()}


def refined_clusters(project_dir: str) -> dict[str, dict]:
    """Return ``{cluster_id: refined_entry}`` from refined_annotations.json."""
    payload = read_json(os.path.join(project_dir, "step5_refine", "refined_annotations.json"))
    if not payload:
        return {}
    return {str(c): v for c, v in payload.get("clusters", {}).items()}


def write_session_end(project_dir: str, summary: dict) -> dict:
    """Append a session_end record so validate_log (e2e mode) is happy."""
    cmd = [
        sys.executable, os.path.join(SKILL_SCRIPTS, "write_judgment.py"),
        "session-end",
        "--project-dir", project_dir,
        "--final-summary", json.dumps(summary, ensure_ascii=False),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {"status": "error", "stdout_tail": last}