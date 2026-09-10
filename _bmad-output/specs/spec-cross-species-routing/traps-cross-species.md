# Traps — 跨物种专项陷阱(精选 6 条)

> SPEC cross-species-routing 的陷阱扩展伴侣。直接合并到 `skills/cell-annotation/references/traps.md`,作为新小节"## 7. 跨物种专项陷阱"。
> 从 `knowledge/cross-species-annotation-handbook.md` 第二部分精选最影响本 pipeline 自动化的 6 条,补工程视角的检测与对策。

---

## 7. 跨物种专项陷阱

完整跨物种陷阱清单见 `knowledge/cross-species-annotation-handbook.md` 第二部分(28 条)。本节只列**本 pipeline 自动执行过程中最容易踩到的 6 条**,每条给出**信号 / 第一步排查 / 工程对策**。

### 陷阱 C-1 物种命名格式错(Plants 走 Plants 端点、Animal 走 Vertebrates)

- **现象**: Ensembl REST 调用 404,或返回空 homologies;`ortholog_map.summary.hit_rate=0`。
- **原因**: Ensembl REST 的物种名严格用 Ensembl 命名空间(Plant 用 `rest.plants.ensembl.org`,Animal/Vertebrate 用 `rest.ensembl.org`)。同名物种在两个域名的 API 行为不同。
- **工程对策**: `step3b_cross_species_map` 按 `species_type` 路由端点(Plant→plants,其它→vertebrates),**严禁**让用户在工具调用时手选端点;若物种不在两个端点任何一个,自动 fallback 到"无 ortholog"。

### 陷阱 C-2 Ensembl REST 速率限制 → 429

- **现象**: `step3b_cross_species_map` 运行中断,run_log.jsonl 有 HTTP 429 记录;`ortholog_map` 部分填充。
- **原因**: Ensembl Compara REST 未鉴权限速 ~15 req/s / 55 req/min,POST 批接口也计入限额。批量 234 个 marker 全跑会撞限。
- **工程对策**: `step3b_cross_species_map` 内置指数退避(1s/2s/4s/8s,遇 429 sleep `Retry-After`)+ 4 次重试。POST batch 优先(50 genes/req),单 fallback GET。POST 仍触发限速时切回 GET 逐个。运行成功条件:全部基因尝试完,失败数计入 `summary.n_ensembl_errors` + warning,不阻断。

### 陷阱 C-3 一对多未处理 → 注释分散

- **现象**: 某 cluster 第一候选 marker_count=5,第二候选=4,第三候选=4(全部接近);所有候选都是同一 cell type 不同 ortholog。
- **原因**: 多拷贝家族(嗅觉受体 / 免疫基因 / 转录因子)一个 target marker 命中 ref species 5+ 个同源基因,每个 ref gene 又标记不同 cell type,信号被稀释。
- **工程对策**:
  1. `step3b_cross_species_map --max-hits-per-gene 3`(默认),超过按 `identity` 降序截断;
  2. `step3b_cross_species_map` 在 summary 记录 `type_distribution.ortholog_one2many:N`,N>30% 时 warn;
  3. LLM 在 `candidate_gap` 看到第一候选 `marker_count` 接近后续候选且 source_species 单一(都是同一 ref species) → 触发 `ambiguous_true`,进 step5 refine。

### 陷阱 C-4 沿 KG 层级过度传播(跨物种时更易触发)

- **现象**: 所有 cluster 都被注释到粗粒度(全是"immune cell" / "epithelial cell"),无亚型;但本地 ground truth(若有)显示有 T/B 区分。
- **原因**: 跨物种路径下 KG 候选的 `relation_confidence` 普遍偏低(参考物种的 marker 没有目标物种验证过),LLM 倾向沿本体层级向上传播以保 coverage,结果注释粒度粗。
- **工程对策**:
  1. `step3c_kg` 候选聚合时 `source_path=ortholog` 的 hit `confidence` 折扣(默认 0.9x,在 SPEC 实施时可参数化);
  2. `global_quality` 新指标 `mixed_candidate_fraction` > 50% 时 warn;
  3. LLM 在 `global_quality` 看到所有 cluster 都被注释到 ancestor level(粒度过粗) → 收紧 `--min-confidence`,或切回保守模式(只认直接命中,不沿层级传播)。
  4. 参考手册第 1 部分第 4 步"明显领先"判断:第一候选支持 marker 数远多于第二 + 只有第一候选的 canonical marker 高表达。

### 陷阱 C-5 ortholog 高相似度但功能已分化(扩张家族)

- **现象**: 注释结果里某 cluster 是"T cell",但 canonical marker CD3D 在该 cluster 不表达;其它 cluster 注释为完全不相关类型(如神经元)。
- **原因**: 目标物种某基因家族扩张(参考物种单拷贝,目标物种 N 个),ortholog 映射取最相似的一个,但功能已分化(参考做 T 细胞,目标做神经)。
- **工程对策**:
  1. `step3b_cross_species_map` 检测扩张家族:`n_ref_genes_per_target` 的反向计数 —— 若 1 个 ref gene 被 ≥ 5 个 target gene 命中 → 该 ref gene 处于目标物种扩张家族,记 warning;
  2. `step6_validate.label_confirm`(cluster 级)必查 top-3 marker 在该 cluster 的 pct1(应 ≥ 0.5),canonical marker 不表达 → `label_downgraded`。
  3. 长期: 灌库 ortholog 关系时记录"扩张家族"标志,query 时降权。

### 陷阱 C-6 参考物种 KG 覆盖不足但仍在用它

- **现象**: 跨物种路径跑完,`ortholog_map.hit_rate=80%`(命中),但 `kg_hits.overall_hit_rate=5%`(参考物种 KG 极少 cell type 标注);所有 cluster 注释都模糊或 unknown。
- **原因**: Ensembl Compara 收录丰富但 KG 中参考物种 cell type 标注稀薄(比如选了一个 KG 中只有 50 个 gene 有标注的 ref species)。
- **工程对策**:
  1. `step3a_kg_precheck` 的 `recommended_reference_species[]` 已按"同 species_type + 高 cell type 覆盖"加权排序,LLM 在 `routing_accept` 默认采用 precheck 推荐;
  2. `routing_multi_reference` 时 LLM 应保留至少 1 个 `score > 0.5` 的高覆盖 ref,而不是凑数选远缘 ref;
  3. 若已跑 `step3b_cross_species_map` 但 `kg_hits.overall_hit_rate < 30%` → 在 `kg_match` 决策点判断 `id_match_ok`(但 `n_ortholog_hits > n_direct_hits`),选 `routing_multi_reference` 重打。

---

## 速查表:症状 → 工程层排查

| 症状 | 工程层第一排查 |
|---|---|
| `ortholog_map.summary.hit_rate < 30%` | 1) species_type 路由对不对(陷阱 C-1) 2) 目标物种是否在 Ensembl Compara 收录(`/info/species` 查) 3) marker ID 是否带版本号(如 `AT1G31340.1`) |
| `kg_hits.overall_hit_rate < 30%` 但 `n_direct_hits 正常` | ref species 的 KG 物种名格式未规范化(陷阱 C-6 + KG 命名混用)|
| `ortholog_map.summary.n_ensembl_errors > 10` | 网络问题或限速;陷阱 C-2,检查 `warnings` |
| 全部 cluster 注释粒度过粗(全是 ancestor level) | 陷阱 C-4;`global_quality` 应有相关指标 |
| 某 cluster 注释明显错(标记 T cell 但 canonical 不表达) | 陷阱 C-5;扩张家族 → `label_downgraded` |
| 跨物种路径与直接路径第一候选完全无关 | 1) 物种名 ID 错 2) ref species 亲缘过远 3) 陷阱 C-5 扩张家族 |

---

## 与原 traps.md 的关系

- 原 6 条陷阱(plant mt/cp / 层级本体 / 小样本 ratio / pct1 / silhouette / 批次熵)完全保留。
- 本节是新增小节,与原陷阱不冲突,可同时引用。
- LLM 通过 `references/traps.md` 完整读取;新小节在文档末尾追加。
