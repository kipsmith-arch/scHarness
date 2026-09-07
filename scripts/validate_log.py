"""L-4 日志校验:校验 run_log.jsonl 合规性(轨迹完整性)。

依据 design/trajectory_design.md §2(记录格式)/ §3.2(decision 枚举):
- 公共字段:ts(ISO 8601)、seq(int,文件内唯一从 1 单调递增)、type
- type ∈ {session_start, exec, judgment, session_end}
- exec:run_id 格式 ``{step}.{op}#{attempt}`` + parameters + metrics
- judgment:decision_point(14 枚举之一)、scope、run_ref、inputs[]、output、reasoning
- 记录级别:error(exit 1)/ warning(exit 0 但报告)

校验形态(mode):
- e2e : 完整轨迹校验——session_start/session_end/exec 必须存在,judgment run_ref 必须指向已出现的 exec,cluster 级决策点必须逐簇留痕(完整 pipeline 跑测产物,E-1 用)
- mini: 单决策点 mini-session——不要求 session_start/end/exec;只校验 judgment 的决策枚举/scope 粒度/字段完整性;调过 pipeline 工具(有 exec)但缺会话边界只给 warning(E-2~E-4 用)
- auto: 按内容推断——存在 session_start 记录 → e2e;否则 → mini(默认)

E-5 日志合规用例的判据:本脚本对跑测产物 run_log.jsonl 退出码 0。
本脚本是 P5 测试循环工具,不在 harness / skill 包内。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "cell-annotation", "scripts"))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from trajectory_schema import REQUIRED_SCOPE  # noqa: E402

# trajectory_design.md §3.2 decision 枚举词汇表
DECISION_ENUMS: dict[str, set[str]] = {
    "qc_threshold": {"threshold_set", "threshold_default"},
    "resolution_select": {"resolution_chosen"},
    "clustering_quality": {"clustering_accept", "clustering_adjust"},
    "batch_effect": {"batch_effect", "condition_specific", "well_mixed"},
    "de_method": {"wilcoxon", "pseudobulk_all", "pseudobulk_rare"},
    "marker_quality": {"markers_accept", "markers_adjust_filter", "markers_fail"},
    "kg_match": {"id_match_ok", "id_mismatch_gene_key", "id_mismatch_organ"},
    "cross_species_routing": {
        "routing_accept", "routing_force_single", "routing_force_cross", "routing_multi_reference",
    },
    "candidate_gap": {"first_decisive", "ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true", "unknown"},
    "candidate_disambiguate": {"ambiguous_parent_child", "ambiguous_synonym", "ambiguous_true"},
    "refine_effect": {"refine_effective", "refine_ineffective", "refine_skipped", "refine_autocorr_low"},
    "unknown_cluster": {"single_unknown_type", "multiple_unknown_types"},
    "label_confirm": {"label_confirmed", "label_downgraded", "label_unknown"},
    "global_quality": {"quality_good", "quality_acceptable", "quality_poor"},
}
VALID_TYPES = {"session_start", "exec", "judgment", "session_end"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+#[1-9][0-9]*$")

# 决策点→scope 类型由 skills/cell-annotation/scripts/trajectory_schema.py 提供。
# 10 session 级 + 4 cluster 级;粒度违规报 ERROR。

ERROR, WARNING = "error", "warning"


def validate(path: str, mode: str = "auto") -> tuple[list[dict], int]:
    """Return (issues, exit_code).

    mode: "auto"(按记录推断 e2e/mini)| "e2e" | "mini"。
    """
    issues: list[dict] = []
    n_records = 0
    seen_types = set()
    exec_run_ids = set()
    prev_seq = 0
    # 循环内暂存 run_ref 悬空的 judgment(行号, run_ref),形态判定后统一报。
    orphan_run_refs: list[tuple[int, str]] = []
    # 从 step4_rank.rank_candidates 记录的 metrics.n_clusters 跟踪最新簇数,
    # 供 cluster_id 范围与覆盖度检查使用。
    n_clusters_latest: int | None = None
    n_clusters_seq: int = 0
    # 各决策点已出现的 cluster_id 集合(仅在 cluster 级 dp 下记录),用于覆盖度 warning。
    coverage: dict[str, set[str]] = {}

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
                # 跟踪最新 n_clusters(取最近一次 step4_rank.rank_candidates 的 seq)。
                if isinstance(run_id, str) and run_id.startswith("step4_rank.rank_candidates#"):
                    n = (rec.get("metrics") or {}).get("n_clusters")
                    if isinstance(n, int) and n >= 0 and seq > n_clusters_seq:
                        n_clusters_latest = n
                        n_clusters_seq = seq
            elif rtype == "judgment":
                dp = rec.get("decision_point")
                if dp not in DECISION_ENUMS:
                    add(ERROR, lineno, f"decision_point 非法: {dp!r}(14 枚举之一)")
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
                # 粒度强制(REQUIRED_SCOPE,与 write_judgment.py 同源):session 级拒绝 cluster,反之亦然。
                if dp in REQUIRED_SCOPE and isinstance(scope, dict):
                    required_type = REQUIRED_SCOPE[dp]
                    actual_type = scope.get("type")
                    if actual_type != required_type:
                        add(ERROR, lineno,
                             f"decision_point {dp!r} 粒度违规:要求 scope-type={required_type!r}(trajectory_design §3.1),实际 {actual_type!r}")
                    # cluster 级决策点:cluster_id 必须存在且是 string;跳数字范围检查交给后续 n_clusters 提供时。
                    if required_type == "cluster":
                        cid = scope.get("cluster_id")
                        if not isinstance(cid, str) or not cid:
                            add(ERROR, lineno, f"{dp!r} scope-type=cluster 缺 cluster_id")
                        elif n_clusters_latest is not None:
                            # 数字簇 id 校验范围;非数字跳过(部分数据集使用字符串簇 id)
                            if cid.isdigit() and not (0 <= int(cid) < n_clusters_latest):
                                add(ERROR, lineno,
                                     f"{dp!r} cluster_id {cid!r} 越界(step4_rank.rank_candidates n_clusters={n_clusters_latest})")
                        if isinstance(cid, str) and cid:
                            coverage.setdefault(dp, set()).add(cid)
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
                    orphan_run_refs.append((lineno, run_ref))
            elif rtype == "session_start":
                if "session_id" not in rec:
                    add(ERROR, lineno, "session_start 缺 session_id")
            elif rtype == "session_end":
                if "final_summary" not in rec:
                    add(WARNING, lineno, "session_end 缺 final_summary")

    if n_records == 0:
        return [{"level": ERROR, "line": 0, "msg": "run_log 无任何记录"}], 1

    # 形态判定:auto 按内容推断。判据 = 是否存在 session_start:
    # - 有 session_start → 完整会话(e2e):要求 session_start/end/exec 齐备、run_ref 指向 exec。
    # - 无 session_start → 单决策点会话(mini):不要求完整性;即使调过 pipeline 工具(有 exec)
    #   也算 mini(如 E-4 调 step1 取 QC 数据后做一次判断),只对"有 exec 却无 start/end"给 warning。
    effective = mode if mode in ("e2e", "mini") else ("e2e" if "session_start" in seen_types else "mini")

    if effective == "e2e":
        for required in ("session_start", "session_end"):
            if required not in seen_types:
                add(ERROR, 0, f"缺少 {required} 记录")
        if "exec" not in seen_types:
            add(ERROR, 0, "缺少 exec 记录")
        for lineno, rr in orphan_run_refs:
            add(WARNING, lineno, f"judgment run_ref {rr!r} 未指向已出现的 exec run_id")
    else:  # mini
        # 单决策点会话不要求完整性,但若实际调过 pipeline 工具却无会话边界,提示留痕不全。
        if "exec" in seen_types and "session_start" not in seen_types:
            add(WARNING, 0, "mini 会话含 exec 记录但缺 session_start(如非单决策点会话,请补会话边界或显式 --mode e2e)")

    # 簇覆盖度检查(candidate_gap / label_confirm 应覆盖所有 n_clusters 簇);
    # 只检查数字 cluster_id;字符串簇名(如 "A","B")不参与覆盖度计算。
    # 仅 e2e 形态执行:mini 无 exec 记录,无从获取 n_clusters_latest。
    if effective == "e2e" and n_clusters_latest is not None and n_clusters_latest > 0:
        for dp in ("candidate_gap", "label_confirm"):
            cids = coverage.get(dp, set())
            numeric_cids = {c for c in cids if c.isdigit()}
            if numeric_cids and len(numeric_cids) < n_clusters_latest:
                add(WARNING, 0,
                     f"{dp} 覆盖度不足:distinct numeric cluster_id={len(numeric_cids)}/{n_clusters_latest}(可能为晅缩或重跑补全)")

    n_errors = sum(1 for i in issues if i["level"] == ERROR)
    return issues, 1 if n_errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="L-4 校验 run_log.jsonl 合规性")
    ap.add_argument("run_log", help="run_log.jsonl 路径(或 --project-dir 下的该文件)")
    ap.add_argument("--project-dir", default=None, help="若给出,run_log 参数视为 project-dir 内的文件名")
    ap.add_argument("--mode", choices=("auto", "e2e", "mini"), default="auto",
                    help="校验形态:auto=按记录推断(e2e=有 exec / mini=仅 judgment);e2e=完整轨迹校验;mini=单决策点会话(只验 judgment)")
    args = ap.parse_args()

    path = args.run_log
    if args.project_dir:
        path = os.path.join(args.project_dir, path if path != args.run_log else "run_log.jsonl")
    issues, code = validate(path, mode=args.mode)

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
