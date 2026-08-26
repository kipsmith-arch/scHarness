# SOP Extension — 跨物种路由 SOP-2.5 + SOP-3 扩展

> SPEC cross-species-routing 的 SOP 扩展伴侣。直接合并到 `skills/cell-annotation/references/sop.md`,不替换原文件;LLM 通过 `references/sop.md` 完整读取。
> 对应 SOP 章节: SOP-1 / SOP-2 不变;新增 SOP-2.5 跨物种路由预检;SOP-3 重写为 3a→3d。

---

## SOP-2.5 跨物种路由预检(CAP-1 + CAP-4)

> **何时执行:** step2_markers 完成、step3_kg 之前。session 级,每个数据集一次。

### 步骤

1. 调 `step3_kg_precheck__run` —— 输入 `--target-species`(从 dataset 元数据读,如 `arabidopsis_thaliana`)、`--organ`、`--species-type`;输出 `coverage_report.json`。
2. 读 `coverage_report.json`,关键字段:
   - `coverage_tier`(`high` / `medium` / `low`)
   - `recommended_strategy`(`single_species` / `mixed` / `cross_species_only`)
   - `recommended_reference_species[]`(每个含 `species` / `kg_genes` / `kg_unique_ct` / `score`)
3. 写一条 `cross_species_routing` judgment(session 级),从枚举选一个:
   - `routing_accept` —— 采用 precheck 推荐(recommended_strategy + recommended_reference_species)
   - `routing_force_single` —— 强制单物种(例如用户明确说"只用本物种",或 coverage_tier=high 且 LLM 确认无 cross 必要)
   - `routing_force_cross` —— 强制跨物种(例如用户明确说"用某 ref 物种",或 KG 覆盖足够但用户额外要求)
   - `routing_multi_reference` —— 显式指定多个 reference(override precheck 推荐)
4. 根据 decision 决定后续 SOP-3 路径:
   - `routing_accept` 且 `recommended_strategy=single_species` → 直接进 SOP-3 单物种路径,跳过 SOP-2.5b
   - `routing_accept` 且 `recommended_strategy=mixed/cross_species_only` → 进 SOP-2.5b
   - `routing_force_single` → 单物种路径
   - `routing_force_cross` / `routing_multi_reference` → 进 SOP-2.5b

### SOP-2.5b 同源映射(条件执行,CAP-2)

> 仅在 SOP-2.5 决定走 cross / mixed 路径时执行。

1. 调 `step2_ortholog__run`,输入 `--target-species`、`--reference-species`(单或多个)、`--species-type`、`--input step2_markers/markers.json`;可选 `--min-identity`(默认 30)、`--max-hits-per-gene`(默认 3)、`--force-refresh`(默认 false)。
2. 读 `ortholog_map.json`,关键字段:
   - `ortholog_map`(每个 target gene → ref gene 列表,含 identity / coverage / type)
   - `summary.hit_rate`(应 ≥ 50% 才算可用)
   - `summary.identity_distribution`(median ≥ 50% 算可信)
   - `warnings`(Ensembl 不可达 / 物种不在 Compara 等)
3. 若 `hit_rate < 30%` 或 `warnings` 非空,在 reasoning 中说明,LLM 可决定:
   - 降低 `--min-identity` 重打(若 warnings 是因为阈值严)
   - 换 reference species 重打
   - 接受当前结果 + 标低置信度走 cross 路径
   - 退回单物种路径(`routing_force_single`)
4. 输出 `ortholog_map.json` 供 SOP-3 消费。

---

## SOP-3 查参考知识(扩展为 3a → 3d)

> 原 SOP-3 仅"按 organ 过滤 → 查 KG"。本 spec 扩展为 4 步。沿用 `knowledge/cross-species-annotation-handbook.md` 第 1 部分第 2~3 步的语义,但工具化为已有 step3_kg + 新增 precheck/ortholog。

### 3a 预检(已在 SOP-2.5 完成)

仅回顾: precheck 报告 + 决策点记录已经在 SOP-2.5 写入。

### 3b 同源映射(已在 SOP-2.5b 完成,仅在 cross 路径)

`ortholog_map.json` 准备好。

### 3c organ 对齐 + 候选查询(CAP-3)

1. 调 `step3_kg__query`,传 `--organ <organ>`(必填)、`--species-type <type>`、`--ortholog-map step2_ortholog/ortholog_map.json`(若 SOP-2.5b 已跑;否则不传)。
2. 工具内部并行跑两条路径:
   - **直接路径:** `MATCH (g:Gene)-[r:marker_of]->(o:Ontology) WHERE g.Name IN $target_genes AND g.Species_type = $species_type`
   - **跨物种路径:** 对 ortholog_map 中每个 (target_gene → ref_gene),`MATCH (g:Gene)-[r:marker_of]->(o:Ontology) WHERE g.Name IN $ref_genes AND g.Species = $ref_species`
3. 两路径合并,每个 hit 标注 `source_path` ∈ {direct, ortholog}。
4. 候选聚合按"cell_type" group:
   - `supporting_markers` 保留所有来源 marker(target_gene + ref_gene 都在,带 `source_path` 标注)
   - `source_species_set` 列出所有命中来源物种
   - `mean_confidence` 按 hit 加权
5. 输出 `kg_hits.json`,含 `n_direct_hits` / `n_ortholog_hits` / `n_mixed_hits` 计数。

### 3d LLM 候选解读(决策点 kg_match + candidate_gap)

LLM 在 `kg_match`(session 级)决策点读:
- `kg_hits.query_stats.overall_hit_rate` / `n_direct_hits` / `n_ortholog_hits`
- `kg_hits.query_stats.ortholog_warnings`(Ensembl 降级提示)
- 候选的 `source_species_set`(直接路径 vs 跨物种路径)

判断要点:
- `overall_hit_rate` 高 + `n_direct_hits` 主导 → 直接路径够用
- `overall_hit_rate` 低 + `n_direct_hits=0` 但 `n_ortholog_hits > 0` → ID/物种命名问题或 KG 覆盖不足,跨物种路径救命
- `overall_hit_rate` 低 + `n_direct_hits=0` + `n_ortholog_hits=0` → 物种名/ID 错,查上游
- `ortholog_warnings` 非空 → 考虑重打或退回单物种

`candidate_gap`(cluster 级)决策点读:
- 候选的 `source_species_set`:`{"arabidopsis_thaliana"}` 比 `{"oryza_sativa"}` 更可信(同物种直接路径命中 > 跨物种)
- 第一候选与第二候选的 `source_species_set` 是否一致:
  - 一致(都直接路径或都跨物种) → 正常 gap 判断
  - 不一致(第一直接路径,第二跨物种) → 第一候选更可信,降一档第二候选的强度

`candidate_disambiguate`(cluster 级)在跨物种并列时的判断:
- 两候选都同源到同一 reference species → 真正的并列模糊(参考手册第 1 部分第 4 步判断标准)
- 两候选同源到不同 reference species → 可能是不同 cell type,不能简单并列,要分别评估

`global_quality`(session 级,step7_diagnose)读:
- `cross_species_ortholog_rate`(cluster 层面使用 ortholog 路径的比例)
- `mixed_candidate_fraction`(候选来源含多物种的比例)
- 整体看跨物种路径是否带来新候选、占比是否合理

### 决策枚举与原 SOP-3 兼容

- `kg_match` 枚举不变:`id_match_ok` / `id_mismatch_gene_key` / `id_mismatch_organ`
- `candidate_gap` 枚举不变:`first_decisive` / `ambiguous_parent_child` / `ambiguous_synonym` / `ambiguous_true` / `unknown`
- `candidate_disambiguate` 枚举不变

---

## 与原 SOP 的关系

- SOP-1 / SOP-2 / SOP-4 / SOP-5 / SOP-6 / SOP-7 不变。
- 原 SOP-3 全文保留为"3c organ 对齐"的简化描述,新 SOP-3 是其细化。
- 本扩展**不破坏**现有 evals(E-1 拟南芥数据在 coverage_tier=high 时 SOP-2.5 选 `routing_accept single_species`,跳过 SOP-2.5b,3c 走直接路径,行为与现状一致)。
