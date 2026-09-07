"""Deterministic DAG walker for non-LLM arms.

Shares ``dispatcher.dispatch`` with the LLM loop. The walker is domain-agnostic:
it understands nodes, deps, decision hooks, proceed/retry/skip, and the attempt
cap. Judges and cell-annotation DAG instances live outside this package.

Judge protocol (duck-typed)::

    decide(dp, exec_record, history, *, scope=None, project_dir=None) -> dict
        Required: decision, reasoning
        Optional: confidence, action, inputs,
                  driver={kind, params, skip_nodes, node_args, label_patch}
        kind: proceed | retry | skip | cap_exhausted_proceed
    commit_labels(project_dir, history) -> None
        Materialise already-decided label/confidence/status onto artifacts.

``output.action`` written to the log is the trajectory string (⊆ §3.2 space);
``driver`` is runtime-only and is stripped before the record is appended.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .dag import MAX_ATTEMPTS, DagError, DecisionAfter, Node, topological_order
from .dispatcher import dispatch as _dispatch

DispatchFn = Callable[[dict, dict, dict], dict]


class ScriptedRunError(Exception):
    """Session aborted (no successful exec for a required node, or dispatch error)."""

    def __init__(self, message: str, result: Optional[dict] = None):
        super().__init__(message)
        self.result = result or {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_record(log_path: str, record: dict) -> dict:
    """Append one NDJSON record, filling ts/seq. Does not import skill common."""
    record = dict(record)
    record.setdefault("ts", _now_iso())
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            record["seq"] = sum(1 for line in f if line.strip()) + 1
    else:
        record["seq"] = 1
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _load_log(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _latest_exec(log_path: str, min_seq: int = 0, ok_only: bool = False) -> Optional[dict]:
    best = None
    for rec in _load_log(log_path):
        if rec.get("type") != "exec":
            continue
        if rec.get("seq", 0) <= min_seq:
            continue
        if ok_only and rec.get("status") not in (None, "ok"):
            continue
        if best is None or rec.get("seq", 0) > best.get("seq", 0):
            best = rec
    return best


def _has_ok_exec_since(log_path: str, min_seq: int) -> bool:
    for rec in _load_log(log_path):
        if rec.get("type") != "exec":
            continue
        if rec.get("seq", 0) <= min_seq:
            continue
        if rec.get("status") in (None, "ok"):
            return True
    return False


def _empty_args(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def _merge_args(*parts: dict) -> dict:
    out: dict = {}
    for part in parts:
        if not part:
            continue
        for k, v in part.items():
            if k == "clusters" and k in out:
                if _empty_args(v):
                    continue
                out[k] = _merge_clusters(out[k], v)
            else:
                out[k] = v
    return out


def _merge_clusters(a: Any, b: Any) -> str:
    def _as_list(x: Any) -> list[str]:
        if x is None:
            return []
        if isinstance(x, (list, tuple)):
            return [str(i) for i in x]
        return [p for p in str(x).split(",") if p]
    seen = []
    for item in _as_list(a) + _as_list(b):
        if item not in seen:
            seen.append(item)
    return ",".join(seen)


def _judgment_record(dp: str, decision: str, exec_record: Optional[dict],
                     reasoning: str, *, scope: Optional[dict] = None,
                     confidence: str = "high", action: str = "proceed",
                     inputs: Optional[list] = None) -> dict:
    run_ref = (exec_record or {}).get("run_id") or "unknown.pending#1"
    return {
        "type": "judgment",
        "decision_point": dp,
        "scope": scope or {"type": "session"},
        "run_ref": run_ref,
        "inputs": inputs if inputs is not None else [],
        "output": {
            "decision": decision,
            "confidence": confidence,
            "action": action,
        },
        "reasoning": reasoning,
    }


def _call_judge(judge, dp, exec_record, history, *, scope=None, project_dir=None) -> dict:
    decide = getattr(judge, "decide", None)
    if decide is None:
        raise TypeError("judge must implement decide(dp, exec_record, history, **kwargs)")
    return decide(dp, exec_record, history, scope=scope, project_dir=project_dir) or {}


def _driver_from_judgment(raw: dict) -> dict:
    driver = dict(raw.get("driver") or {})
    kind = driver.get("kind")
    decision = str(raw.get("decision") or (raw.get("output") or {}).get("decision") or "")
    action = str(raw.get("action") or (raw.get("output") or {}).get("action") or driver.get("kind") or "proceed")
    if not kind:
        if action == "cap_exhausted_proceed" or action.startswith("cap_exhausted"):
            kind = "cap_exhausted_proceed"
        elif action == "retry" or action.startswith("retry:") or decision.endswith("_adjust"):
            kind = "retry"
        elif action.startswith("skip:"):
            kind = "skip"
            driver.setdefault("skip_nodes", [action.split(":", 1)[1]])
        else:
            kind = "proceed"
    driver["kind"] = kind
    driver.setdefault("params", raw.get("params") or {})
    driver.setdefault("skip_nodes", [])
    driver.setdefault("node_args", {})
    return driver


def _write_judgment_record(log_path: str, raw: dict, dp: str,
                           exec_record: Optional[dict], scope: Optional[dict]) -> dict:
    output = raw.get("output") or {}
    decision = raw.get("decision") or output.get("decision")
    reasoning = raw.get("reasoning") or ""
    confidence = raw.get("confidence") or output.get("confidence") or "high"
    action = raw.get("action") or output.get("action") or "proceed"
    inputs = raw.get("inputs") if "inputs" in raw else output.get("inputs")
    rec = _judgment_record(
        dp, decision, exec_record, reasoning,
        scope=raw.get("scope") or scope,
        confidence=confidence, action=action, inputs=inputs,
    )
    return _append_record(log_path, rec)


def run_scripted(
    dag,
    judge,
    tool_runtime: dict,
    project_dir: str,
    *,
    base_args: Optional[dict] = None,
    session_id: Optional[str] = None,
    dataset: Optional[dict] = None,
    dispatch_fn: Optional[DispatchFn] = None,
    log_path: Optional[str] = None,
    state: Optional[dict] = None,
) -> dict:
    """Walk ``dag`` in topological order, calling ``judge`` at each decision hook.

    ``dag`` is a sequence of ``Node``. ``tool_runtime`` maps tool names to
    dispatcher specs (same object the loop uses). ``dispatch_fn`` defaults to
    ``dispatcher.dispatch``; tests inject a fake.
    """
    nodes = list(dag)
    order = topological_order(nodes)
    os.makedirs(project_dir, exist_ok=True)
    log_path = log_path or os.path.join(project_dir, "run_log.jsonl")
    if not os.path.exists(log_path):
        open(log_path, "w", encoding="utf-8").close()

    dispatch_fn = dispatch_fn or _dispatch
    state = dict(state or {})
    state.setdefault("project_dir", project_dir)
    base_args = dict(base_args or {})
    base_args.setdefault("project_dir", project_dir)

    session_id = session_id or f"sess-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    _append_record(log_path, {
        "type": "session_start",
        "session_id": session_id,
        "dataset": dataset or {"id": os.path.basename(project_dir)},
    })

    history: dict[str, Any] = {"_project_dir": project_dir, "_log_path": log_path}
    node_args: dict[str, dict] = {}
    skipped: set[str] = set()
    n_dispatch: dict[str, int] = {n.id: 0 for n in nodes}
    last_ok: dict[str, dict] = {}
    ran: list[str] = []
    judgments: list[dict] = []

    def _do_dispatch(node: Node, tool: str, args: dict) -> dict:
        if tool not in tool_runtime:
            raise ScriptedRunError(f"tool {tool!r} not in tool_runtime (node {node.id})")
        spec = dict(tool_runtime[tool])
        spec["_node_id"] = node.id
        n_dispatch[node.id] += 1
        return dispatch_fn(spec, args, state)

    def _fire_hooks(node: Node, hooks: list[DecisionAfter], exec_record: Optional[dict],
                    *, allow_retry: bool) -> dict:
        """Return {kind, params, skip_nodes, node_args} aggregated across hooks."""
        agg = {"kind": "proceed", "params": {}, "skip_nodes": [], "node_args": {}}
        for hook in hooks:
            scopes: list[Optional[dict]]
            if hook.scope == "cluster":
                getter = hook.clusters_from
                cids = list(getter(project_dir, history) if getter else [])
                if not cids:
                    continue
                scopes = [{"type": "cluster", "cluster_id": str(c)} for c in cids]
            else:
                scopes = [{"type": "session"}]
            for scope in scopes:
                raw = _call_judge(
                    judge, hook.decision_point, exec_record, history,
                    scope=scope, project_dir=project_dir,
                )
                driver = _driver_from_judgment(raw)
                rec = _write_judgment_record(
                    log_path, raw, hook.decision_point, exec_record, scope,
                )
                judgments.append(rec)
                if driver.get("skip_nodes"):
                    agg["skip_nodes"].extend(driver["skip_nodes"])
                if driver.get("node_args"):
                    for nid, extra in driver["node_args"].items():
                        agg["node_args"][nid] = _merge_args(
                            agg["node_args"].get(nid, {}), extra,
                        )
                if driver.get("params"):
                    agg["params"] = _merge_args(agg["params"], driver["params"])
                if driver["kind"] == "retry" and allow_retry:
                    agg["kind"] = "retry"
                elif driver["kind"] == "cap_exhausted_proceed":
                    agg["kind"] = "cap_exhausted_proceed"
                elif driver["kind"] == "skip":
                    agg["kind"] = "skip"
        return agg

    def _cap_proceed(node: Node, hook: Optional[DecisionAfter], exec_record: Optional[dict]) -> None:
        if hook is None or not hook.accept_decision:
            raise ScriptedRunError(
                f"node {node.id}: cap_exhausted_proceed requires DecisionAfter.accept_decision"
            )
        rec = _append_record(log_path, _judgment_record(
            hook.decision_point, hook.accept_decision, exec_record,
            f"cap_exhausted_proceed: {n_dispatch[node.id]} dispatches, "
            "gate unmet, continuing with last successful measurement",
            action="cap_exhausted_proceed",
        ))
        judgments.append(rec)

    try:
        for node in order:
            if node.id in skipped:
                continue
            merged = _merge_args(base_args, node.default_args, node_args.get(node.id, {}))
            if node.skippable and node.require_args:
                if any(_empty_args(merged.get(k)) for k in node.require_args):
                    skipped.add(node.id)
                    continue

            # A-group: judge before the first dispatch of this node
            if node.decision_before:
                prev = last_ok.get(node.id) or (history.get(node.deps[-1]) if node.deps else None)
                if isinstance(prev, dict) and prev.get("type") != "exec":
                    prev = None
                before = _fire_hooks(node, node.decision_before, prev, allow_retry=False)
                skipped.update(before["skip_nodes"])
                for nid, extra in before["node_args"].items():
                    node_args[nid] = _merge_args(node_args.get(nid, {}), extra)
                if before["params"]:
                    merged = _merge_args(merged, before["params"])
                    node_args[node.id] = _merge_args(node_args.get(node.id, {}), before["params"])

            retry_params: dict = {}
            node_ok = False
            last_exec: Optional[dict] = None
            while True:
                if n_dispatch[node.id] >= MAX_ATTEMPTS:
                    if node_ok or node.id in last_ok:
                        retry_hooks = [h for h in node.decision_after if h.retry_on]
                        _cap_proceed(node, retry_hooks[0] if retry_hooks else None,
                                     last_ok.get(node.id) or last_exec)
                        break
                    raise ScriptedRunError(
                        f"node {node.id}: no successful exec and attempt cap reached",
                    )
                seq_before = 0
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8") as f:
                        seq_before = sum(1 for line in f if line.strip())
                args = _merge_args(merged, retry_params)
                tool = node.retry_tool if (n_dispatch[node.id] > 0 and node.retry_tool) else node.tool
                result = _do_dispatch(node, tool, args)
                status = (result or {}).get("status", "error")
                if status != "ok":
                    err = (result or {}).get("error", "")
                    tail = (result or {}).get("stderr_tail") or (result or {}).get("stdout_tail") or ""
                    print(f"[driver] {node.id} {tool} failed: {err}", file=sys.stderr)
                    if tail:
                        print(str(tail)[-1500:], file=sys.stderr)
                last_exec = _latest_exec(log_path, min_seq=seq_before)
                if last_exec is not None:
                    history[node.id] = last_exec
                if status == "ok":
                    node_ok = True
                    if last_exec is not None:
                        last_ok[node.id] = last_exec
                else:
                    last_exec = last_ok.get(node.id) or last_exec

                after_hooks = node.decision_after
                if n_dispatch[node.id] > 1:
                    after_hooks = [h for h in after_hooks if h.retry_on]
                after = _fire_hooks(
                    node, after_hooks,
                    last_ok.get(node.id) or last_exec,
                    allow_retry=True,
                )
                skipped.update(after["skip_nodes"])
                for nid, extra in after["node_args"].items():
                    node_args[nid] = _merge_args(node_args.get(nid, {}), extra)
                if after["kind"] == "retry":
                    if n_dispatch[node.id] >= MAX_ATTEMPTS:
                        if node_ok or node.id in last_ok:
                            retry_hooks = [h for h in node.decision_after if h.retry_on]
                            _cap_proceed(node, retry_hooks[0] if retry_hooks else None,
                                         last_ok.get(node.id) or last_exec)
                            break
                        raise ScriptedRunError(
                            f"node {node.id}: no successful exec and attempt cap reached",
                        )
                    retry_params = _merge_args(retry_params, after.get("params") or {})
                    continue
                break

            if not node_ok and node.id not in last_ok:
                raise ScriptedRunError(
                    f"node {node.id}: no successful exec",
                )
            ran.append(node.id)

        commit = getattr(judge, "commit_labels", None)
        if commit is not None:
            commit(project_dir, history)

        summary = {
            "ok": True,
            "n_nodes_run": len(ran),
            "nodes_run": ran,
            "nodes_skipped": sorted(skipped),
            "n_dispatch": dict(n_dispatch),
            "judgment_count": len(judgments),
        }
        _append_record(log_path, {"type": "session_end", "final_summary": summary})
        summary["session_id"] = session_id
        summary["log_path"] = log_path
        return summary

    except ScriptedRunError as exc:
        err_summary = {
            "ok": False,
            "error": str(exc),
            "n_nodes_run": len(ran),
            "nodes_run": ran,
            "nodes_skipped": sorted(skipped),
            "n_dispatch": dict(n_dispatch),
            "judgment_count": len(judgments),
        }
        _append_record(log_path, {"type": "session_end", "final_summary": err_summary})
        exc.result = err_summary
        raise
    except Exception as exc:
        err_summary = {
            "ok": False,
            "error": str(exc),
            "n_nodes_run": len(ran),
            "nodes_run": ran,
            "nodes_skipped": sorted(skipped),
            "n_dispatch": dict(n_dispatch),
            "judgment_count": len(judgments),
        }
        _append_record(log_path, {"type": "session_end", "final_summary": err_summary})
        wrapped = ScriptedRunError(str(exc), err_summary)
        raise wrapped from exc
