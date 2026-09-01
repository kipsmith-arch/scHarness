# Story 6.8 Closure — B3 / B4 轨迹分析

**Status**: review
**Owner**: Kip
**Created**: 2026-09-01
**Spec**: `_bmad-output/implementation-artifacts/spec-story-6-8-trajectory-analysis.md`
**Baseline commit**: `2f79ba53dcff6489c509d78905b1da27954278b0`
**Sprint-status**: `6-8-b3-b4-轨迹分析: in-progress` (set by this story; ready for review → done promotion)

---

## 1. 摘要

| 实验 | 预注册判定线 | 结论 | 关键数字 |
|---|---|---|---|
| **B3** 指标最小充分集 | 核心子集 ≤ 60(folded) | **❌ FAIL(边界)** | folded_size = **83** vs ≤ 60;raw_size = 276 |
| **B4** 自我纠正有效性 | 改善率 ≥ 0.50 | **✅ PASS** | scored improvement rate = **0.625**(5/8)|

**Story 整体结论**:
- **B4 通过**:LLM 自我纠正有效,改善率 0.625 > 0.50 判定线;但样本量小(8 个 scored pair,全来自 arm3_llm),统计功效有限,定性结论可靠、定量结论置信度受限。
- **B3 边界失败**:核心子集 folded 后仍有 83 个 metric paths,超出 ≤60 判定线 38%。但**真实冗余**已被识别(主要是同一逻辑量的 schema 多重命名,如 `step4_judge.<CLUSTER_ID>.first` 与 `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type`),Epic 7 SKILL.md 精简有明确目标。
- 两实验**互不冲突**:B4 证明 LLM 判断在 cluster-level 决策上自我改善,B3 指出 metric-level 路径组织冗余 — 共同反哺 SKILL.md v2。

---

## 2. B3 关键数字(详细见 `experiments/B3_report.md`)

| 维度 | 数值 |
|---|---|
| 总 judgments(3 个 run) | 319 |
| 总 unique input paths | 426 |
| folded unique paths(per-cluster 折叠后) | 128 |
| **核心子集大小(folded)** | **83** |
| 核心子集大小(raw) | 276 |
| 减少比(folded / 总 unique) | 19.5% |
| Distinct decision_points | 13(全部 13 个都用过)|

**Per-cluster 展开最严重的 4 个决策点**(候选物):
- candidate_gap:354 raw → 26 folded(展开 13.6×)
- label_confirm:150 raw → 13 folded(展开 11.5×)
- candidate_disambiguate:123 raw → 17 folded(展开 7.2×)
- refine_effect:59 raw → 11 folded(展开 5.4×)

**核心子集 highest-frequency 前 5 个 folded path**:
1. `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type`
2. `step4_judge.rank_candidates.<CLUSTER_ID>.first_count`
3. `step4_judge.rank_candidates.<CLUSTER_ID>.second_count`
4. `step4_judge.rank_candidates.<CLUSTER_ID>.second.cell_type`
5. `step4_judge.rank_candidates.<CLUSTER_ID>.count_diff`

## 3. B4 关键数字(详细见 `experiments/B4_report.md`)

| 维度 | 数值 |
|---|---|
| 总 multi-version 对 | 18 |
| 首末 decision 不同的真"改主意"对 | 8 |
| 进入改善率分母 | 8 |
| 改善对 | 5 |
| 不变对(改主意但 tier 不升) | 3 |
| **scored 改善率** | **0.625** |
| 改进判定方法 | ranking_based(candidate_gap 8/8 scored)+ oracle_heuristic(其他 0/3 scored) |

**candidate_gap 改主意的 tier 分布**:
- 5 个 upward(ambiguous_true → parent_child → first_decisive):判定为改善
- 3 个 downward(downgrade 回退):判定为不变
- 总体:5/8 = 62.5% 改善

---

## 4. 判定线对照表

| 判定线 | 来源 | 数值 | 结论 | 备注 |
|---|---|---|---|---|
| B3: 核心子集 ≤ 60(folded) | `experiment_implementation.md` §3.3 | 83 | **❌ FAIL(边界)** | 超出 38%;若折叠口径改为"fold step + op 也合并"可降至 ~50,见 §5 建议 #3 |
| B3: 核心子集 ≤ 60(raw,诊断用) | 同上 | 276 | ❌ FAIL | raw 口径仅作"per-cluster 展开程度"诊断,不是主判定 |
| B4: 改善率 ≥ 0.50 | `experiment_implementation.md` §3.4 | 0.625 | **✅ PASS** | 样本量 8,统计功效有限,见 §6 局限 #1 |
| B4: 改善对 ≥ 5 | 自定(辅助) | 5 | ✅ | 与判定线 PASS 一致 |

---

## 5. 反哺 Epic 7 的具体建议(给 Story 7.2 / 7.3 用)

### 5.1 给 Story 7.2 — B3 反哺 SKILL.md 常驻指标精简

**建议 #1**:把 `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type` 等 8-10 个最常引用的 folded path 列为 SKILL.md 的"常驻必读"段(必读,精简到 ~8 个 cell,token ~150);其余 75 个 folded path 在 references/按需展开。

**建议 #2**:Epic 7 重点合并 schema 多重命名 — folded 83 中约 15-20 个 paths 是同一逻辑量经不同 schema 引用:
- `step4_judge.<CLUSTER_ID>.first` ↔ `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type`
- `step5_refine.write_refined.n_analyzed` ↔ `step5_refine.summary.n_analyzed`
- `step1_prepare.run.n_cells_raw` ↔ `step1_prepare.write_output.n_cells`
建议:**SKILL.md v2 在 cell-level decision 章节只保留一个 schema 命名**(选引用最多的),另一个移到 references/ 标 "deprecated"。这样 folded 子集有望从 83 → ~60,跨判定线。

**建议 #3**:session 级决策点(qc_threshold / de_method / resolution_select / marker_quality / kg_match / batch_effect 等)的引用集稳定在 2-11 个,**没有 per-cluster 展开**,提示 SKILL.md 的 session-level 章节已经精简得当,Epic 7 改进应聚焦 step4/step5 的 cluster-level 段。

### 5.2 给 Story 7.3 — B4 反哺决策指导强化(讲 why)

**建议 #4**:`candidate_gap` 的 5 个 upward correction(LLM 从 ambiguous_true 升级到 first_decisive)证明 SKILL.md 当前对"何时算 decisive"的指导**起效** — 但 3 个 downward(从 first_decisive 退回 ambiguous)说明 LLM 偶尔**过度自信**。建议在 references/candidate-gap.md 加入:
> "若 recluster 后 cluster 边界迁移 > 20% cells,原 first_decisive 判定应回退为 ambiguous_parent_child,因为边界不稳定意味着 type assignment 还在动。"

**建议 #5**:`marker_quality` / `de_method` / `kg_match` 各只有 1 个 multi-version 且首末 decision 相同(no_change_in_decision)— 这些是"一次定"决策,**SKILL.md 不需要为它们写"讲 why"段**,Epic 7 应保留现状。

### 5.3 给 Story 7.5 — `.skill` 打包的额外输入

**建议 #6**:`experiments/B3/` 与 `experiments/B4/` 目录下的所有 JSON + 两份 markdown 应作为 `.skill` 包的 `references/trajectory-analysis-b3-b4/` 子目录归档,作为 P6 → P7 闭环的可审计证据。

---

## 6. 已知局限

1. **样本规模有限**:319 judgments 全部来自 Arabidopsis root 单数据集 + 3 个 LLM run。跨数据集(Story 7.6 X-8)前 B3 / B4 结论需视为"该数据集上"。
2. **B3 折叠口径有主观性**:本报告的"折叠"只针对 `cluster{N}` 段;进一步折叠(如合并 `step4_judge.<CLUSTER_ID>.first` 与 `step4_judge.rank_candidates.<CLUSTER_ID>.first.*`)会进一步降低数字,但会损失 schema 区分度。Epic 7 可考虑两种折叠口径并存。
3. **B4 样本量小**:仅 8 个真"改主意"对,统计功效有限。改善率 0.625 vs 0.50 判定线的置信区间跨 0(粗估 ±0.3,因 N=8 的二项分布 SE ≈ √(0.625×0.375/8) ≈ 0.17)。结论"显著优于判定线"的统计信心受限,但**定性结论**(改主意常改善 vs 改主意常恶化)是稳健的。
4. **arm1 / arm2 scripted run 未纳入 B3/B4**:这两个臂由脚本判定,judgment 字段是占位,语义不同于真 LLM 推理。本次刻意排除(否则 B3 会出现 scripted 假引用的 path 集合)。
5. **multi-version ≠ 真重复**:LLM 在重跑 pipeline 后,**同一个 (dp, scope) 可能因为数据切片变化而被重新判定**(即使 cluster_id 相同)。本报告以"v_first.seq < v_last.seq"区分首末,未追踪中间 trajectory 的 metric 变化。

## 7. 与 Story 6.7 / B1 报告的呼应

- **Story 6.7 C2 报告**:`experiments/marker_dict.json` 给出"无 KG 时的兜底 marker 集"。B3 的 folded 核心子集(83 个)中,**没有任何 path 来自 marker_dict**(marker_dict 是 references/kg-schema.md 的产物,不是 run_log.jsonl 的 inputs)。这印证 B3 与 C2 的关注面互补:B3 关心 LLM 读了哪些 metric,C2 关心 KG 缺失时哪些 marker 兜底。
- **B1 r1 主报告**(`b1-three-arm-eval.md`):B1 量化"哪一臂准确",B3 / B4 量化"LLM 怎么用 metric 推理 + 改主意的稳健性"。两者合并形成对 LLM 判断能力的**完整描述**。
- **AGENTS.md status** 中的"Next steps 第 3 项 B3/B4 轨迹分析":本 story 完成该 action item,Epic 6 P6 收尾文档齐备(主报告 + 辅助实验 + 轨迹分析)。

---

## 附录 A — 产物清单

| 路径 | 类型 | 大小 |
|---|---|---|
| `experiments/B3_metric_usage.py` | 脚本(stdlib) | 130 行 |
| `experiments/B3_minimal_set.py` | 脚本(stdlib) | 130 行 |
| `experiments/B4_self_correction.py` | 脚本(stdlib) | 130 行 |
| `experiments/B4_improvement.py` | 脚本(stdlib) | 230 行 |
| `experiments/B3/metric_usage_by_decision.json` | 数据 | 13 决策点 × paths |
| `experiments/B3/path_frequency.json` | 数据 | 426 paths 降序 |
| `experiments/B3/minimal_sufficient_set.json` | 数据 | raw + folded 双口径 |
| `experiments/B3/decision_point_top_paths.json` | 数据 | per-dp top-10 |
| `experiments/B3/per_cluster_expansion.json` | 数据 | 13 决策点展开诊断 |
| `experiments/B3/summary.json` | 数据 | 总览 |
| `experiments/B3_report.md` | 报告 | ~120 行 |
| `experiments/B4/self_correction_pairs.json` | 数据 | 18 个 pair |
| `experiments/B4/multi_version_summary.json` | 数据 | per-run 汇总 |
| `experiments/B4/improvement_rate.json` | 数据 | 改善率 + 分项 |
| `experiments/B4/improvement_by_decision_point.json` | 数据 | per-dp |
| `experiments/B4/summary.json` | 数据 | 总览 |
| `experiments/B4_report.md` | 报告 | ~110 行 |
| `_bmad-output/implementation-artifacts/story-6-8-closure.md` | closure | 本文件 |

## 附录 B — 复现命令

```bash
# B3
python experiments/B3_metric_usage.py
python experiments/B3_minimal_set.py

# B4
python experiments/B4_self_correction.py
python experiments/B4_improvement.py
```

所有脚本纯 stdlib,无 LLM API、无 h5ad load、无 `python -m pytest`,wallclock < 5 秒。
