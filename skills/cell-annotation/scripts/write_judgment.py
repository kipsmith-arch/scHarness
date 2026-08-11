"""Write LLM-owned trajectory records to run_log.jsonl (trajectory_design.md §2, SKILL.md §7).

Subcommands:
    add           — one judgment record (decision-point留痕)
    session-start — one session_start record (dataset metadata)
    session-end   — one session_end record (final_summary)

Division of labor:
- ``exec`` records: pipeline scripts append automatically (common.exec_record)
- judgment / session records: the LLM appends via THIS tool

run_log.jsonl is skill-owned trajectory; the loop only provides the tool
execution channel (dispatcher subprocess). seq counter is shared with exec
records via common.append_log — no separate counter to keep in sync.

Validation (before append, so illegal values never enter the trajectory):
- add           : decision_point ∈ 13 points; decision ∈ that point's enum;
                  scope ∈ {session, cluster} (cluster_id required when cluster);
                  run_ref matches ``{step}.{op}#{attempt}``; inputs JSON array
                  of {path, value}; confidence ∈ {high, medium, low}; reasoning non-empty
- session-start : session_id non-empty; dataset JSON object
- session-end   : final_summary JSON object
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# decision 枚举词汇表(trajectory_design.md §3.2;与 validate_log.py 保持一致)
DECISION_ENUMS: dict[str, set[str]] = {
    "qc_threshold": {"threshold_set", "threshold_default"},
    "resolution_select": {"resolution_chosen"},
    "clustering_quality": {"clustering_accept", "clustering_adjust"},
    "batch_effect": {"batch_effect", "condition_specific", "well_mixed"},
    "de_method": {"wilcoxon", "pseudobulk_all", "pseudobulk_rare"},
    "marker_quality": {"markers_accept", "markers_adjust_filter", "markers_fail"},
    "kg_match": {"id_match_ok", "id_mismatch_gene_key", "id_mismatch_organ"},
    "candidate_gap": {"first_decisive", "ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true", "unknown"},
    "candidate_disambiguate": {"ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true"},
    "refine_effect": {"refine_effective", "refine_ineffective", "refine_skipped", "refine_autocorr_low"},
    "unknown_cluster": {"single_unknown_type", "multiple_unknown_types"},
    "label_confirm": {"label_confirmed", "label_downgraded", "label_unknown"},
    "global_quality": {"quality_good", "quality_acceptable", "quality_poor"},
}
VALID_SCOPES = {"session", "cluster"}
VALID_CONFIDENCE = {"high", "medium", "low"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+#[1-9][0-9]*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="write_judgment.py",
        description="追加 LLM 侧轨迹记录(judgment / session_start / session_end)到 run_log.jsonl(0 次 h5ad 加载)",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    p_add = sub.add_parser("add", help="校验并追加一条 judgment 记录")
    common.add_common_args(p_add)
    p_add.add_argument("--decision-point", required=True, help="13 个决策点之一(见 SKILL.md §3)")
    p_add.add_argument("--decision", required=True, help="该决策点的 decision 枚举值")
    p_add.add_argument("--scope-type", required=True, choices=sorted(VALID_SCOPES),
                       help="session 或 cluster")
    p_add.add_argument("--cluster-id", default=None, help="scope-type=cluster 时必填")
    p_add.add_argument("--run-ref", required=True, help="基于的 exec 记录 run_id,如 step1_prepare.leiden_cluster#1")
    p_add.add_argument("--inputs", required=True, help='JSON 数组字符串,如 [{"path": "...", "value": 0.15}]')
    p_add.add_argument("--confidence", required=True, choices=sorted(VALID_CONFIDENCE),
                       help="high / medium / low")
    p_add.add_argument("--action", required=True, help="后续动作指令(调哪个工具、带什么参数)")
    p_add.add_argument("--reasoning", required=True, help="自然语言推理链(非空)")

    p_ss = sub.add_parser("session-start", help="追加一条 session_start 记录(首次调用工具前)")
    common.add_common_args(p_ss)
    p_ss.add_argument("--session-id", required=True, help="会话标识,如 sess-20260811-SRP171040")
    p_ss.add_argument("--dataset", required=True, help="JSON 对象字符串:{id, h5ad_path, n_cells_raw, n_genes_raw, organism, organ, ...}")

    p_se = sub.add_parser("session-end", help="追加一条 session_end 记录(交付总结时)")
    common.add_common_args(p_se)
    p_se.add_argument("--final-summary", required=True,
                      help="JSON 对象字符串:{n_clusters, n_unknown, unknown_rate, n_unique_labels, run_count, judgment_count}")
    return parser


def _validate_add(args) -> list[str]:
    errors: list[str] = []
    if args.decision_point not in DECISION_ENUMS:
        errors.append(
            f"decision_point {args.decision_point!r} 非法,应为: {sorted(DECISION_ENUMS)}")
        return errors  # decision 校验依赖合法 decision_point
    if args.decision not in DECISION_ENUMS[args.decision_point]:
        errors.append(
            f"decision {args.decision!r} 不在 {args.decision_point} 枚举内: "
            f"{sorted(DECISION_ENUMS[args.decision_point])}")
    if args.scope_type == "cluster" and not args.cluster_id:
        errors.append("scope-type=cluster 时必须提供 --cluster-id")
    if not RUN_ID_RE.match(args.run_ref):
        errors.append(f"run_ref {args.run_ref!r} 格式非法,应为 {{step}}.{{op}}#{{attempt}}")
    try:
        inputs = json.loads(args.inputs)
        if not isinstance(inputs, list):
            errors.append("--inputs 应为 JSON 数组")
        else:
            for i, inp in enumerate(inputs):
                if not (isinstance(inp, dict) and "path" in inp and "value" in inp):
                    errors.append(f"--inputs[{i}] 缺 path/value")
    except json.JSONDecodeError as exc:
        errors.append(f"--inputs 不是合法 JSON: {exc}")
    if not args.reasoning.strip():
        errors.append("--reasoning 为空")
    return errors


def _validate_json_obj(raw: str, flag: str, errors: list[str]):
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            errors.append(f"{flag} 应为 JSON 对象")
    except json.JSONDecodeError as exc:
        errors.append(f"{flag} 不是合法 JSON: {exc}")


def op_add(args) -> dict:
    log_path = common.run_log_path(args.project_dir)
    scope = {"type": args.scope_type}
    if args.scope_type == "cluster":
        scope["cluster_id"] = args.cluster_id
    record = {
        "type": "judgment",
        "decision_point": args.decision_point,
        "scope": scope,
        "run_ref": args.run_ref,
        "inputs": json.loads(args.inputs),
        "output": {
            "decision": args.decision,
            "confidence": args.confidence,
            "action": args.action,
        },
        "reasoning": args.reasoning,
    }
    common.append_log(log_path, record)
    return {"seq": record["seq"], "decision_point": args.decision_point,
            "decision": args.decision, "run_ref": args.run_ref}


def op_session_start(args) -> dict:
    log_path = common.run_log_path(args.project_dir)
    record = {"type": "session_start", "session_id": args.session_id,
              "dataset": json.loads(args.dataset)}
    common.append_log(log_path, record)
    return {"seq": record["seq"], "session_id": args.session_id}


def op_session_end(args) -> dict:
    log_path = common.run_log_path(args.project_dir)
    record = {"type": "session_end", "final_summary": json.loads(args.final_summary)}
    common.append_log(log_path, record)
    return {"seq": record["seq"]}


SCHEMA = {
    "add": {
        "description": "追加一条 judgment 记录(决策留痕;校验 decision 枚举,非法值不写入)",
        "args": [
            ("project_dir", "string", True, "output", "项目目录(含 run_log.jsonl)"),
            ("decision_point", "string", True, None, "13 个决策点之一"),
            ("decision", "string", True, None, "该决策点的 decision 枚举值"),
            ("scope_type", "string", True, None, "session 或 cluster"),
            ("cluster_id", "string", False, None, "scope-type=cluster 时必填"),
            ("run_ref", "string", True, None, "基于的 exec 记录 run_id"),
            ("inputs", "string", True, None, "JSON 数组 [{path, value}]"),
            ("confidence", "string", True, None, "high / medium / low"),
            ("action", "string", True, None, "后续动作指令"),
            ("reasoning", "string", True, None, "自然语言推理链"),
        ],
    },
    "session-start": {
        "description": "追加一条 session_start 记录(会话开始,含数据集元数据)",
        "args": [
            ("project_dir", "string", True, "output", "项目目录(含 run_log.jsonl)"),
            ("session_id", "string", True, None, "会话标识,如 sess-20260811-SRP171040"),
            ("dataset", "string", True, None, "JSON 对象:{id, h5ad_path, n_cells_raw, n_genes_raw, organism, organ, ...}"),
        ],
    },
    "session-end": {
        "description": "追加一条 session_end 记录(会话结束,含 final_summary)",
        "args": [
            ("project_dir", "string", True, "output", "项目目录(含 run_log.jsonl)"),
            ("final_summary", "string", True, None, "JSON 对象:{n_clusters, n_unknown, unknown_rate, n_unique_labels, run_count, judgment_count}"),
        ],
    },
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.dump_schema:
        tools = []
        for sub, spec in SCHEMA.items():
            tools.append({
                "subcommand": sub,
                "description": spec["description"],
                "args": [{"name": n, "type": t, "required": r, "default": d, "help": h}
                         for n, t, r, d, h in spec["args"]],
            })
        print(json.dumps({"tools": tools}, ensure_ascii=False))
        return 0
    if not args.subcommand:
        parser.print_help()
        return 1

    if args.subcommand == "add":
        errors = _validate_add(args)
    else:
        errors: list[str] = []
        if args.subcommand == "session-start":
            if not args.session_id.strip():
                errors.append("--session-id 为空")
            _validate_json_obj(args.dataset, "--dataset", errors)
        elif args.subcommand == "session-end":
            _validate_json_obj(args.final_summary, "--final-summary", errors)

    if errors:
        print(json.dumps({"status": "error", "error": "; ".join(errors)}, ensure_ascii=False))
        return 1

    data = {"add": op_add, "session-start": op_session_start,
            "session-end": op_session_end}[args.subcommand](args)
    print(json.dumps({"status": "ok", "data": data}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
