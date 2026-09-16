---
title: '共享 REQUIRED_SCOPE 规则并增加回归验证'
type: 'refactor'
created: '2026-08-11'
status: 'in-progress'
baseline_commit: '467bf39'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `write_judgment.py` 与 `validate_log.py` 各自维护一份 `REQUIRED_SCOPE`，两份规则当前一致但未来可能漂移，导致写入校验和日志校验对同一决策点得出不同结论。

**Approach:** 建立一个可被两个脚本导入的共享模块，将 scope 规则集中维护；补充不依赖外部数据的回归验证，确认共享表内容及两个调用方行为保持一致。

## Boundaries & Constraints

**Always:** 保持现有 13 个 decision point 及 session/cluster 映射不变；保持脚本现有 CLI、错误语义和输出格式；共享模块必须能从两个脚本当前的执行方式导入。

**Ask First:** 无。若发现现有包结构或执行路径无法安全共享，停止并报告，不自行改变运行时布局。

**Never:** 不修改 organ_status；不扩展日志覆盖率；不改变 `DECISION_ENUMS`；不引入第三方依赖或要求现有项目新增测试框架。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|----------------------------|----------------|
| VALID_MAPPING | 两个脚本导入共享模块 | 两者使用同一 `REQUIRED_SCOPE` 对象/内容 | N/A |
| SESSION_MISMATCH | session 级 decision point 搭配 cluster scope | `write_judgment.py` 拒绝；`validate_log.py` 报 ERROR | 保持现有错误级别和信息语义 |
| CLUSTER_MISMATCH | cluster 级 decision point 搭配 session scope | `write_judgment.py` 拒绝；`validate_log.py` 报 ERROR | 保持现有错误级别和信息语义 |

</frozen-after-approval>

## Code Map

- `skills/cell-annotation/scripts/trajectory_schema.py` -- 新的共享 trajectory 规则模块
- `skills/cell-annotation/scripts/write_judgment.py` -- 写入 judgment 前的 scope 校验
- `scripts/validate_log.py` -- 对已有 judgment 记录执行 scope 合规校验
- `_bmad-output/implementation-artifacts/deferred-work.md` -- deferred 项状态与后续动作记录

## Tasks & Acceptance

**Execution:**
- [x] `skills/cell-annotation/scripts/trajectory_schema.py` -- 定义并导出唯一的 `REQUIRED_SCOPE` 映射 -- 消除跨目录同源拷贝
- [x] `skills/cell-annotation/scripts/write_judgment.py` -- 导入共享映射并删除本地定义 -- 保持现有 `_validate_add` 行为
- [x] `scripts/validate_log.py` -- 通过稳定路径导入共享映射并删除本地定义 -- 保持现有日志校验行为
- [x] `annot_harness/tests/test_trajectory_schema.py` -- 新增 pytest 回归测试(共享表形状、双调用方一致、粒度违规拒写/报 ERROR、合规日志通过) -- 防止规则漂移,纳入项目现有测试套件
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- 在验证完成后将对应条目标记 resolved -- 反映 deferred 清理结果
- [x] `AGENTS.md` -- 更新 repo status / directory map / 测试基础设施描述 -- 反映 harness 已实现与 pytest 套件真实状态

**Acceptance Criteria:**
- Given 两个脚本被独立执行，when 它们加载 scope 规则，then 都从同一共享模块取得完全相同的 13 项映射。
- Given 现有合法 judgment，when 运行写入工具或日志校验，then 行为与重构前一致。
- Given session/cluster 粒度不匹配，when 分别调用写入校验和日志校验，then 前者拒写、后者报告 ERROR。
- Given标准库测试命令执行，when 共享模块和调用方可导入，then 全部测试通过。

## Spec Change Log

## Design Notes

共享模块放在 skill 脚本目录，使 `write_judgment.py` 可沿用当前 `sys.path` 注入方式；`validate_log.py` 通过基于自身位置计算的绝对脚本目录导入，避免依赖当前工作目录。

## Verification

**Commands:**
- `python -m pytest` -- expected: 57 passed(52 既有 + 5 新增 REQUIRED_SCOPE 回归)
