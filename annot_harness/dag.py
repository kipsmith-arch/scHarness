"""Domain-agnostic DAG for scripted (non-LLM) tool walks.

This module holds the data model and topological sort only. The walker lives
in ``annot_harness.scripted_driver``. Cell-annotation node lists, oracle tables, and
judges must not be imported here.

Retry cap (enforced by the walker AND by skill ``next_run_id``):
    first attempt ``#1`` + at most ``MAX_RETRIES`` further attempts ``#2``–``#6``.
    ``#7`` is never dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

MAX_ATTEMPTS = 6  # #1 + 5 retries
MAX_RETRIES = MAX_ATTEMPTS - 1


class DagError(Exception):
    """Invalid DAG (unknown dep, cycle, duplicate id)."""


@dataclass(frozen=True)
class DecisionAfter:
    """A decision point that fires after a node produces an exec record."""

    decision_point: str
    scope: str = "session"  # "session" | "cluster"
    # Callable(project_dir, history) -> list[str] of cluster ids. Ignored when
    # scope is "session". Lives on the DAG *instance*, not in this module.
    clusters_from: Optional[Callable] = None
    # Used when the walker synthesises cap_exhausted_proceed.
    accept_decision: Optional[str] = None
    # Decisions that mean "retry this node with new params".
    retry_on: tuple[str, ...] = ()


# Alias kept so DAG instances can attach the same hook before first dispatch
# (A-group: qc_threshold / resolution_select / de_method).
DecisionBefore = DecisionAfter


@dataclass
class Node:
    """One dispatchable unit (typically a skill tool / subcommand)."""

    id: str
    tool: str  # key in tool_runtime
    deps: list[str] = field(default_factory=list)
    decision_before: list[DecisionAfter] = field(default_factory=list)
    decision_after: list[DecisionAfter] = field(default_factory=list)
    # If set, an adjust-retry dispatches this tool instead of re-running ``tool``.
    retry_tool: Optional[str] = None
    skippable: bool = False
    # If skippable and any of these args are missing/empty after merge, skip.
    require_args: tuple[str, ...] = ()
    default_args: dict = field(default_factory=dict)
    # Atomic ops this node is known to emit (documentation / catalog count).
    ops: tuple[str, ...] = ()


def topological_order(nodes: Sequence[Node]) -> list[Node]:
    """Kahn topological sort. Raises DagError on duplicates, missing deps, or cycles."""
    by_id: dict[str, Node] = {}
    for node in nodes:
        if node.id in by_id:
            raise DagError(f"duplicate node id: {node.id!r}")
        by_id[node.id] = node
    incoming: dict[str, int] = {n.id: 0 for n in nodes}
    children: dict[str, list[str]] = {n.id: [] for n in nodes}
    for node in nodes:
        for dep in node.deps:
            if dep not in by_id:
                raise DagError(f"node {node.id!r} depends on unknown {dep!r}")
            incoming[node.id] += 1
            children[dep].append(node.id)
    queue = [nid for nid, n in incoming.items() if n == 0]
    # stable: original declaration order among ready nodes
    order_index = {n.id: i for i, n in enumerate(nodes)}
    queue.sort(key=lambda nid: order_index[nid])
    out: list[Node] = []
    while queue:
        nid = queue.pop(0)
        out.append(by_id[nid])
        nxt = sorted(children[nid], key=lambda i: order_index[i])
        for cid in nxt:
            incoming[cid] -= 1
            if incoming[cid] == 0:
                queue.append(cid)
        queue.sort(key=lambda i: order_index[i])
    if len(out) != len(nodes):
        leftover = [nid for nid, n in incoming.items() if n > 0]
        raise DagError(f"cycle or unreachable nodes: {leftover}")
    return out


def all_decision_points(nodes: Iterable[Node]) -> list[str]:
    """Unique decision_point names in declaration order (before then after)."""
    seen: set[str] = set()
    out: list[str] = []
    for node in nodes:
        for hook in list(node.decision_before) + list(node.decision_after):
            if hook.decision_point not in seen:
                seen.add(hook.decision_point)
                out.append(hook.decision_point)
    return out


def all_ops(nodes: Iterable[Node]) -> list[str]:
    """Flatten Node.ops in declaration order (duplicates kept if listed twice)."""
    out: list[str] = []
    for node in nodes:
        out.extend(node.ops)
    return out
