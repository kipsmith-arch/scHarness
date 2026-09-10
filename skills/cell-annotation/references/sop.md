# 单细胞注释标准操作流程(SOP)

> 本文档是 `knowledge/cell-annotation-sop.md` 全文的 skill 包副本,由 SOP 素材原样收录。
> SKILL.md §2/§3 引用本文件:流程细节、质量检查表、决策流程图、常见症状速查在此查。

## 目录(TOC)

- [目的](#目的)
- [适用范围](#适用范围)
- [输入](#输入)
- [输出](#输出)
- [步骤总览](#步骤总览)
- [SOP-1 数据预处理 QC](#sop-1数据预处理-qc)
- [SOP-2 找 marker 基因](#sop-2找-marker-基因)
- [SOP-3 查参考知识](#sop-3查参考知识)
- [SOP-4 判断每个 cluster 是什么细胞](#sop-4判断每个-cluster-是什么细胞)
- [SOP-5 处理模糊和缺失](#sop-5处理模糊和缺失)
- [SOP-6 验证与交付](#sop-6验证与交付)
- [决策流程图](#决策流程图)
- [常见症状速查](#常见症状速查)

---

# 单细胞注释标准操作流程（SOP）

## 目的
规范单细胞 RNA 测序数据的细胞类型注释流程，确保结果可追溯、可复现、带置信度。

## 适用范围
单物种 scRNA-seq 数据（已做完预处理和聚类，拿到 cluster 列表）。本物种在知识图谱覆盖不足时，在 SOP-3 查图谱阶段做同源映射以提高命中率（不是 SOP-2 找 marker 的一部分），见跨物种手册。

## 输入
- 已聚类的 scRNA-seq 数据（h5ad 或等价格式）
- 每个 cluster 的细胞数
- 原始 counts 矩阵（用于 DE）

## 输出
- 每个 cluster 的细胞类型标签 + 置信度（high/medium/low）
- 每个注释的 top 3 marker 表达证据（violin/heatmap）
- 元数据：参考数据库版本、阈值参数、注释日期

## 步骤总览
SOP-1 预处理 QC → SOP-2 找 marker → SOP-3 查参考知识（覆盖预检 → 需要时同源映射 → 查图谱） → SOP-4 判断 cluster → SOP-5 处理模糊/缺失 → SOP-6 验证交付

---

## SOP-1　数据预处理 QC

**输入**：原始 scRNA-seq 数据
**操作**：
1. 画 QC 指标分布（每个细胞的基因数、总 counts、线粒体基因占比）。
2. 按分布尾部定阈值过滤低质量细胞（线粒体占比阈值按组织调整：肝脏天然高，植物看叶绿体）。
3. 跑 doublet 检测，标记/移除双细胞。
4. 归一化（常规用 log 归一化；稀疏数据用基于原始 counts 的统计模型，如负二项回归）。
5. 选高变基因（HVG）。如有批次，先批次校正再选 HVG，或分批选。
6. 聚类（跑多个分辨率，看 cluster 数随分辨率的拐点，选拐点附近值）。

**输出**：一批 cluster，每个 cluster 是表达相似的细胞群

**质量检查**：
| 检查项 | 合格标准 | 不合格处理 |
|---|---|---|
| 过滤后细胞数 | 未大幅减少（<50%）且已知该有的细胞类型 marker 阳性细胞还在 | 阈值太严，按分布重新定 |
| 批次混合 | 每个 cluster 样本来源混合，不是单一来源 | 做批次校正后重聚类 |
| UMAP 分群 | 按细胞类型分群，不是按样本/批次 | 同上 |
| cluster 数 | 和该组织的生物学预期一致 | 调分辨率 |

---

## SOP-2　找 marker 基因

**输入**：SOP-1 的 cluster 结果 + 原始 counts
**操作**：
1. 对每个 cluster 跑 DE（cluster vs 其他所有 cluster）。注意：DE 输入必须是原始 counts，不要在 log 归一化数据上跑。
2. 每个 cluster 取 top 30 左右 marker。
3. 过滤管家基因（RPL/RPS/MT-/HSP/Histone）。
4. 过滤低质量 marker：要求 pct1 在 0.5-0.9、pct1-pct2 > 0.25。（<0.1 直接丢，0.1-0.25 灰色地带）
5. 稀有细胞类型（占比 <5%）单细胞 DE 检验力不够，改用 pseudo-bulk（按样本×cluster 聚合 counts）再跑 DE。

**输出**：每个 cluster 一个 marker 列表（约 30 个，已过滤）

**质量检查**：
| 检查项 | 合格标准 | 不合格处理 |
|---|---|---|
| marker 数量 | 每 cluster 10-50 个 | 太少→检查 DE 输入是否 raw counts；太多→收紧过滤 |
| 管家基因 | top marker 里无 RPL/RPS/MT-/HSP | 显式过滤管家基因列表 |
| pct1 | 在 0.5-0.9 | <0.5 丢；>0.9 多半是管家基因，丢 |
| pct1-pct2 | >0.25 | <0.1 丢；0.1-0.25 灰色 |
| 假 marker | marker 在多样本都特异 | 只在一个样本高表达→批次 marker，丢 |

---

## SOP-3　查参考知识

同源映射属于本步，不是 SOP-2。目的是把本物种 marker 对到图谱里已有注释的参考物种基因，从而提高知识图谱命中率。

**输入**：SOP-2 的 marker 列表
**操作**：

### 3a 覆盖预检
1. 调 `step3a_kg_precheck__run`（`--target-species` `--organ` `--species-type`），读 `coverage_report.json`：`coverage_tier` / `recommended_strategy` / `recommended_reference_species`。
2. 写 `cross_species_routing` judgment：`routing_accept`（跟推荐）/ `routing_force_single` / `routing_force_cross` / `routing_multi_reference`。
3. `single_species`：跳过同源，直接 3c。
4. `mixed` / `cross_species_only`：进 3b。

**不要**先用本物种 ID 查空 KG、发现 0 命中后再去同源。

### 3b 同源映射（条件，提高 KG 命中率）
调 `step3b_cross_species_map__run`，产出 `step3b_cross_species_map/cross_species_map.json`。映射失败（空表 / DNS）时工具返回空 map + warnings，3c 自动只走直接路径。

### 3c 查图谱
1. 选定 organ（必须和数据来源 organ 对齐）。
2. 调 `step3c_kg__query`。若跑过 3b，传 `--ortholog-map step3b_cross_species_map/cross_species_map.json`；工具并行跑直接路径与同源路径。
3. 拿每个 cluster 的 marker（及映出的参考物种基因）去查它标记什么细胞类型。
4. 统计每个 cluster 命中多少种候选细胞类型。

**输出**：每个 marker → 它标记的细胞类型；每个 cluster → 候选细胞类型列表（含 `n_direct_hits` / `n_ortholog_hits`）

**质量检查**：
| 检查项 | 合格标准 | 不合格处理 |
|---|---|---|
| marker 命中率 | 大部分 marker 能查到对应细胞类型 | 命中率低→查 organ 是否对齐；换数据库/参考图谱 |
| 候选类型数 | 每 cluster 1-5 个候选 | 太多→marker 数过多或一对多未处理；太少→图谱覆盖不全 |

---

## SOP-4　判断每个 cluster 是什么细胞

**输入**：SOP-3 的候选细胞类型列表
**操作**：
1. 对每个 cluster，汇总它的 marker 命中的细胞类型，按支持 marker 数和命中相似度排序。
2. 定第一候选和第二候选。
3. 查第一候选和第二候选的 canonical marker 在该 cluster 是否高表达。
4. 判断：

   **明显领先（直接定，置信度 high）**：
   - 第一候选支持 marker 远多于第二（如 15 vs 3）
   - 只有第一候选的 canonical marker 高表达，第二候选的不表达

   **势均力敌（标模糊，进 SOP-5）**，满足任一即算：
   - 支持 marker 数接近（如 8 vs 7）
   - 命中相似度接近（如 70% vs 68%）
   - 双方 canonical marker 都高表达（可能是双细胞/过渡态/祖细胞）

**输出**：每个 cluster 一个候选细胞类型 + 置信度（high 或 模糊待 refine）

**质量检查**：
| 检查项 | 合格标准 | 不合格处理 |
|---|---|---|
| 第一候选领先度 | 明显领先 | 势均力敌→进 SOP-5 refine |
| canonical marker 表达 | 第一候选的 canonical marker 真在该 cluster 高表达 | 不表达→标签错了，重查 |
| 模糊 cluster 占比 | <20% | >20%→整体注释不够 decisive，批量 refine |

---

## SOP-5　处理模糊和缺失

**输入**：SOP-4 标记的模糊 cluster 和没命中的 cluster
**操作**：

**A. 模糊 cluster（refine）**：
1. 前置检查：cluster 细胞数 ≥100？少于 100 直接标"细胞数不足，未细分"，跳过 refine。
2. 对该 cluster 内部重新聚类，分成子 cluster。
3. 对子 cluster 在父 cluster 范围内重做 DE（不要复用父级 marker）。
4. 拿子 cluster 特异 marker 重走 SOP-3、SOP-4。

**refine 成功的检验**（全部满足才算成功）：
- 每个子 cluster 第一候选明显领先（不再纠缠不清）
- 子 cluster 之间 DE marker 各自特异、不重叠
- 子 cluster 注释落在父级候选范围内（如父在 T/B 间纠结，子分出 T 和 B——合理）

**refine 失败的处理**：
| 失败情况 | 表现 | 处理 |
|---|---|---|
| 没分纯 | 子 cluster 第一第二候选仍纠缠不清 | 退回父级标签或标 ambiguous |
| 分错了 | 子 cluster 注释和父级候选完全无关（如父在 T/B 间纠结却分出"神经元""成纤维"） | 分裂是批次/质量驱动的假分裂；查该 cluster 的样本组成和 QC |
| 分得太碎 | 子 cluster 细胞数都很少（如各 10 几个） | DE 没检验力，丢弃结果 |

**B. 没命中的 cluster**：
1. 不要硬贴标签。
2. 检查 unknown cluster 之间彼此的 marker 是否重叠。
3. 不重叠→各自独立的"新"细胞类型，分别标注"unknown type A/B"。
4. 重叠→统称 unknown。

**输出**：模糊 cluster 被细分成更纯的亚型（已检验）；没命中的 cluster 标 unknown 或 unknown type A/B

---

## SOP-6　验证与交付

**输入**：所有 cluster 的注释结果
**操作**：
1. 对每个注释，附 top 3 canonical marker 在该 cluster 的表达分布（violin/heatmap）。
2. 拿注释出的细胞类型，查该类型的经典 marker 是否真在该 cluster 高表达——不表达就是错的。
3. 标置信度：
   - **high**：第一候选明显领先 + marker 数 >15 + canonical marker 高表达
   - **medium**：部分满足
   - **low**：势均力敌、marker 数少、或 canonical marker 弱表达
4. 记录元数据：参考数据库版本、阈值参数、注释日期、参考物种（若跨物种）。
5. 交付：标签 + 置信度 + marker 表达证据 + 元数据。

**输出**：可追溯、可复现的注释结果

**质量检查（交付前总检）**：
| 检查项 | 合格标准 | 不合格处理 |
|---|---|---|
| 每个注释有 marker 证据 | top 3 marker 表达分布附上 | 补 |
| canonical marker 核对 | 标签对应的 canonical marker 真在该 cluster 高表达 | 标签错了，重查 |
| 置信度标注 | 所有 cluster 都有 high/medium/low | 补 |
| 元数据完整 | 参考数据库版本、阈值、日期 | 补 |
| unknown 率 | <30% | >30% 查阈值/organ/参考覆盖；>40% 多参考交叉；>50% 标准太严 |

---

## 决策流程图

```
原始数据
   │
   ▼
[SOP-1 预处理 QC] ──QC 不合格──→ 重新预处理
   │QC 合格
   ▼
[SOP-2 找 marker] ──marker 质量差──→ 检查 DE 输入/过滤参数
   │marker 合格
   ▼
[SOP-3 查参考知识]
   │  3a 覆盖预检 →（覆盖不足则 3b 同源映射，提高命中率）→ 3c 查图谱
   │命中率低──→ 查 organ / 换参考物种 / 换数据库
   │命中合格
   ▼
[SOP-4 判断 cluster]
   │
   ├─ 明显领先 ──→ 标 high，进 SOP-6
   │
   └─ 势均力敌 ──→ [SOP-5 refine]
                       │
                       ├─ refine 成功 ──→ 标亚型，进 SOP-6
                       ├─ 没分纯 ──→ 标 ambiguous，进 SOP-6
                       ├─ 分错了 ──→ 退回父级 + 查批次，进 SOP-6
                       └─ 分得太碎 ──→ 丢弃，标父级，进 SOP-6
   │
   ▼
[SOP-6 验证交付] ──总检不合格──→ 回对应 SOP 修复
   │总检合格
   ▼
 交付：标签 + 置信度 + marker 证据 + 元数据
```

---

## 常见症状速查

| 症状 | 最可能原因 | 第一步排查 |
|---|---|---|
| 全部 cluster 都 unknown | marker 没命中 / organ 选错 / 阈值太严 | 看命中率；查 organ 是否对齐 |
| 所有 cluster 注释成同一种细胞 | 管家基因当 marker / 批次效应 | 看 top marker 是否管家基因；看 cluster 样本组成 |
| 注释粒度太细（一种细胞分 10 类） | 聚类分辨率太高 / refine 太激进 | 降分辨率；检查 refine 触发条件 |
| 少数 cluster 注释很离谱 | 这几个 cluster 是批次/质量产物 | 看 cluster 的样本/质量分布 |
| canonical marker 不表达 | 标签错了 | 重查 SOP-3/SOP-4 |
| unknown 率 >30% | 阈值太严 / 图谱覆盖不全 / organ 错 | 分层排查（30% 警惕/40% 覆盖不全/50% 标准太严） |
