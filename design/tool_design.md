# 工具设计文档

## 1. 设计目标

1. **最小化 h5ad 加载次数** — h5ad 文件可达 GB 级,每次 I/O 是主要时间瓶颈
2. **最大化单次加载的计算密度** — 在一次加载中完成所有需要该数据的操作和指标计算
3. **轻量中间件** — 将 obs/var/统计量分离写出,让不需要表达矩阵的操作读小文件而非 h5ad
4. **零额外加载** — 130 个扩展指标全部在已有加载中完成,不新增 h5ad 读取
5. **可恢复** — 每步输出独立 JSON,断点续跑无需从头开始
6. **统一日志** — 所有指标和 LLM 判断写入 `run_log.jsonl`(见 `trajectory_design.md`)
7. **环境变量管外部依赖** — 知识图谱等外部依赖通过环境变量配置,不硬编码在代码或配置文件中

---

## 2. 数据依赖分析

### 2.1 数据层级分类

将 AnnData 的数据分为 5 个层级,不同操作只需要不同层级:

| 层级 | 内容 | 大小(本数据集) | 可否分离写出 |
|---|---|---|---|
| **FULL** | 完整 AnnData (X + raw.X + obs + var + obsm + obsp) | GB 级 | 已有 processed.h5ad |
| **RAW** | raw.X (归一化前的原始 counts,53K 基因) | ~1.5 GB | 嵌在 h5ad 内,无法分离 |
| **X** | scaled HVG 矩阵 (2K 基因) | ~50 MB | 嵌在 h5ad 内 |
| **OBS** | 细胞元数据 (leiden, batch, QC 变量等) | ~5 MB | **可分离写出 CSV** |
| **JSON** | 前一步的 JSON 输出 | ~100 KB | 已有 |

### 2.2 每个操作的数据需求

| 操作 | 需要的层级 | 能否用 sidecar 替代 h5ad? |
|---|---|---|
| step1_prepare.load_data 加载原始数据 | FULL (raw h5ad) | — |
| step1_prepare.compute_qc 计算质控变量 | FULL (需要 X 计算 counts) | 否 |
| step1_prepare.qc_distribution 质控分布统计 | **OBS** (obs 列) | **是 → obs_snapshot.csv** |
| step1_prepare.qc_plot 质控可视化 | **OBS** (obs 列) | 是,但绘图通常和 step1_prepare.compute_qc 一起做 |
| step1_prepare.filter_cells 细胞过滤 | FULL (修改 X) | 否 |
| step1_prepare.filter_genes 基因过滤 | FULL (修改 X) | 否 |
| step1_prepare.detect_doublets 双峰检测 | FULL (需要 X) | 否 |
| step1_prepare.normalize 归一化 | FULL (修改 X, 创建 raw) | 否 |
| step1_prepare.select_hvg HVG 选择 | FULL (需要 X) | 否 |
| step1_prepare.pca 缩放与 PCA | FULL (修改 X, 创建 obsm) | 否 |
| step1_prepare.knn_graph kNN 图构建 | **OBSM** (X_pca) | 否(但在 step1_prepare.pca 加载中已完成) |
| step1_prepare.leiden_cluster Leiden 聚类 | **OBSP** (connectivities) | 否(但在 step1_prepare.knn_graph 加载中已完成) |
| step1_prepare.choose_resolution 分辨率选择 | **OBS** (leiden_r 列) | **是 → obs_snapshot.csv** |
| step1_prepare.umap UMAP 嵌入 | **OBSP** (connectivities) | 否(但在 step1_prepare.leiden_cluster 加载中已完成) |
| step1_prepare.batch_mixing 批次混合 | **OBS** (leiden + batch) | **是 → obs_snapshot.csv** |
| step1_prepare.write_output 写出 | FULL → h5ad + sidecars | — |
| step2_markers.de_rank DE 排序 | **RAW** (raw.X + leiden) | 否 |
| step2_markers.pct1_pct2 pct1/pct2 | **RAW** (raw counts + leiden) | 否 |
| step2_markers.filter_markers Marker 过滤 | **JSON** (DE 表 + pct) | **是,不需要 h5ad** |
| step2_markers.pseudobulk_de Pseudobulk DE | **RAW** (raw.X + leiden + sample) | 否 |
| step2_markers.write_markers 写出 markers | **JSON** | **不需要 h5ad** |
| step3c_kg.connect KG 连接 | **JSON** (markers.json) | **不需要 h5ad** |
| step3c_kg.query_genes KG 查询 | **JSON** (markers.json) | **不需要 h5ad** |
| step3c_kg.query_hierarchy 层级查询 | **JSON** (KG 结果) | **不需要 h5ad** |
| step3c_kg.aggregate_candidates 候选聚合 | **JSON** (gene_to_cts) | **不需要 h5ad** |
| step3c_kg.write_hits 写出 KG 命中 | **JSON** | **不需要 h5ad** |
| step4_judge.rank_candidates 候选排名 | **JSON** (kg_hits.json) | **不需要 h5ad** |
| step4_judge.write_annotations 写出注释 | **JSON** | **不需要 h5ad** |
| step5_refine.candidate_autocorr 候选自相关 | **X** (kNN 图 + score_genes 需表达) | 否(在 step5_refine.subcluster 加载中完成) |
| step5_refine.subcluster 子聚类 | **X + RAW** (但只需 ambiguous 簇的细胞) | 部分(backed 模式子集化) |
| step5_refine.subcluster_de 子簇 DE | **RAW** (子集 raw.X) | 否(但在 step5_refine.subcluster 加载中已完成) |
| step5_refine.subcluster_kg 子簇 KG 重查 | **JSON** (cached gene_to_cts) | **不需要 h5ad** |
| step5_refine.marker_overlap marker 重叠 | **JSON** (sub_markers) | **不需要 h5ad** |
| step5_refine.type_membership 类型归属 | **JSON** | **不需要 h5ad** |
| step5_refine.unknown_overlap Unknown 重叠 | **JSON** (markers.json) | **不需要 h5ad** |
| step5_refine.write_refined 写出细化 | **JSON** | **不需要 h5ad** |
| step6_validate.marker_expression marker 表达验证 | **RAW** (raw.X + leiden, 但只需 top-3 基因) | **部分(backed 模式按列读)** |
| step6_validate.violin_plot 小提琴图 | **X** (normalized, 但只需 top-3 基因) | **部分(backed 模式按列读)** |
| step6_validate.global_summary 全局汇总 | **JSON** | **不需要 h5ad** |
| step6_validate.write_report 报告 | **JSON** | **不需要 h5ad** |
| step6_validate.write_final 写出最终 | **JSON** | **不需要 h5ad** |
| step7_diagnose.hit_rate 命中率 | **JSON** (kg_hits.json) | **不需要 h5ad** |
| step7_diagnose.candidate_count 候选计数 | **JSON** | **不需要 h5ad** |
| step7_diagnose.first_second first/second | **JSON** (annotations.json) | **不需要 h5ad** |
| step7_diagnose.batch_entropy 批次熵 | **OBS** (leiden + batch) | **是 → obs_snapshot.csv** |
| step7_diagnose.metadata_check 元数据完整性 | **JSON** (final.json) | **不需要 h5ad** |
| step7_diagnose.cross_cluster 跨簇报告 | **JSON** + 部分 OBS | **不需要 h5ad(用 sidecar)** |

### 2.3 统计

| 数据需求 | 操作数 | 占比 |
|---|---|---|
| 仅需 JSON | 22 | 48% |
| 仅需 OBS (可用 sidecar) | 5 | 11% |
| 需 RAW (全量表达矩阵) | 6 | 13% |
| 需 FULL (完整 AnnData) | 10 | 22% |
| 可用 backed 模式 | 3 | 7% |

**关键发现:48% 的操作完全不需要 h5ad;59% 的操作可用 JSON 或 sidecar 替代。**

---

## 3. 中间数据(sidecar)方案

### 3.1 sidecar 文件设计

在 step1_prepare.write_output(写出 processed.h5ad)时,同步写出以下轻量文件:

```
step1_prepare/
  processed.h5ad          ← 完整 AnnData (GB 级,只在需要表达矩阵时加载)
  qc_metrics.json         ← 全部 Step 1 指标 (enriched, ~50 KB)
  obs_snapshot.csv        ← obs DataFrame (~5 MB) — 新增
  var_snapshot.csv        ← var DataFrame (~500 KB) — 新增
```

### 3.2 obs_snapshot.csv 内容

```csv
cell_id,leiden,batch,total_counts,n_genes_by_counts,pct_counts_mt,pct_counts_chloroplast,doublet_score,...
```

- 行数 = 过滤后的细胞数 (~34K)
- 列数 = obs 的全部列 (leiden, batch, QC 变量, doublet_score)
- 用途: step1_prepare.qc_distribution/13/15/44/46 可直接读 CSV 而非 h5ad
- 写出时机: step1_prepare.write_output,与 processed.h5ad 同步

### 3.3 var_snapshot.csv 内容

```csv
gene_id,highly_variable,mt,chloroplast,means,dispersions,dispersions_norm,...
```

- 行数 = HVG 数 (~2K)
- 列数 = var 的全部列
- 用途: 调试 HVG 选择,检查基因过滤

### 3.4 qc_metrics.json 结构(enriched)

```json
{
  "n_cells_before": 10000,
  "n_cells_after": 9980,
  "frac_cells_lost": 0.002,

  "qc_variables": {
    "n_genes_by_counts": { "mean":..., "std":..., "skewness":..., "kurtosis":..., "bimodality":..., "percentiles":{...}, "histogram":{...} },
    "total_counts": { ... },
    "pct_counts_mt": { ... },
    "pct_counts_chloroplast": { ... }
  },

  "cell_filtering": {
    "n_before": 10000,
    "n_after": 9980,
    "frac_lost": 0.002,
    "funnel": {
      "min_genes": { "n_passed": 9990, "n_lost": 8 },
      "max_mt_pct": { "n_passed": 9985, "n_lost": 5 },
      "max_cp_pct": { "n_passed": 9980, "n_lost": 5 },
      "n_lost_multiple_criteria": 0
    },
    "distribution_shift": { "n_genes_by_counts": {"delta_median":..., "delta_iqr":..., "ks_stat":...}, ... }
  },

  "gene_filtering": {
    "n_before": 20000,
    "n_after": 19500,
    "frac_lost": 0.037,
    "n_mt_removed": 7,
    "n_cp_removed": 99
  },

  "doublet": {
    "n_detected": 280,
    "frac_doublets": 0.008,
    "score_distribution": { "mean":..., "std":..., "percentiles":{...}, "bimodality":... },
    "implied_threshold": 0.28
  },

  "normalization": {
    "median_library_size_before": 45000,
    "target_sum": 10000,
    "frac_zero_after_norm": 0.72
  },

  "hvg": {
    "n_selected": 2000,
    "frac_of_total": 0.039,
    "dispersion_distribution": { "mean":..., "median":..., "percentiles":{...} },
    "mean_expression_distribution": { ... },
    "nongvg_dispersion_gap": 0.8
  },

  "pca": {
    "variance_explained": [0.08, 0.05, 0.03, ...],
    "cumulative_variance": [0.08, 0.13, 0.16, ...],
    "n_pcs_for_50pct": 12,
    "n_pcs_for_80pct": 25,
    "n_pcs_for_90pct": 35,
    "knee": 15
  },

  "knn_graph": {
    "n_connected_components": 1,
    "graph_density": 0.0009,
    "mean_degree": 15.0,
    "degree_distribution": { "median":15, "p99":30, "frac_isolated":0.0 }
  },

  "clustering": {
    "resolution_cluster_counts": {"0.4":18, "0.6":24, "0.8":29, "1.0":34, "1.2":38},
    "resolution_chosen": 0.8,
    "n_clusters": 29,
    "cluster_sizes": {"0":1200, "1":800, ...},
    "cluster_size_distribution": {"min":50, "max":5000, "median":800, "gini":0.4, "cv":1.2, "n_rare":3, "n_singleton":0},
    "silhouette": {
      "overall": {"mean":0.35, "std":0.15},
      "per_cluster": {"0": {"mean":0.45, "median":0.48}, "1": {"mean":0.32, "median":0.30}, ...},
      "n_clusters_negative_mean_silhouette": 2
    },
    "modularity": 0.72
  },

  "resolution_stability": {
    "adjacent_ari": {"0.4-0.6":0.85, "0.6-0.8":0.92, "0.8-1.0":0.88, "1.0-1.2":0.82},
    "stability_at_chosen": 0.90,
    "n_stable_clusters": 22
  },

  "umap": {
    "trustworthiness": 0.95,
    "separation_ratio": 2.3,
    "n_overlapping_clusters": 3
  },

  "batch_mixing": {
    "per_cluster_entropy": {"0":1.2, "1":0.8, ...},
    "per_cluster_max_batch_fraction": {"0":0.45, "1":0.80, ...},
    "batch_key_used": "sample"
  }
}
```

### 3.5 其他步骤的 enriched JSON

同理,step2_markers~step6_validate + step7_diagnose 的 JSON 也按操作分块 enriched:

| 文件 | 新增操作块 |
|---|---|
| `step2_markers/markers.json` | `de_distribution` (logfc/pval 分布 + BH-FDR + AUC + inflation λ), `filter_funnel` |
| `step3c_kg/kg_hits.json` | `query_stats` (n_genes_queried/with_hits/multiplicity), `candidate_stats` (n_candidates/ranking_entropy/n_tied) |
| `step4_judge/annotations.json` | `gap_metrics` (count_ratio, count_diff, confidence_diff, ancestor_overlap) |
| `step5_refine/refined_annotations.json` | `sub_cluster_quality` (silhouette, size_dist), `overlap_metrics` (jaccard, mean_overlap, unique_frac) |
| `step6_validate/final_annotations.json` | `effect_sizes` (cohen_d, AUC, fold_change per marker), `global_stats` (label_diversity, co_annotation_matrix) |
| `step7_diagnose/step7_diagnose.json` | `cross_cluster` (marker_reuse_matrix, label_uniqueness, annotation_entropy) |

---

## 4. 子命令结构与加载策略

### 4.1 核心原则

**一个子命令 = 一次 h5ad 加载 = 完成该加载中所有操作和指标**

```
step1_prepare.py metrics     [1× raw h5ad]  → step1_prepare.load_data~04 (inspect before filter)
step1_prepare.py run         [1× raw h5ad]  → step1_prepare.load_data~16 (full pipeline, all metrics)
step1_prepare.py recluster   [1× proc h5ad] → step1_prepare.leiden_cluster~14,16 (re-cluster only)

step2_markers.py run [1× proc h5ad] → step2_markers.de_rank~21 (DE + all DE metrics)

step3c_kg.py query  [0× h5ad]      → step3c_kg.connect~26 (pure JSON)
step3c_kg.py test-conn [0× h5ad]

step4_judge.py run [0× h5ad]   → step4_judge.rank_candidates~28 (pure JSON)

step5_refine.py run      [1× proc h5ad] → step5_refine.subcluster~35 (subset + sub-cluster + DE)

step6_validate.py run    [1× proc h5ad*] → step6_validate.marker_expression~40 (*backed 模式可选)
step6_validate.py report [0× h5ad]        → step6_validate.write_report (regenerate report)

step7_diagnose.py run      [0× h5ad]       → step7_diagnose.hit_rate~46 (reads JSON + obs_snapshot.csv)
```

### 4.2 加载次数

| 场景 | 设计后加载次数 |
|---|---|
| 完整 pipeline(含 step7_diagnose) | 4 (1 raw + 3 proc) |
| 完整 pipeline(不含 step7_diagnose) | 4 (1 raw + 3 proc) |
| 仅 step7_diagnose | 0 (读 obs_snapshot.csv) |
| recluster | 1 (proc h5ad) |
| step6_validate report | 0 (读 final.json) |
| 新增 130 个扩展指标 | **0 次额外加载**(全部在已有加载中完成) |

### 4.3 详细数据流

```
                ┌─────────────────────────────────────────────────────┐
                │              raw h5ad (磁盘)                         │
                │              GB 级                                  │
                └──────────┬──────────────────────────┬──────────────┘
                           │                          │
                    step1_prepare metrics               step1_prepare run
                    [1× load]                  [1× load]
                           │                          │
                    ┌──────┴──────┐          ┌───────┴────────────────┐
                    │ step1_prepare.load_data load   │          │ step1_prepare.load_data~16 全部           │
                    │ step1_prepare.compute_qc QC vars│          │  (QC→filter→doublet→   │
                    │ step1_prepare.qc_distribution dist  │          │   norm→HVG→PCA→kNN→    │
                    │ step1_prepare.qc_plot plot  │          │   Leiden→select→UMAP→   │
                    └──────┬──────┘          │   batch→metrics)        │
                           │                 └───────┬────────────────┘
                    ┌──────┴──────┐                  │
                    │ qc_metrics   │          ┌───────┴────────────────┐
                    │ .json        │          │ processed.h5ad          │
                    │ (pre-filter) │          │ qc_metrics.json (FULL)  │
                    └──────────────┘          │ obs_snapshot.csv       │
                                              │ var_snapshot.csv       │
                                              └───┬──────────┬────────┘
                                                  │          │
                                          ┌───────┴───┐  ┌───┴──────────────┐
                                          │           │  │ obs_snapshot.csv  │
                                          │           │  │ (5 MB, 侧加载)    │
                                          │           │  └───┬──────────────┘
                                    step2_markers run    step5_refine run  │
                                    [1× load]   [1× load] │
                                          │           │    │
                                    ┌─────┴────┐ ┌────┴───┴──┐
                                    │step2_markers.de_rank DE  │ │step5_refine.subcluster sub  │
                                    │step2_markers.pct1_pct2 pct │ │step5_refine.subcluster_de subDE│
                                    │step2_markers.filter_markers filt│ │step5_refine.subcluster_kg~35   │
                                    │step2_markers.pseudobulk_de pb  │ └────┬──────┘
                                    │step2_markers.write_markers wrt│      │
                                    └────┬────┘      │
                                         │           │
                              ┌──────────┴───────────┴┐
                              │                        │
                        step3c_kg query              step6_validate run
                        [0× h5ad]               [1× load, backed*]
                              │                        │
                        ┌─────┴──────┐          ┌──────┴──────┐
                        │step3c_kg.connect conn  │          │step6_validate.marker_expression expr   │
                        │step3c_kg.query_genes query │          │step6_validate.violin_plot violin │
                        │step3c_kg.query_hierarchy hier  │          │step6_validate.global_summary~40     │
                        │step3c_kg.aggregate_candidates agg   │          └──────┬──────┘
                        │step3c_kg.write_hits wrt   │                 │
                        └─────┬──────┘                 │
                              │                        │
                        step4_judge run                 ┌─────┴──────┐
                        [0× h5ad]                │final.json  │
                              │                  │report.md   │
                        ┌─────┴──────┐           └─────┬──────┘
                        │step4_judge.rank_candidates rank  │                 │
                        │step4_judge.write_annotations wrt   │                 │
                        └─────┬──────┘                 │
                              │                        │
                              └──────────┬─────────────┘
                                         │
                                   step7_diagnose run
                                   [0× h5ad!]
                                         │
                                  ┌──────┴──────┐
                                  │step7_diagnose.hit_rate~46     │
                                  │(reads JSONs │
                                  │+ obs_snap)  │
                                  └─────────────┘

* backed 模式: step6_validate 可选 ad.read_h5ad(backed='r'),只按列读取 top-3 marker 基因,
  避免全量加载 raw.X (~1.5 GB)。当 top-3 markers 跨所有簇去重后 <100 个基因时,
  可将内存占用从 ~1.5 GB 降到 <10 MB。
```

---

## 5. 各阶段详细设计

### 5.1 Step 1: 数据准备

#### 5.1.1 metrics 子命令

```
输入: raw h5ad + --organ
加载: 1× raw h5ad
操作: step1_prepare.load_data~04
输出: step1_prepare/qc_metrics.json (pre-filter distributions)
```

在已有 `_distribution_stats()` 基础上增加: mean, std, IQR, CV, skewness, kurtosis, bimodality_coefficient。全部在 `describe_distribution()` 通用函数中一次计算。

#### 5.1.2 run 子命令

```
输入: raw h5ad + 全部参数
加载: 1× raw h5ad (THE BIG ONE)
操作: step1_prepare.load_data~16 (全部 16 个操作,单次加载内完成)
输出:
  step1_prepare/processed.h5ad
  step1_prepare/qc_metrics.json (enriched: 所有 Step 1 指标)
  step1_prepare/obs_snapshot.csv (新增)
  step1_prepare/var_snapshot.csv (新增)
```

**单次加载内的操作流程:**

```
load raw h5ad                          → step1_prepare.load_data
compute QC vars                         → step1_prepare.compute_qc
compute pre_filter_distributions        → step1_prepare.qc_distribution
plot QC                                → step1_prepare.qc_plot
─── 以下全部在内存中完成,不重新加载 ───
filter cells (funnel: 记录每步丢失)     → step1_prepare.filter_cells
filter genes (记录 n_before/after)      → step1_prepare.filter_genes
scrublet (保存 doublet_score 分布)      → step1_prepare.detect_doublets
normalize (保存 pre/post 统计)          → step1_prepare.normalize
HVG (保存 dispersion 分布)              → step1_prepare.select_hvg
scale + PCA (保存 variance_explained)   → step1_prepare.pca
kNN (保存 graph 指标)                   → step1_prepare.knn_graph
Leiden multi-res (保存 silhouette,      → step1_prepare.leiden_cluster
  modularity, cluster_size_dist)
resolution select (保存 ARI stability) → step1_prepare.choose_resolution
UMAP (保存 trustworthiness)            → step1_prepare.umap
batch mixing (保存 entropy, max_frac)  → step1_prepare.batch_mixing
write h5ad + obs_snapshot.csv +         → step1_prepare.write_output
  var_snapshot.csv + qc_metrics.json
```

#### 5.1.3 recluster 子命令

```
输入: processed.h5ad + --resolution
加载: 1× processed h5ad
操作: step1_prepare.leiden_cluster~14, 16 (re-cluster + UMAP + 更新指标)
输出: 更新 processed.h5ad + 更新 qc_metrics.json 中的 clustering/resolution_stability 块
```

#### 5.1.4 关键实现: silhouette 计算

silhouette 需要 X_pca + leiden 标签。`sklearn.metrics.silhouette_samples` 可计算,但 34K 细胞 × 2K 维度可能慢。使用采样: 对 >10K 细胞的数据集,随机采样 10K 计算 silhouette,标注 `silhouette_sampled: true`。

#### 5.1.5 关键实现: 过滤漏斗

过滤需分步执行 + 记录漏斗(每步各去掉多少),而非串联:

```python
n_before = adata.n_obs
mask_min_genes = adata.obs["n_genes_by_counts"] >= min_genes
adata = adata[mask_min_genes].copy()
n_after_min_genes = adata.n_obs

mask_mt = adata.obs["pct_counts_mt"] < max_mt_pct
adata = adata[mask_mt].copy()
n_after_mt = adata.n_obs
# ... etc

funnel = {
    "n_before": n_before,
    "min_genes": {"n_lost": n_before - n_after_min_genes},
    "max_mt_pct": {"n_lost": n_after_min_genes - n_after_mt},
    # ...
}
```

### 5.2 Step 2: Marker 发现

```
输入: processed.h5ad
加载: 1× processed h5ad
操作: step2_markers.de_rank~21
输出: step2_markers/markers.csv, step2_markers/markers.json (enriched)
```

**新增指标(全部在已有加载中计算):**
- BH-adjusted pval: `statsmodels.stats.multitest.multipletests` 对每簇的 pval 做 BH 校正
- AUC: 对每个 DE 基因,用 `sklearn.metrics.roc_auc_score` 计算(簇内 vs 簇外 binary classification)
- DE 分布: logfc/pval 的 mean/std/percentiles
- 过滤漏斗: 记录 n_before_filter, n_pass_each_criterion

### 5.3 Step 3: KG 查询

```
输入: step2_markers/markers.json
加载: 0× h5ad
操作: step3c_kg.connect~26
输出: step3c_kg/kg_hits.json (enriched)
```

**新增指标(纯 JSON 计算,无 I/O 瓶颈):**
- n_unique_genes_queried, n_genes_with/without_hits
- mapping_multiplicity_per_gene
- candidate_ranking_entropy (Shannon over candidate marker_counts)
- n_tied_at_top
- **ancestors map 写入 kg_hits.json**(供 step4_judge 使用)

### 5.4 Step 4: 簇判断

```
输入: step3c_kg/kg_hits.json
加载: 0× h5ad
操作: step4_judge.rank_candidates~28
输出: step4_judge/annotations.json (enriched)
```

**新增指标(纯 JSON 计算):**
- count_ratio, count_diff, confidence_diff
- frac_support_captured_by_first
- first_second_ancestor_overlap (使用 step3c_kg.query_hierarchy 写入的 ancestors map)

### 5.5 Step 5: 细化

```
输入: processed.h5ad + step4_judge/annotations.json + step2_markers/markers.json + step3c_kg/kg_hits.json
加载: 1× processed h5ad
操作: step5_refine.subcluster~35
输出: step5_refine/refined_annotations.json (enriched)
```

**子集化策略:** 加载 h5ad 后,先读 annotations.json 确定 ambiguous 簇列表,然后用 boolean mask 子集化:
```python
adata_full = load_processed(paths)  # 1× load
ambiguous = [c for c, a in annotations.items() if a["first_count"] <= a["second_count"]]
for c in ambiguous:
    mask = adata_full.obs["leiden"] == c
    sub = adata_full[mask].copy()  # 子集在内存中,远小于全量
    # sub-cluster + DE on sub ...
```

**新增指标:**
- sub_silhouette (在子集上计算,子集小所以快)
- Jaccard_index (替代 overlap/min)
- mean_overlap, frac_unique_markers

### 5.6 Step 6: 验证

```
输入: processed.h5ad + step5_refine/refined_annotations.json + step2_markers/markers.json + step3c_kg/kg_hits.json
加载: 1× processed h5ad (可选 backed 模式)
操作: step6_validate.marker_expression~40
输出: step6_validate/final_annotations.json, step6_validate/report.md
```

**backed 模式优化:**

```python
# 收集所有 top-3 markers (跨簇去重)
all_top_markers = set()
for c in clusters:
    all_top_markers.update(markers["markers_per_cluster"][c]["markers"][:3])
all_top_markers = sorted(all_top_markers)  # 通常 <100 个基因

# backed 模式:只读需要的列
adata = ad.read_h5ad(paths.processed_h5ad, backed="r")
var_names = list(adata.raw.var_names)
idx = [var_names.index(g) for g in all_top_markers if g in var_names]
# 从 raw counts 中只读这些列到内存
sub_raw = adata.raw.X[:, idx].to_memory()  # ~34K × 100, <50 MB
```

**新增指标:**
- Cohen's d: `(mean_in - mean_out) / pooled_std`
- AUC: `roc_auc_score` per top marker
- fold_change: `mean_in / max(mean_out, eps)`
- mean_top3_pct1, mean_top3_specificity (簇级)
- label_diversity, co_annotation_matrix (全局)

### 5.7 Diagnostics: 诊断

```
输入: step1_prepare/qc_metrics.json + step1_prepare/obs_snapshot.csv + step3c_kg/kg_hits.json + 
      step4_judge/annotations.json + step6_validate/final_annotations.json
加载: 0× h5ad!
操作: step7_diagnose.hit_rate~46
输出: step7_diagnose/step7_diagnose.json, step7_diagnose/report.md
```

**关键改进:** step7_diagnose 读 `obs_snapshot.csv` 而非 processed.h5ad 计算 batch entropy:

```python
obs = pd.read_csv(paths.obs_snapshot, index_col=0)
# batch entropy from obs, 不需要 h5ad
for c in clusters:
    cells = obs[obs["leiden"] == c]
    batch_entropy[c] = entropy(cells["sample"].tolist())
```

**新增指标(纯 JSON + obs_snapshot 计算):**
- label_uniqueness, annotation_entropy
- cross_cluster_marker_overlap_matrix (从 markers.json 计算)
- batch_cluster_independence_chi_square

---

## 6. 通用函数设计(common.py)

### 6.1 describe_distribution(values) → dict

一次计算全部分布统计量,供 8 个操作复用:

```python
def describe_distribution(values, n_bins=20):
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) == 0:
        return None
    from scipy.stats import skew, kurtosis
    pct_keys = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    counts, edges = np.histogram(values, bins=n_bins)
    mean = float(values.mean())
    std = float(values.std())
    p25, p75 = np.percentile(values, [25, 75])
    sk = float(skew(values))
    kt = float(kurtosis(values))  # excess kurtosis
    bimod = (sk**2 + 1) / (kt + 3) if (kt + 3) > 0 else None  # bimodality coefficient
    # bimodality > 0.555 suggests bimodal
    return {
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": mean,
        "std": std,
        "median": float(np.median(values)),
        "iqr": float(p75 - p25),
        "cv": float(std / mean) if mean != 0 else None,
        "skewness": sk,
        "kurtosis": kt,
        "bimodality_coefficient": bimod,
        "percentiles": {f"p{k}": float(np.percentile(values, k)) for k in pct_keys},
        "histogram": [int(c) for c in counts],  # n_bins 个整数,与 percentiles 同级;边界=linspace(min,max,n_bins+1)
    }
```

**适用操作:** step1_prepare.compute_qc, 03, 07, 08, 17, 18, 36

### 6.2 filter_funnel(masks, labels) → dict

```python
def filter_funnel(masks, labels):
    """Record how many items pass each filter stage.
    
    masks: list of boolean arrays (each = pass this stage)
    labels: list of stage names
    """
    n_before = len(masks[0]) if masks else 0
    combined = np.ones(n_before, dtype=bool)
    stages = []
    for mask, label in zip(masks, labels):
        n_pass = int(mask.sum())
        n_lost = int((combined & ~mask).sum())
        stages.append({"n_passed": n_pass, "n_lost": n_lost})
        combined &= mask
    n_after = int(combined.sum())
    # items lost at multiple stages
    n_lost_multiple = int(sum(~m for m in masks) > 1) if masks else 0
    return {
        "n_before": n_before,
        "n_after": n_after,
        "frac_retained": n_after / max(n_before, 1),
        "stages": dict(zip(labels, stages)),
        "n_lost_multiple_criteria": n_lost_multiple,
    }
```

**适用操作:** step1_prepare.filter_cells, 06, 19

### 6.3 effect_size(group_in, group_out) → dict

```python
def effect_size(group_in, group_out):
    """Compute effect size metrics for in-cluster vs out-cluster expression."""
    from sklearn.metrics import roc_auc_score
    mean_in = float(np.mean(group_in))
    mean_out = float(np.mean(group_out))
    std_in = float(np.std(group_in))
    std_out = float(np.std(group_out))
    pooled_std = np.sqrt((std_in**2 + std_out**2) / 2)
    cohen_d = (mean_in - mean_out) / pooled_std if pooled_std > 0 else 0
    # AUC: binary classification (in=1, out=0)
    labels = np.concatenate([np.ones(len(group_in)), np.zeros(len(group_out))])
    scores = np.concatenate([group_in, group_out])
    auc = float(roc_auc_score(labels, scores))
    eps = 1e-6
    fold_change = mean_in / max(mean_out, eps)
    logfc = np.log2((mean_in + 1) / (mean_out + 1))
    return {
        "mean_in": mean_in,
        "mean_out": mean_out,
        "cohen_d": float(cohen_d),
        "auc": auc,
        "fold_change": float(fold_change),
        "logfc": float(logfc),
    }
```

**适用操作:** step2_markers.de_rank (DE), step6_validate.marker_expression (marker validation)

### 6.4 pairwise_overlap(sets, labels) → dict

```python
def pairwise_overlap(sets, labels):
    """Compute pairwise overlap metrics for a collection of sets."""
    from itertools import combinations
    keys = [k for k, s in zip(labels, sets) if s]
    if len(keys) < 2:
        return {"mean_overlap": 0.0, "max_overlap": 0.0, "pairs": []}
    pairs = []
    overlaps = []
    jaccards = []
    for a, b in combinations(keys, 2):
        sa, sb = sets[labels.index(a)], sets[labels.index(b)]
        inter = len(sa & sb)
        overlap = inter / max(min(len(sa), len(sb)), 1)
        jaccard = inter / max(len(sa | sb), 1)
        pairs.append({"pair": f"{a}-{b}", "overlap": float(overlap), "jaccard": float(jaccard)})
        overlaps.append(overlap)
        jaccards.append(jaccard)
    return {
        "mean_overlap": float(np.mean(overlaps)),
        "max_overlap": float(max(overlaps)),
        "mean_jaccard": float(np.mean(jaccards)),
        "pairs": pairs,
    }
```

**适用操作:** step5_refine.marker_overlap (sub-cluster overlap), step5_refine.unknown_overlap (unknown overlap), step7_diagnose.cross_cluster (cross-cluster)

### 6.5 batch_mixing(cluster_labels, batch_labels) → dict

```python
def batch_mixing(cluster_labels, batch_labels):
    """Compute batch mixing metrics per cluster."""
    import math
    from collections import Counter
    from scipy.stats import chi2_contingency
    clusters = sorted(set(cluster_labels))
    per_cluster = {}
    contingency = []
    for c in clusters:
        mask = cluster_labels == c
        batch_counts = Counter(batch_labels[mask])
        total = sum(batch_counts.values())
        entropy = -sum((v/total) * math.log2(v/total) for v in batch_counts.values() if v > 0)
        max_frac = max(batch_counts.values()) / total if total > 0 else 0
        per_cluster[c] = {"entropy": float(entropy), "max_batch_fraction": float(max_frac)}
        contingency.append([batch_counts.get(b, 0) for b in sorted(set(batch_labels))])
    # chi-square test
    try:
        chi2, p, dof, _ = chi2_contingency(contingency)
        chi2_stat = float(chi2)
        chi2_p = float(p)
    except Exception:
        chi2_stat = None
        chi2_p = None
    return {
        "per_cluster": per_cluster,
        "chi_square": chi2_stat,
        "chi_square_p": chi2_p,
    }
```

**适用操作:** step1_prepare.batch_mixing, step7_diagnose.batch_entropy, step7_diagnose.cross_cluster

### 6.6 cluster_quality(adata, labels, X_pca=None, max_silhouette_samples=10000) → dict

```python
def cluster_quality(adata, labels, X_pca=None, max_silhouette_samples=10000):
    """Compute clustering quality metrics."""
    from sklearn.metrics import silhouette_samples, silhouette_score, davies_bouldin_score, calinski_harabasz_score
    if X_pca is None:
        X_pca = adata.obsm["X_pca"]
    labels = np.asarray(labels)
    unique_labels = sorted(set(labels))
    n = len(labels)
    # silhouette (sample if too many cells)
    if n > max_silhouette_samples:
        idx = np.random.choice(n, max_silhouette_samples, replace=False)
        sil_samples = silhouette_samples(X_pca[idx], labels[idx])
        sampled = True
    else:
        sil_samples = silhouette_samples(X_pca, labels)
        sampled = False
    per_cluster = {}
    for c in unique_labels:
        mask = labels == c
        per_cluster[c] = {
            "mean": float(np.mean(sil_samples[mask])),
            "median": float(np.median(sil_samples[mask])),
        }
    n_negative = sum(1 for c in unique_labels if np.mean(sil_samples[labels == c]) < 0)
    # cluster size distribution
    sizes = [int(np.sum(labels == c)) for c in unique_labels]
    # modularity (from igraph, if available)
    modularity = None
    try:
        import igraph as ig
        # ... compute modularity from connectivities
    except ImportError:
        pass
    return {
        "silhouette_overall": {"mean": float(np.mean(sil_samples)), "std": float(np.std(sil_samples))},
        "silhouette_per_cluster": per_cluster,
        "silhouette_sampled": sampled,
        "n_clusters_negative_mean_silhouette": n_negative,
        "cluster_size_distribution": describe_distribution(sizes),
        "frac_largest_cluster": float(max(sizes) / n),
        "n_rare_clusters": int(sum(1 for s in sizes if s / n < 0.05)),
        "n_singleton_clusters": int(sum(1 for s in sizes if s <= 2)),
        "modularity": modularity,
    }
```

**适用操作:** step1_prepare.leiden_cluster, step5_refine.subcluster

### 6.7 variance_explained(adata) → dict

```python
def variance_explained(adata):
    """Extract PCA variance explained from fitted AnnData."""
    pca = adata.uns.get("pca", {})
    var_ratio = pca.get("variance_ratio", [])
    if not hasattr(var_ratio, "__iter__"):
        var_ratio = list(var_ratio)
    var_ratio = [float(v) for v in var_ratio]
    cumulative = []
    s = 0
    for v in var_ratio:
        s += v
        cumulative.append(s)
    def n_pcs_for(target):
        for i, c in enumerate(cumulative):
            if c >= target:
                return i + 1
        return len(cumulative)
    return {
        "per_pc": var_ratio,
        "cumulative": cumulative,
        "n_pcs_for_50pct": n_pcs_for(0.5),
        "n_pcs_for_80pct": n_pcs_for(0.8),
        "n_pcs_for_90pct": n_pcs_for(0.9),
    }
```

**适用操作:** step1_prepare.pca

### 6.8 resolution_stability(adata, res_list) → dict

```python
def resolution_stability(adata, res_list):
    """Compute ARI/NMI between adjacent resolutions."""
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
    results = {"adjacent_ari": {}, "adjacent_nmi": {}}
    for i in range(len(res_list) - 1):
        r1, r2 = res_list[i], res_list[i + 1]
        l1 = adata.obs[f"leiden_{r1}"].astype(str)
        l2 = adata.obs[f"leiden_{r2}"].astype(str)
        ari = adjusted_rand_score(l1, l2)
        nmi = normalized_mutual_info_score(l1, l2)
        results["adjacent_ari"][f"{r1}-{r2}"] = float(ari)
        results["adjacent_nmi"][f"{r1}-{r2}"] = float(nmi)
    return results
```

**适用操作:** step1_prepare.choose_resolution

### 6.9 candidate_autocorr(cluster_adata, candidate1_markers, candidate2_markers) → dict

```python
def candidate_autocorr(cluster_adata, candidate1_markers, candidate2_markers):
    """Compute Moran's I / Geary's C of candidate preference score on kNN graph.

    Uses scanpy built-in: sc.tl.score_genes + sc.metrics.morans_i/gearys_c.
    The kNN graph must already exist on cluster_adata (obsp['connectivities']).
    """
    import scanpy as sc
    import numpy as np

    # 1. Score each cell for both candidates
    sc.tl.score_genes(cluster_adata, candidate1_markers, score_name="cand1_score")
    sc.tl.score_genes(cluster_adata, candidate2_markers, score_name="cand2_score")

    # 2. Candidate preference score
    x = cluster_adata.obs["cand1_score"].values - cluster_adata.obs["cand2_score"].values

    # 3. Moran's I and Geary's C (one-liner each, uses existing kNN graph)
    moran = float(sc.metrics.morans_i(cluster_adata, vals=x))
    geary = float(sc.metrics.gearys_c(cluster_adata, vals=x))

    # 4. Score distribution (for LLM to check bimodality)
    dist = describe_distribution(x)

    return {
        "morans_i": moran,
        "gearys_c": geary,
        "score_distribution": dist,
        "score_bimodality_coefficient": dist.get("bimodality"),
        "cand1_score_mean": float(cluster_adata.obs["cand1_score"].mean()),
        "cand2_score_mean": float(cluster_adata.obs["cand2_score"].mean()),
    }
```

**适用操作:** step5_refine.candidate_autocorr(预判 subcluster 必要性)

**设计要点:**
- `sc.tl.score_genes` 默认对 mean expression 做 z-score,再取均值——跨基因可比
- kNN 图来自 step1_prepare.knn_graph,子集化后需在子集上重建 neighbors(子集空间 ≠ 全量空间的子空间)
- 成本极低:两次 score_genes + 两次 morans_i,都是矩阵乘法级别
- **不加载额外 h5ad**——在 step5_refine.subcluster 的 h5ad 加载中完成(子集化后直接算)

| 操作 | 数据源(obs_snapshot.csv) | 省掉的 h5ad 加载 |
|---|---|---|
| step1_prepare.qc_distribution 质控分布 | pd.read_csv | 1 (但通常在 run 中完成) |
| step1_prepare.choose_resolution 分辨率选择 | pd.read_csv | 0 (在 run 中完成) |
| step1_prepare.batch_mixing 批次混合 | pd.read_csv | 0 (在 run 中完成) |
| step7_diagnose.batch_entropy 诊断批次熵 | pd.read_csv | **1** |
| step7_diagnose.cross_cluster 跨簇报告 | pd.read_csv | (已被 step7_diagnose.batch_entropy 覆盖) |

**核心收益: step7_diagnose 从 1× h5ad 加载降到 0×。**

---

## 8. 实施计划

### Phase A: 通用函数 + Step 1 enrichment(最高价值)

1. 在 `common.py` 中实现 8 个通用函数 + `append_log()` + `next_run_id()`
2. 实现 `step1_prepare` 的 run 子命令 — 在单次加载中增加全部 Step 1 扩展指标,每个原子操作完成后调 `append_log()` 写入 `run_log.jsonl`
3. 新增 `obs_snapshot.csv` + `var_snapshot.csv` 写出
4. 实现 `step1_prepare` 的 metrics 子命令 — 增强分布统计

**验证:** 跑完后检查 `run_log.jsonl` 有 `step1_prepare.leiden_cluster#1` 等记录,`metrics` 含 silhouette/pca/knn_graph 等新块

### Phase B: Step 2 + Step 6 enrichment

5. 实现 `step2_markers` — 增加 BH-FDR, AUC, 过滤漏斗, DE 分布,每个 op 调 `append_log()`
6. 实现 `step6_validate` — 增加 Cohen's d, AUC, fold_change, 全局汇总指标,每个 op 调 `append_log()`
7. 实现 step6_validate backed 模式(可选优化)

**验证:** 检查 `run_log.jsonl` 有 `step2_markers.de_rank#1` 等记录,`metrics` 含 de_distribution/effect_sizes 等新块

### Phase C: Step 3/4/5 enrichment(纯 JSON,无 I/O)

8. 实现 `step3c_kg` — 增加查询统计,候选排名熵,ancestors 写入,每个 op 调 `append_log()`
9. 实现 `step4_judge` — 增加 gap metrics, ancestor overlap,调 `append_log()`
10. 实现 `step5_refine` — 增加 Jaccard, sub_silhouette, type membership 计数,每个 op 调 `append_log()`

**验证:** 检查 `run_log.jsonl` 有各步记录,`metrics` 含 ancestors/gap_metrics/jaccard 等新块

### Phase D: Diagnostics 重构

11. 实现 `step7_diagnose` — 读 obs_snapshot.csv 替代 h5ad,增加跨簇指标,每个 op 调 `append_log()`

**验证:** step7_diagnose 不加载 h5ad,检查 `run_log.jsonl` 有 `step7_diagnose.cross_cluster#1` 等记录

### Phase E: metrics_interpretation.md 更新

12. 更新 `knowledge/metrics_interpretation.md` — 增加所有新指标的 LLM 解读指南

**验证:** 文档完整,所有 `run_log.jsonl` 中的指标字段都有解读说明

---

## 9. 文件结构(设计后)

```
<project-dir>/
├── step1_prepare/
│   ├── processed.h5ad           (GB 级, 只在需要表达矩阵时加载)
│   ├── qc_metrics.json          (~50 KB, enriched: 全部 Step 1 指标)
│   ├── obs_snapshot.csv         (~5 MB, 新增: 细胞元数据)
│   └── var_snapshot.csv         (~500 KB, 新增: 基因元数据)
├── step2_markers/
│   ├── markers.csv              (已有)
│   └── markers.json             (~100 KB, enriched: DE 分布 + 过滤漏斗)
├── step3c_kg/
│   ├── kg_hits.json             (~200 KB, enriched: 查询统计 + 候选排名熵 + ancestors)
│   └── kg_source.txt
├── step4_judge/
│   └── annotations.json         (~50 KB, enriched: gap_metrics + ancestor_overlap)
├── step5_refine/
│   └── refined_annotations.json  (~100 KB, enriched: Jaccard + sub_silhouette)
├── step6_validate/
│   ├── final_annotations.json   (~200 KB, enriched: effect_sizes + global_stats)
│   ├── report.md
│   └── figures/
└── step7_diagnose/
    ├── step7_diagnose.json         (~50 KB, enriched: cross_cluster 指标)
    └── report.md
│
├── run_log.jsonl                      ← 统一运行日志(见 trajectory_design.md)
│
└── exports/                           ← 微调导出(可选,脚本生成)
```

---

## 10. 外部依赖配置

pipeline 的外部依赖(知识图谱、BLAST subject 库)通过环境变量配置,不硬编码在代码或配置文件中。脚本按 `CLI flags > 环境变量 > 硬编码默认值` 的优先级读取。**cell-annotation 的环境类 key 在 `skills/cell-annotation/.env`**,harness 不知情。完整清单见 `docs/CONFIGURATION_REFERENCE.md`。BLASTP 规范见 `_bmad-output/specs/spec-blastp-homology/SPEC.md`。

### 10.1 环境变量清单

| 环境变量 | 用途 | 默认值 | 哪个脚本读 |
|---|---|---|---|
| `NEO4J_URI` | Neo4j 连接地址 | `bolt://localhost:7687` | step3c_kg.py, step3a_kg_precheck.py, step5_refine.py |
| `NEO4J_USER` | Neo4j 用户名 | `neo4j` | 同上 |
| `NEO4J_PASSWORD` | Neo4j 密码 | (无默认,必须设置) | 同上 |
| `CELL_ANNOTATION_BLASTDB_DIR` | subject BLAST 库缓存根 | `~/.cache/annot-harness/cell-annotation/blastdb` | step3b(provider=blastp) |
| `CELL_ANNOTATION_BLASTDB_URL` | subject zip | 公开下载 `blastdb.zip`(URL 走配置,不写进设计文档) | ensure_blastdb |
| `CELL_ANNOTATION_BLASTDB_SHA256` | zip 校验 | 实现时 pin | ensure_blastdb |
| `CELL_ANNOTATION_QUERY_FASTA` | 用户蛋白 FASTA 默认路径 | 无 | step3b `--query-fasta` 回落 |

### 10.2 配置方式

在项目根目录的 `.env` 文件中设置(已被 .gitignore 排除):

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password_here
```

或在运行前 export:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password_here
```

或通过 CLI flags 覆盖:

```bash
python scripts/step3c_kg.py query --project-dir ./output --organ root \
    --uri bolt://localhost:7687 --user neo4j --password your_password_here
```

### 10.3 设计原则

- **不硬编码密码** — 密码只通过环境变量或 CLI flags 传入,不写入任何文件
- **不使用配置文件** — 不在 skill 目录或项目目录中放 config.json,避免密码泄露到 git
- **skill 自管外部资源** — Neo4j 与 BLAST 库路径/URL 在 cell-annotation `.env`,不进 harness
- **BLAST 库不进 skill 包** — 二进制缓存在用户目录,仅 `--provider blastp` 且缺库时 GET;不在 skill 加载时下载
- **过滤 FASTA 不二次加载 h5ad** — 用 `var_snapshot.csv` sidecar

### 10.4 BLASTP 与加载纪律

- 一个子命令仍最多一次 h5ad 加载。step3a / step3b / step3c query 均为 0 次 h5ad。
- `ncbi-blast+` 是系统依赖,skill 不下载该二进制。
- 默认 `--provider` 仍是 `ensembl_compara`;blastp 故障改道 Ensembl,对外 `status=ok`。

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| silhouette 计算慢(34K 细胞) | step1_prepare run 时间增加 | 采样: >10K 细胞时随机采 10K |
| statsmodels 依赖(BH-FDR) | 可能未安装 | 用 `scipy.stats.false_discovery_control` (SciPy 1.11+) 或手动实现 BH |
| igraph 依赖(modularity) | 可能未安装 | 可选: try/except,无 igraph 时跳过 modularity |
| backed 模式兼容性 | step6_validate backed 可能不支持 raw | try/except,失败时回退到全量加载 |
| obs_snapshot.csv 一致性 | 用户手动改 h5ad 后 sidecar 过期 | run_log.jsonl 记录了每次执行的指标,可按 seq 追溯 |
