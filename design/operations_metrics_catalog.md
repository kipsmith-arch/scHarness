# 操作-指标目录

本文件将 pipeline 从生物学背景中抽离,拆成**原子操作**,为每个操作列出**可描述其结果的全部统计学指标**。

- `[核心]` = 基础必备(无★)或高优先级(★★★)指标,第一批实现
- `[扩展]` = 有价值(★★)或诊断参考(★)指标,后续迭代
- ★★★ = 对 LLM 判断价值最高
- ★★ = 有价值
- ★ = 诊断参考
- 无★ = 基础测量值(n_cells、pct1、logfc 等),pipeline 必须输出

**说明**:本目录只列出有候选指标的操作。以下操作因无独立统计指标(纯 I/O 或格式化操作)而省略:
`step1_prepare.load_data`(加载)、`step1_prepare.qc_plot`(可视化)、`step1_prepare.write_output`(写出)、`step2_markers.write_markers`、`step3_kg.connect`、`step3_kg.query_hierarchy`、`step3_kg.write_hits`、`step4_judge.write_annotations`、`step5_refine.subcluster_kg`(复用 query_genes 逻辑)、`step5_refine.write_refined`、`step6_validate.violin_plot`、`step6_validate.write_final`、`step6_validate.write_report`。

---

## 1. 数据准备阶段(全部在 Step 1 内)

### step1_prepare.compute_qc　质控变量计算

**操作**:对每个细胞计算 n_genes(检测到的基因数)、total_counts(总UMI)、pct_mt(线粒体占比)、pct_cp(叶绿体占比,植物)。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | percentiles (p1,p5,p10,p25,p50,p75,p90,p95,p99) | 分位数 | — |
| `[核心]` | histogram (20-bin) | 直方图 | — |
| `[核心]` | min, max, median | 基本位置 | — |
| `[扩展]` | mean, std | 位置与离散度 | ★★ |
| `[扩展]` | IQR = p75 - p25 | 稳健离散度 | ★★ |
| `[扩展]` | CV = std / mean | 相对离散度,可跨数据集比较 | ★★ |
| `[核心]` | skewness | 偏态(>0=右偏长尾,<0=左偏截断) | ★★★ |
| `[扩展]` | kurtosis (excess) | 尖峰(>0)或厚尾(<0) | ★★ |
| `[核心]` | bimodality_coefficient = (skew² + 1) / kurt | 双峰系数,>0.555 提示双峰 | ★★★ |
| `[扩展]` | Gini coefficient (total_counts) | 测序深度不均匀度(0=均匀,1=极不均) | ★ |

### step1_prepare.qc_distribution　质控分布统计

**操作**:对每个质控变量计算分布统计量(分位数+直方图),用于替代肉眼看图判断分布形状。在 `metrics` 子命令中输出过滤前的分布,在 `run` 子命令中输出过滤前后的分布对比。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | pre_filter_distributions (percentiles + histogram per QC var) | 过滤前的分布统计 | — |
| `[扩展]` | post_filter_distributions | 过滤后的分布统计(对比用) | ★★ |
| `[扩展]` | distribution_diff (Δmedian, ΔIQR, Δskewness per QC var) | 过滤前后分布偏移 | ★★ |
| `[核心]` | bimodality_coefficient per QC var | 各 QC 变量的双峰系数 | ★★★ |
| `[扩展]` | tail_fraction (p99 - p90) | 尾部厚度(长尾=有少量异常细胞) | ★★ |
| `[扩展]` | valley_detection | 双峰分布的谷底位置(若存在) | ★★ |
| `[扩展]` | KS_statistic vs normal | 与正态分布的 KS 检验值 | ★ |

### step1_prepare.filter_cells　细胞过滤

**操作**:按 min_genes、max_mt_pct、max_chloroplast_pct 阈值过滤细胞。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_cells_before, n_cells_after | 过滤前后细胞数 | — |
| `[核心]` | frac_cells_lost | 过滤掉的总比例 | — |
| `[核心]` | n_lost_per_criterion | 各规则各去掉多少(min_genes / mt / cp / doublet) | ★★★ |
| `[扩展]` | n_lost_multiple_criteria | 同时违反多条规则的细胞数(交集) | ★★ |
| `[扩展]` | frac_lost_per_criterion | 每条规则占总过滤的比例 | ★★ |
| `[扩展]` | Δmedian (per QC var) | 过滤前后中位数偏移(过滤是否真改变了分布) | ★★ |
| `[扩展]` | ΔIQR (per QC var) | 过滤前后 IQR 变化 | ★★ |
| `[核心]` | KS statistic (per QC var) | 过滤前后分布的 KS 检验值(0=无变化,1=完全不同) | ★★★ |

### step1_prepare.filter_genes　基因过滤

**操作**:按 min_cells 阈值过滤基因(表达细胞数不足的剔除)。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_genes_before, n_genes_after, frac_genes_lost | 过滤前后基因数 | ★★★ |
| `[扩展]` | expression_breadth_distribution | 各基因的表达细胞比例分布(>0) | ★★ |
| `[扩展]` | n_genes_in_<1%_cells | 极低表达基因数 | ★ |

### step1_prepare.detect_doublets　双峰检测

**操作**:scrublet 检测双峰(doublet)细胞并移除。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | doublet_score_distribution (percentiles, mean, std) | doublet 分数分布 | ★★★ |
| `[核心]` | n_doublets_detected, frac_doublets | 检出并移除的数量与比例 | ★★★ |
| `[扩展]` | doublet_score_bimodality | 分数是否形成独立峰(双峰=有意义的分离) | ★★ |
| `[扩展]` | implied_threshold | scrublet 隐含的分数阈值 | ★★ |

### step1_prepare.normalize　归一化

**操作**:total-count 归一化(target_sum=1e4)→ log1p 变换。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[扩展]` | median_library_size_before | 归一化前的中位库大小 | ★★ |
| `[扩展]` | normalization_target | 归一化目标值(1e4) | ★ |
| `[扩展]` | post_norm_mean_expression_distribution | 归一化后表达均值分布(percentiles) | ★★ |
| `[扩展]` | frac_zero_after_norm | 归一化后仍为 0 的基因×细胞比例(稀疏度) | ★★ |

### step1_prepare.select_hvg　HVG 选择

**操作**:Seurat flavor 高变基因选择,默认 n_top_genes=2000,可选 batch_key 分批选。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_genes_after (= n_hvg) | 选出的 HVG 数 | — |
| `[扩展]` | n_genes_before_hvg, frac_hvg_of_total | HVG 占总基因比例 | ★★ |
| `[核心]` | hvg_dispersion_distribution (mean, median, percentiles) | 选出基因的离散度分布 | ★★★ |
| `[扩展]` | hvg_mean_expression_distribution | HVG 是高表达还是低表达 | ★★ |
| `[扩展]` | hvg_nongvg_dispersion_gap | top HVG 与非 HVG 的离散度落差(选择是否清晰) | ★★ |
| `[核心]` | n_batch_specific_hvg (if batch_key) | 多少 HVG 是批次特异的(批次污染检测) | ★★★ |

### step1_prepare.pca　PCA 降维

**操作**:在 HVG 上做 PCA,n_comps=50。后续 kNN 图用前 30 个 PC。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | variance_explained_per_pc (PC1~PC50) | 每个 PC 的方差解释量 | ★★★ |
| `[核心]` | cumulative_variance_explained | 累积方差解释 | ★★★ |
| `[核心]` | n_pcs_for_50pct, n_pcs_for_80pct, n_pcs_for_90pct | 解释 X% 方差需要多少 PC | ★★★ |
| `[扩展]` | variance_explained_knee | 方差曲线拐点(PC 数选择依据) | ★★ |
| `[扩展]` | PC1_PC2_loading_top_genes | PC1/PC2 载荷最高的基因(方向解释) | ★ |

### step1_prepare.knn_graph　kNN 邻域图构建

**操作**:`sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)`,基于 PCA 空间构建 k 近邻图。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_connected_components | 图的连通分量数(>1=数据碎片化) | ★★★ |
| `[扩展]` | graph_density | 边数/最大可能边数 | ★★ |
| `[扩展]` | mean_degree, median_degree | 节点平均/中位度数(应接近 2×n_neighbors) | ★★ |
| `[扩展]` | degree_distribution (percentiles) | 度数分布(偏态=有孤点) | ★★ |
| `[扩展]` | frac_isolated_nodes (degree=0) | 完全孤立细胞比例 | ★★ |
| `[扩展]` | avg_clustering_coefficient | 平均聚类系数(高=社区结构明显) | ★ |

### step1_prepare.leiden_cluster　Leiden 聚类(多分辨率)

**操作**:在多个分辨率(0.4~1.2)下运行 Leiden 社区检测。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | resolution_cluster_counts | 各分辨率下簇数 | — |
| `[核心]` | modularity_score per resolution | 社区结构强度(0~1,Leiden 优化目标) | ★★★ |
| `[核心]` | cluster_size_distribution per resolution (min, max, median, mean, CV, Gini, skewness) | 簇大小均匀度 | ★★★ |
| `[核心]` | frac_largest_cluster | 最大簇占比(>50%=主群体主导) | ★★★ |
| `[扩展]` | frac_smallest_cluster | 最小簇占比 | ★★ |
| `[核心]` | n_rare_clusters (<5% of cells) | 稀有簇数(DE 检验力不足) | ★★★ |
| `[扩展]` | n_singleton_clusters (≤2 cells) | 无意义簇数 | ★★ |
| `[核心]` | silhouette_score per cell (-1~1) | 细胞级轮廓系数 | ★★★ |
| `[核心]` | silhouette per cluster (mean, median, p25, p75) | 簇级轮廓系数 | ★★★ |
| `[核心]` | silhouette_overall (mean, std) | 全局聚类质量 | ★★★ |
| `[核心]` | n_clusters_with_negative_mean_silhouette | 轮廓为负的簇数(分错) | ★★★ |
| `[扩展]` | Davies-Bouldin index | 簇间距离/簇内离散比(越低越好) | ★★ |
| `[扩展]` | Calinski-Harabasz index | 簇间方差/簇内方差(越高越好) | ★★ |
| `[扩展]` | WCSS per cluster (within-cluster sum of squares) | 簇内离散度 | ★★ |
| `[扩展]` | BCSS (between-cluster sum of squares) | 簇间离散度 | ★★ |
| `[扩展]` | WCSS/BCSS ratio | 紧凑度/分离度比(越低越好) | ★★ |

### step1_prepare.choose_resolution　分辨率选择

**操作**:用户根据 resolution_cluster_counts 选一个分辨率,或自动选拐点附近。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | resolution_chosen, resolution_cluster_counts | — | — |
| `[核心]` | n_clusters_derivative | d(n_clusters)/d(resolution),接近 0=平台期 | ★★★ |
| `[扩展]` | resolution_knee (auto-detected) | 曲线拐点 | ★★ |
| `[核心]` | adjacent_resolution_ARI | 相邻分辨率间 Adjusted Rand Index(高=稳定) | ★★★ |
| `[扩展]` | adjacent_resolution_NMI | Normalized Mutual Information | ★★ |
| `[扩展]` | cluster_persistence | 每个簇在多少分辨率下持续存在 | ★★ |
| `[扩展]` | cluster_merge_split_tree | 分辨率变化时合并/分裂的树结构 | ★ |
| `[扩展]` | n_stable_clusters | 多分辨率下稳定的簇数 | ★★ |
| `[核心]` | stability_at_chosen_resolution | 选中分辨率与相邻分辨率的 ARI 均值 | ★★★ |

### step1_prepare.umap　UMAP 嵌入

**操作**:`sc.tl.umap(adata)`,高维→2D 可视化嵌入。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | trustworthiness | UMAP 保留局部邻域的程度(0~1,>0.9=良好) | ★★★ |
| `[扩展]` | continuity | 原始空间近邻在 UMAP 空间仍近邻的比例 | ★★ |
| `[扩展]` | mean_intra_cluster_distance_umap | UMAP 空间簇内平均距离 | ★★ |
| `[扩展]` | mean_inter_cluster_distance_umap | UMAP 空间簇间平均距离 | ★★ |
| `[扩展]` | umap_separation_ratio = inter / intra | 可视化分离度 | ★★ |
| `[扩展]` | n_overlapping_clusters_umap | UMAP 凸包重叠的簇对数 | ★ |
| `[扩展]` | umap_coordinate_range (UMAP1, UMAP2) | 坐标范围(异常大=离群) | ★ |

### step1_prepare.batch_mixing　批次混合检查

**操作**:检查每个簇包含哪些批次/样本。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | per_cluster_batch_nunique | 每簇包含几个批次 | — |
| `[核心]` | per_cluster_batch_entropy (Shannon) | 批次混合的香农熵(接近 0=单批次主导) | ★★★ |
| `[核心]` | per_cluster_max_batch_fraction | 单批次最大占比(→1=批次主导) | ★★★ |
| `[扩展]` | batch_cluster_chi_square | 批次×聚类卡方统计量(独立性检验) | ★★ |
| `[扩展]` | overall_batch_mixing_index | 全局批次混合度 | ★★ |
| `[核心]` | batch_graph_autocorr (Moran's I on batch one-hot) | 批次标签在 kNN 图上的 Moran's I(高=批次在图上分离,低=混合好)。用 `sc.metrics.morans_i(adata, vals=batch_one_hot)` | ★★★ |
| `[扩展]` | embedding_density_per_batch | 每批次在 UMAP 空间的细胞密度分布(一批次挤在一片=批次效应)。用 `sc.tl.embedding_density(adata, basis='umap', groupby='batch')` | ★★ |

---

## 2. Marker 发现阶段(Step 2)

### step2_markers.de_rank　DE 排序

**操作**:Wilcoxon 秩和检验(或 pseudobulk t-test)对每簇 vs 其余做差异表达,排序基因。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | logfc, pval (per gene) | 效应量与显著性 | — |
| `[扩展]` | n_genes_tested per cluster | DE 检验的基因总数 | ★★ |
| `[核心]` | BH_adjusted_pval (FDR) | Benjamini-Hochberg 校正 p 值 | ★★★ |
| `[核心]` | AUC per gene (0.5~1.0) | ROC 曲线下面积,Wilcoxon 等价的判别力指标 | ★★★ |
| `[扩展]` | logfc_distribution per cluster (mean, median, std, percentiles) | DE 整体强度 | ★★ |
| `[扩展]` | pval_distribution (histogram) | p 值分布(全小=系统性偏差) | ★★ |
| `[扩展]` | genomic_inflation_factor_lambda | p 值膨胀因子(中位 χ²_obs / 中位 χ²_exp) | ★★ |
| `[核心]` | n_significant at FDR<0.05, <0.01, <0.001 | 不同显著性水平下的 marker 数 | ★★★ |
| `[扩展]` | top_marker_logfc_gap (rank1-rank2, rank2-rank3) | 前几名之间的差距(清晰 vs 接近) | ★★ |
| `[扩展]` | frac_positive_logfc | 正向 DE 基因比例(是否系统性偏移) | ★ |

### step2_markers.pct1_pct2　pct1/pct2 计算

**操作**:对每个 DE 候选基因,计算簇内表达比例(pct1)和簇外表达比例(pct2)。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | pct1, pct2, pct1_minus_pct2 (per gene) | — | — |
| `[扩展]` | pct1_distribution per cluster (mean, median, percentiles) | 簇内 marker 稳定性总体水平 | ★★ |
| `[扩展]` | pct2_distribution per cluster | 簇外表达基线 | ★★ |
| `[扩展]` | specificity_distribution per cluster (pct1-pct2) | 特异性分布 | ★★ |
| `[扩展]` | mean_specificity_of_top_N_markers | 簇级 marker 质量 | ★★ |

### step2_markers.filter_markers　Marker 过滤

**操作**:按 pct1 ∈ [0.5, 0.9]、pct1-pct2 > 0.25 过滤,保留 top-N。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_markers, n_grey_zone | — | — |
| `[核心]` | n_before_filter | DE 排序后总基因数 | ★★★ |
| `[核心]` | filter_funnel: n_pass_pct1_min, n_pass_pct1_max, n_pass_specificity | 每条规则各通过多少 | ★★★ |
| `[扩展]` | filter_efficiency = n_markers / n_before_filter | 过滤收紧程度 | ★★ |
| `[扩展]` | grey_zone_rate = n_grey / n_before_filter | 灰区比例 | ★ |

### step2_markers.pseudobulk_de　稀有簇 Pseudobulk 切换

**操作**:稀有簇(<5%)切换到 pseudobulk DE(按样本聚合后 t-test)。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | is_rare, de_method | — | — |
| `[扩展]` | n_pseudobulk_samples per rare cluster | pseudobulk 聚合了几个样本 | ★★ |
| `[扩展]` | pseudobulk_wilcoxon_marker_overlap | 两种方法的一致性(若都跑了) | ★ |

---

## 3. 知识图谱查询阶段(Step 3)

### step3_kg.query_genes　KG 查询

**操作**:收集所有 marker 基因,查 KG 获取 gene→cell_type 映射。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | overall_hit_rate, n_markers_hit per cluster | — | — |
| `[扩展]` | n_unique_genes_queried, n_genes_with_hits, n_genes_without_hits | 查询规模与命中情况 | ★★ |
| `[扩展]` | mapping_multiplicity_per_gene (mean, distribution) | 每个 hit gene 映射到多少个 cell type | ★★ |
| `[扩展]` | genes_with_no_kg_entry (list) | 供调试 ID 匹配问题 | ★ |
| `[扩展]` | mean_candidates_per_gene | 每个 marker 平均产生多少候选(噪声水平) | ★ |

### step3_kg.aggregate_candidates　候选聚合

**操作**:per cluster 聚合 gene→cell_type 映射,按 marker_count → mean_confidence 排名候选。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | candidates: cell_type, supporting_markers, marker_count, mean_confidence, min_confidence, sources | — | — |
| `[核心]` | n_candidates per cluster | 候选数量(过多=噪声,过少=KG窄) | ★★★ |
| `[扩展]` | marker_coverage = n_supporting_markers_unique / n_markers_queried | 有多少比例 marker 支持了候选 | ★★ |
| `[扩展]` | candidate_count_distribution | marker_count 在候选间的分布(集中 vs 分散) | ★★ |
| `[核心]` | candidate_ranking_entropy | Shannon 熵 over candidate marker_counts(高=分散,低=集中) | ★★★ |
| `[扩展]` | n_tied_at_top | 与第一候选 marker_count 相同的候选数 | ★★ |
| `[扩展]` | confidence_distribution_across_candidates (mean, std) | 候选间置信度离散度 | ★ |
| `[扩展]` | n_unique_cell_types_across_clusters | 全局命中了多少不同 cell type | ★ |

---

## 4. 簇判断阶段(Step 4)

### step4_judge.rank_candidates　候选排名与差距

**操作**:取 first/second 候选,报告 raw evidence。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | first/second candidate, count, mean_confidence, supporting_markers | — | — |
| `[核心]` | count_ratio = first_count / max(second_count, 1) | 相对优势(1=并列,2=2倍) | ★★★ |
| `[核心]` | count_diff = first_count - second_count | 绝对差距 | ★★★ |
| `[扩展]` | confidence_diff = first_mean_confidence - second_mean_confidence | 置信度差距 | ★★ |
| `[扩展]` | n_tied_at_first | 与第一并列的候选数 | ★★ |
| `[扩展]` | frac_support_captured_by_first = first_count / sum(all counts) | 第一候选捕获了多少支持 | ★★ |
| `[核心]` | first_second_ancestor_overlap | 两个候选在 KG 本体中是否有父子关系 | ★★★ |

---

## 5. 细化阶段(Step 5)

### step5_refine.candidate_autocorr　候选倾向自相关(预判细分必要性)

**操作**:对 ambiguous 簇,用两个候选的 marker 列表计算每个细胞的候选倾向分数,在 kNN 图上算 Moran's I 和 Geary's C。**在 subcluster 之前执行**,作为预判:高自相关→有子群体→细分有效;低自相关→marker 共享→细分无效,可跳过。

**scanpy 接口**:
- `sc.tl.score_genes(adata, gene_list=candidate1_markers, score_name='cand1_score')` — 算候选 1 的每细胞分数
- `sc.tl.score_genes(adata, gene_list=candidate2_markers, score_name='cand2_score')` — 算候选 2 的每细胞分数
- 候选倾向分数 `x_i = cand1_score - cand2_score`
- `sc.metrics.morans_i(cluster_adata, vals=x_i)` — 一行算出 Moran's I
- `sc.metrics.gearys_c(cluster_adata, vals=x_i)` — 一行算出 Geary's C

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | morans_i | 候选倾向分数在 kNN 图上的 Moran's I(-1~1,高=有子群体结构,低≈0=随机混合) | ★★★ |
| `[核心]` | gearys_c | 候选倾向分数的 Geary's C(0~2,低=有子群体,≈1=随机,>1=负相关) | ★★ |
| `[扩展]` | score_distribution (percentiles, histogram) | 候选倾向分数的分布(LLM 用这个替代看图,判断是否双峰) | ★★ |
| `[扩展]` | score_bimodality_coefficient | 倾向分数的双峰系数(>0.555=两群体) | ★★ |
| `[扩展]` | cand1_score_mean, cand2_score_mean | 两组 marker 的平均得分(哪个候选表达更强) | ★ |

### step5_refine.subcluster　子聚类

**操作**:对 ambiguous 簇子集化 + 重新 Leiden 聚类。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_subclusters, outcome (analyzed/skipped) | — | — |
| `[扩展]` | sub_cluster_size_distribution (min, max, median, CV) | 子簇大小均匀度 | ★★ |
| `[扩展]` | sub_silhouette | 子聚类轮廓系数 | ★★ |
| `[扩展]` | frac_smallest_subcluster | 最小子簇占比(太小=DE无检验力) | ★★ |

### step5_refine.subcluster_de　子簇 DE + KG 重查

**操作**:在子簇间重做 DE + 重查 KG。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | sub_clusters: first/second candidate + count | — | — |
| `[核心]` | n_subclusters_with_distinct_type | 多少子簇得到不同标签(细化是否有效) | ★★★ |
| `[扩展]` | sub_count_ratio per sub-cluster | 子簇内 first/second 比 | ★ |

### step5_refine.marker_overlap　子簇间 Marker 重叠

**操作**:计算子簇间 marker 集合的两两重叠。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | max_overlap, pairs (overlap/min) | — | — |
| `[扩展]` | mean_overlap | 平均重叠(不只 max) | ★★ |
| `[核心]` | Jaccard_index per pair | 交集/并集(比 overlap/min 更严格) | ★★★ |
| `[扩展]` | frac_unique_markers per sub-cluster | 每个子簇的独有 marker 比例 | ★★ |
| `[扩展]` | overlap_matrix | 完整 N×N 重叠矩阵 | ★ |

### step5_refine.type_membership　类型归属检查

**操作**:检查子簇的候选类型是否在父簇候选范围内。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | types_in_parent_candidates (boolean per sub-cluster) | — | — |
| `[扩展]` | n_in_range, n_out_of_range | 归属/非归属计数 | ★★ |
| `[扩展]` | frac_novel_types | 子簇产生的新类型比例 | ★ |

### step5_refine.unknown_overlap　Unknown 簇 Marker 重叠

**操作**:对无 KG 命中的簇,计算两两 marker 重叠。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | avg_overlap, pairs | — | — |
| `[扩展]` | Jaccard per pair | 交集/并集 | ★★ |
| `[扩展]` | n_unknown_clusters, frac_unknown | unknown 簇数与比例 | ★ |

---

## 6. 验证阶段(Step 6)

### step6_validate.marker_expression　Top Marker 表达验证

**操作**:对每簇 top-3 DE marker 计算簇内外表达统计。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | pct1, pct2, mean_expr, mean_expr_other (per marker) | — | — |
| `[扩展]` | specificity_index = pct1 - pct2 (预计算) | 不必让 LLM 自己减 | ★ |
| `[核心]` | effect_size (Cohen's d) = (mean_in - mean_out) / pooled_std | 标准化效应量 | ★★★ |
| `[扩展]` | fold_change = mean_in / max(mean_out, ε) | 倍数变化 | ★★ |
| `[核心]` | AUC per top marker | 判别力(0.5=无,1.0=完美) | ★★★ |
| `[扩展]` | expression_ratio = mean_expr / max(mean_expr_other, ε) | 簇内/簇外表达比 | ★ |
| `[扩展]` | mean_top3_pct1 | 簇级 marker 稳定性 | ★★ |
| `[扩展]` | mean_top3_specificity | 簇级 marker 特异性 | ★★ |
| `[扩展]` | frac_top3_pct1_above_0.5 | top-3 中达标的比例 | ★ |
| `[扩展]` | marker_gene_overlap_score | DE marker 与 KG 已知 marker 的重叠分数(验证 marker 质量)。用 `sc.tl.marker_gene_overlap(adata, reference_markers, key='rank_genes_groups')` | ★★ |

### step6_validate.global_summary　全局汇总

**操作**:跨簇汇总注释结果。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | n_clusters, n_unknown, unknown_rate | — | — |
| `[核心]` | n_unique_labels | 多少不同的注释标签 | ★★★ |
| `[核心]` | label_diversity = n_unique_labels / n_clusters | 1=全不同,低=大量重复(过聚类) | ★★★ |
| `[扩展]` | cell_type_proportions | 每种注释类型的细胞比例 | ★★ |
| `[扩展]` | effective_n_types (Shannon) | 类型多样性(香农熵) | ★★ |
| `[扩展]` | mean_first_second_gap | 全局 first-second 平均差距 | ★★ |
| `[扩展]` | mean_top3_specificity_across_clusters | 全局 marker 质量 | ★ |
| `[扩展]` | co_annotation_matrix | 哪些簇共享同一标签(过聚类信号) | ★★ |
| `[扩展]` | cross_cluster_marker_reuse | 多少 marker 被多簇共享(高=过聚类) | ★★ |

---

## 7. 诊断阶段(Step 7)

### step7_diagnose.hit_rate　每簇 KG 命中率

**操作**:对每个簇计算 marker 基因在 KG 中有命中的比例。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | per_cluster_hit_rate | 每簇的命中率 | — |

### step7_diagnose.candidate_count　每簇候选计数

**操作**:对每个簇计算 KG 返回的候选细胞类型数量。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | per_cluster_candidate_count | 每簇的候选数 | — |

### step7_diagnose.first_second　每簇 first/second

**操作**:对每个簇报告 first_count, second_count, 以及 top_strictly_ahead 布尔值。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | per_cluster_first_second (first_count, second_count, top_strictly_ahead) | — | — |

### step7_diagnose.batch_entropy　每簇批次熵

**操作**:对每个簇计算批次/样本的 Shannon 熵。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | per_cluster_batch_entropy, batch_key_used | — | — |
| `[扩展]` | batch_cluster_chi_square | 批次×聚类卡方检验 | ★ |

### step7_diagnose.metadata_check　元数据完整性

**操作**:检查 final_annotations.json 的 _meta 中哪些必需字段缺失。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[核心]` | metadata_missing | 缺失的元数据字段列表 | — |

### step7_diagnose.cross_cluster　跨簇质量测量

**操作**:跨所有簇的端到端原始测量与汇总。

| 状态 | 指标 | 说明 | 优先级 |
|---|---|---|---|
| `[扩展]` | label_uniqueness = n_unique_labels / n_clusters | 标签唯一性 | ★★ |
| `[扩展]` | annotation_entropy | Shannon 熵 over label proportions | ★★ |
| `[扩展]` | cluster_purity_proxy = mean(silhouette) | 全局聚类纯度代理 | ★★ |
| `[扩展]` | mean_first_count_gap | 全局 first-second 差距均值 | ★ |
| `[扩展]` | cross_cluster_marker_overlap_matrix | 簇间 marker 共享度矩阵 | ★★ |
| `[扩展]` | paga_connectivity_matrix | 簇间 PAGA 连通性矩阵(高=簇间关系密切,补充 KG 层级判断)。用 `sc.tl.paga(adata, groups='leiden')` | ★★ |
| `[扩展]` | paga_expression_entropies | 每簇的表达熵(高=表达多样,可能需细化)。用 `sc.tl.paga_expression_entropies(adata, groups='leiden')` | ★ |
| `[扩展]` | cell_cycle_contamination | 细胞周期基因驱动的簇数(S/G2M 高分=周期活跃,可能不是真实细胞类型)。用 `sc.tl.score_genes_cell_cycle(adata, s_genes, g2m_genes)` | ★★ |

---

## 8. 通用指标函数(跨操作复用)

很多操作共享相同的统计模式,可以提炼成 `common.py` 的通用函数:

| 函数 | 适用操作 | 输入 | 输出 |
|---|---|---|---|
| `describe_distribution(values)` | compute_qc, detect_doublets, normalize, de_rank, pct1_pct2, marker_expression | numpy array | {mean, std, median, IQR, CV, skewness, kurtosis, bimodality, percentiles, histogram} |
| `filter_funnel(mask_stages)` | filter_cells, filter_genes, filter_markers | list of boolean masks | {n_before, n_after_each_stage, n_after_all, frac_lost_per_stage, frac_retained} |
| `ranking_gap(ranked_values)` | de_rank, aggregate_candidates, rank_candidates | sorted array | {top1_top2_gap, top1_total_ratio, ranking_entropy, n_ties} |
| `pairwise_overlap(sets, method)` | marker_overlap, unknown_overlap, cross_cluster | list of sets | {mean_overlap, max_overlap, jaccard_per_pair, overlap_matrix, mean_unique_frac} |
| `batch_mixing(cluster_labels, batch_labels)` | batch_mixing, batch_entropy, cross_cluster | two label arrays | {per_cluster_entropy, per_cluster_max_frac, chi_square, overall_mixing_index} |
| `effect_size(group_in, group_out)` | de_rank, marker_expression | two arrays | {logfc, cohen_d, AUC, fold_change} |
| `variance_explained(PCA_obj)` | pca | fitted PCA | {per_pc_variance, cumulative, n_pcs_for_Xpct, knee} |
| `cluster_quality(adata, labels)` | leiden_cluster, subcluster | adata + labels | {silhouette_per_cell, per_cluster, overall, modularity, Davies_Bouldin, Calinski_Harabasz, WCSS, BCSS} |
| `resolution_stability(adata, res_list)` | choose_resolution | adata + resolutions | {ari_adjacent, nmi_adjacent, cluster_persistence, merge_split_tree} |

---

## 9. 实施优先级排序

### 第一批(LLM 最缺的信息,最高价值)

| 操作 | 指标 | 理由 |
|---|---|---|
| step1_prepare.leiden_cluster | silhouette per cluster + modularity | 聚类质量是整个 pipeline 的基石 |
| step1_prepare.pca | variance_explained per PC | LLM 无法判断 PCA 质量,影响 n_pcs 选择 |
| step1_prepare.leiden_cluster | cluster_size 分布统计 (Gini, CV, n_rare, n_singleton) | 判断过聚类/欠聚类 |
| step1_prepare.choose_resolution | adjacent_resolution_ARI | 判断分辨率选择是否稳定 |
| step2_markers.de_rank | BH_adjusted_pval (FDR) | 多重检验校正(BH-FDR) |
| step2_markers.de_rank | AUC per gene | 判别力指标,比 logfc 更直观 |
| step6_validate.marker_expression | Cohen's d + AUC per top marker | 标准化效应量,跨基因/跨簇可比 |
| step1_prepare.filter_cells | filter funnel (n_lost_per_criterion) | 过滤透明度 |
| step1_prepare.compute_qc | bimodality_coefficient | 分布形状量化(替代看图) |

### 第二批(有价值,实现中等难度)

| 操作 | 指标 | 理由 |
|---|---|---|
| step1_prepare.knn_graph | n_connected_components + degree_distribution | 图碎片化诊断 |
| step1_prepare.select_hvg | hvg_dispersion_distribution + n_batch_specific_hvg | HVG 质量与批次污染 |
| step1_prepare.umap | UMAP trustworthiness | UMAP 图可信度 |
| step1_prepare.batch_mixing | per_cluster_batch_entropy + max_batch_fraction | 批次主导检测 |
| step2_markers.filter_markers | filter_funnel | 过滤透明度 |
| step3_kg.aggregate_candidates | candidate_ranking_entropy + n_candidates | 候选集中/分散 |
| step4_judge.rank_candidates | count_ratio + ancestor_overlap | 差距量化 |
| step5_refine.marker_overlap | Jaccard_index | 比 overlap/min 更严格 |
| step6_validate.global_summary | label_diversity + co_annotation_matrix | 过聚类检测 |

### 第三批(诊断参考,较低优先级)

| 操作 | 指标 | 理由 |
|---|---|---|
| step1_prepare.normalize | post_norm 稀疏度指标 | 归一化质量 |
| step1_prepare.leiden_cluster | Davies-Bouldin / Calinski-Harabasz | 全局质量指数 |
| step2_markers.de_rank | genomic_inflation_factor_lambda | p 值膨胀 |
| step1_prepare.choose_resolution | cluster_merge_split_tree | 嵌套结构 |
| step5_refine.subcluster | sub_silhouette | 子聚类质量 |
| step7_diagnose.cross_cluster | cross_cluster_marker_overlap_matrix | 过聚类信号 |