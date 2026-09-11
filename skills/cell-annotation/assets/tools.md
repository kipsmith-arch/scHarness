# 工具总览(加载器派生,供人工核对)

> 本文档是 pipeline 工具的**人工核对快照**:内容整理自 `scripts/*.py --dump-schema` 的实际输出与 `design/atomic_operations.md` 的 op 清单。SOP-3 脚本按执行顺序命名为 3a / 3b / 3c。
> 加载器(skill_loader.py)不读取本文件——它直接运行 `python <script> --dump-schema` 实时派生工具。**若 scripts 参数或 op 有变更,以重新派生结果为准,并回来更新本表。**

## TOC

- [派生契约](#派生契约)
- [总览表](#总览表)
- [逐工具参数](#逐工具参数)
- [op 映射(47 个唯一 op)](#op-映射47-个唯一-op)
- [核对清单](#核对清单)

---

## 派生契约

- 工具名 = `{脚本名}__{子命令}`(如 `step1_prepare__run`);SOP-3 为 `step3a_kg_precheck` → `step3b_cross_species_map`(可选) → `step3c_kg`。
- 每个工具 stdout 的**最后一行**必须是 JSON:`{"status":"ok","data":{...}}` 或 `{"status":"error",...}`;`data` 内通常含 `run_id`。
- `run_id` 格式:`{step}.{op}#{attempt}`(如 `step1_prepare.leiden_cluster#1`),重跑追加 `#2`…;judgment 记录用 `run_id` 做 `run_ref`。脚本 stdout 的 `data` 内通常含 `last_exec_run_id`(即最后一条 exec 的 run_id);`step1_prepare__metrics` / `step3c_kg__test-connection` / `step6_validate__report` 不返回 run_id。
- 数据加载纪律:一个子命令 = 一次 h5ad 加载;`step3a_kg_precheck` / `step3b_cross_species_map` / `step3c_kg` / `step4_rank` / `step7_diagnose` 与 `report` 类零加载,只读 JSON 与 sidecar(`obs_snapshot.csv` / `var_snapshot.csv`)。
- 参数优先级:CLI 显式传参 > 环境变量(如 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`)> 脚本默认值。**不要把密码写死在参数里。**

## 总览表

| # | 工具 | 作用 | h5ad 加载 | 唯一 op 数 |
|---|---|---|---|---|
| 1 | `step1_prepare__metrics` | 只算并输出 QC 分布(load_data → compute_qc → qc_distribution → qc_plot),不产出处理数据 | 1× raw | 4 |
| 2 | `step1_prepare__run` | 完整预处理:QC → 过滤 → doublet → 归一化 → HVG → PCA → kNN → Leiden → UMAP → 批次检查 | 1× raw | 16 |
| 3 | `step1_prepare__recluster` | 换分辨率重新聚类(leiden → choose_resolution → umap → write_output) | 1× proc | 4 |
| 4 | `step2_markers__run` | DE 排序 + pct1/pct2 + marker 过滤 + 稀有簇 pseudobulk | 1× proc | 5 |
| 5 | `step3a_kg_precheck__run` | KG 覆盖预检(不推荐参考物种) | 0 | 2 |
| 6 | `step3b_cross_species_map__run` | 同源映射(条件,提高 KG 命中率;可选 blastp) | 0 | 5 |
| 7 | `step3c_kg__query` | KG 查询:marker → 候选细胞类型 + 本体祖先 | 0 | 5 |
| 8 | `step3c_kg__test-connection` | 检查 Neo4j 连通性 | 0 | 1 |
| 9 | `step4_rank__run` | first/second 候选排名与差距 | 0 | 2 |
| 10 | `step5_refine__run` | 模糊簇:自相关预判 → 子聚类 → 子簇 DE/KG → 重叠检查 | 1× proc | 8 |
| 11 | `step6_validate__run` | top-marker 表达验证 + 全局摘要 + 报告 + 最终注释 | 1× proc(backed 可选) | 5 |
| 12 | `step6_validate__report` | 从 final_annotations.json 重新生成 report.md | 0 | 1 |
| 13 | `step7_diagnose__run` | 诊断与全局质量测量 | 0 | 6 |

唯一 op 合计:原目录 47(与 `design/atomic_operations.md` 一致);SOP-3 扩展另计 `step3a_kg_precheck` 2 op + `step3b_cross_species_map` 5 op。部分 op 被多个子命令复用(如 `load_data` 在 metrics/run 共用、`qc_plot` 在 metrics/run 共用、`write_output` 在 run/recluster 共用、`connect` 在 query/test-connection 共用、`write_report` 在 validate run/report 共用)。

---

## 逐工具参数

> 下列参数名 / 类型 / 默认值 / 必填均取自 `--dump-schema` 实况;`req` 列 `*` 表示必填。

### step1_prepare__metrics

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| organ | string | `root` | 组织(默认 root) |
| batch_key | string | `Orig.ident` | 批次列名 |
| mt_pattern | string | `^(ATMG\|MT-)` | 线粒体基因前缀正则 |
| cp_pattern | string | `^ATCG` | 叶绿体基因前缀正则(植物) |
| seed | integer | `0` | 随机种子 |
| project_dir | string | `output` | 项目目录(含 run_log.jsonl 与各 step 数据目录) |
| input | string | `null` | 输入 h5ad 路径(缺省时按 step 约定自动寻找) |

### step1_prepare__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| organ / batch_key / mt_pattern / cp_pattern / seed | — | 同 metrics | 同上 |
| min_genes | integer | `300` | 细胞最小检测基因数 |
| max_mt_pct | number | `15.0` | 线粒体占比上限 |
| max_chloroplast_pct | number | `15.0` | 叶绿体占比上限(植物) |
| min_cells | integer | `3` | 基因最小表达细胞数 |
| expected_doublet_rate | number | `0.06` | scrublet 期望双峰率 |
| target_sum | number | `10000.0` | 归一化 target_sum |
| n_top_genes | integer | `2000` | HVG 数量 |
| hvg_batch_key | string | `null` | 分批选 HVG 的批次列(可选) |
| n_comps | integer | `50` | PCA 主成分数 |
| n_neighbors | integer | `15` | kNN 邻居数 |
| n_pcs | integer | `30` | kNN 使用的 PC 数 |
| resolution_list | string | `0.4,0.6,0.8,1.0,1.2` | Leiden 分辨率列表(逗号分隔) |
| target_resolution | string | `null` | 选中的分辨率(必须由判断层显式给出,禁止 knee 静默选定) |
| project_dir / input | — | — | 同上 |

### step1_prepare__recluster

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| organ / batch_key / mt_pattern / cp_pattern / seed | — | 同 metrics | 同上 |
| resolution_list | string | `0.4,0.6,0.8,1.0,1.2` | Leiden 分辨率列表 |
| target_resolution | string | `null` | 选中的分辨率(必须由判断层显式给出,禁止 knee 静默选定) |
| n_neighbors | integer | `15` | kNN 邻居数 |
| n_pcs | integer | `30` | kNN 使用的 PC 数 |
| project_dir / input | — | — | 同上 |

### step2_markers__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| n_genes | integer | `500` | 每簇 DE top-N 基因 |
| min_pct1 | number | `0.5` | marker 最小 pct1 |
| max_pct1 | number | `0.9` | marker 最大 pct1(排除管家基因) |
| min_pct1_pct2 | number | `0.25` | 最小 pct1-pct2 特异性 |
| top_n | integer | `30` | 每簇最终保留 marker 数 |
| rare_threshold | number | `0.05` | 稀有簇判定阈值(细胞比例) |
| use_pseudobulk_for_rare | boolean | `false` | 稀有簇切换 pseudobulk DE |
| pseudobulk_min_samples | integer | `2` | pseudobulk 每组的样本数下限 |
| batch_key | string | `Orig.ident` | 样本列名(pseudobulk 聚合用) |
| project_dir / input | — | — | 同上 |

### step3a_kg_precheck__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| target_species | string | (必填) | KG/Ensembl `lower_underscore` 物种 id |
| organ | string | (必填) | 组织,传给下游 step3c |
| species_type | string | `Plant` | Plant / Animal / ... |
| project_dir | string | `output` | 项目目录 |
| high_threshold | integer | `500` | coverage_tier=high 下限 |
| low_threshold | integer | `50` | coverage_tier=low 上限 |

不输出 `recommended_reference_species`。参考物种由 LLM 从 `references/reference-species.md` 点名。

### step3b_cross_species_map__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| provider | string | `ensembl_compara` | `ensembl_compara` / `blastp` |
| target_species | string | (必填) | 本物种 KG id |
| reference_species | string | (必填,可重复) | 参考物种;超过 3 个 warning 并截断 |
| species_type | string | `Plant` | 供 Ensembl division 路由 |
| input | string | (必填) | `step2_markers/markers.json` |
| project_dir | string | `output` | 项目目录 |
| min_score | number | `30.0` | 丢弃 score 低于此的映射 |
| max_hits_per_gene | integer | `1` | 每个 (gene, ref) best-N;默认 best-1 |
| query_fasta | string | `null` | 用户蛋白 FASTA；文件存在时自动优先 blastp（Ensembl 默认被忽略） |
| force_refresh | boolean | `false` | 忽略缓存 |
| provider_timeout / provider_max_retries / provider_concurrency / max_genes | — | Ensembl 10s；blastp 1800s（30 min） | provider 调优 / smoke cap。`--provider-timeout` argparse 默认仍是 10（Ensembl REST）；blastp 把这个 10 当成未覆盖，改用 30 分钟。显式传入其它秒数则照用。 |

`blastp` 时读 `step1_prepare/var_snapshot.csv` 过滤 FASTA(不二次 load h5ad)。库按需下载。配置失败改道 Ensembl,仍 `ok`。

### step3c_kg__query

LLM-facing 参数（任务类，生物决策）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| organ | string | `null` (LLM 必填) | 组织过滤(对应 o.Organ) |
| species | string | `null` | 物种(对应 g.Species);默认不传(TAIR ID 天然物种隔离) |
| species_type | string | `"Plant"` | 物种类型(对应 g.Species_type);LLM 跨物种场景需传 |
| strict_organ | boolean | `false` | 严格按 organ 过滤命中 |
| project_dir / input | — | — | 同上 |

运维可 CLI 覆盖（但隐藏于 LLM schema）的环境参数：

| CLI flag | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--min-confidence` | number | `0.0` | `marker_of.relation_confidence` 下限 |
| `--max-ancestor-hops` | integer | `3` | ontology_relation 祖先最大跳数；设为 0 跳过 hierarchy 查询 |

> 注：step3c_kg **不做基因 ID 映射**（TAIR locus → symbol 等）。h5ad 的 `var_names` 原样查 KG。ID 转换由用户上游完成（数据处理责任）。

Neo4j 连接 (`skills/cell-annotation/.env`)：

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `NEO4J_URI` | string | `bolt://localhost:7687` | Neo4j URI |
| `NEO4J_USER` | string | `neo4j` | Neo4j 用户 |
| `NEO4J_PASSWORD` | string | 无（必须设） | Neo4j 密码 |

### step3c_kg__test-connection

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| project_dir | string | `output` | 同 query |
| `uri` / `user` / `password` (SUPPRESS) | string | env / `null` | 运维可 CLI 覆盖（隐藏于 LLM schema），默认从 `skills/cell-annotation/.env` 读 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` |

### step4_rank__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| project_dir | string | `output` | 同上 |
| input | string | `null` | 同上 |

### step5_refine__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| subcluster_resolution | number | `0.5` | 子聚类分辨率 |
| min_cells | integer | `100` | 可细分的最小父簇细胞数(SOP-5A) |
| subcluster_n_pcs | integer | `15` | 子聚类 PCA 主成分数 |
| subcluster_n_neighbors | integer | `15` | 子聚类 kNN 邻居数 |
| sub_de_n_genes | integer | `50` | 子簇 DE top-N |
| sub_top_n | integer | `10` | 每子簇保留 marker 数 |
| min_pct1 | number | `0.5` | 子簇 marker 最小 pct1 |
| max_pct1 | number | `0.9` | 子簇 marker 最大 pct1 |
| min_pct1_pct2 | number | `0.25` | 子簇最小 pct1-pct2 |
| project_dir / input | — | — | 同上 |

### step6_validate__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| top_n_markers | integer | `3` | 每簇验证的 top marker 数 |
| backed | boolean | `false` | backed 模式按列读 raw.X |
| no_violin | boolean | `false` | 跳过小提琴图生成 |
| project_dir / input | — | — | 同上 |

### step6_validate__report

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| project_dir / input | — | — | 同上 |

### step7_diagnose__run

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| batch_key | string | `null` | 批次列名(缺省自动识别) |
| project_dir / input | — | — | 同上 |

---

## op 映射(47 个唯一 op)

| 脚本 | 子命令 | op(run_id 用) |
|---|---|---|
| step1_prepare | metrics | load_data / compute_qc / qc_distribution / qc_plot |
| step1_prepare | run | load_data / compute_qc / qc_distribution / qc_plot / filter_cells / filter_genes / detect_doublets / normalize / select_hvg / pca / knn_graph / leiden_cluster / choose_resolution / umap / batch_mixing / write_output |
| step1_prepare | recluster | leiden_cluster / choose_resolution / umap / write_output |
| step2_markers | run | de_rank / pct1_pct2 / pseudobulk_de / filter_markers / write_markers |
| step3a_kg_precheck | run | target_coverage / write_report |
| step3b_cross_species_map | run | collect_marker_genes / filter_query_fasta / query_provider / collapse_best1 / write_output |
| step3c_kg | query | connect / query_genes / query_hierarchy / aggregate_candidates / write_hits |
| step3c_kg | test-connection | connect |
| step4_rank | run | rank_candidates / write_annotations |
| step5_refine | run | candidate_autocorr / subcluster / subcluster_de / subcluster_kg / marker_overlap / type_membership / unknown_overlap / write_refined |
| step6_validate | run | marker_expression / violin_plot / global_summary / write_report / write_final |
| step6_validate | report | write_report |
| step7_diagnose | run | hit_rate / candidate_count / first_second / batch_entropy / metadata_check / cross_cluster |

## 核对清单

- [ ] `load_skill('skills/cell-annotation')` → 工具名含 `step3a_kg_precheck__run` / `step3b_cross_species_map__run` / `step3c_kg__query`,与上表 SOP-3 三行一致。
- [ ] 每个工具 stdout 最后一行是 `{"status":"ok"/"error",...}`,且 `data.last_exec_run_id`(如返回)符合 `{step}.{op}#{attempt}`。
- [ ] 上表 op 去重后 == 47,与 `design/atomic_operations.md` 阶段表逐条对齐。
- [ ] 每子命令 h5ad 加载次数与"总览表"一致(0 / 1× raw / 1× proc),无新增加载。
- [ ] 参数默认值与 `--dump-schema` 输出一致(scripts 变更后需重跑核对)。
