"""L-4 日志校验:校验 run_log.jsonl 合规性(轨迹完整性)。

依据 design/trajectory_design.md §2(记录格式)/ §3.2(decision 枚举):
- 公共字段:ts(ISO 8601)、seq(int,文件内唯一从 1 单调递增)、type
- type ∈ {session_start, exec, judgment, session_end}
- exec:run_id 格式 ``{step}.{op}#{attempt}`` + parameters + metrics
- judgment:decision_point(13 枚举之一)、scope、run_ref、inputs[]、output、reasoning
- 记录级别:error(exit 1)/ warning(exit 0 但报告)

E-5 日志合规用例的判据:本脚本对跑测产物 run_log.jsonl 退出码 0。
本脚本是 P5 测试循环工具,不在 harness / skill 包内。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# trajectory_design.md §3.2 decision 枚举词汇表
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
VALID_TYPES = {"session_start", "exec", "judgment", "session_end"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+#[1-9][0-9]*$")

ERROR, WARNING = "error", "warning"


def validate(path: str) -> tuple[list[dict], int]:
    """Return (issues, exit_code)."""
    issues: list[dict] = []
    n_records = 0
    seen_types = set()
    exec_run_ids = set()
    prev_seq = 0

    def add(level: str, lineno: int, msg: str) -> None:
        issues.append({"level": level, "line": lineno, "msg": msg})

    if not os.path.exists(path):
        return [{"level": ERROR, "line": 0, "msg": f"run_log 不存在: {path}"}], 1
    if os.path.getsize(path) == 0:
        return [{"level": ERROR, "line": 0, "msg": "run_log 为空文件"}], 1

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                add(ERROR, lineno, f"坏 JSON 行: {exc}")
                continue
            n_records += 1

            # 公共字段
            if not isinstance(rec, dict):
                add(ERROR, lineno, "记录不是 JSON 对象")
                continue
            if "ts" not in rec or not isinstance(rec.get("ts"), str) or not rec["ts"]:
                add(ERROR, lineno, "缺 ts(ISO 时间戳)")
            seq = rec.get("seq")
            if not isinstance(seq, int) or seq < 1:
                add(ERROR, lineno, f"seq 非法: {seq!r}(应为 >=1 的整数)")
            elif seq != prev_seq + 1:
                add(ERROR, lineno, f"seq 不连续: 期望 {prev_seq + 1},实际 {seq}")
                prev_seq = seq
            else:
                prev_seq = seq
            rtype = rec.get("type")
            if rtype not in VALID_TYPES:
                add(ERROR, lineno, f"type 非法: {rtype!r}(合法: {sorted(VALID_TYPES)})")
                continue
            seen_types.add(rtype)

            # 类型专属校验
            if rtype == "exec":
                run_id = rec.get("run_id")
                if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
                    add(ERROR, lineno, f"exec run_id 格式非法: {run_id!r}(应为 step.op#attempt)")
                else:
                    exec_run_ids.add(run_id)
                for field in ("parameters", "metrics"):
                    if not isinstance(rec.get(field), dict):
                        add(ERROR, lineno, f"exec 缺 {field}(object)")
            elif rtype == "judgment":
                dp = rec.get("decision_point")
                if dp not in DECISION_ENUMS:
                    add(ERROR, lineno, f"decision_point 非法: {dp!r}(13 枚举之一)")
                else:
                    decision = (rec.get("output") or {}).get("decision")
                    if decision not in DECISION_ENUMS[dp]:
                        add(ERROR, lineno,
                             f"output.decision {decision!r} 不在 {dp} 枚举内: {sorted(DECISION_ENUMS[dp])}")
                for field in ("scope", "run_ref", "inputs", "output", "reasoning"):
                    if field not in rec:
                        add(ERROR, lineno, f"judgment 缺 {field}")
                scope = rec.get("scope")
                if isinstance(scope, dict) and scope.get("type") not in ("session", "cluster"):
                    add(ERROR, lineno, f"scope.type 非法: {scope.get('type')!r}(session/cluster)")
                if not isinstance(rec.get("inputs"), list):
                    add(ERROR, lineno, "judgment inputs 应为数组")
                else:
                    for i, inp in enumerate(rec["inputs"]):
                        if not (isinstance(inp, dict) and "path" in inp and "value" in inp):
                            add(WARNING, lineno, f"judgment inputs[{i}] 缺 path/value")
                output = rec.get("output")
                if isinstance(output, dict):
                    for field in ("confidence", "action"):
                        if field not in output:
                            add(WARNING, lineno, f"judgment output 缺 {field}")
                elif "output" in rec:
                    add(ERROR, lineno, "judgment output 应为对象")
                reasoning = rec.get("reasoning")
                if not isinstance(reasoning, str) or not reasoning.strip():
                    add(ERROR, lineno, "judgment reasoning 为空")
                run_ref = rec.get("run_ref")
                if isinstance(run_ref, str) and run_ref not in exec_run_ids:
                    add(WARNING, lineno, f"judgment run_ref {run_ref!r} 未指向已出现的 exec run_id")
            elif rtype == "session_start":
                if "session_id" not in rec:
                    add(ERROR, lineno, "session_start 缺 session_id")
            elif rtype == "session_end":
                if "final_summary" not in rec:
                    add(WARNING, lineno, "session_end 缺 final_summary")

    if n_records == 0:
        return [{"level": ERROR, "line": 0, "msg": "run_log 无任何记录"}], 1
    for required in ("session_start", "session_end"):
        if required not in seen_types:
            add(ERROR, 0, f"缺少 {required} 记录")
    if not any(t == "exec" for t in seen_types):
        add(ERROR, 0, "缺少 exec 记录")

    n_errors = sum(1 for i in issues if i["level"] == ERROR)
    return issues, 1 if n_errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="L-4 校验 run_log.jsonl 合规性")
    ap.add_argument("run_log", help="run_log.jsonl 路径(或 --project-dir 下的该文件)")
    ap.add_argument("--project-dir", default=None, help="若给出,run_log 参数视为 project-dir 内的文件名")
    args = ap.parse_args()

    path = args.run_log
    if args.project_dir:
        path = os.path.join(args.project_dir, path if path != args.run_log else "run_log.jsonl")
    issues, code = validate(path)

    print(f"[validate_log] {path}: {code == 0 and 'PASS' or 'FAIL'}")
    if issues:
        print(f"[validate_log] 问题 {len(issues)} 条(error={sum(1 for i in issues if i['level'] == ERROR)}, "
              f"warning={sum(1 for i in issues if i['level'] == WARNING)}):")
        for i in issues:
            loc = f"L{i['line']}" if i["line"] else "全局"
            print(f"  [{i['level']}] {loc}: {i['msg']}")
    else:
        print("[validate_log] 无问题")
    return code


if __name__ == "__main__":
    sys.exit(main())
