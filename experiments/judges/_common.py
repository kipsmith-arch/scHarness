"""Judge protocol helpers for B1 scripted arms.

Protocol::

    decide(dp, exec_record, history, *, scope=None, project_dir=None) -> dict
    commit_labels(project_dir, history) -> None   # materialise judgment labels only

Judges never rewrite measurements. ``commit_labels`` only patches
label / confidence / status onto ``final_annotations.json``.
"""
from __future__ import annotations

import json
import os
from typing import Any


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


def latest_exec_record(run_log: list[dict], step_op: str) -> dict | None:
    prefix = f"{step_op}#"
    best = None
    for r in run_log:
        if r.get("type") == "exec" and r.get("run_id", "").startswith(prefix):
            if best is None or r.get("seq", 0) > best.get("seq", 0):
                best = r
    return best


def latest_exec_metrics(run_log: list[dict], step_op: str) -> dict | None:
    rec = latest_exec_record(run_log, step_op)
    return rec["metrics"] if rec else None


def rank_annotations(project_dir: str) -> dict[str, dict]:
    payload = read_json(os.path.join(project_dir, "step4_rank", "annotations.json"))
    if not payload:
        return {}
    return {str(c): v for c, v in payload.get("annotations", {}).items()}


def final_annotations_clusters(project_dir: str) -> dict[str, dict]:
    payload = read_json(os.path.join(project_dir, "step6_validate", "final_annotations.json"))
    if not payload:
        return {}
    return {str(c): v for c, v in payload.get("annotations", {}).items()}


def refined_clusters(project_dir: str) -> dict[str, dict]:
    payload = read_json(os.path.join(project_dir, "step5_refine", "refined_annotations.json"))
    if not payload:
        return {}
    return {str(c): v for c, v in payload.get("clusters", {}).items()}


def commit_label_patches(project_dir: str, patches: dict[str, dict]) -> str:
    """Merge judge-written label/confidence/status into final_annotations.json."""
    final_path = os.path.join(project_dir, "step6_validate", "final_annotations.json")
    payload = read_json(final_path) or {"annotations": {}, "_summary": {}}
    base = {str(c): v for c, v in payload.get("annotations", {}).items()}
    for cid, patch in patches.items():
        key = str(cid)
        prev = base.get(key, {"cluster_id": key})
        base[key] = {**prev, **patch}
    payload["annotations"] = base
    n_clusters = len(base)
    n_unknown = sum(1 for a in base.values() if a.get("status") == "unknown")
    unique = sorted({a.get("label") for a in base.values() if a.get("label")})
    payload["_summary"] = {
        **payload.get("_summary", {}),
        "n_clusters": n_clusters,
        "n_unknown": n_unknown,
        "unknown_rate": (n_unknown / n_clusters) if n_clusters else 0.0,
        "n_unique_labels": len(unique),
    }
    write_json(final_path, payload)
    return final_path


def cluster_entry(project_dir: str, cid: str) -> dict:
    """Measurement view for one cluster: refine overlay, else rank, else final."""
    cid = str(cid)
    r = refined_clusters(project_dir).get(cid) or {}
    a = rank_annotations(project_dir).get(cid) or {}
    f = final_annotations_clusters(project_dir).get(cid) or {}
    return {**a, **f, **r}


def materialize_llm_labels(project_dir: str) -> str:
    """Write ③ label_confirm judgments onto final_annotations.json.

    Loop does not execute ``output.action``; this is the ③ analog of
    ``commit_labels``. Label text comes from the measurement first_candidate
    (or unknown); confidence/status come from the latest label_confirm
    judgment per cluster.
    """
    latest: dict[str, dict] = {}
    for rec in load_run_log(project_dir):
        if rec.get("type") != "judgment":
            continue
        if rec.get("decision_point") != "label_confirm":
            continue
        cid = str((rec.get("scope") or {}).get("cluster_id") or "")
        if cid and cid != "_none":
            latest[cid] = rec
    if not latest:
        raise SystemExit(
            f"error: {project_dir} 没有 label_confirm judgment，无法把标签写入 final_annotations"
        )
    patches: dict[str, dict] = {}
    for cid, rec in latest.items():
        entry = cluster_entry(project_dir, cid)
        out = rec.get("output") or {}
        decision = out.get("decision")
        conf = out.get("confidence") or "medium"
        first = entry.get("first_candidate") or {}
        if decision == "label_unknown" or not first.get("cell_type"):
            patches[cid] = {
                "label": "unknown", "confidence": "low", "status": "unknown",
                "arm_decision_source": "llm",
            }
            continue
        if decision == "label_downgraded":
            conf = "low"
        patches[cid] = {
            "label": first["cell_type"],
            "confidence": conf,
            "status": "decisive",
            "arm_decision_source": "llm",
        }
    return commit_label_patches(project_dir, patches)
