# 交付报告模板(report-template)

> 本模板是注释会话结束时的**人类可读交付报告**骨架,供 LLM 在 `session_end` 前后填写,或供人工核对 pipeline 产物。
> 各字段的来源文件:
> - 元数据/逐簇注释 → `{project-dir}/step6_validate/final_annotations.json`(脚本 `step6_validate__run` / `step6_validate__report` 已生成 `step6_validate/report.md`)
> - 诊断/全局质量 → `{project-dir}/step7_diagnose/step7_diagnose.json`(已生成 `step7_diagnose/report.md`)
> - 轨迹统计 → `{project-dir}/run_log.jsonl`(`session_start` / `judgment` / `session_end` 记录)
>
> 占位符 `{...}` 用实际值替换;不确定的字段写 `N/A` 并注明原因,不要编造。流水线自带的两个 report.md 是程序生成的事实快照,本模板是交付层汇总,两者不冲突。

## TOC

- [1. 会话与数据集元数据](#1-会话与数据集元数据)
- [2. 全局摘要](#2-全局摘要)
- [3. 逐簇注释](#3-逐簇注释)
- [4. 诊断检查](#4-诊断检查)
- [5. 轨迹统计](#5-轨迹统计)
- [6. 输出文件清单](#6-输出文件清单)

---

# 细胞类型注释交付报告

## 1. 会话与数据集元数据

| 项 | 值 |
|---|---|
| 数据集 ID | `{dataset_id}` |
| 组织 | `{organ}` |
| 批次列 / 批次数 | `{batch_key}` / `{n_batches}` |
| 原始细胞数 / 基因数 | `{n_cells_raw}` / `{n_genes_raw}` |
| 物种 | `{organism}` |
| KG 来源 / 版本 | `{kg_source}` / `{kg_version}` |
| scanpy 版本 | `{scanpy_version}` |
| 注释日期 | `{annotation_date}` |
| 关键阈值 | `{thresholds}`(如 top_n_markers、max_mt_pct、分辨率等实际使用值) |
| 置信度规则 | `{confidence_rule}`(证据规则 + 是否有 LLM 覆盖) |

> 来源:run_log.jsonl 的 `session_start` 记录 + final_annotations.json `_meta`。

## 2. 全局摘要

| 指标 | 值 |
|---|---|
| 簇数 n_clusters | `{n_clusters}` |
| unknown 数 / 率 | `{n_unknown}` / `{unknown_rate}` |
| 唯一标签数 / 多样性 | `{n_unique_labels}` / `{label_diversity}` |
| 有效类型数(熵) | `{effective_n_types}` |
| 平均 first/second 差距 | `{mean_first_second_gap}` |
| 平均 top-3 marker 特异度 | `{mean_top3_specificity_across_clusters}` |
| 跨簇 marker 复用 | `{cross_cluster_marker_reuse}` |
| 全局质量 decision | `{global_quality_decision}`(quality_good / quality_acceptable / quality_poor) |

> 来源:session_end 的 `final_summary` + step6 `global_summary` + step7 `cross_cluster`。逐簇标签占比见 final_annotations.json `_summary.cell_type_proportions`。`global_quality_decision` 来自 run_log.jsonl 中 decision_point=`global_quality` 的 judgment 记录输出。

## 3. 逐簇注释

> 每簇一节,结构与 `step6_validate/report.md` 一致;`{...}` 逐簇替换。

### 簇 `{cluster_id}` — `{label}`(置信度 `{confidence}`)

- first 候选:`{cell_type}`(marker_count=`{first_count}`,mean_confidence=`{mean_confidence}`)
- second 候选:`{cell_type}`(marker_count=`{second_count}`)
- 差距:count_ratio=`{count_ratio}`  count_diff=`{count_diff}`
- top-3 marker 表达:

| gene | pct1 | pct2 | cohen_d | auc |
|---|---|---|---|---|
| `{gene}` | `{pct1}` | `{pct2}` | `{cohen_d}` | `{auc}` |

- refine 历史(如做过细分):outcome=`{outcome}` 子簇数=`{n_subclusters}`;子簇结果:`{sub_results}`
- KG 命中率:`{per_cluster_hit_rate}`
- 判断依据(judgment 记录引用):decision=`{decision}`(candidate_gap / candidate_disambiguate / label_confirm),run_ref=`{run_id}`

## 4. 诊断检查

| 检查项 | 结果 |
|---|---|
| 每簇 KG 命中率 | `{per_cluster_hit_rate}` |
| 每簇候选数 / first / second / strictly_ahead | `{per_cluster_candidate_count}` / `{per_cluster_first_second}` |
| 批次检查(batch_key=`{batch_key_used}`) | 每簇批次熵 `{per_cluster_batch_entropy}`,最大批次占比 `{per_cluster_max_batch_fraction}`;结论 `{batch_effect_decision}`(batch_effect / condition_specific / well_mixed) |
| 元数据完整性 | 缺失字段:`{metadata_missing}`(无 或 列出) |
| 跨簇指标 | `{cross_cluster}`(label_uniqueness、annotation_entropy、cluster_purity_proxy、mean_first_count_gap、mean_cross_cluster_marker_overlap) |

> 来源:step7_diagnose.json。诊断不达标时在此列出回到哪个 SOP/决策点修复。

## 5. 轨迹统计

| 项 | 值 |
|---|---|
| 工具调用次数(run_count) | `{run_count}` |
| judgment 记录数 | `{judgment_count}` |
| 各决策点 judgment 数 | `{per_decision_point_counts}`(13 决策点覆盖核对) |
| session_id | `{session_id}` |

> 来源:run_log.jsonl;`judgment` 记录必带 `reasoning`,缺失即不合规(trajectory §10)。

## 6. 输出文件清单

| 文件 | 路径 | 说明 |
|---|---|---|
| 最终注释 | `{project-dir}/step6_validate/final_annotations.json` | 每簇标签 + 证据 + 元数据 |
| 注释报告 | `{project-dir}/step6_validate/report.md` | 程序生成 |
| 诊断 JSON | `{project-dir}/step7_diagnose/step7_diagnose.json` | 全局质量指标 |
| 诊断报告 | `{project-dir}/step7_diagnose/report.md` | 程序生成 |
| 轨迹文件 | `{project-dir}/run_log.jsonl` | session_start / exec / judgment / session_end |

---

*生成时间:`{ts}` · 本报告由 cell-annotation 技能在注释会话结束时填写。*
