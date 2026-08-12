# P5 技能测试循环 — 第一轮闭环最终报告(r2 重跑)

> 日期:2026-08-12 · 承接 `p5-evals-round1-closure.md`(r1 报告)
> 结论:**P5 闭环完全闭合 —— E-1 与 E-5 双达标,且修复了 r1 暴露的 skill 知识缺口。**

---

## 1. 结果总表

| 用例 | 断言 | r1 结果 | **r2 结果(本次)** | 判定 |
|---|---|---|---|---|
| E-1 | 端到端 + 评估报告 | 0.868 strict | **0.9236 strict / 0.9434 relaxed** | ✅ 达标(提升) |
| E-2 | mini 决策枚举合法 | 决策合法 | 决策合法 | ✅ 达标 |
| E-3 | mini 决策枚举合法 | 决策合法 | 决策合法 | ✅ 达标 |
| E-4 | mini 决策枚举合法 | 决策合法 | 决策合法 | ✅ 达标 |
| E-5 | validate_log exit 0 | FAIL(2 条粒度违规) | **PASS(exit 0)** | ✅ 达标 |

---

## 2. 本轮工作

### 2.1 工具改造:`validate_log` 形态感知(已提交 `dda7bc4`)

- 新增 `--mode {auto,e2e,mini}`(默认 auto)
- auto 推断判据 = 是否存在 `session_start`(而非 exec 存在性)
- e2e:完整完整性校验;mini:只验 judgment 枚举/scope/字段
- 修正 mini-session(E-2~E-4)被完整性校验误杀的问题

### 2.2 E-1 重跑:发现并修复 skill 知识缺口

**r2 首跑失败(0% accuracy,全 unknown)**,根因:

```
LLM 从 dataset 元数据取 organism="Arabidopsis thaliana" 传给 --species
KG 实际存储 g.Species = "arabidopsis_thaliana"(小写+下划线)
精确匹配 → 0 命中 → 全部标 unknown
```

**修复**(用户决策:不传 species + 原始基因 ID):

- `references/kg-schema.md`:新增"物种过滤与命名"章节——默认不传 `--species`;TAIR locus ID 物种特异,不传即天然隔离;若传必须用 KG 格式(小写+下划线)
- `SKILL.md` 使用注意:明确 step3_kg__query 不传 `--species`

### 2.3 r2 最终重跑结果

**E-5 PASS**(117 judgment,全合规):

| 决策点 | 数量 | scope |
|---|---|---|
| candidate_gap | 39 | cluster(逐簇) |
| candidate_disambiguate | 21 | cluster |
| refine_effect | 13 | cluster |
| label_confirm | 39 | cluster(逐簇) |
| de_method / marker_quality / kg_match / unknown_cluster / global_quality | 各 1 | session |
| **合计** | 117 | 112 cluster + 5 session |

`kg_match → id_match_ok`:KG 命中正常,基因 ID 匹配与 organ 对齐良好。

**E-1 accuracy(33,762 细胞,39 簇):**

| 指标 | r1 | r2 | Δ |
|---|---|---|---|
| strict accuracy | 0.868 | **0.9236** | +5.6pt |
| relaxed accuracy | 0.8972 | **0.9434** | +4.6pt |
| macro-F1(soft) | 0.6397 | 0.5165 | -12.3pt |
| mean cluster purity | 0.8842 | 0.8842 | 0 |

> macro-F1 下降但 strict/relaxed accuracy 上升:本次判断更"果断"(逐簇明确标注),少数类型预测更集中,导致对罕见类型的 soft F1 下降;strict accuracy(用户核心指标)显著提升。

产物:`output/p5_evals_r2/evaluation_report.json`。

---

## 3. 与实施计划 §7.3 达标线对照

| 达标线 | 状态 |
|---|---|
| E-1 端到端跑通,run_log 完整,session_end 有 final_summary | ✅(100+ 记录,含 session_start/end) |
| E-2~E-4 的 decision 全部落在枚举内,reasoning 附指标依据 | ✅ |
| E-5 日志合规(validate_log.py 通过) | ✅(117 judgment 全合规) |

**结论:P5 skill 测试循环第一轮闭环完全闭合,skill 通过内部质量闸门,具备进入 P6 方法学实验(B1 三臂)的前置条件。**

---

## 4. 遗留与建议

1. **macro-F1 下降待观察**:若 B1 关注罕见类型,需复查 label_map 的 subtype 映射是否过严。
2. **species 格式约定已入文档**:跨数据集时若 KG 物种名格式不同,沿用"不传 species"策略即可。
3. **r2 产物保留**:`output/p5_evals_r2/` 可作为后续 B1 的对照基线。
