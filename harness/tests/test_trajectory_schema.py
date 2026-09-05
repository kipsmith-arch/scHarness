"""REQUIRED_SCOPE 共享规则回归测试(P5-r2 deferred 项收尾)。

背景:write_judgment.py(skill 包内)与 validate_log.py(根 scripts/ 评测工具)
原先各持一份 REQUIRED_SCOPE 同源拷贝。本轮抽取为共享模块
skills/cell-annotation/scripts/trajectory_schema.py,两个调用方均导入它。

本测试验证:
- 共享表完整性:13 个决策点,9 session 级 + 4 cluster 级
- write_judgment 与 validate_log 都从同一模块导入(同一对象,杜绝漂移)
- write_judgment._validate_add 对粒度违规拒写入
- validate_log.validate 对粒度违规报 ERROR(exit 1)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_SCRIPTS = PROJECT_ROOT / "skills" / "cell-annotation" / "scripts"
ROOT_SCRIPTS = PROJECT_ROOT / "scripts"


def _load_module(name: str, path: Path):
    """Load a standalone script as a module without requiring it to be a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 三个模块以同一 name 加载,保证内部 ``from trajectory_schema import ...``
# 命中同一 sys.modules 条目,从而断言两个调用方引用的是同一个对象。
_SCHEMA = _load_module("trajectory_schema", SKILL_SCRIPTS / "trajectory_schema.py")
_WRITER = _load_module("write_judgment", SKILL_SCRIPTS / "write_judgment.py")
_VALIDATOR = _load_module("validate_log", ROOT_SCRIPTS / "validate_log.py")


def test_scope_map_shape():
    assert len(_SCHEMA.REQUIRED_SCOPE) == 13
    assert sum(v == "session" for v in _SCHEMA.REQUIRED_SCOPE.values()) == 9
    assert sum(v == "cluster" for v in _SCHEMA.REQUIRED_SCOPE.values()) == 4


def test_both_consumers_share_same_object():
    """两个调用方导入的 REQUIRED_SCOPE 与共享模块内容一致(防漂移)。"""
    assert _WRITER.REQUIRED_SCOPE == _SCHEMA.REQUIRED_SCOPE
    assert _VALIDATOR.REQUIRED_SCOPE == _SCHEMA.REQUIRED_SCOPE


def test_writer_rejects_scope_mismatch():
    """cluster 级决策点配 session scope 必须被写入校验拒绝。"""
    args = type(
        "Args",
        (),
        {
            "decision_point": "candidate_gap",
            "decision": "unknown",
            "scope_type": "session",
            "cluster_id": None,
            "run_ref": "step4_rank.rank_candidates#1",
            "inputs": "[]",
            "reasoning": "reason",
        },
    )()
    errors = _WRITER._validate_add(args)
    assert any("scope-type='cluster'" in e for e in errors)


def test_validator_reports_scope_mismatch(tmp_path):
    """session 级决策点配 cluster scope 必须被日志校验报 ERROR。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "session_start", "session_id": "sess-t"},
        {"ts": "2026-08-11T00:00:01Z", "seq": 2, "type": "exec",
         "run_id": "step4_rank.rank_candidates#1", "parameters": {}, "metrics": {"n_clusters": 5}},
        {"ts": "2026-08-11T00:00:02Z", "seq": 3, "type": "judgment",
         "decision_point": "kg_match", "decision": "id_match_ok",
         "scope": {"type": "cluster", "cluster_id": "0"},
         "run_ref": "step4_rank.rank_candidates#1",
         "inputs": [], "output": {"decision": "id_match_ok", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
        {"ts": "2026-08-11T00:00:03Z", "seq": 4, "type": "session_end", "final_summary": {}},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log))
    assert code == 1
    assert any(i["level"] == "error" and "粒度违规" in i["msg"] for i in issues)


def test_validator_passes_well_formed_log(tmp_path):
    """全合规日志(含正确 scope 粒度)应通过。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "session_start", "session_id": "sess-t"},
        {"ts": "2026-08-11T00:00:01Z", "seq": 2, "type": "exec",
         "run_id": "step4_rank.rank_candidates#1", "parameters": {}, "metrics": {"n_clusters": 5}},
        {"ts": "2026-08-11T00:00:02Z", "seq": 3, "type": "judgment",
         "decision_point": "kg_match", "decision": "id_match_ok",
         "scope": {"type": "session"},
         "run_ref": "step4_rank.rank_candidates#1",
         "inputs": [], "output": {"decision": "id_match_ok", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
        {"ts": "2026-08-11T00:00:03Z", "seq": 4, "type": "session_end", "final_summary": {}},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log))
    assert code == 0, issues


def test_mini_mode_passes_judgment_only_log(tmp_path):
    """mini 形态:只有 judgment、无 session_start/end/exec,应通过(auto 推断)。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "judgment",
         "decision_point": "qc_threshold", "decision": "threshold_set",
         "scope": {"type": "session"},
         "run_ref": "step1_prepare.metrics#1",
         "inputs": [], "output": {"decision": "threshold_set", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log), mode="auto")
    assert code == 0, issues
    issues, code = _VALIDATOR.validate(str(log), mode="mini")
    assert code == 0, issues


def test_mini_mode_warns_on_exec_without_session_boundary(tmp_path):
    """mini 形态:调过 pipeline 工具(有 exec)但无 session_start → 通过但带 warning。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "exec",
         "run_id": "step1_prepare.load_data#1", "parameters": {}, "metrics": {}},
        {"ts": "2026-08-11T00:00:01Z", "seq": 2, "type": "judgment",
         "decision_point": "qc_threshold", "decision": "threshold_set",
         "scope": {"type": "session"},
         "run_ref": "step1_prepare.load_data#1",
         "inputs": [], "output": {"decision": "threshold_set", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log), mode="auto")
    assert code == 0, issues
    assert any(i["level"] == "warning" and "缺 session_start" in i["msg"] for i in issues)


def test_mini_mode_still_checks_scope_and_enum(tmp_path):
    """mini 形态仍校验决策枚举与 scope 粒度(不合规则 FAIL)。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "judgment",
         "decision_point": "qc_threshold", "decision": "bogus_decision",
         "scope": {"type": "cluster", "cluster_id": "0"},
         "run_ref": "step1_prepare.metrics#1",
         "inputs": [], "output": {"decision": "bogus_decision", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log), mode="mini")
    assert code == 1
    assert any(i["level"] == "error" and "不在" in i["msg"] for i in issues)
    assert any(i["level"] == "error" and "粒度违规" in i["msg"] for i in issues)


def test_explicit_e2e_rejects_judgment_only_log(tmp_path):
    """显式 e2e 形态:只有 judgment 的日志必须 FAIL(缺完整性记录)。"""
    records = [
        {"ts": "2026-08-11T00:00:00Z", "seq": 1, "type": "judgment",
         "decision_point": "qc_threshold", "decision": "threshold_set",
         "scope": {"type": "session"},
         "run_ref": "step1_prepare.metrics#1",
         "inputs": [], "output": {"decision": "threshold_set", "confidence": "high", "action": "none"},
         "reasoning": "reason"},
    ]
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    issues, code = _VALIDATOR.validate(str(log), mode="e2e")
    assert code == 1
    assert any(i["level"] == "error" and "缺少 session_start" in i["msg"] for i in issues)
    assert any(i["level"] == "error" and "缺少 exec" in i["msg"] for i in issues)
