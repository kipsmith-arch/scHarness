# P5 技能测试循环 — 第一轮闭环评估报告

> 日期:2026-08-12 · 依据 `design/implementation_plan.md` §7 / `skills/cell-annotation/evals/evals.json`
> 状态:**部分达标(E-1 达标;E-2~E-4 决策合法但日志不完整;E-5 未达标)**
> 目的:把已有跑测产物变成可验证的结论,对齐实施计划 §7.3 达标线。

---

## 1. 用例结果总表

| 用例 | 模式 | 断言 | 实测结果 | 判定 |
|---|---|---|---|---|
| E-1 | e2e | final_annotations 有标签/置信度/marker;run_log 有 session_start/end;evaluation 报告落盘 | 产物齐全(step1→step7 全链);`evaluation_report.json` 落盘;run_log 89 条(65 judgment) | ✅ **达标** |
| E-2 | mini | decision ∈ {ambiguous_parent_child, ambiguous_synonym, ambiguous_true};reasoning 非空 | `candidate_gap → ambiguous_parent_child`(cluster 级,cluster_id=3)合法 | ⚠️ 决策合法,日志不完整 |
| E-3 | mini | decision ∈ {single_unknown_type, multiple_unknown_types};不硬贴标签 | `unknown_cluster → multiple_unknown_types` ×2(session 级)合法 | ⚠️ 决策合法,日志不完整 |
| E-4 | mini | decision ∈ {threshold_set, threshold_default};不套动物阈值 | `qc_threshold → threshold_set`(session 级)合法 | ⚠️ 决策合法,日志不完整 |
| E-5 | validate | `validate_log` exit 0;judgment 字段齐全 | E-1 run_log:`validate_log` FAIL(2 条 scope 粒度违规);E-2~E-4 run_log:均 FAIL(缺 session_start/end/exec) | ❌ **未达标** |

---

## 2. E-1 端到端(✅ 达标)

**产物链完整**(`output/p5_evals/`):

```
step1_prepare/processed.h5ad + qc_metrics.json + obs/var_snapshot.csv
step2_markers/markers.csv + markers.json
step3_kg/kg_hits.json + kg_source.txt
step4_judge/annotations.json
step5_refine/refined_annotations.json
step6_validate/final_annotations.json + report.md
step7_diagnose/step7_diagnose.json + report.md
run_log.jsonl(89 条:65 judgment + 22 exec + session_start/end)
conversation.jsonl + notes.jsonl + e1_session.log
```

**细胞级评估**(`evaluate_annotations.py`,33,762 细胞,39 簇):

| 指标 | 值 |
|---|---|
| strict accuracy(exact/synonym) | **0.868** |
| relaxed accuracy(partial=0.5) | 0.8972 |
| macro-F1(soft) | 0.6397 |
| mean cluster purity | 0.8842 |

报告:`output/p5_evals/evaluation_report.json`。

**遗留问题(来自 session log,人工复核建议)**:step4/5/6/7 只返回汇总指标,top-3 marker 的 pct1/pct2/cohen_d/auc 及 cross_cluster 细节未逐一核对;人工复核重点:root cap 相关簇(6/20/21/27/29/35)与簇 15 的唯一标签。

---

## 3. E-2~E-4 mini-session(⚠️ 决策合法,日志不完整)

**决策枚举与 scope 粒度全部合法**(逐条核对 trajectory §3.2 枚举表 + REQUIRED_SCOPE):

- E-2 `output/p5_evals_E2/run_log.jsonl`:`candidate_gap → ambiguous_parent_child`,cluster 级 ✅
- E-3 `output/p5_evals_E3/run_log.jsonl`:`unknown_cluster → multiple_unknown_types` ×2,session 级 ✅
- E-4 `output/p5_evals_E4/run_log.jsonl`:`qc_threshold → threshold_set`,session 级 ✅

**但 `validate_log` 全部 FAIL**,原因:

| 用例 | 错误 |
|---|---|
| E-2 | 缺 session_start / session_end / exec 记录;run_ref 悬空 |
| E-3 | 缺 session_start / session_end / exec 记录;run_ref 悬空 ×2 |
| E-4 | 缺 session_start / session_end;run_ref 悬空 |

**根因**:mini-session 是"只喂该决策点指标 + skill 决策指导"的单决策点会话,不跑 pipeline,因此 run_log 里没有对应 exec 记录——`validate_log` 的完整性校验(要求 session_start/end/exec)对 mini-session 形态不适用。

**结论**:E-2~E-4 的**决策正确性已验证**,但**轨迹完整性校验工具对 mini-session 形态过严**。这不是 skill 缺陷,而是校验工具的适用边界问题——E-5 断言(`validate_log_exit_0`)不应机械套用于 mini-session。

---

## 4. E-5 日志合规(❌ 未达标)

### 4.1 违规明细

**E-1 run_log 存在 2 条真实粒度违规**(REQUIRED_SCOPE 强制后由 validate_log 抓出):

```
L74: decision_point 'refine_effect' 粒度违规:要求 scope-type='cluster',实际 'session'
L74: 'refine_effect' scope-type=cluster 缺 cluster_id
L80: decision_point 'label_confirm' 粒度违规:要求 scope-type='cluster',实际 'session'
L80: 'label_confirm' scope-type=cluster 缺 cluster_id
```

**根因**:这两条 judgment 是 LLM 在 E-1 跑测时写入的,当时 `write_judgment` 尚未强制 REQUIRED_SCOPE(强制是 P5-r2 加入的);SKILL.md §7 现指引明确"cluster 级决策点逐簇写、不得合并为 session 级",且工具现在会拒写——**旧账被新校验抓到,符合预期**。

### 4.2 E-5 判定

- **E-1 产物**:❌ `validate_log exit=1`(2 条 cluster 级决策点写成 session 级)
- **E-2~E-4 产物**:❌ 完整性校验不适用(mini-session 无 exec 轨迹)

**E-5 断言 `validate_log_exit_0` 当前不满足,判未达标。**

---

## 5. 处理建议(供决策)

| 选项 | 动作 | 成本 | 效果 |
|---|---|---|---|
| **A. 重跑 E-1**(推荐) | 用当前 SKILL.md + 强制后的 write_judgment 重跑端到端;新增 exec 缺失检查 | 1 次全 session LLM 调用 | E-1/E-5 一次闭环,产物可复现 |
| B. 修旧账 | 手工修正 run_log L74/L80 为 cluster 级 | 低,但改轨迹不干净 | E-5 通过,但掩盖真实问题 |
| C. 放宽 E-5 | 区分 e2e/mini 两种校验形态;mini 只验决策枚举+scope,不验完整性 | 改 validate_log | E-2~E-4 判定为达标;E-1 仍需重跑 |
| D. 接受现状 | E-5 记未达标,进入 P6 时以重跑产物为准 | 0 | 闭环未闭合 |

**建议组合:C(改校验工具区分形态)+ A(重跑 E-1)**,使 P5 闭环真实闭合且工具边界合理。

---

## 6. 实施计划 §7.3 达标线对照

| 达标线 | 状态 |
|---|---|
| E-1 端到端跑通,run_log 完整,session_end 有 final_summary | ✅(E-1 产物满足) |
| E-2~E-4 的 decision 全部落在枚举内,reasoning 附指标依据 | ✅(决策枚举全部合法;reasoning 均有) |
| E-5 日志合规(validate_log.py 通过) | ❌(E-1 2 条粒度违规;mini-session 完整性校验不适用) |

**结论:P5 第一轮闭环未完全闭合,卡在 E-5。**
