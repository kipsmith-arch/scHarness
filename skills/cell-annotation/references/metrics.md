# 指标解读指南(skill references 版)

> 本文档是 `knowledge/metrics_interpretation.md` 全文的 skill 包副本,由指标解读素材原样收录。
> SKILL.md §3 各决策点给出的指标路径,其数值解读在本文件按阶段分节查询。

## 目录(TOC)

- [1. 数据准备阶段(step1_prepare)](#1-数据准备阶段step1_prepare)
- [2. Marker 发现阶段(step2_markers)](#2-marker-发现阶段step2_markers)
- [3. 知识图谱查询阶段(step3_kg)](#3-知识图谱查询阶段step3_kg)
- [4. 簇判断阶段(step4_judge)](#4-簇判断阶段step4_judge)
- [5. 细化阶段(step5_refine)](#5-细化阶段step5_refine)
- [6. 验证阶段(step6_validate)](#6-验证阶段step6_validate)
- [7. 诊断阶段(step7_diagnose)](#7-诊断阶段step7_diagnose)
- [实现补充说明(P2 评审修复)](#实现补充说明p2-评审修复)
- [附:解读时的常见陷阱](#附解读时的常见陷阱)

---

# 指标解读指南

本文件说明 pipeline 每个**原子操作**产出的**原始指标**(不经过任何阈值规则换算),以及 LLM 在向用户描述结果时应如何解读这些数字。

## 文档定位

- **设计侧**:`design/operations_metrics_catalog.md` 列出 247 个指标"有哪些"(WHAT)
- **知识侧**(本文件):每个指标"数值高低意味着什么、怎么说"(HOW TO READ)
- 两者通过相同的原子操作名 + 指标名对齐

## 原则

1. **pipeline 产出测量值,LLM 产出判断**。所有 high/medium/low、pass/fail、clear/ambiguous 等判定由 LLM 基于原始指标解读,不写进代码
2. **判断必须说明依据**。LLM 不能只说"这个簇是 T 细胞,高置信度",必须说"第一候选 T cell 有 18 个支持 marker(第二候选 B cell 只有 3 个),top marker CD3D pct1=0.82"
3. **不确定就说不确定**。first≈second 时不要硬选一个,如实报告"两个候选势均力敌"
4. **交叉验证靠外部知识**。LLM 可以用自己的生物学知识判断候选是否是同义词/父子类、marker 是否合理,但要告知用户这是 LLM 判断而非 KG 数据

## 标注约定

- `[核心]` = 基础必备(无★)或高优先级(★★★)指标,本文件给出完整解读
- `[扩展]` = 有价值(★★)或诊断参考(★)指标,本文件给出"读什么 + 解读方向"

---

## 1. 数据准备阶段(step1_prepare)

> 产出文件:`step1_prepare/processed.h5ad`(QC + 聚类后的数据)、`step1_prepare/qc_metrics.json`(全部 Step1 指标)、`step1_prepare/obs_snapshot.csv` + `var_snapshot.csv`(**sidecar**:细胞/基因元数据,供 step7 与 OBS 级操作零加载读取)、`step1_prepare/qc_distributions.png`(供人类查看,LLM 应读 JSON 中的分布数据)
> 加载纪律:`run` 子命令 = 1× raw 加载内完成全部 16 个原子操作;`metrics` 只到 qc_plot(4 op);`recluster` 1× proc 加载。

### compute_qc — 质控变量计算

对每个细胞计算 n_genes(检测到的基因数)、total_counts(总 UMI)、pct_mt(线粒体占比)、pct_cp(叶绿体占比,植物)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `percentiles` (p1,p5,p10,p25,p50,p75,p90,p95,p99) | 分位数 | 看 p99 是否远高于 p90(长尾=有少量高 mt 细胞→设阈值截尾) |
| `[核心]` | `histogram` (20-bin) | 直方图 | 检查 `n_genes_by_counts` 是否双峰(双峰=混合低质量和正常细胞→阈值设在谷底) |
| `[核心]` | min, max, median | 基本位置 | — |
| `[扩展]` | mean, std | 位置与离散度 | std 大说明细胞间 QC 变异大,可能异质或质量参差 |
| `[扩展]` | IQR = p75 - p25 | 稳健离散度 | 比 std 抗异常值;IQR 大→分布散 |
| `[扩展]` | CV = std / mean | 相对离散度 | 可跨数据集比较;CV>1 说明高度变异 |
| `[核心]` | skewness | 偏态 | >0=右偏长尾(少量高值异常),<0=左偏截断(可能已过滤) |
| `[扩展]` | kurtosis (excess) | 尖峰 | >0=尖峰厚尾(异常值集中),<0=平坦 |
| `[核心]` | bimodality_coefficient = (skew² + 1) / kurt | 双峰系数 | **>0.555 提示双峰**→阈值设在两峰谷底,直接过滤会丢真实群体 |
| `[扩展]` | Gini coefficient (total_counts) | 测序深度不均匀度 | 0=均匀,1=极不均;高 Gini 说明少数细胞占大量 reads |

### qc_distribution — 质控分布统计

对每个 QC 变量计算分布统计量(分位数 + 直方图),**LLM 用这个替代看图判断分布形状**。在 `metrics` 子命令中输出过滤前的分布,在 `run` 子命令中输出过滤前后的对比。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `pre_filter_distributions` (percentiles + histogram per QC var) | 过滤前的分布 | 检查 `pct_counts_mt.percentiles.p99` 是否远高于 p90(长尾→截尾);检查 `n_genes_by_counts.histogram.counts` 是否双峰(双峰→阈值设在谷底) |
| `[扩展]` | `post_filter_distributions` | 过滤后的分布 | 与 pre_filter 对比,看过滤是否真改变了分布 |
| `[扩展]` | `distribution_diff` (Δmedian, ΔIQR, Δskewness) | 过滤前后偏移 | Δ 大→过滤有效;Δ≈0→过滤没改变分布(阈值没切到东西) |
| `[核心]` | bimodality_coefficient per QC var | 双峰系数 | >0.555→该变量分布双峰,过滤阈值要设在谷底 |
| `[扩展]` | tail_fraction (p99 - p90) | 尾部厚度 | 大→有少量极端异常细胞;小→尾部干净 |
| `[扩展]` | valley_detection | 双峰谷底位置 | 若分布双峰,谷底就是合理阈值位置 |
| `[扩展]` | KS_statistic vs normal | 与正态 KS 检验 | 偏离正态越远,越不能用均值±std 设阈值,要改用分位数 |

### filter_cells — 细胞过滤

按 min_genes、max_mt_pct、max_chloroplast_pct 阈值过滤细胞。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_cells_before`, `n_cells_after` | 过滤前后细胞数 | 看 `frac_cells_lost`。>50%→过滤过严可能丢真实细胞群;<5%→过滤保守可能残留低质量细胞 |
| `[核心]` | `frac_cells_lost` | 过滤掉的比例 | 植物组织天然有高叶绿体/低线粒体,与动物不同,需结合组织类型判断 |
| `[核心]` | `n_lost_per_criterion` | 各规则各去掉多少(min_genes / mt / cp / doublet) | 看哪条规则主导过滤——若 mt 主导但组织本就高 mt(如肝),阈值需放宽 |
| `[扩展]` | `n_lost_multiple_criteria` | 同时违反多条规则的细胞数(交集) | 高交集→过滤规则耦合,调一条阈值会同时影响多条 |
| `[扩展]` | `frac_lost_per_criterion` | 每条规则占总过滤的比例 | 与 n_lost_per_criterion 同视角,百分比版 |
| `[扩展]` | Δmedian (per QC var) | 过滤前后中位数偏移 | 过滤是否真改变了分布 |
| `[扩展]` | ΔIQR (per QC var) | 过滤前后 IQR 变化 | 同上,稳健版 |
| `[核心]` | KS statistic (per QC var) | 过滤前后分布 KS 检验(0=无变化,1=完全不同) | KS 大→过滤切到了真东西;KS≈0→过滤没动到分布 |

### filter_genes — 基因过滤

按 min_cells 阈值过滤基因(表达细胞数不足的剔除)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_genes_before`, `n_genes_after`, `frac_genes_lost` | 过滤前后基因数 | frac_genes_lost 高→数据稀疏(很多基因只在极少数细胞表达) |
| `[扩展]` | expression_breadth_distribution | 各基因的表达细胞比例分布 | 看分布尾部——长尾→有大量极低表达基因,过滤合理;无尾→数据密集 |
| `[扩展]` | n_genes_in_<1%_cells | 极低表达基因数 | 高→数据稀疏,影响 DE 检验力 |
| `[扩展]` | `n_mt_removed`, `n_cp_removed` | 移除的线粒体/叶绿体基因数(植物) | 植物中 ATCG(叶绿体)基因通常数百个、ATMG(线粒体)十几个;数值异常(如 0)→前缀正则没匹配上,检查 `--mt-pattern`/`--cp-pattern` |

### detect_doublets — 双峰检测

scrublet 检测 doublet 并移除。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `doublet_score_distribution` (percentiles, mean, std) | doublet 分数分布 | 分数分布是否有明显双峰(双峰→有意义的阈值分离) |
| `[核心]` | `n_doublets_detected`, `frac_doublets` | 检出并移除的数量与比例 | frac_doublets 远超 expected_rate(如 >2×)→阈值可能过严;远低→可能漏检 |
| `[扩展]` | `doublet_score_bimodality` | 分数是否形成独立峰 | 双峰→分离可信;单峰→阈值是硬切的,不确定 |
| `[扩展]` | `implied_threshold` | scrublet 隐含的分数阈值 | 看阈值落在分布的什么位置——落在谷底才合理 |
| `[扩展]` | `de_method` | 检测方法(scrublet / scrublet-failed) | **scrublet 失败时不崩溃**:保留全部细胞、`de_method=scrublet-failed`、error 字段记录原因——此时下游标注需考虑未去双峰的影响 |

### normalize — 归一化

total-count 归一化(target_sum=1e4)→ log1p 变换。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[扩展]` | `median_library_size_before` | 归一化前的中位库大小 | 与 target_sum 对比,差太多→归一化拉伸幅度大 |
| `[扩展]` | `normalization_target` | 归一化目标值(1e4) | 记录用,供追溯 |
| `[扩展]` | `post_norm_mean_expression_distribution` | 归一化后表达均值分布(percentiles) | 看分布是否合理(log 后应近似正态) |
| `[扩展]` | `frac_zero_after_norm` | 归一化后仍为 0 的基因×细胞比例(稀疏度) | 高稀疏→DE 可能需 pseudobulk;低→数据密集 |

### select_hvg — HVG 选择

Seurat flavor 高变基因选择,默认 n_top_genes=2000,可选 batch_key 分批选。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_genes_after` (= n_hvg) | 选出的 HVG 数 | 默认 2000;影响聚类分辨率 |
| `[扩展]` | `n_genes_before_hvg`, `frac_hvg_of_total` | HVG 占总基因比例 | 比例高→数据信息密度大;比例低→稀疏 |
| `[核心]` | `hvg_dispersion_distribution` (mean, median, percentiles) | 选出基因的离散度分布 | 看分布是否清晰高于非 HVG(清晰→选择有效) |
| `[扩展]` | `hvg_mean_expression_distribution` | HVG 是高表达还是低表达 | 应该是高表达高离散;若选了低表达→选择有问题 |
| `[扩展]` | `hvg_nongvg_dispersion_gap` | top HVG 与非 HVG 的离散度落差 | gap 大→选择清晰;gap 小→HVG 与背景区分不开 |
| `[核心]` | `n_batch_specific_hvg` (if batch_key) | 多少 HVG 是批次特异的 | **高→批次污染 HVG 池**,需调整 batch_key 或排除 |

### pca — PCA 降维

在 HVG 上做 PCA,n_comps=50。后续 kNN 图用前 30 个 PC。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `variance_explained_per_pc` (PC1~PC50) | 每个 PC 的方差解释量 | 前几 PC 应显著高,后面趋于平台 |
| `[核心]` | `cumulative_variance_explained` | 累积方差解释 | 看 n_pcs_for_Xpct 判断 PC 数是否够 |
| `[核心]` | `n_pcs_for_50pct`, `n_pcs_for_80pct`, `n_pcs_for_90pct` | 解释 X% 方差需要多少 PC | 若 30 PC 解释<50%→信息不够,kNN 图质量打折 |
| `[扩展]` | `variance_explained_knee` | 方差曲线拐点 | 拐点处即合理 PC 数 |
| `[扩展]` | `PC1_PC2_loading_top_genes` | PC1/PC2 载荷最高的基因 | 看这些基因是否生物学合理(如 PC1 对应细胞周期/线粒体→可能是质量效应主导) |

### knn_graph — kNN 邻域图构建

`sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)`,基于 PCA 空间构建 k 近邻图。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_connected_components` | 图的连通分量数 | **>1=数据碎片化**,孤立群体会形成独立簇,需检查 |
| `[扩展]` | `graph_density` | 边数/最大可能边数 | 太低→图稀疏,聚类不稳;太高→过度连接 |
| `[扩展]` | `mean_degree`, `median_degree` | 节点平均/中位度数 | 应接近 2×n_neighbors;偏离→有异常连接 |
| `[扩展]` | `degree_distribution` (percentiles) | 度数分布 | 偏态→有孤点;均匀→正常 |
| `[扩展]` | `frac_isolated_nodes` (degree=0) | 完全孤立细胞比例 | >0→这些细胞在聚类中被抛弃,需检查 |
| `[扩展]` | `avg_clustering_coefficient` | 平均聚类系数 | 高→社区结构明显,聚类有意义;低→图扁平 |

### leiden_cluster — Leiden 聚类(多分辨率)

在多个分辨率(0.4~1.2)下运行 Leiden 社区检测。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `resolution_cluster_counts` | 各分辨率下簇数 | 簇数随分辨率增加趋于平台时,平台附近即合理分辨率 |
| `[核心]` | `modularity_score` per resolution | 社区结构强度(0~1,Leiden 优化目标) | 高→聚类结构清晰;低→社区不明显 |
| `[核心]` | `cluster_size_distribution` per resolution (min, max, median, mean, CV, Gini, skewness) | 簇大小均匀度 | CV/Gini 高→大小悬殊;skewness 高→有少数大簇 + 很多小簇 |
| `[核心]` | `frac_largest_cluster` | 最大簇占比 | **>50%→主群体主导**,其他簇可能是边缘;>80%→过聚类或质量效应 |
| `[扩展]` | `frac_smallest_cluster` | 最小簇占比 | 极小→稀有群体或噪声 |
| `[核心]` | `n_rare_clusters` (<5%) | 稀有簇数 | 多→DE 检验力不足,后续 marker 质量可能差 |
| `[扩展]` | `n_singleton_clusters` (≤2 cells) | 无意义簇数 | >0→分辨率过高,降分辨率 |
| `[核心]` | `silhouette_score` per cell (-1~1) | 细胞级轮廓系数 | 接近 1=归对簇,接近 0=边界,负=归错 |
| `[核心]` | `silhouette` per cluster (mean, median, p25, p75) | 簇级轮廓系数 | 低或负→该簇与邻居混淆 |
| `[核心]` | `silhouette_overall` (mean, std) | 全局聚类质量 | 全局低→聚类整体质量差,需调分辨率或上游 |
| `[核心]` | `n_clusters_with_negative_mean_silhouette` | 轮廓为负的簇数 | **多→多个簇分错**,需重新聚类 |
| `[核心]` | `silhouette_sampled` | 是否采样计算(>10K 细胞采 10K,固定 seed=0) | True→数值基于 10K 子样本,簇内均值仍是近似,不要拿它和未采样的数据集直接比 |
| `[扩展]` | Davies-Bouldin index | 簇间距离/簇内离散比(越低越好) | 与其他分辨率对比,低的更优 |
| `[扩展]` | Calinski-Harabasz index | 簇间方差/簇内方差(越高越好) | 与其他分辨率对比,高的更优 |
| `[扩展]` | WCSS per cluster | 簇内离散度 | 高→簇内杂,可能需细分 |
| `[扩展]` | BCSS | 簇间离散度 | 高→簇间分得开 |
| `[扩展]` | WCSS/BCSS ratio | 紧凑度/分离度比(越低越好) | 高→整体聚类不紧凑 |

### choose_resolution — 分辨率选择

用户根据 resolution_cluster_counts 选一个,或自动选拐点附近。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `resolution_chosen`, `resolution_cluster_counts` | 选中的分辨率 + 各分辨率簇数 | 让用户看分布来选 |
| `[核心]` | `n_clusters_derivative` | d(n_clusters)/d(resolution),接近 0=平台期 | 平台期→分辨率稳定,选这里 |
| `[扩展]` | `resolution_knee` (auto-detected) | 曲线拐点 | 自动检测的平台位置 |
| `[核心]` | `adjacent_resolution_ARI` | 相邻分辨率间 Adjusted Rand Index | 高(>0.9)→分辨率间稳定,选哪个都行;低→分辨率敏感 |
| `[扩展]` | `adjacent_resolution_NMI` | Normalized Mutual Information | 同 ARI 视角 |
| `[扩展]` | `cluster_persistence` | 每个簇在多少分辨率下持续存在 | 持续存在的簇→稳定真实;只在一两个分辨率出现→噪声簇 |
| `[扩展]` | `cluster_merge_split_tree` | 分辨率变化时合并/分裂的树结构 | 看哪些簇是合并/分裂产生的 |
| `[扩展]` | `n_stable_clusters` | 多分辨率下稳定的簇数 | 与最终簇数对比,一致→选择合理 |
| `[核心]` | `stability_at_chosen_resolution` | 选中分辨率与相邻分辨率的 ARI 均值 | **低→选择不稳定**,需重新选 |

### umap — UMAP 嵌入

`sc.tl.umap(adata)`,高维→2D 可视化嵌入。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `trustworthiness` | UMAP 保留局部邻域的程度(0~1) | **>0.9=良好**;<0.8→UMAP 图有误导,不能只看图 |
| `[扩展]` | `continuity` | 原始空间近邻在 UMAP 空间仍近邻的比例 | 低→UMAP 把真邻居画远了 |
| `[扩展]` | `mean_intra_cluster_distance_umap` | UMAP 空间簇内平均距离 | 大→簇在图上散 |
| `[扩展]` | `mean_inter_cluster_distance_umap` | UMAP 空间簇间平均距离 | 小→簇在图上挨着 |
| `[扩展]` | `umap_separation_ratio` = inter / intra | 可视化分离度 | 低→图上分不开,可能过聚类 |
| `[扩展]` | `n_overlapping_clusters_umap` | UMAP 凸包重叠的簇对数 | 多→图上重叠,需结合指标判断 |
| `[扩展]` | `umap_coordinate_range` (UMAP1, UMAP2) | 坐标范围 | 异常大→有离群点拉大坐标 |

### batch_mixing — 批次混合检查

检查每个簇包含哪些批次/样本。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `per_cluster_batch_nunique` | 每簇包含几个批次 | 若某簇只有 1 个批次,可能是批次效应或真实条件特异性群体(如突变体),需交叉查条件注释 |
| `[核心]` | `per_cluster_batch_entropy` (Shannon) | 批次混合的香农熵 | **接近 0=单批次主导**→批次效应或真实条件群体;高→混合好 |
| `[核心]` | `per_cluster_max_batch_fraction` | 单批次最大占比 | →1=批次主导;→1/n_batches=均匀混合 |
| `[扩展]` | `batch_cluster_chi_square` | 批次×聚类卡方统计量(独立性检验) | 显著→批次与聚类不独立,有批次效应 |
| `[扩展]` | `overall_batch_mixing_index` | 全局批次混合度 | 低→整体有批次效应 |
| `[核心]` | `batch_graph_autocorr_morans_i` | 批次标签在 kNN 图上的 Moran's I(每批次 + `mean_abs`) | **高(>0.3)→批次在图上分离**(每个批次的细胞聚在一起);低(≈0)→混合好。比 per_cluster 熵更直接——直接看批次标签在图上是否有空间结构 |
| `[扩展]` | `embedding_density_per_batch` | 每批次在 UMAP 空间的细胞密度 | 一批次挤在一片→批次效应 |

### LLM 描述模板(step1)
> "Step1 完成:从 X 个细胞过滤到 Y 个(去掉 Z%)。在分辨率 R 下得到 N 个簇,最小簇 C 个细胞。批次检查:[哪些簇是单批次]。建议:..."

---

## 2. Marker 发现阶段(step2_markers)

> 产出文件:`step2_markers/markers.csv`(全部 marker DE 表)、`step2_markers/markers.json`(按簇组织,含 de_distribution/filter_funnel/pseudobulk)

### de_rank — DE 排序

Wilcoxon 秩和检验(或 pseudobulk t-test)对每簇 vs 其余做差异表达,排序基因。DE 始终基于原始 counts(scanpy `rank_genes_groups` 用 `adata.raw`)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `logfc` (per gene) | log2 倍数变化 | >1 说明强差异表达;<0.5 说明差异弱 |
| `[核心]` | `pval` (per gene) | p 值 | 注意:pval 未做多重检验校正,看 BH-FDR 才靠谱 |
| `[扩展]` | `n_genes_tested` per cluster | DE 检验的基因总数 | 看分母,marker 数 / tested 数 = 命中率 |
| `[核心]` | `BH_adjusted_pval` (FDR) | Benjamini-Hochberg 校正 p 值 | **pval 未校正**,FDR<0.05 才算显著 |
| `[核心]` | `AUC` per gene (0.5~1.0) | ROC 曲线下面积,Wilcoxon 等价的判别力指标 | **0.5=无判别力,1.0=完美**;>0.7=好 marker;比 logfc 更直观 |
| `[扩展]` | `logfc_distribution` per cluster (mean, median, std, percentiles) | DE 整体强度 | mean logfc 低→DE 整体弱,簇可能不清晰 |
| `[扩展]` | `pval_distribution` (histogram) | p 值分布 | 全小→系统性偏差;均匀→无信号 |
| `[扩展]` | `genomic_inflation_factor_lambda` | p 值膨胀因子(中位 χ²_obs / 中位 χ²_exp) | >1→p 值膨胀,显著性被夸大;<1→保守 |
| `[核心]` | `n_significant` at FDR<0.05, <0.01, <0.001 | 不同显著性水平下的 marker 数 | 看不同严格度下 marker 数变化,判断信号稳健性 |
| `[扩展]` | `top_marker_logfc_gap` (rank1-rank2, rank2-rank3) | 前几名之间的差距 | gap 大→marker 清晰;gap 小→前几名接近,marker 选择敏感 |
| `[扩展]` | `frac_positive_logfc` | 正向 DE 基因比例 | 偏离 0.5→系统性偏移(可能归一化问题) |

### pct1_pct2 — pct1/pct2 计算

对每个 DE 候选基因,计算簇内表达比例(pct1)和簇外表达比例(pct2)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `pct1`, `pct2`, `pct1_minus_pct2` (per gene) | 簇内/簇外表达比例 | pct1 高(>0.5)+ pct2 低→好 marker;pct1-pct2 差值接近 0→不特异(可能管家基因) |
| `[扩展]` | `pct1_distribution` per cluster (mean, median, percentiles) | 簇内 marker 稳定性总体水平 | mean pct1 低→marker 稳定性差,DE 整体弱 |
| `[扩展]` | `pct2_distribution` per cluster | 簇外表达基线 | 高→基线嘈杂,marker 特异性打折 |
| `[扩展]` | `specificity_distribution` per cluster (pct1-pct2) | 特异性分布 | 看分布尾部——长尾向上→有强特异 marker |
| `[扩展]` | `mean_specificity_of_top_N_markers` | 簇级 marker 质量 | 跨簇对比,低→该簇 marker 质量差 |

### filter_markers — Marker 过滤

按 pct1 ∈ [0.5, 0.9]、pct1-pct2 > 0.25 过滤,保留 top-N。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_markers`, `n_grey_zone` | 每簇 marker 数 + 灰区数 | 0→DE 无结果(pseudobulk 样本不足/数据稀疏/过滤太严);1-5→marker 弱;>30→充足 |
| `[扩展]` | `n_before_filter` | DE 排序后总基因数 | 看漏斗入口规模 |
| `[核心]` | `filter_funnel`: `n_pass_pct1_min`, `n_pass_pct1_max`, `n_pass_specificity` | 每条规则各通过多少 | 看哪条规则卡得最严——若 specificity 卡掉大部分→marker 普遍不特异;若 pct1_min 卡掉大部分→表达普遍低 |
| `[扩展]` | `filter_efficiency` = n_markers / n_before_filter | 过滤收紧程度 | 太低→过滤太严;太高→过滤太松 |
| `[扩展]` | `grey_zone_rate` = n_grey / n_before_filter | 灰区比例 | 高→大量 marker 处于 0.1-0.25 灰区,特异性普遍弱 |

### pseudobulk_de — 稀有簇 Pseudobulk 切换

稀有簇(<5%)切换到 pseudobulk DE(按样本聚合后 t-test)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `is_rare`, `de_method` | 该簇是否稀有、用的 DE 方法 | 若 pseudobulk 且 marker 数为 0→pseudobulk 不适用(样本太少),切回单细胞 Wilcoxon |
| `[扩展]` | `n_pseudobulk_samples` per rare cluster | pseudobulk 聚合了几个样本 | <3→pseudobulk 检验力不足 |
| `[扩展]` | `pseudobulk_wilcoxon_marker_overlap` | 两种方法的一致性(若都跑了) | 高→两种方法一致,结果可信;低→方法敏感,选 pseudobulk |

### LLM 描述模板(step2)
> "Step2 完成:N 个簇共找到 M 个 marker 行。簇 X 只有 0 个 marker(原因:pseudobulk 样本不足)。建议:..."

---

## 3. 知识图谱查询阶段(step3_kg)

> 产出文件:`step3_kg/kg_hits.json`(每簇候选细胞类型 + gene_to_cts + ancestors)、`step3_kg/kg_source.txt`(KG 来源)

### query_genes — KG 查询

收集所有 marker 基因,查 KG 获取 gene→cell_type 映射。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `overall_hit_rate` | marker 基因在 KG 中的命中率 | >80%→基因 ID 匹配且 KG 覆盖好;10-30%→可能 ID 不匹配(换 `--gene-key`)或 organ 不对齐;<10%→严重不匹配 |
| `[核心]` | `n_markers_hit` (每簇) | 该簇 marker 命中 KG 的数 | 若 n_markers 充足但 n_markers_hit 很低→基因在 KG 里没对应(物种特异性基因) |
| `[扩展]` | `n_unique_genes_queried`, `n_genes_with_hits`, `n_genes_without_hits` | 查询规模与命中情况 | 看命中的分母 |
| `[扩展]` | `mapping_multiplicity_per_gene` (mean, distribution) | 每个 hit gene 映射到多少个 cell type | 高→KG 噪声大(marker 关联很多类型);低→映射干净 |
| `[扩展]` | `genes_with_no_kg_entry` (list) | 供调试 ID 匹配问题 | 看 these 基因是否 ID 系统不对 |
| `[扩展]` | `mean_candidates_per_gene` | 每个 marker 平均产生多少候选(噪声水平) | 高→KG 嘈杂,候选会多 |

### aggregate_candidates — 候选聚合

per cluster 聚合 gene→cell_type 映射,按 marker_count → mean_confidence 排名候选。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `candidates`: cell_type, supporting_markers, marker_count, mean_confidence, min_confidence, sources | 候选列表 | marker_count 是核心证据(多少独立 marker 支持);mean_confidence 是 KG 的 relation_confidence(0-1),高(>0.8)→有文献背书 |
| `[核心]` | `n_candidates` per cluster | 候选数量 | **过多(>20)→marker 集太嘈杂,需收紧 step2 过滤;过少(1-2)→KG 覆盖窄** |
| `[扩展]` | `marker_coverage` = n_supporting_markers_unique / n_markers_queried | 有多少比例 marker 支持了候选 | 低→大量 marker 没命中任何候选,候选代表性弱 |
| `[扩展]` | `candidate_count_distribution` | marker_count 在候选间的分布 | 集中(一个候选独大)→清晰;分散(都差不多)→模糊 |
| `[核心]` | `candidate_ranking_entropy` | Shannon 熵 over candidate marker_counts | **高→分散(候选势均力敌,模糊);低→集中(第一候选独大)** |
| `[扩展]` | `n_tied_at_top` | 与第一候选 marker_count 相同的候选数 | >0→第一候选不唯一,需细化 |
| `[扩展]` | `confidence_distribution_across_candidates` (mean, std) | 候选间置信度离散度 | std 大→候选质量参差 |
| `[扩展]` | `n_unique_cell_types_across_clusters` | 全局命中了多少不同 cell type | 看全局多样性 |

### query_hierarchy — 本体层级查询(ancestors)

对每个命中的细胞类型,查 KG 本体中的祖先节点(ontology_relation,默认 ≤3 跳)。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `ancestors` map(写入 kg_hits.json) | {cell_type: [ancestor_name,...]} | **step4 的 `first_second_ancestor_overlap` 直接用它**;也用于判断两个候选是否为父子关系(如 "root cap" 是 "lateral root cap" 的祖先)。空列表→该类型在本体中没有已收录祖先,不代表不存在,只是 KG 没存 |
| `[核心]` | `n_cell_types_queried` / `n_cell_types_with_ancestors` | 层级查询规模 | 命中类型大多无祖先→本体收录浅,父子关系判断需更多依赖生物学知识 |

### LLM 描述模板(step3)
> "Step3 完成:KG 来源 neo4j,整体命中率 98%。簇 0 的第一候选 'lateral root cap' 有 36 个支持 marker(平均置信度 0.75),第二候选 'root cap' 也有 36 个..."

---

## 4. 簇判断阶段(step4_judge)

> 产出文件:`step4_judge/annotations.json`(每簇第一/第二候选 + 原始证据)

### rank_candidates — 候选排名与差距

取 first/second 候选,报告 raw evidence。**无 status label,无 threshold,无 pass/fail**。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `first_candidate`, `second_candidate` | 候选细胞类型 | 按 marker_count 降序的第一和第二 |
| `[核心]` | `first_count`, `second_count` | 支持 marker 数 | **核心判断依据**。first >> second(如 40 vs 10)→第一可信;first ≈ second(如 36 vs 36)→势均力敌,可能是同义词/父子类或真模糊 |
| `[核心]` | `first_mean_confidence`, `second_mean_confidence` | KG 置信度均值 | 若 first_count 接近但 first_mean_confidence 明显更高→倾向第一("少而强"胜"多而弱") |
| `[核心]` | `first_supporting_markers` | 第一候选的支持基因 | LLM 可检查这些是否是该细胞类型的已知 marker(需外部知识) |
| `[核心]` | `count_ratio` = first_count / max(second_count, 1) | 相对优势(1=并列,2=2倍) | 直观量化差距 |
| `[核心]` | `count_diff` = first_count - second_count | 绝对差距 | 与 count_ratio 互补,小样本时 ratio 易夸大 |
| `[扩展]` | `confidence_diff` = first_mean_confidence - second_mean_confidence | 置信度差距 | 正→第一候选质量更高 |
| `[扩展]` | `n_tied_at_first` | 与第一并列的候选数 | >0→第一不唯一 |
| `[扩展]` | `frac_support_captured_by_first` = first_count / sum(all counts) | 第一候选捕获了多少支持 | →1→第一候选主导;低→支持分散 |
| `[核心]` | `first_second_ancestor_overlap` | 两个候选在 KG 本体中是否有父子关系 | **有→并列是同义词/层级关系**(如 "root cap" ⊃ "lateral root cap"),选更具体的;无→真模糊 |

### LLM 判断逻辑(不写进代码,由 LLM 执行)
1. **first_count > second_count 且差距大**(如 first >= 2× second):第一候选可信,报告
2. **first_count ≈ second_count**:检查两个候选是否同义词/层级关系(查 `first_second_ancestor_overlap` 或用生物学知识)。若是,选更具体;若不是,说明"势均力敌,需 step5 细化"
3. **first_candidate = None**:无 KG 命中,标记 unknown

### LLM 描述模板(step4)
> "Step4 完成:29 簇中 20 个第一候选严格领先(first_count > second_count),9 个并列。并列的簇包括:簇 0(lateral root cap 36 vs root cap 36——父子类关系)..."

---

## 5. 细化阶段(step5_refine)

> 产出文件:`step5_refine/refined_annotations.json`
> 触发条件(定义性,非阈值):`first_count <= second_count` 的簇会被分析(子聚类 + 重做 DE + 重查 KG);`< min_cells(默认 100)` 的簇直接 skipped(细胞数不足,SOP-5A)。

### candidate_autocorr — 候选倾向自相关(预判细分必要性)

对 ambiguous 簇,用 `sc.tl.score_genes` 计算每个细胞对两个候选的倾向分数 x_i,用 `sc.metrics.morans_i` 在 kNN 图上算自相关。**在 subcluster 之前执行**,作为预判。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `morans_i` | 候选倾向分数在 kNN 图上的 Moran's I(-1~1) | **高(>0.3)→有子群体结构,细分有效**;低(≈0)→随机混合,marker 共享,细分无效;负→两个候选反相关(一种抑制另一种),也可能有子群体 |
| `[核心]` | `gearys_c` | 候选倾向分数的 Geary's C(0~2) | 低(<0.7)→有子群体(与 Moran's I 高一致);≈1→随机;>1.3→负相关 |
| `[扩展]` | `score_distribution` (percentiles, histogram) | 倾向分数的分布 | **双峰→两群体**(一个偏 candidate-1,一个偏 candidate-2);单峰居中→所有细胞同时表达两组 marker(同质) |
| `[扩展]` | `score_bimodality_coefficient` | 双峰系数 | >0.555→倾向分数双峰→两群体 |
| `[扩展]` | `cand1_score_mean`, `cand2_score_mean` | 两组 marker 的平均得分 | 差距大→一个候选表达更强,可能倾向那个;接近→两组都表达(marker 共享,可能是 KG 层级关系) |

### LLM 判断逻辑(预判,不写进代码)
1. **morans_i 高 + score 双峰**:有真实子群体,触发 subcluster,细化大概率有效
2. **morans_i 低 + score 单峰居中**:所有细胞同时表达两组 marker,没有子群体结构,可跳过 subcluster(refine_skipped)
3. **morans_i 低 + score 双峰但无空间结构**:两个群体在 kNN 图上随机混合,subcluster 可能分不出来,需谨慎

### subcluster — 子聚类

对 ambiguous 簇子集化 + 重新 Leiden 聚类。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_subclusters`, `outcome` (analyzed/skipped) | 子簇数 + 结果 | skipped→细胞不足或子聚类只产生 1 簇 |
| `[扩展]` | `sub_cluster_size_distribution` (min, max, median, CV) | 子簇大小均匀度 | CV 高→子簇大小悬殊 |
| `[扩展]` | `sub_silhouette` | 子聚类轮廓系数 | 低→子聚类不清晰,细化无效 |
| `[扩展]` | `frac_smallest_subcluster` | 最小子簇占比 | 太小(<100 细胞)→DE 无检验力 |

### subcluster_de — 子簇 DE + KG 重查

在子簇间重做 DE + 重查 KG。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `sub_clusters`: first/second candidate + count | 子簇候选 | 若子簇第一候选严格领先→细化有效,父簇确实混合;仍并列→同质不确定群体 |
| `[核心]` | `n_subclusters_with_distinct_type` | 多少子簇得到不同标签 | **高→细化有效(父簇是混合);0→细化无效(父簇同质)** |
| `[扩展]` | `sub_count_ratio` per sub-cluster | 子簇内 first/second 比 | 看子簇内部是否清晰 |

### marker_overlap — 子簇间 Marker 重叠

计算子簇间 marker 集合的两两重叠。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `max_overlap`, `pairs` (overlap/min) | 最大重叠 + 簇对 | 高重叠(>0.5)→子簇没真正分开,细化无效 |
| `[扩展]` | `mean_overlap` | 平均重叠 | 不只看 max,看整体 |
| `[核心]` | `Jaccard_index` per pair | 交集/并集(比 overlap/min 更严格) | **高→子簇 marker 高度重合,细化无效;低→子簇是不同类型** |
| `[扩展]` | `frac_unique_markers` per sub-cluster | 每个子簇的独有 marker 比例 | 高→子簇有独立身份;低→子簇是人为切分 |
| `[扩展]` | `overlap_matrix` | 完整 N×N 重叠矩阵 | 看哪些子簇对最像 |

### type_membership — 类型归属检查

检查子簇的候选类型是否在父簇候选范围内。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `types_in_parent_candidates` (boolean per sub-cluster) | 子簇类型是否在父候选范围 | 若子簇类型与父候选完全无关→可能是批次/质量驱动的假分裂 |
| `[扩展]` | `n_in_range`, `n_out_of_range` | 归属/非归属计数 | out_of_range 多→子簇产生新类型,需谨慎 |
| `[扩展]` | `frac_novel_types` | 子簇产生的新类型比例 | 高→父簇确实含未知类型 |

### unknown_overlap — Unknown 簇 Marker 重叠

对无 KG 命中的簇,计算两两 marker 重叠。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `avg_overlap`, `pairs` | 平均重叠 | 高→单一未知类型;低→多个独立未知类型 |
| `[扩展]` | `Jaccard` per pair | 交集/并集 | 更严格的重叠度量 |
| `[扩展]` | `n_unknown_clusters`, `frac_unknown` | unknown 簇数与比例 | 高(>30%)→KG 覆盖不足或 organ 不对齐 |

### LLM 描述模板(step5)
> "Step5 完成:20 个簇第一候选严格领先,9 个被子簇分析。其中簇 0 分成 8 个子簇,但子簇间 marker 重叠 60%,细化无效——该簇是同质的 lateral root cap / root cap 混合群体..."

---

## 6. 验证阶段(step6_validate)

> 产出文件:`step6_validate/final_annotations.json`(最终注释带证据)、`step6_validate/report.md`(人类可读报告)、`step6_validate/figures/cluster_*_markers.png`(供人类查看,LLM 应读 JSON 中的 `top_markers_expression`)
> `run` 1× proc 加载(`--backed` 时只按列读 top-3 marker 的 raw.X,`backed_mode` 字段标注实际模式;失败自动回退全量加载)。`report` 子命令 0 加载。

### marker_expression — Top Marker 表达验证

对每簇 top-3 DE marker 计算簇内外表达统计。pipeline 给出**透明规则的证据型置信度**(`confidence` 字段:first_count>15 且 count_ratio≥2 → high;count_ratio≥1.5 → medium;否则 low,规则写在 `_meta.confidence_rule`);**这是证据快照,LLM 在 label_confirm 判断点可依据更多证据覆盖它**。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `pct1` (per marker) | top-3 DE marker 在本簇的表达比例 | 高(>0.7)→marker 稳定表达,标签有支撑;低(<0.3)→不稳定 |
| `[核心]` | `pct2` (per marker) | top-3 DE marker 在其他簇的表达比例 | pct1-pct2 差值大→特异性好;pct2 接近 pct1→所有簇都表达,特异性差 |
| `[核心]` | `mean_expr` (per marker) | top-3 marker 在本簇的平均表达量 | 与 `mean_expr_other` 对比看特异性 |
| `[核心]` | `mean_expr_other` (per marker) | top-3 marker 在其他簇的平均表达量 | 远低于 `mean_expr`→特异;接近→不特异 |
| `[扩展]` | `specificity_index` = pct1 - pct2 (预计算) | 不必让 LLM 自己减 | 直观看特异性 |
| `[核心]` | `effect_size` (Cohen's d) = (mean_in - mean_out) / pooled_std | 标准化效应量 | **跨基因/跨簇可比**;>0.8=大效应,0.5=中,0.2=小 |
| `[扩展]` | `fold_change` = mean_in / max(mean_out, ε) | 倍数变化 | 高→marker 在簇内富集 |
| `[核心]` | `AUC` per top marker | 判别力(0.5=无,1.0=完美) | >0.7=好 marker |
| `[扩展]` | `expression_ratio` = mean_expr / max(mean_expr_other, ε) | 簇内/簇外表达比 | 高→特异 |
| `[扩展]` | `mean_top3_pct1` | 簇级 marker 稳定性 | 跨簇对比,低→该簇 marker 稳定性差 |
| `[扩展]` | `mean_top3_specificity` | 簇级 marker 特异性 | 跨簇对比 |
| `[扩展]` | `frac_top3_pct1_above_0.5` | top-3 中达标的比例 | 低→top-3 里混了弱 marker |
| `[扩展]` | `marker_gene_overlap_score` | DE marker 与 KG 已知 marker 的重叠分数 | 高→marker 与知识图谱一致,可信;低→marker 可能是新的或 DE 噪声 |

### global_summary — 全局汇总

跨簇汇总注释结果。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `n_clusters`, `n_unknown`, `unknown_rate` | 簇数 + unknown 数与比例 | unknown_rate 高(>30%)→KG 覆盖不足或 organ 不对齐 |
| `[核心]` | `n_unique_labels` | 多少不同注释标签 | 看多样性 |
| `[核心]` | `label_diversity` = n_unique_labels / n_clusters | 1=全不同,低=大量重复 | **低→过聚类(多个簇贴同标签)** |
| `[扩展]` | `cell_type_proportions` | 每种注释类型的细胞比例 | 看是否符合组织生物学预期 |
| `[扩展]` | `effective_n_types` (Shannon) | 类型多样性(香农熵) | 看有效类型数 |
| `[扩展]` | `mean_first_second_gap` | 全局 first-second 平均差距 | 低→整体模糊,需细化 |
| `[扩展]` | `mean_top3_specificity_across_clusters` | 全局 marker 质量 | 低→整体 marker 质量差 |
| `[扩展]` | `co_annotation_matrix` | 哪些簇共享同一标签 | 共享多→过聚类 |
| `[扩展]` | `cross_cluster_marker_reuse` | 多少 marker 被多簇共享 | **高→过聚类(本应分开的簇用了相同 marker)** |

### LLM 向用户总结的模板
> "注释完成:N 个簇,KG 来源 neo4j(版本 X),整体命中率 Y%。
> - Z 个簇第一候选明确领先(first_count 远大于 second_count)
> - W 个簇第一/第二候选并列(多为同义词或父子类关系)
> - V 个簇无 KG 命中(unknown)
>
> 逐簇标签:[cluster 0 = lateral root cap(36 支持 marker,置信度 0.75,top marker AT4G23590 pct1=0.85 pct2=0.02)...]
>
> 注意:以上可信度判断基于 first/second count 差距和 marker 表达,由 LLM 解读;`confidence` 字段为证据型初值,LLM 可覆盖。"

### 元数据块
`final_annotations.json` 的 `_meta` 包含 KG source + version、阈值参数、scanpy 版本、日期。**必须在总结中告知用户,保证可追溯**——"cluster 0 = T cell" 没有依据是没意义的。

---

## 7. 诊断阶段(step7_diagnose)

> 产出文件:`step7_diagnose/step7_diagnose.json`(原始测量值)、`step7_diagnose/report.md`(表格报告)
> **0 次 h5ad 加载**:批次熵读 `step1_prepare/obs_snapshot.csv`,其余全读 step JSON——这是硬约束,任何需要表达矩阵的指标都不属于本步。
> 原则:报告原始测量,无阈值警告,无 pass/fail。LLM 结合组织生物学背景判断。

### hit_rate — 每簇 KG 命中率

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `per_cluster_hit_rate` | 每簇的命中率 | 低(<30%)的簇,标签可信度应打折——可能是基因 ID 不匹配或 KG 覆盖不足 |

### candidate_count — 每簇候选计数

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `per_cluster_candidate_count` | 每簇的候选数 | 多(>20)→marker 集太嘈杂,需收紧 step2 过滤;少(1-2)→KG 覆盖窄 |

### first_second — 每簇 first/second

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `per_cluster_first_second` (first_count, second_count, top_strictly_ahead) | 第一候选是否严格领先 | top_strictly_ahead=False 的簇是 step5 分析对象;大量 False→数据与本体匹配度有结构性模糊(如层级本体) |

### batch_entropy — 每簇批次熵

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `per_cluster_batch_entropy`, `batch_key_used` | 批次/样本的 Shannon 熵 | 接近 0→单批次主导——**不要自动判为批次效应**,先查该簇对应的样本/基因型/条件,若对应生物学有意义的条件(如突变体)则是真实群体 |
| `[扩展]` | `batch_cluster_chi_square` | 批次×聚类卡方检验 | 显著→批次与聚类不独立 |

### metadata_check — 元数据完整性

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[核心]` | `metadata_missing` | 缺失的元数据字段 | 影响可追溯性,应补齐 |

### cross_cluster — 跨簇质量测量

跨所有簇的端到端原始测量与汇总。

| 状态 | 指标 | 含义 | LLM 解读要点 |
|---|---|---|---|
| `[扩展]` | `label_uniqueness` = n_unique_labels / n_clusters | 标签唯一性 | 低→过聚类 |
| `[扩展]` | `annotation_entropy` | Shannon 熵 over label proportions | 看有效类型多样性 |
| `[扩展]` | `cluster_purity_proxy` = mean(silhouette) | 全局聚类纯度代理 | 低→整体聚类质量差 |
| `[扩展]` | `mean_first_count_gap` | 全局 first-second 差距均值 | 低→整体模糊 |
| `[扩展]` | `cross_cluster_marker_overlap_matrix` | 簇间 marker 共享度矩阵 | 高共享→过聚类 |
| `[扩展]` | `mean_cross_cluster_marker_overlap` | 全局簇间平均 marker 重叠 | 高→大量簇共享 marker,过聚类信号 |
| `[扩展]` | `n_clusters_no_candidates` | 无任何 KG 候选的簇数 | 与 unknown 率一致时→KG 覆盖不足或 organ 不对齐 |
| `[扩展]` | `paga_connectivity_matrix` | 簇间 PAGA 连通性矩阵 | 高连通→簇间关系密切(可能是同类型的不同状态);低→簇独立。可补充 KG 层级判断:若两个 ambiguous 簇 PAGA 高,可能是同一类型的子状态 |
| `[扩展]` | `paga_expression_entropies` | 每簇的表达熵 | 高→表达多样,可能需细化;低→表达集中,同质 |
| `[扩展]` | `cell_cycle_contamination` | 细胞周期基因驱动的簇数 | >0→有簇被细胞周期主导,不是真实细胞类型,需在 step1 regress_out 细胞周期后重跑 |

### LLM 解读逻辑
- **不自动判 pass/fail**。LLM 读取上述原始值,结合组织生物学背景做判断
- 批次熵低的簇:不要自动判为"批次效应"。先查该簇对应的样本/基因型/条件——若对应一个生物学上有意义的条件(如突变体),则是真实群体
- 命中率低的簇:不要自动判为"标签错误"。先查基因 ID 系统是否匹配

---

### 实现补充说明(P2 评审修复)

- `step1_prepare` 的 `qc_metrics.json` 顶部含 `organ` 字段(来自 `--organ`),step6 的 `_meta.organ` 从它继承——不要用写死的 "root" 解读非根组织数据。
- `choose_resolution` 的 `auto_knee_not_applicable=true` 表示簇数随分辨率单调递增(无拐点),自动选择了中间分辨率而非最高;此时 `resolution_select` 判断点应人工确认。
- `step3_kg` 的 `kg_version` 是 Neo4j 服务版本(字段 `kg_version_source=neo4j-server`)——KG 本身无版本号,这是溯源代理值;`--species` 现在会作为 `g.Species` 过滤参与查询。
- `step3_kg.query_hierarchy` 的查询错误单独存于 `query_errors` 字段,不会混入 `ancestors` 统计。
- `--max-ancestor-hops 0` 表示跳过层级查询(ancestors 为空);`>=1` 才执行。
- step6 `final_annotations.json` 的 `_meta.kg_version` 亦为上述代理值;`metadata_check` 只检查字段存在。

## 附:解读时的常见陷阱

1. **植物 mt/chloroplast 与动物不同**:线粒体基因是 `ATMG` 前缀,叶绿体是 `ATCG` 前缀,`MT-` 前缀匹配不到植物基因。`--max-mt-pct 20` 默认值是动物/血液的,肝脏天然高 mt,植物叶看 chloroplast。
2. **层级本体下的并列不是模糊**:植物根 "root cap" ⊃ "lateral root cap" 共享所有 marker,first_count == second_count 是正常的,选更具体的。查 `first_second_ancestor_overlap` 或用生物学知识。
3. **小样本时 ratio 会骗人**:first_count=2, second_count=1 时 count_ratio=2 看着像"2 倍优势",其实只差 1 个 marker。同时看 count_diff。
4. **pct1 高不等于好 marker**:管家基因 pct1 也高(所有细胞都表达)。必须同时看 pct2(低才特异)。
5. **silhouette 低不一定是聚类错**:连续谱的细胞类型(如发育梯度)silhouette 天然低,不代表聚类无效,可能是数据本身是连续的。
6. **批次熵低不一定是批次效应**:突变体/条件特异群体天然单批次,先查条件注释。