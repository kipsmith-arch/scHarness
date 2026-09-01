# B3 — 指标引用频次统计报告(Story 6.8)

**目的**:回答"247 个指标是否冗余?LLM 实际引用了哪些?"(`design/experiment_implementation.md` §3.3)。
**输入**:3 个 LLM session 的 `run_log.jsonl`:
- `output/B1/arm3_llm/run_log.jsonl`(B1 r1 主臂,139 judgments)
- `output/p5_evals_r2/run_log.jsonl`(P5 r2 closure,117 judgments)
- `output/N1_on/run_log.jsonl`(N1 notebook-on,63 judgments)
- **合计 319 judgments,426 unique input paths,13 distinct decision_points**

**产物**:
- `experiments/B3/metric_usage_by_decision.json` — 每个决策点引用的 paths + 频次
- `experiments/B3/path_frequency.json` — 全量 paths 降序
- `experiments/B3/minimal_sufficient_set.json` — raw + folded 两种核心子集
- `experiments/B3/decision_point_top_paths.json` — 每个决策点的 top-10 paths(folded)
- `experiments/B3/per_cluster_expansion.json` — per-cluster 展开诊断

**脚本**:`experiments/B3_metric_usage.py` + `experiments/B3_minimal_set.py`(纯 stdlib,无 LLM 调用,无 h5ad load)

---

## 1. 决策点引用分布

| decision_point | raw_unique | folded_unique | 展开倍数 | 评估 |
|---|---|---|---|---|
| candidate_gap | 354 | 26 | **13.6×** | 高度 per-cluster 展开 |
| label_confirm | 150 | 13 | **11.5×** | 高度 per-cluster 展开 |
| candidate_disambiguate | 123 | 17 | **7.2×** | 高度 per-cluster 展开 |
| refine_effect | 59 | 11 | **5.4×** | 中度 per-cluster 展开 |
| kg_match | 11 | 11 | 1.0× | 簇级,稳定 |
| global_quality | 10 | 10 | 1.0× | 簇级,稳定 |
| qc_threshold | 8 | 8 | 1.0× | session 级 |
| unknown_cluster | 8 | 8 | 1.0× | session 级 |
| de_method | 7 | 7 | 1.0× | session 级 |
| clustering_quality | 5 | 5 | 1.0× | session 级 |
| marker_quality | 5 | 5 | 1.0× | session 级 |
| resolution_select | 5 | 5 | 1.0× | session 级 |
| batch_effect | 2 | 2 | 1.0× | session 级 |
| **总计** | **426** | **128** | **3.3×** | — |

观察:只有 4 个决策点有 per-cluster 展开,且都集中在 step4/step5 簇级判断。session 级决策点(qc / resolution / marker_quality / de_method 等)的引用集稳定在 2-10 个。

## 2. 核心子集对比

| 口径 | 大小 | 与 426 unique paths 比 |
|---|---|---|
| raw(不折叠) | 276 | 64.8% |
| folded(per-cluster 折叠) | **83** | 19.5% |
| folded 之外的 45 个 paths | — | 各决策点的"次要"路径(cited ≤1 次,或在某决策点 top-20 之外) |

### folded 核心子集(83 个)的构成

最高频引用前 10 个 path(folded):
1. `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type`(14+ 次)
2. `step4_judge.rank_candidates.<CLUSTER_ID>.first_count`(10+ 次)
3. `step4_judge.rank_candidates.<CLUSTER_ID>.second_count`(8+ 次)
4. `step4_judge.rank_candidates.<CLUSTER_ID>.second.cell_type`
5. `step4_judge.rank_candidates.<CLUSTER_ID>.count_diff`
6. `step4_judge.rank_candidates.<CLUSTER_ID>.first_second_ancestor_overlap.related`
7. `step4_judge.<CLUSTER_ID>.first`(legacy / parallel schema)
8. `step4_judge.rank_candidates.<CLUSTER_ID>.first.mean_confidence`
9. `step4_judge.<CLUSTER_ID>.second`(legacy / parallel schema)
10. `step4_judge.<CLUSTER_ID>.gap.count_diff`

观察:**`step4_judge.<CLUSTER_ID>.first` 与 `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type` 是两条 schema 不同的路径引用同一个逻辑量**(簇 0..N 的 first 候选)。这种 schema 冗余是当前 folded 子集 83 > 60 的主要原因。

## 3. 判定线对照

| 判定线 | 数值 | 状态 |
|---|---|---|
| **核心子集 ≤ 60**(实验设计 §3.3) | folded = **83** | **❌ FAIL** |
| raw(诊断用) | 276 | — |
| unique_paths_folded 总数 | 128 | — |

**结论**:核心子集大小 83,超过判定线 ≤60 约 38%。FAIL 不剧烈(83 与 60 差 23),但确实存在真实冗余。

## 4. 已知局限

1. **判定线对照口径选择**:实验设计 §3.3 没有显式定义 "core subset" 是否折叠 cluster_id;我们采用 folded 口径(主判定线)+ raw 口径(诊断)并列报告。若改判定线为 raw 口径,结果为 276,远超 60,判定线失去意义 — 说明折叠是必需的口径,但即便折叠仍未通过。
2. **schema 冗余问题**:folded 83 中约 15-20 个 paths 是同一逻辑量的多条 schema(`step4_judge.<CLUSTER_ID>.first` vs `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type` 等)。这是 Epic 7 SKILL.md 精简的**真实可改进点**。
3. **样本规模有限**:319 judgments 全部来自同一 dataset(Arabidopsis root);跨数据集(Story 7.6 X-8)可能引用分布有偏移。
4. **N1_on 与 p5_r2 的引用分布与 arm3_llm 一致**:同 skill 跨多个 session 的引用结构稳定 — 跨决策点引用结构可作 skill 的"指纹",不应跨数据集失效。

## 5. 反哺 Epic 7 的具体建议

详见 `story-6-8-closure.md` §5。
