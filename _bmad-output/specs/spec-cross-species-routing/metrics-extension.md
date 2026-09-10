# Metrics Extension — 跨物种新指标解读

> SPEC cross-species-routing 的指标解读扩展伴侣。直接合并到 `skills/cell-annotation/references/metrics.md` 末尾"## 8. 跨物种指标"小节。
> LLM 通过 `references/metrics.md` 完整读取,新增小节提供新指标的高/低/异常解读与对应的决策点。

---

## 8. 跨物种指标

### 8.1 step3a_kg_precheck 指标

#### `coverage_tier`

- **取值**: `high` / `medium` / `low`
- **解读**:
  - `high` (genes_with_ct ≥ 500): 目标物种在 KG 中 cell type 标注充分,直接路径足够;`routing_accept single_species` 是合理默认
  - `medium` (50 ≤ x < 500): 部分覆盖,建议 mixed 路径(直接路径优先,跨物种路径补漏);LLM 看 `recommended_reference_species[]` 选高分 ref
  - `low` (< 50): 严重不足,必须跨物种路径;`routing_force_cross` 或 `routing_multi_reference` 优先
- **异常**: KG 不可达时 precheck 失败,LLM 应走默认单物种路径 + warn
- **决策点**: `cross_species_routing`(session 级)

#### `recommended_strategy`

- **取值**: `single_species` / `mixed` / `cross_species_only`(与 `coverage_tier` 一一对应)
- **解读**: precheck 给出的推荐策略;LLM 在 `routing_accept` 时直接采用,在 `routing_force_*` 时 override
- **决策点**: `cross_species_routing`

#### `recommended_reference_species[].score`

- **取值**: 0~1 浮点
- **解读**: 越大越适合做 reference;算法 = `ct_coverage_norm * 0.5 + phylogenetic_distance_norm * 0.4 + ensembl_divisible * 0.1`
  - `ct_coverage_norm`: 参考物种在 KG 中的 cell type 覆盖度归一化
  - `phylogenetic_distance_norm`: 与目标物种的亲缘距离(同属 = 1.0,同科 = 0.7,同目 = 0.4,跨目 = 0.2)
  - `ensembl_divisible`: Ensembl Compara 是否收录(1.0 / 0.0)
- **解读区间**:
  - `score > 0.7`: 强烈推荐
  - `0.5 ≤ score ≤ 0.7`: 可选
  - `score < 0.5`: 不推荐,LLM 应忽略
- **决策点**: `cross_species_routing`(LLM 在 `routing_multi_reference` 时挑 score > 0.5 的 ref)

---

### 8.2 step3b_cross_species_map 指标

#### `summary.hit_rate`

- **取值**: 0~1
- **解读**:
  - `> 0.7`: 健康,跨物种路径可用
  - `0.3 ~ 0.7`: 部分命中,看 `warnings` 与 unmapped 原因
  - `< 0.3`: 跨物种路径不可靠;LLM 应考虑退回单物种(`routing_force_single`)
- **异常**: `hit_rate = 0` 且 warnings 非空(Ensembl 不可达 / 物种不在 Compara) → 必然退回单物种

#### `summary.identity_distribution.median`

- **取值**: 0~100 百分比
- **解读**:
  - `median > 70%`: 高度保守,跨物种注释可信
  - `40% ~ 70%`: 中等保守,参考物种应选近缘
  - `< 40%`: 高度分化,跨物种注释风险高;LLM 应降 `--min-identity` 收紧或换 ref
- **决策点**: `cross_species_routing`(影响 action 选 ref)

#### `summary.type_distribution`

- **字段**: `ortholog_one2one` / `ortholog_one2many` / `ortholog_many2many` 计数
- **解读**:
  - `one2one` 占比 > 70%: 健康一对一映射
  - `one2many` 占比 > 30%: 多拷贝家族警示(陷阱 C-3);LLM 应保留 `--max-hits-per-gene=3` 截断,不可放宽
- **决策点**: `marker_quality`(session 级,SOP-2),LLM 看 one2many 比例决定是否放宽 marker 数

#### `summary.n_ref_genes_per_target_mean`

- **取值**: 浮点
- **解读**:
  - `< 1.1`: 健康,大部分一对一同源
  - `1.1 ~ 2.0`: 存在一对多;正常
  - `> 2.0`: 大量一对多;考虑扩张家族风险(陷阱 C-5)
- **决策点**: `marker_quality`(LLM 应收紧过滤或保留更多 marker 以稀释一对多影响)

---

### 8.3 step3c_kg 跨物种融合指标

#### `per_cluster[].n_markers_direct_hit` / `n_markers_ortholog_hit` / `n_markers_mixed_hit`

- **取值**: 整数
- **解读**:
  - `n_markers_direct_hit` 主导(`> 50%`): 直接路径覆盖好
  - `n_markers_ortholog_hit` 主导(`> 50%`): 直接路径覆盖不足,跨物种路径救命
  - `n_markers_mixed_hit` 主导: 两路径互补,健康状态
  - 全部为 0: 该 cluster 注释失败(unknown)
- **决策点**: `kg_match`(session 级),LLM 据此判断"覆盖率低的原因"

#### `per_cluster[].candidates[].source_species_set`

- **取值**: 物种名数组(小写下划线格式)
- **解读**:
  - 含目标物种名(如 `arabidopsis_thaliana`): 含直接路径命中,比纯跨物种候选更可信
  - 仅含 ref species: 纯跨物种路径命中,降一档置信度
  - 含多个 ref species: 多参考物种融合,信息丰富但需谨慎
- **决策点**: `candidate_gap`(cluster 级),候选优先级:含目标物种名 > 含同 species_type ref > 远缘 ref

#### `per_cluster[].candidates[].source_path_set`

- **取值**: `{"direct"}` / `{"ortholog"}` / `{"direct", "ortholog"}`
- **解读**:
  - `{"direct"}`: 仅直接路径命中
  - `{"ortholog"}`: 仅跨物种路径命中
  - `{"direct", "ortholog"}`: 双路径相互验证(高可信)
- **决策点**: `candidate_gap`(LLM 优先级双路径 > 仅 ortholog)

#### `query_stats.n_direct_hits` / `n_ortholog_hits` / `n_mixed_hits`

- **取值**: 整数(全数据集累计)
- **解读**: 与 `per_cluster` 同,但 session 级
- **决策点**: `global_quality`(session 级),LLM 看 session 级两路径比例是否合理

#### `query_stats.ortholog_unavailable` / `ortholog_warnings`

- **取值**: bool / string[]
- **解读**:
  - `ortholog_unavailable=false` + `warnings=[]`: 跨物种路径完整
  - `ortholog_unavailable=false` + warnings 非空(部分 marker 失败): 部分降级,LLM 在 reasoning 中说明
  - `ortholog_unavailable=true`: Ensembl 完全不可达,跨物种路径跳过,`n_ortholog_hits=0`
- **决策点**: `kg_match`(LLM 据此给 `id_match_ok` 但需说明"跨物种路径不可用")

---

### 8.4 cross_species_routing 决策点字段(REQUIRED_SCOPE 注册用)

详见 `data-contracts.md` §4。本节不重复。

---

## 与原 metrics.md 的关系

- 原 247 个指标章节(Step 1-7)完全保留。
- 本节是新增小节,对应 `knowledge/metrics_interpretation.md` 中**没有**的跨物种维度。
- LLM 通过 `references/metrics.md` 完整读取;新小节在文档末尾追加。
