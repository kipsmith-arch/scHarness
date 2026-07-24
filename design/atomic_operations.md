# 原子操作清单

本文件将 pipeline 的每一步拆成**原子操作**——最小的、可独立运行的、有明确输入输出的计算单元。

每个操作定义以下属性:
- **ID**: 全局唯一编号 `Op-XX`
- **描述**: 去生物化的纯操作描述
- **输入**: 消耗的数据(文件 / 内存对象)
- **输出**: 产出的数据(文件 / 内存对象 / JSON 指标块)
- **当前实现**: 脚本名 + 函数 + 行号
- **当前指标**: 该操作当前写出的统计量(`[无]` = 完全空白)
- **候选指标**: 见 `operations_metrics_catalog.md` 对应的 Op 编号
- **依赖**: 必须先完成的操作

共 **46 个原子操作**,分布在 **7 个阶段**。当前由 **7 个脚本**实现,其中 `step1_prepare.py` 单独承担 16 个操作。

---

## 全局操作索引

| ID | 操作名 | 阶段 | 当前脚本 | 当前指标数 | 候选指标数 |
|---|---|---|---|---|---|
| step1_prepare.load_data | 加载原始数据 | 1 数据准备 | step1_prepare.py | 0 | 0 |
| step1_prepare.compute_qc | 计算质控变量 | 1 | step1_prepare.py | 5 | 10 |
| step1_prepare.qc_distribution | 质控分布统计 | 1 | step1_prepare.py | 3 | 7 |
| step1_prepare.qc_plot | 质控可视化 | 1 | step1_prepare.py | 0 | 0 |
| step1_prepare.filter_cells | 细胞过滤(多准则) | 1 | step1_prepare.py | 2 | 6 |
| step1_prepare.filter_genes | 基因过滤(多准则) | 1 | step1_prepare.py | 0 | 3 |
| step1_prepare.detect_doublets | 双峰检测与移除 | 1 | step1_prepare.py | 0 | 4 |
| step1_prepare.normalize | 归一化 | 1 | step1_prepare.py | 0 | 4 |
| step1_prepare.select_hvg | HVG 选择与子集化 | 1 | step1_prepare.py | 1 | 5 |
| step1_prepare.pca | 缩放与 PCA | 1 | step1_prepare.py | 0 | 5 |
| step1_prepare.knn_graph | kNN 邻域图构建 | 1 | step1_prepare.py | 0 | 6 |
| step1_prepare.leiden_cluster | Leiden 聚类(多分辨率) | 1 | step1_prepare.py | 1 | 16 |
| step1_prepare.choose_resolution | 分辨率选择 | 1 | step1_prepare.py | 2 | 8 |
| step1_prepare.umap | UMAP 嵌入 | 1 | step1_prepare.py | 0 | 7 |
| step1_prepare.batch_mixing | 批次混合评估 | 1 | step1_prepare.py | 1 | 4 |
| step1_prepare.write_output | 写出处理后数据与指标 | 1 | step1_prepare.py | — | — |
| step2_markers.de_rank | DE 排序 | 2 Marker | step2_markers.py | 2 | 9 |
| step2_markers.pct1_pct2 | pct1/pct2 计算 | 2 | step2_markers.py | 3 | 4 |
| step2_markers.filter_markers | Marker 过滤 | 2 | step2_markers.py | 2 | 4 |
| step2_markers.pseudobulk_de | 稀有簇 Pseudobulk DE | 2 | step2_markers.py | 2 | 2 |
| step2_markers.write_markers | 写出 markers | 2 | step2_markers.py | — | — |
| step3_kg.connect | KG 连接与来源 | 3 KG | step3_kg.py | 3 | 0 |
| step3_kg.query_genes | KG 查询(基因→细胞类型) | 3 | step3_kg.py | 2 | 4 |
| step3_kg.query_hierarchy | 本体层级查询 | 3 | step3_kg.py | 0 | 0 |
| step3_kg.aggregate_candidates | 候选聚合 | 3 | step3_kg.py | 5 | 7 |
| step3_kg.write_hits | 写出 KG 命中 | 3 | step3_kg.py | — | — |
| step4_judge.rank_candidates | 候选排名(first/second) | 4 判断 | step4_judge.py | 5 | 6 |
| step4_judge.write_annotations | 写出注释 | 4 | step4_judge.py | — | — |
| step5_refine.subcluster | 子聚类 | 5 细化 | step5_refine.py | 2 | 3 |
| step5_refine.subcluster_de | 子簇 DE | 5 | step5_refine.py | 0 | 0 |
| step5_refine.subcluster_kg | 子簇 KG 重查 | 5 | step5_refine.py | 4 | 1 |
| step5_refine.marker_overlap | 子簇间 marker 重叠 | 5 | step5_refine.py | 2 | 4 |
| step5_refine.type_membership | 类型归属检查 | 5 | step5_refine.py | 1 | 2 |
| step5_refine.unknown_overlap | Unknown 簇 marker 重叠 | 5 | step5_refine.py | 2 | 2 |
| step5_refine.write_refined | 写出细化注释 | 5 | step5_refine.py | — | — |
| step6_validate.marker_expression | Top marker 表达验证 | 6 验证 | step6_validate.py | 4 | 8 |
| step6_validate.violin_plot | 小提琴图生成 | 6 | step6_validate.py | 0 | 0 |
| step6_validate.global_summary | 全局汇总统计 | 6 | step6_validate.py | 3 | 8 |
| step6_validate.write_report | 报告生成 | 6 | step6_validate.py | — | — |
| step6_validate.write_final | 写出最终注释 | 6 | step6_validate.py | — | — |
| step7_diagnose.hit_rate | 每簇 KG 命中率 | 7 诊断 | step7_diagnose.py | 1 | 0 |
| step7_diagnose.candidate_count | 每簇候选计数 | 7 | step7_diagnose.py | 1 | 0 |
| step7_diagnose.first_second | 每簇 first/second | 7 | step7_diagnose.py | 2 | 0 |
| step7_diagnose.batch_entropy | 每簇批次熵 | 7 | step7_diagnose.py | 1 | 0 |
| step7_diagnose.metadata_check | 元数据完整性 | 7 | step7_diagnose.py | 1 | 0 |
| step7_diagnose.cross_cluster | 跨簇测量与报告 | 7 | step7_diagnose.py | 0 | 6 |

**汇总**: 当前已有指标 **55 个** | 候选新增指标 **146 个** | 完全空白操作 **11 个**

---

## 依赖关系图(DAG)

```
step1_prepare.load_data 加载原始数据
  │
  ├─→ step1_prepare.compute_qc 计算质控变量
  │     │
  │     ├─→ step1_prepare.qc_distribution 质控分布统计 ──→ step1_prepare.qc_plot 质控可视化
  │     │                              │
  │     │              (metrics 子命令到此为止)  │
  │     │                              │
  │     ├─→ step1_prepare.filter_cells 细胞过滤(多准则)    │
  │     │     ├─ min_genes             │
  │     │     ├─ max_mt_pct            │
  │     │     └─ max_cp_pct(植物)      │
  │     │                              │
  │     ├─→ step1_prepare.filter_genes 基因过滤(多准则)    │
  │     │     ├─ min_cells             │
  │     │     ├─ 移除 mt 基因           │
  │     │     └─ 移除 cp 基因(植物)    │
  │     │                              │
  │     └─→ step1_prepare.detect_doublets 双峰检测与移除       │
  │                                    │
  └────────────────────────────────────┘
                  │
                  ▼
           step1_prepare.normalize 归一化(counts→normalize_total→log1p→raw)
                  │
                  ▼
           step1_prepare.select_hvg HVG 选择与子集化
                  │
                  ▼
           step1_prepare.pca 缩放与 PCA
                  │
                  ▼
           step1_prepare.knn_graph kNN 邻域图构建
                  │
                  ▼
           step1_prepare.leiden_cluster Leiden 聚类(多分辨率) ◄── 候选: 轮廓系数 / 模块度 / WCSS
                  │
                  ▼
           step1_prepare.choose_resolution 分辨率选择 ◄── 候选: ARI / NMI / 稳定性
                  │
                  ▼
           step1_prepare.umap UMAP 嵌入 ◄── 候选: trustworthiness
                  │
                  ▼
           step1_prepare.batch_mixing 批次混合评估
                  │
                  ▼
           step1_prepare.write_output 写出 processed.h5ad + qc_metrics.json
                  │
    ════════════════════════════════════════════
                  │
                  ▼
           step2_markers.de_rank DE 排序(Wilcoxon) ◄── 候选: BH-FDR / AUC / inflation λ
                  │
                  ▼
           step2_markers.pct1_pct2 pct1/pct2 计算
                  │
                  ▼
           step2_markers.filter_markers Marker 过滤 ◄── 候选: 过滤漏斗
                  │                  ▲
                  │     step2_markers.pseudobulk_de 稀有簇 Pseudobulk DE(条件触发)
                  │                  │
                  ▼                  │
           step2_markers.write_markers 写出 markers.csv + markers.json
                  │
    ════════════════════════════════════════════
                  │
                  ▼
           step3_kg.connect KG 连接与来源
                  │
                  ▼
           step3_kg.query_genes KG 查询(基因→细胞类型) ◄── step3_kg.query_hierarchy 本体层级查询(并行)
                  │
                  ▼
           step3_kg.aggregate_candidates 候选聚合(per cluster, rank by count + confidence)
                  │
                  ▼
           step3_kg.write_hits 写出 kg_hits.json
                  │
    ════════════════════════════════════════════
                  │
                  ▼
           step4_judge.rank_candidates 候选排名(first/second)
                  │
                  ▼
           step4_judge.write_annotations 写出 annotations.json
                  │
    ════════════════════════════════════════════
                  │
          ┌───────┴───────┐
          │ first>second   │ first<=second
          │ (decisive)     │
          │               ▼
          │        step5_refine.subcluster 子聚类(subset+PCA+neighbors+Leiden)
          │               │
          │               ▼
          │        step5_refine.subcluster_de 子簇 DE(within parent scope)
          │               │
          │               ▼
          │        step5_refine.subcluster_kg 子簇 KG 重查
          │               │
          │               ▼
          │        step5_refine.marker_overlap 子簇间 marker 重叠
          │               │
          │               ▼
          │        step5_refine.type_membership 类型归属检查
          │               │
          └───────┬───────┘
                  │
          step5_refine.unknown_overlap Unknown 簇 marker 重叠(no-candidate 簇)
                  │
                  ▼
           step5_refine.write_refined 写出 refined_annotations.json
                  │
    ════════════════════════════════════════════
                  │
                  ▼
           step6_validate.marker_expression Top marker 表达验证(pct1/pct2/mean) ◄── 候选: Cohen's d / AUC
                  │
                  ├─→ step6_validate.violin_plot 小提琴图生成(人类用)
                  │
                  ▼
           step6_validate.global_summary 全局汇总统计
                  │
                  ▼
           step6_validate.write_report 报告生成(report.md)
                  │
                  ▼
           step6_validate.write_final 写出 final_annotations.json
                  │
    ════════════════════════════════════════════
                  │
                  ▼
           step7_diagnose.hit_rate~45 每簇诊断(命中率/候选数/first-second/批次熵/元数据)
                  │
                  ▼
           step7_diagnose.cross_cluster 跨簇测量与报告
```

---

## 详细操作定义

### 阶段 1: 数据准备

---

#### step1_prepare.load_data　加载原始数据

| 属性 | 值 |
|---|---|
| **描述** | 从磁盘读取 h5ad 文件到内存 AnnData 对象 |
| **输入** | `--input data.h5ad` |
| **输出** | 内存 AnnData (n_cells × n_genes, raw counts) |
| **当前实现** | `step1_prepare.py:cmd_run:138` `ad.read_h5ad(args.input)` |
| **当前指标** | n_cells, n_genes(仅 log) |
| **候选指标** | (无) |
| **依赖** | (无) |

---

#### step1_prepare.compute_qc　计算质控变量

| 属性 | 值 |
|---|---|
| **描述** | 对每个细胞计算:检测到的基因数(n_genes_by_counts)、总 UMI 数(total_counts)、某类基因占比如质体(pct_mt)、质体基因组(pct_cp,植物)。标记 mt/cp 基因到 var 列 |
| **输入** | 原始 AnnData |
| **输出** | AnnData.obs 新增列: n_genes_by_counts, total_counts, pct_counts_mt, pct_counts_chloroplast; AnnData.var 新增列: mt, chloroplast |
| **当前实现** | `step1_prepare.py:compute_qc_metrics:77-86`; `_mt_genes:28`, `_chloroplast_genes:33` |
| **当前指标** | median_genes_per_cell, median_counts_per_cell, pct_mt_median, pct_mt_p90, pct_mt_p99 |
| **候选指标** | 见 catalog step1_prepare.compute_qc: mean/std, IQR, CV, skewness, kurtosis, bimodality_coefficient, Gini |
| **依赖** | step1_prepare.load_data |

---

#### step1_prepare.qc_distribution　质控分布统计

| 属性 | 值 |
|---|---|
| **描述** | 对每个质控变量计算分布统计量:分位数(p1~p99)、直方图(20-bin),用于替代肉眼看图判断分布形状(长尾、双峰、偏态) |
| **输入** | AnnData.obs 中的质控列 |
| **输出** | dict: {min, max, median, percentiles, histogram} per QC var |
| **当前实现** | `step1_prepare.py:_distribution_stats:41-62`; `_qc_distributions:65-74` |
| **当前指标** | percentiles (9个), histogram (20-bin), min/max/median |
| **候选指标** | 见 catalog step1_prepare.compute_qc: mean, std, IQR, CV, skewness, kurtosis, bimodality_coefficient |
| **依赖** | step1_prepare.compute_qc |
| **备注** | `metrics` 子命令输出为 `distributions`; `run` 子命令输出为 `pre_filter_distributions`(过滤前的分布) |

---

#### step1_prepare.qc_plot　质控可视化

| 属性 | 值 |
|---|---|
| **描述** | 生成质控变量直方图 PNG,供人类查看。LLM 应读 step1_prepare.qc_distribution 的 JSON 分布数据 |
| **输入** | AnnData.obs 质控列 |
| **输出** | `step1_prepare/qc_distributions.png` |
| **当前实现** | `step1_prepare.py:plot_qc:89-105` |
| **当前指标** | (无,这是可视化操作) |
| **候选指标** | (无) |
| **依赖** | step1_prepare.compute_qc |

---

#### step1_prepare.filter_cells　细胞过滤(多准则)

| 属性 | 值 |
|---|---|
| **描述** | 按多个阈值过滤细胞:(1) min_genes — 检测基因数不足;(2) max_mt_pct — 质体基因占比过高(破损/死细胞信号);(3) max_chloroplast_pct — 质体基因组占比过高(植物) |
| **输入** | AnnData + 阈值参数 (min_genes, max_mt_pct, max_chloroplast_pct) |
| **输出** | 过滤后的 AnnData (细胞数减少) |
| **当前实现** | `step1_prepare.py:cmd_run:147` (filter_cells), `:156-157` (max_mt_pct), `:158-159` (max_cp_pct) |
| **当前指标** | n_cells_before, n_cells_after, frac_cells_lost |
| **候选指标** | 见 catalog step1_prepare.filter_cells: n_lost_per_criterion, n_lost_multiple_criteria, frac_lost_per_criterion, Δmedian, ΔIQR, KS statistic |
| **依赖** | step1_prepare.compute_qc |
| **备注** | 当前三条过滤规则串联执行,无法知道每条各去掉多少细胞。需要改为分步执行 + 记录漏斗 |

---

#### step1_prepare.filter_genes　基因过滤(多准则)

| 属性 | 值 |
|---|---|
| **描述** | 按多个准则过滤基因:(1) min_cells — 表达细胞数不足的基因;(2) 移除质体基因(var 层);(3) 移除质体基因组基因(var 层,植物) |
| **输入** | AnnData + 阈值参数 (min_cells) + var 标记 (mt, chloroplast) |
| **输出** | 过滤后的 AnnData (基因数减少) |
| **当前实现** | `step1_prepare.py:cmd_run:148` (filter_genes), `:149-151` (mt removal), `:152-155` (cp removal) |
| **当前指标** | n_genes_after (HVG 后的数字,不独立) |
| **候选指标** | 见 catalog step1_prepare.filter_genes: n_genes_before, n_genes_after_gene_filter, frac_genes_lost, expression_breadth_distribution, n_genes_in_<1%_cells |
| **依赖** | step1_prepare.compute_qc |
| **备注** | 当前完全不输出基因过滤的独立指标。n_genes_after 实际是 HVG 后的数量,不是过滤后的 |

---

#### step1_prepare.detect_doublets　双峰检测与移除

| 属性 | 值 |
|---|---|
| **描述** | 用 scrublet 检测双峰(doublet)细胞——两个细胞被封装进同一个液滴产生的假细胞。计算 doublet score,标记预测双峰,然后移除 |
| **输入** | 过滤后的 AnnData + expected_doublet_rate |
| **输出** | AnnData (移除双峰后) + adata.obs["doublet_score"], adata.obs["predicted_doublet"] |
| **当前实现** | `step1_prepare.py:cmd_run:163-170` `sc.external.pp.scrublet` + filter |
| **当前指标** | (仅 log 输出 n doublets removed,**未写入 JSON!**) |
| **候选指标** | 见 catalog step1_prepare.detect_doublets: doublet_score_distribution, n_doublets_detected, frac_doublets, doublet_score_bimodality, implied_threshold |
| **依赖** | step1_prepare.filter_cells, step1_prepare.filter_genes |

---

#### step1_prepare.normalize　归一化

| 属性 | 值 |
|---|---|
| **描述** | (1) 保存原始 counts 到 layer; (2) total-count 归一化(每个细胞的 UMI 缩放到 target_sum=1e4); (3) log1p 变换; (4) 将归一化后的数据存为 adata.raw(供后续 DE 使用) |
| **输入** | 过滤+去双峰后的 AnnData (raw counts in .X) |
| **输出** | AnnData.layers["counts"] = raw counts; AnnData.X = normalized+log; AnnData.raw = normalized+log copy |
| **当前实现** | `step1_prepare.py:cmd_run:172-175` |
| **当前指标** | (无!) |
| **候选指标** | 见 catalog step1_prepare.normalize: median_library_size_before, normalization_target, post_norm_mean_expression_distribution, frac_zero_after_norm |
| **依赖** | step1_prepare.detect_doublets |

---

#### step1_prepare.select_hvg　HVG 选择与子集化

| 属性 | 值 |
|---|---|
| **描述** | 用 Seurat flavor 选择高变基因(HVG),默认 n_top=2000。可选 batch_key 分批选(防止批次差异基因占据 HVG)。然后子集化到仅 HVG |
| **输入** | 归一化后的 AnnData + n_top_genes + batch_key |
| **输出** | AnnData (仅 HVG 基因) + adata.var["highly_variable"] |
| **当前实现** | `step1_prepare.py:cmd_run:178-180` `sc.pp.highly_variable_genes` + subset |
| **当前指标** | n_genes_after (= n_hvg, 但不标注这是 HVG 数) |
| **候选指标** | 见 catalog step1_prepare.select_hvg: n_genes_before_hvg, frac_hvg_of_total, hvg_dispersion_distribution, hvg_mean_expression_distribution, hvg_nongvg_dispersion_gap, n_batch_specific_hvg |
| **依赖** | step1_prepare.normalize |

---

#### step1_prepare.pca　缩放与 PCA

| 属性 | 值 |
|---|---|
| **描述** | (1) 对 HVG 子集做 z-score 缩放(clip max_value=10); (2) PCA 降维(n_comps=50),将细胞从 HVG 空间投影到 50 维 PC 空间 |
| **输入** | HVG 子集 AnnData |
| **输出** | AnnData.X = scaled; AnnData.obsm["X_pca"] (n_cells × 50) |
| **当前实现** | `step1_prepare.py:cmd_run:182-183` `sc.pp.scale` + `sc.pp.pca` |
| **当前指标** | (无!) |
| **候选指标** | 见 catalog step1_prepare.pca: variance_explained_per_pc, cumulative_variance_explained, n_pcs_for_50/80/90pct, variance_explained_knee, PC1_PC2_loading_top_genes |
| **依赖** | step1_prepare.select_hvg |
| **备注** | PCA variance explained 是选择 n_pcs 的关键依据,当前完全没有输出 |

---

#### step1_prepare.knn_graph　kNN 邻域图构建

| 属性 | 值 |
|---|---|
| **描述** | 基于 PCA 空间构建 k 近邻图(k=15, 用前 30 个 PC)。每个细胞连接到最近的 15 个邻居,形成图结构供 Leiden 使用 |
| **输入** | AnnData.obsm["X_pca"] + n_neighbors + n_pcs |
| **输出** | AnnData.obsp["connectivities"], AnnData.obsp["distances"] (kNN 图) |
| **当前实现** | `step1_prepare.py:cmd_run:184` `sc.pp.neighbors` |
| **当前指标** | (无!) |
| **候选指标** | 见 catalog step1_prepare.knn_graph: n_connected_components, graph_density, mean/median_degree, degree_distribution, frac_isolated_nodes, avg_clustering_coefficient |
| **依赖** | step1_prepare.pca |

---

#### step1_prepare.leiden_cluster　Leiden 聚类(多分辨率)

| 属性 | 值 |
|---|---|
| **描述** | 在 kNN 图上运行 Leiden 社区检测,在多个分辨率(如 0.4, 0.6, 0.8, 1.0, 1.2)下各跑一次,记录每个分辨率下得到的簇数。社区检测:将图划分为使模块度最大化的子图 |
| **输入** | kNN 图 + resolution_list |
| **输出** | AnnData.obs["leiden_{r}"] per resolution; cluster_counts dict |
| **当前实现** | `step1_prepare.py:cmd_run:186-191` `sc.tl.leiden` loop |
| **当前指标** | resolution_cluster_counts |
| **候选指标** | 见 catalog step1_prepare.leiden_cluster: modularity_score, cluster_size_distribution, frac_largest/smallest_cluster, n_rare/singleton_clusters, silhouette per cell/cluster/overall, n_clusters_negative_silhouette, Davies-Bouldin, Calinski-Harabasz, WCSS, BCSS, WCSS/BCSS ratio |
| **依赖** | step1_prepare.knn_graph |
| **备注** | 这是整个 pipeline 最重要的操作——聚类质量直接决定下游一切。当前几乎零指标输出 |

---

#### step1_prepare.choose_resolution　分辨率选择

| 属性 | 值 |
|---|---|
| **描述** | 从多个分辨率的聚类结果中选择一个作为最终标签。用户通过 `--target-resolution` 指定。将选中分辨率的 leiden 列复制为统一的 "leiden" 列 |
| **输入** | 多分辨率聚类结果 + target_resolution |
| **输出** | AnnData.obs["leiden"] (最终簇标签) |
| **当前实现** | `step1_prepare.py:cmd_run:193-203` |
| **当前指标** | resolution_chosen, resolution_cluster_counts |
| **候选指标** | 见 catalog step1_prepare.choose_resolution: n_clusters_derivative, resolution_knee, adjacent_resolution_ARI, adjacent_resolution_NMI, cluster_persistence, cluster_merge_split_tree, n_stable_clusters, stability_at_chosen_resolution |
| **依赖** | step1_prepare.leiden_cluster |
| **备注** | 当前要求用户手动指定 --target-resolution。分辨率稳定性(ARI)能帮 LLM 判断选择是否合理 |

---

#### step1_prepare.umap　UMAP 嵌入

| 属性 | 值 |
|---|---|
| **描述** | 将高维(PCA 空间)数据通过 UMAP 算法降到 2 维,生成 UMAP1/UMAP2 坐标供可视化 |
| **输入** | kNN 图 (AnnData) |
| **输出** | AnnData.obsm["X_umap"] (n_cells × 2) |
| **当前实现** | `step1_prepare.py:cmd_run:204` `sc.tl.umap` |
| **当前指标** | (无!) |
| **候选指标** | 见 catalog step1_prepare.umap: trustworthiness, continuity, mean_intra/inter_cluster_distance_umap, umap_separation_ratio, n_overlapping_clusters_umap, umap_coordinate_range |
| **依赖** | step1_prepare.choose_resolution (需要最终 leiden 标签来计算簇间/簇内距离) |

---

#### step1_prepare.batch_mixing　批次混合评估

| 属性 | 值 |
|---|---|
| **描述** | 检查每个簇包含哪些批次/样本。如果某簇几乎全是单一批次,可能是批次效应而非真实细胞类型 |
| **输入** | 最终 leiden 标签 + batch_key 列 |
| **输出** | per_cluster_batch_nunique dict |
| **当前实现** | `step1_prepare.py:cmd_run:209-212` |
| **当前指标** | per_cluster_batch_nunique |
| **候选指标** | 见 catalog step1_prepare.batch_mixing: per_cluster_batch_entropy, per_cluster_max_batch_fraction, batch_cluster_chi_square, overall_batch_mixing_index |
| **依赖** | step1_prepare.choose_resolution |

---

#### step1_prepare.write_output　写出处理后数据与指标

| 属性 | 值 |
|---|---|
| **描述** | 将处理后的 AnnData 写入 h5ad;将 QC 指标汇总写入 JSON |
| **输入** | 最终 AnnData + QC 指标 dict |
| **输出** | `step1_prepare/processed.h5ad`, `step1_prepare/qc_metrics.json` |
| **当前实现** | `step1_prepare.py:cmd_run:206-238` |
| **当前指标** | (汇总 step1_prepare.compute_qc~15 的所有指标) |
| **候选指标** | (各操作的候选指标在此汇总写出) |
| **依赖** | step1_prepare.compute_qc~15 全部 |

---

### 阶段 2: Marker 发现阶段

---

#### step2_markers.de_rank　DE 排序

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇 vs 其余细胞做差异表达检验(Wilcoxon 秩和检验),按显著性排序基因。返回每簇 top-500 基因的 names, logfc, pval |
| **输入** | processed.h5ad (含 leiden 标签 + adata.raw) |
| **输出** | rank_genes_groups 结果 (每簇 top-500: names, logfc, pval) |
| **当前实现** | `step2_markers.py:cmd_run:105-106` `sc.tl.rank_genes_groups(method="wilcoxon")` |
| **当前指标** | logfc, pval (per gene) |
| **候选指标** | 见 catalog step2_markers.de_rank: n_genes_tested, BH_adjusted_pval, AUC, logfc_distribution, pval_distribution, genomic_inflation_factor_lambda, n_significant_at_FDR_thresholds, top_marker_logfc_gap, frac_positive_logfc |
| **依赖** | step1_prepare.write_output |
| **备注** | 当前 pval 未做多重检验校正(BH-FDR),高基因数下假阳性率很高 |

---

#### step2_markers.pct1_pct2　pct1/pct2 计算

| 属性 | 值 |
|---|---|
| **描述** | 对每个 DE 候选基因计算:pct1 = 该基因在本簇表达(>0)的细胞比例;pct2 = 该基因在其他簇表达的比例。两者差值衡量特异性 |
| **输入** | rank_genes_groups 结果 + raw counts + leiden 标签 |
| **输出** | 每基因的 pct1, pct2, pct1_minus_pct2 |
| **当前实现** | `step2_markers.py:_pct1_pct2:23-42` |
| **当前指标** | pct1, pct2, pct1_minus_pct2 (per gene) |
| **候选指标** | 见 catalog step2_markers.pct1_pct2: pct1/pct2/specificity_distribution per cluster, mean_specificity_of_top_N |
| **依赖** | step2_markers.de_rank |

---

#### step2_markers.filter_markers　Marker 过滤

| 属性 | 值 |
|---|---|
| **描述** | 按多重准则过滤 DE 结果:(1) pct1 >= min_pct1 (0.5); (2) pct1 <= max_pct1 (0.9); (3) pct1-pct2 >= min_pct1_pct2 (0.25)。灰区(0.1 <= pct1-pct2 < 0.25)被记录但不保留。最终取 top-N |
| **输入** | DE 表 + pct1/pct2 + 过滤参数 |
| **输出** | 每簇 filtered marker 列表 + n_markers + n_grey_zone |
| **当前实现** | `step2_markers.py:cmd_run:126-133` |
| **当前指标** | n_markers, n_grey_zone |
| **候选指标** | 见 catalog step2_markers.filter_markers: n_before_filter, filter_funnel (n_pass_each_criterion), filter_efficiency, grey_zone_rate |
| **依赖** | step2_markers.pct1_pct2 |

---

#### step2_markers.pseudobulk_de　稀有簇 Pseudobulk DE

| 属性 | 值 |
|---|---|
| **描述** | 对稀有簇(<rare_threshold 的细胞比例),按样本聚合 counts 后做 t-test DE。条件触发:仅当 `--use-pseudobulk-for-rare` 且簇为稀有 |
| **输入** | AnnData + cluster_id + sample 列 |
| **输出** | pseudobulk DE 表 (gene, logfc, pval) → 替换 Wilcoxon 结果 |
| **当前实现** | `step2_markers.py:_pseudobulk_de:45-71`; `_rank_de:74-91` |
| **当前指标** | is_rare, de_method |
| **候选指标** | 见 catalog step2_markers.pseudobulk_de: n_pseudobulk_samples, pseudobulk_wilcoxon_marker_overlap |
| **依赖** | step2_markers.filter_markers (条件触发) |

---

#### step2_markers.write_markers　写出 markers

| 属性 | 值 |
|---|---|
| **描述** | 将所有簇的 marker 表写出 CSV + 按簇组织的 JSON |
| **输入** | 每簇 filtered markers + thresholds + 指标 |
| **输出** | `step2_markers/markers.csv`, `step2_markers/markers.json` |
| **当前实现** | `step2_markers.py:cmd_run:169-192` |
| **当前指标** | (汇总 step2_markers.de_rank~20) |
| **候选指标** | (各操作候选在此汇总) |
| **依赖** | step2_markers.de_rank~20 |

---

### 阶段 3: 知识图谱查询

---

#### step3_kg.connect　KG 连接与来源

| 属性 | 值 |
|---|---|
| **描述** | 连接到 Neo4j 数据库(或加载本地 JSON 文件)。采样 KG 的来源信息:datasets, species, species_types, marker_resources |
| **输入** | NEO4J_URI/USER/PASSWORD 或 --kg-file |
| **输出** | driver 对象 + kg_provenance dict + kg_source, kg_version, kg_date |
| **当前实现** | `step3_kg.py:_connect_neo4j:101-112`; `_kg_provenance:145-167` |
| **当前指标** | kg_source, kg_version, kg_provenance (datasets/species/species_types/marker_resources) |
| **候选指标** | (无) |
| **依赖** | step2_markers.write_markers (需要 markers.json) |

---

#### step3_kg.query_genes　KG 查询(基因→细胞类型)

| 属性 | 值 |
|---|---|
| **描述** | 收集所有簇的所有 marker 基因,批量查 KG:对每个基因返回其关联的细胞类型(本体术语)、置信度、来源。过滤:organ, species, species_type, min_confidence |
| **输入** | marker 基因列表 + KgQueryConfig (organ, gene_key, species, species_type, min_confidence, strict_organ) |
| **输出** | gene_to_cts: {gene: [{cell_type, organ, ontology_id, species_type, ontology_type, confidence, source}]} |
| **当前实现** | `step3_kg.py:_query_neo4j:115-142` (Cypher); `_query_local:176-197` |
| **当前指标** | overall_hit_rate, n_markers_hit per cluster |
| **候选指标** | 见 catalog step3_kg.query_genes: n_unique_genes_queried, n_genes_with/without_hits, mapping_multiplicity_per_gene, genes_with_no_kg_entry, mean_candidates_per_gene |
| **依赖** | step2_markers.write_markers, step3_kg.connect |

---

#### step3_kg.query_hierarchy　本体层级查询

| 属性 | 值 |
|---|---|
| **描述** | 对每个命中的细胞类型,查询其在 KG 本体中的祖先节点(ontology_relation, 0~3 跳)。用于判断两个候选是否是父子关系 |
| **输入** | 命中的细胞类型名称 |
| **输出** | ancestors: {cell_type: [ancestor_name, ...]} |
| **当前实现** | `step3_kg.py:_query_neo4j:139-141` (NEO4J_HIERARCHY_QUERY) |
| **当前指标** | (无,数据存入 ancestors map 但未写入输出 JSON) |
| **候选指标** | (无独立指标,但 ancestors 数据应写入输出供 step4_judge.rank_candidates 使用) |
| **依赖** | step3_kg.query_genes |
| **备注** | 当前 ancestors 数据被收集但未写入 kg_hits.json。step4_judge.rank_candidates 的 ancestor_overlap 候选指标依赖此数据 |

---

#### step3_kg.aggregate_candidates　候选聚合

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇:聚合 gene→cell_type 映射,按 cell_type 分组,计算每个候选的 supporting_markers, marker_count, mean_confidence, min_confidence, sources。按 marker_count → mean_confidence 降序排列 |
| **输入** | gene_to_cts + 每簇 marker 列表 |
| **输出** | per_cluster: {n_markers, n_markers_hit, candidates: [{cell_type, supporting_markers, marker_count, mean_confidence, min_confidence, sources}]} |
| **当前实现** | `step3_kg.py:cmd_query:309-337` |
| **当前指标** | candidates: cell_type, supporting_markers, marker_count, mean_confidence, min_confidence, sources |
| **候选指标** | 见 catalog step3_kg.aggregate_candidates: n_candidates, marker_coverage, candidate_count_distribution, candidate_ranking_entropy, n_tied_at_top, confidence_distribution, n_unique_cell_types_across_clusters |
| **依赖** | step3_kg.query_genes |

---

#### step3_kg.write_hits　写出 KG 命中

| 属性 | 值 |
|---|---|
| **描述** | 将 KG 查询结果 + 候选 + 来源 + 查询配置写出 JSON |
| **输入** | per_cluster 候选 + kg_source + query_config |
| **输出** | `step3_kg/kg_hits.json`, `step3_kg/kg_source.txt` |
| **当前实现** | `step3_kg.py:cmd_query:339-356` |
| **当前指标** | (汇总 step3_kg.connect~25) |
| **候选指标** | (各操作候选在此汇总) |
| **依赖** | step3_kg.connect~25 |

---

### 阶段 4: 簇判断

---

#### step4_judge.rank_candidates　候选排名(first/second)

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇:取排序后第一候选(first)和第二候选(second),报告 first/second 的 cell_type, count, mean_confidence, supporting_markers。无候选的簇标记 None |
| **输入** | kg_hits.json |
| **输出** | per cluster: {first_candidate, second_candidate, first_count, second_count, first/second_mean_confidence, first/second_supporting_markers} |
| **当前实现** | `step4_judge.py:cmd_run:24-57` |
| **当前指标** | first/second candidate, count, mean_confidence, supporting_markers |
| **候选指标** | 见 catalog step4_judge.rank_candidates: count_ratio, count_diff, confidence_diff, n_tied_at_first, frac_support_captured_by_first, first_second_ancestor_overlap |
| **依赖** | step3_kg.write_hits |

---

#### step4_judge.write_annotations　写出注释

| 属性 | 值 |
|---|---|
| **描述** | 将 first/second 候选写出 JSON |
| **输入** | 每簇 first/second 候选 |
| **输出** | `step4_judge/annotations.json` |
| **当前实现** | `step4_judge.py:cmd_run:59-69` |
| **当前指标** | (汇总 step4_judge.rank_candidates) |
| **候选指标** | (同上) |
| **依赖** | step4_judge.rank_candidates |

---

### 阶段 5: 细化

---

#### step5_refine.subcluster　子聚类

| 属性 | 值 |
|---|---|
| **描述** | 对 first<=second 的簇:子集化细胞 → 设置 sub.raw → PCA → neighbors → Leiden(resolution=subcluster_resolution)。产生子簇标签 |
| **输入** | processed.h5ad + parent cluster_id + subcluster_resolution + min_cells |
| **输出** | sub AnnData with sub_leiden labels; outcome (analyzed/skipped); n_subclusters |
| **当前实现** | `step5_refine.py:_refine_one_cluster:34-52` |
| **当前指标** | n_subclusters, outcome (analyzed/skipped) |
| **候选指标** | 见 catalog step5_refine.subcluster: sub_cluster_size_distribution, sub_silhouette, frac_smallest_subcluster |
| **依赖** | step4_judge.write_annotations |
| **备注** | 触发条件:first_count <= second_count(定义性,非阈值) |

---

#### step5_refine.subcluster_de　子簇 DE

| 属性 | 值 |
|---|---|
| **描述** | 在子簇间做 Wilcoxon DE(子簇 vs 同一父簇内的其他子簇),对每个子簇取 top-50 基因。按 pct1 ∈ [0.5,0.9] + pct1-pct2 >= 0.25 过滤,保留 top-10 |
| **输入** | sub AnnData + sub_leiden 标签 + raw counts |
| **输出** | sub_markers: {sub_cluster_id: [gene, ...]} |
| **当前实现** | `step5_refine.py:_refine_one_cluster:54-88` |
| **当前指标** | (无独立输出,markers 嵌入 sub_results) |
| **候选指标** | (同 step2_markers.de_rank/19 的候选) |
| **依赖** | step5_refine.subcluster |

---

#### step5_refine.subcluster_kg　子簇 KG 重查

| 属性 | 值 |
|---|---|
| **描述** | 对每个子簇的 marker 基因查 KG(复用 step3_kg.query_genes 的 gene_to_cts 缓存),聚合为候选,取 first/second |
| **输入** | sub_markers + gene_to_cts (from step3_kg.query_genes) |
| **输出** | sub_results: {sub_cluster_id: {first_candidate, second_candidate, first_count, second_count, markers}} |
| **当前实现** | `step5_refine.py:_query_subclusters:104-133` |
| **当前指标** | first/second candidate + count per sub-cluster |
| **候选指标** | 见 catalog step5_refine.subcluster_de: n_subclusters_with_distinct_type, sub_count_ratio |
| **依赖** | step5_refine.subcluster_de, step3_kg.query_genes |

---

#### step5_refine.marker_overlap　子簇间 marker 重叠

| 属性 | 值 |
|---|---|
| **描述** | 计算子簇间 marker 集合的两两重叠率。重叠 = 交集 / min(两集合大小)。高重叠=子簇没分开 |
| **输入** | sub_markers dict |
| **输出** | {max_overlap, pairs: [{pair, overlap}]} |
| **当前实现** | `step5_refine.py:_marker_overlap:136-147` |
| **当前指标** | max_overlap, pairs (overlap/min) |
| **候选指标** | 见 catalog step5_refine.marker_overlap: mean_overlap, Jaccard_index, frac_unique_markers, overlap_matrix |
| **依赖** | step5_refine.subcluster_de |

---

#### step5_refine.type_membership　类型归属检查

| 属性 | 值 |
|---|---|
| **描述** | 检查每个子簇的第一候选类型是否在父簇的候选类型范围内。True=细化有效(子类型在预期范围内);False=子簇产生了意外类型(可能是假分裂) |
| **输入** | sub_results + parent_candidates |
| **输出** | types_in_parent_candidates: {sub_cluster_id: bool} |
| **当前实现** | `step5_refine.py:_refine_one_cluster:92-93` |
| **当前指标** | types_in_parent_candidates (boolean per sub-cluster) |
| **候选指标** | 见 catalog step5_refine.type_membership: n_in_range, n_out_of_range, frac_novel_types |
| **依赖** | step5_refine.subcluster_kg |

---

#### step5_refine.unknown_overlap　Unknown 簇 marker 重叠

| 属性 | 值 |
|---|---|
| **描述** | 对无 KG 命中的簇,计算两两 marker 重叠。高重叠=可能是同一种未知类型;低重叠=各自独立的新类型 |
| **输入** | annotations (first_candidate=None 的簇) + markers_per_cluster |
| **输出** | {_unknown_overlap_summary: {unknown_clusters, avg_overlap, pairs}} |
| **当前实现** | `step5_refine.py:_report_unknowns:157-176` |
| **当前指标** | avg_overlap, pairs (overlap/min) |
| **候选指标** | 见 catalog step5_refine.unknown_overlap: Jaccard per pair, n_unknown_clusters, frac_unknown |
| **依赖** | step4_judge.write_annotations, step2_markers.write_markers |

---

#### step5_refine.write_refined　写出细化注释

| 属性 | 值 |
|---|---|
| **描述** | 汇总所有簇的细化结果(decisive / analyzed / skipped / unknown)写出 JSON |
| **输入** | refined annotations + n_decisive/analyzed/skipped |
| **输出** | `step5_refine/refined_annotations.json` |
| **当前实现** | `step5_refine.py:cmd_run:238-249` |
| **当前指标** | n_decisive_top_candidate, n_analyzed, n_skipped |
| **候选指标** | (各操作候选在此汇总) |
| **依赖** | step5_refine.subcluster~34 |

---

### 阶段 6: 验证

---

#### step6_validate.marker_expression　Top marker 表达验证

| 属性 | 值 |
|---|---|
| **描述** | 对每簇 top-3 DE marker,计算簇内外表达统计:pct1(簇内表达比例), pct2(簇外表达比例), mean_expr(簇内平均表达), mean_expr_other(簇外平均表达) |
| **输入** | processed.h5ad + markers.json + cluster labels |
| **输出** | top_markers_expression: [{gene, pct1, pct2, mean_expr, mean_expr_other}] per cluster |
| **当前实现** | `step6_validate.py:_marker_expr:34-64` |
| **当前指标** | pct1, pct2, mean_expr, mean_expr_other (per top marker) |
| **候选指标** | 见 catalog step6_validate.marker_expression: specificity_index, effect_size (Cohen's d), fold_change, AUC, expression_ratio, mean_top3_pct1, mean_top3_specificity, frac_top3_pct1_above_0.5 |
| **依赖** | step5_refine.write_refined, step1_prepare.write_output |

---

#### step6_validate.violin_plot　小提琴图生成

| 属性 | 值 |
|---|---|
| **描述** | 对每簇 top-3 marker 生成小提琴图(violin plot)PNG,供人类查看。LLM 应读 step6_validate.marker_expression 的 JSON 数据 |
| **输入** | processed.h5ad + top markers + cluster labels |
| **输出** | `step6_validate/figures/cluster_{c}_markers.png` |
| **当前实现** | `step6_validate.py:_violin:67-89` |
| **当前指标** | (无,这是可视化操作) |
| **候选指标** | (无) |
| **依赖** | step6_validate.marker_expression |

---

#### step6_validate.global_summary　全局汇总统计

| 属性 | 值 |
|---|---|
| **描述** | 跨所有簇汇总注释结果:簇数, unknown 数, unknown 比例 |
| **输入** | 所有簇的最终注释 |
| **输出** | _summary: {n_clusters, n_unknown, unknown_rate} |
| **当前实现** | `step6_validate.py:cmd_run:143-147` |
| **当前指标** | n_clusters, n_unknown, unknown_rate |
| **候选指标** | 见 catalog step6_validate.global_summary: n_unique_labels, label_diversity, cell_type_proportions, effective_n_types, mean_first_second_gap, mean_top3_specificity, co_annotation_matrix, cross_cluster_marker_reuse |
| **依赖** | step6_validate.marker_expression |

---

#### step6_validate.write_report　报告生成

| 属性 | 值 |
|---|---|
| **描述** | 生成人类可读的 Markdown 报告:每簇一节,包含标签、first/second 候选+计数、top-3 marker 表达、refine 历史 |
| **输入** | final_annotations dict |
| **输出** | `step6_validate/report.md` |
| **当前实现** | `step6_validate.py:_write_report:154-185` |
| **当前指标** | (无,这是格式化操作) |
| **候选指标** | (无) |
| **依赖** | step6_validate.global_summary |

---

#### step6_validate.write_final　写出最终注释

| 属性 | 值 |
|---|---|
| **描述** | 将所有簇的最终注释 + 证据 + 元数据写出 JSON |
| **输入** | 每簇 final annotation + meta (kg_source, scanpy_version, date, thresholds) + summary |
| **输出** | `step6_validate/final_annotations.json` |
| **当前实现** | `step6_validate.py:cmd_run:113-151` |
| **当前指标** | (汇总 step6_validate.marker_expression~38) |
| **候选指标** | (同上) |
| **依赖** | step6_validate.marker_expression~38 |

---

### 阶段 7: 诊断

---

#### step7_diagnose.hit_rate　每簇 KG 命中率

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇计算:marker 基因在 KG 中有命中的比例 |
| **输入** | kg_hits.json |
| **输出** | per_cluster_hit_rate: {cluster_id: float} |
| **当前实现** | `step7_diagnose.py:cmd_run:44-49` |
| **当前指标** | per_cluster_hit_rate |
| **候选指标** | (无) |
| **依赖** | step6_validate.write_final |

---

#### step7_diagnose.candidate_count　每簇候选计数

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇计算:KG 返回的候选细胞类型数量 |
| **输入** | kg_hits.json |
| **输出** | per_cluster_candidate_count: {cluster_id: int} |
| **当前实现** | `step7_diagnose.py:cmd_run:44-49` |
| **当前指标** | per_cluster_candidate_count |
| **候选指标** | (无) |
| **依赖** | step6_validate.write_final |

---

#### step7_diagnose.first_second　每簇 first/second

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇报告 first_count, second_count, 以及 top_strictly_ahead 布尔值(first_count > second_count) |
| **输入** | annotations.json |
| **输出** | per_cluster_first_second: {cluster_id: {first_count, second_count, top_strictly_ahead}} |
| **当前实现** | `step7_diagnose.py:cmd_run:67-76` |
| **当前指标** | first_count, second_count, top_strictly_ahead |
| **候选指标** | (无) |
| **依赖** | step6_validate.write_final |

---

#### step7_diagnose.batch_entropy　每簇批次熵

| 属性 | 值 |
|---|---|
| **描述** | 对每个簇计算批次/样本的 Shannon 熵。熵→0=单批次主导;熵高=批次混合好 |
| **输入** | processed.h5ad + leiden 标签 + batch 列 |
| **输出** | per_cluster_batch_entropy: {cluster_id: float} |
| **当前实现** | `step7_diagnose.py:_entropy:21-27`; `cmd_run:52-62` |
| **当前指标** | per_cluster_batch_entropy, batch_key_used |
| **候选指标** | (无,但可加 chi_square) |
| **依赖** | step6_validate.write_final |

---

#### step7_diagnose.metadata_check　元数据完整性

| 属性 | 值 |
|---|---|
| **描述** | 检查 final_annotations.json 的 _meta 中哪些必需字段缺失(kg_source, kg_version, organ, annotation_date, scanpy_version) |
| **输入** | final_annotations.json _meta |
| **输出** | metadata_missing: [field_names] |
| **当前实现** | `step7_diagnose.py:cmd_run:64-65` |
| **当前指标** | metadata_missing |
| **候选指标** | (无) |
| **依赖** | step6_validate.write_final |

---

#### step7_diagnose.cross_cluster　跨簇测量与报告

| 属性 | 值 |
|---|---|
| **描述** | 汇总所有诊断指标,生成 step7_diagnose.json + report.md(表格形式) |
| **输入** | step7_diagnose.hit_rate~45 的全部结果 |
| **输出** | `step7_diagnose/step7_diagnose.json`, `step7_diagnose/report.md` |
| **当前实现** | `step7_diagnose.py:cmd_run:78-119` |
| **当前指标** | (汇总 step7_diagnose.hit_rate~45) |
| **候选指标** | 见 catalog step7_diagnose.cross_cluster: label_uniqueness, annotation_entropy, cluster_purity_proxy, mean_first_count_gap, cross_cluster_marker_overlap_matrix, batch_cluster_independence_chi_square |
| **依赖** | step7_diagnose.hit_rate~45 |

---

## 工具重构建议

### 当前结构(7 脚本,粒度过粗)

```
step1_prepare.py     → 16 个操作捆在一个 run 子命令里
step2_markers.py → 5 个操作
step3_kg.py → 5 个操作
step4_judge.py → 2 个操作
step5_refine.py → 7 个操作
step6_validate.py → 5 个操作
step7_diagnose.py → 6 个操作
```

### 建议结构(按操作粒度拆分子命令)

step1_prepare.py 拆为更细的子命令,每个操作可独立运行和检查:

```
step1_prepare.py metrics        → step1_prepare.load_data~04 (加载+QC+分布+图)
step1_prepare.py filter         → step1_prepare.filter_cells~07 (细胞过滤+基因过滤+双峰)
step1_prepare.py normalize      → step1_prepare.normalize (归一化)
step1_prepare.py hvg            → step1_prepare.select_hvg (HVG选择)
step1_prepare.py reduce         → step1_prepare.pca~11 (缩放+PCA+kNN图)
step1_prepare.py cluster        → step1_prepare.leiden_cluster~14 (Leiden+选分辨率+UMAP)
step1_prepare.py batch-check    → step1_prepare.batch_mixing (批次混合)
step1_prepare.py run            → step1_prepare.load_data~16 (全流程,串联以上)
step1_prepare.py recluster      → step1_prepare.leiden_cluster~14 (重聚类)

step2_markers.py run  → step2_markers.de_rank~21 (不变,但 step2_markers.de_rank 增加 BH-FDR/AUC)

step3_kg.py query    → step3_kg.connect~26 (不变,但 step3_kg.aggregate_candidates 增加 ranking_entropy)
step3_kg.py test-connection → step3_kg.connect (不变)

step4_judge.py run → step4_judge.rank_candidates~28 (不变,但 step4_judge.rank_candidates 增加 count_ratio/ancestor_overlap)

step5_refine.py run        → step5_refine.subcluster~35 (不变,但 step5_refine.marker_overlap 增加 Jaccard)

step6_validate.py run      → step6_validate.marker_expression~40 (不变,但 step6_validate.marker_expression 增加 Cohen's d/AUC)
step6_validate.py report   → step6_validate.write_report (不变)

step7_diagnose.py run        → step7_diagnose.hit_rate~46 (不变,但 step7_diagnose.cross_cluster 增加跨簇指标)
```

### 通用函数(在 common.py 中实现,跨操作复用)

| 函数 | 用于操作 | 说明 |
|---|---|---|
| `describe_distribution(values)` | step1_prepare.compute_qc, 03, 07, 08, 17, 18, 36 | 一次性输出 mean/std/IQR/CV/skew/kurt/bimodality/percentiles/histogram |
| `filter_funnel(masks, labels)` | step1_prepare.filter_cells, 06, 19 | 过滤漏斗:每步通过多少,最终保留多少 |
| `ranking_gap(ranked_values)` | step2_markers.de_rank, 25, 27 | top1-top2 gap, top1/total, ranking_entropy, n_ties |
| `pairwise_overlap(sets)` | step5_refine.marker_overlap, 34, 46 | mean_overlap, max_overlap, Jaccard, overlap_matrix, unique_frac |
| `batch_mixing(cluster_labels, batch_labels)` | step1_prepare.batch_mixing, 44, 46 | per_cluster_entropy, max_frac, chi_square, overall_index |
| `effect_size(group_in, group_out)` | step2_markers.de_rank, 36 | logfc, cohen_d, AUC, fold_change |
| `variance_explained(pca_obj)` | step1_prepare.pca | per_pc, cumulative, n_pcs_for_Xpct, knee |
| `cluster_quality(adata, labels)` | step1_prepare.leiden_cluster, 29 | silhouette per cell/cluster/overall, modularity, DB, CH, WCSS, BCSS |
| `resolution_stability(adata, res_list)` | step1_prepare.choose_resolution | ARI/NMI adjacent, cluster_persistence, merge_split_tree |
