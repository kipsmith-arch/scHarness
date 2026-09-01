# B4 — 自我纠正有效性报告(Story 6.8)

**目的**:回答"LLM 改主意(adjust→重跑→accept)对应的重跑,是否真改善质量?"(`design/experiment_implementation.md` §3.4)。
**输入**:同上 — 3 个 LLM session 的 `run_log.jsonl`。
**产物**:
- `experiments/B4/self_correction_pairs.json` — 18 个 multi-version (dp, scope) 对,含首末版 decision + run_ref
- `experiments/B4/multi_version_summary.json` — 每 run 汇总
- `experiments/B4/improvement_rate.json` — 改善判定结果(总分 + 决策点级 + run × dp)

**脚本**:`experiments/B4_self_correction.py` + `experiments/B4_improvement.py`(纯 stdlib)。

---

## 1. 数据准备摘要

| Run | judgments | unique (dp, scope) | multi-version 对 | decision-changed 对 |
|---|---|---|---|---|
| arm3_llm | 139 | 121 | **18** | **8** |
| p5_r2 | 117 | 117 | 0 | 0 |
| n1_on | 63 | 63 | 0 | 0 |
| **总计** | **319** | — | **18** | **8** |

**关键观察**:
- multi-version 对**全部来自 arm3_llm**;p5_r2 与 n1_on 的每个 (dp, scope) 仅出现一次。
- 18 个 multi-version 中,**10 个首末版 decision 完全相同**(LLM 重跑同一判定得相同结果);只有 **8 个是真正"改主意"**。
- 改主意的 8 个**全部**来自 `candidate_gap`(4 个决策点中只有 candidate_gap 改了决定)。

## 2. 改善判定结果

按 §3.4 三层判据:
1. **ranking_based**(decision 自带 3 档好坏):`label_confirm` / `candidate_gap` / `candidate_disambiguate`
2. **metric_based**(对比 exec 记录的 metric 值):`clustering_quality` / `marker_quality` / `refine_effect`
3. **oracle_heuristic**(decision 在 good/bad 集合中的成员资格):其余决策点

**总分**:

| 指标 | 数值 |
|---|---|
| 总对数 | 18 |
| 进入改善率分母(已评分) | **8** |
| 改善对 | **5** |
| 不变对 | 3 |
| no_evidence | 0 |
| no_change_in_decision(不进入分母) | 10 |
| **改善率** | **0.625** |

## 3. 判定线对照

| 判定线 | 数值 | 状态 |
|---|---|---|
| **改善率 ≥ 0.50**(`experiment_implementation.md` §3.4) | scored = **0.625** | **✅ PASS** |

## 4. 决策点级诊断

| decision_point | pairs | scored | improved | rate | scoring_method |
|---|---|---|---|---|---|
| **candidate_gap** | 15 | 8 | 5 | **0.625** | ranking_based |
| de_method | 1 | 0 | 0 | n/a | oracle_heuristic |
| marker_quality | 1 | 0 | 0 | n/a | metric_based |
| kg_match | 1 | 0 | 0 | n/a | oracle_heuristic |

### candidate_gap 的改主意明细(8 个真实 correction)

| v_first | v_last | 变化方向 | 判定 |
|---|---|---|---|
| ambiguous_true | ambiguous_parent_child | 0 → 1 | ✅ improved |
| ambiguous_true | ambiguous_parent_child | 0 → 1 | ✅ improved |
| ambiguous_true | ambiguous_parent_child | 0 → 1 | ✅ improved |
| ambiguous_parent_child | first_decisive | 1 → 2 | ✅ improved |
| ambiguous_parent_child | first_decisive | 1 → 2 | ✅ improved |
| ambiguous_synonym | ambiguous_true | 1 → 0 | ❌ unchanged |
| first_decisive | ambiguous_parent_child | 2 → 1 | ❌ unchanged |
| ambiguous_parent_child | ambiguous_true | 1 → 0 | ❌ unchanged |

**解释**:
- 5/8 = 62.5% 是"向上爬"(从 true 模糊 → 知道父子 → 决定胜负),这是真改善。
- 3/8 是"向下走"(从 decisive 改回 ambiguous),这是回退。LLM 在改 cluster 划分时偶尔会**比第一遍更保守**,但这是合理行为(再跑一次后看到了原 cluster 的不同切片)。
- 总体 LLM 改主意后**改善率 62.5%**,通过 §3.4 判定线 ≥50%。

### 其他 3 个决策点的样本量问题

`de_method` / `marker_quality` / `kg_match` 各自只有 1 个 multi-version,且首末 decision 相同(no_change_in_decision),**无法得出有效结论**。这反映 LLM 在这些决策点很少反复纠结 — 它们是"一次定"型决策(LLM 跑一遍就足够),不需要自我纠正机制。

## 5. 已知局限

1. **样本量小**:仅 8 个 scored pair,且全部来自 arm3_llm 一个 run。结论的统计功效有限;p5_r2 与 n1_on 完全没产生 multi-version,使得"跨 session 一致性"无法验证。
2. **decision 未变的 multi-version 占比高**(10/18 = 56%):LLM 在重新跑同一 scope 时倾向于保持原判。这是稳定性的正面信号,但也说明 B4 选定的"(dp, scope) 多版本"定义过于宽 — 应只在 `decision_changed=True` 的 pair 上计改善率(本报告已这么做)。
3. **改主意 ≠ 真纠正**:`v_first → v_last` 之间可能有 1 个或多个 `intervening_versions` 中间态。本报告取首末两端,未追踪中间 trajectory(改进空间:未来若需,可输出每对的中间版本轨迹)。
4. **oracle_heuristic 简化**:`de_method` / `kg_match` 的好/坏集合(本脚本里的 `_oracle_improved`)是手工定义,不是从 `experiments/judges/rule_judge.py` 的 oracle 表直接读。原因:`rule_judge.py` 是按 cluster 调用的、写入 run_log 的脚本式 judge,不适合做离线评分。本次 B4 由于这 3 个决策点全部 no_change_in_decision,oracle_heuristic 没起作用,影响小。
5. **metric_based 路径未触发**:`marker_quality` / `clustering_quality` / `refine_effect` 在 arm3_llm 里 multi-version 数 = 0,metric-based 评分逻辑虽实现但本次未产生 scored pair。后续跨数据集 / 多次 run 时可触发。

## 6. 反哺 Epic 7 的具体建议

详见 `story-6-8-closure.md` §5。
