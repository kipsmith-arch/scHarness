---
id: SPEC-cross-species-routing
title: cell-annotation skill 跨物种注释能力(默认启用,LLM 静默路由)
status: proposed
created: 2026-08-24
companions:
  - architecture.md
  - data-contracts.md
  - sop-extension.md
  - traps-cross-species.md
  - metrics-extension.md
  - kg-schema-extension.md
  - open-questions.md
sources:
  - knowledge/cross-species-annotation-handbook.md
  - design/tool_design.md
  - skills/cell-annotation/SKILL.md
  - skills/cell-annotation/scripts/step3c_kg.py
  - skills/cell-annotation/references/kg-schema.md
  - skills/cell-annotation/references/sop.md
  - skills/cell-annotation/references/traps.md
  - skills/cell-annotation/references/metrics.md
  - skills/cell-annotation/scripts/trajectory_schema.py
---

# 跨物种路由(Cell-Annotation Skill 默认能力)

> 2026-09-08: 同源映射从 step2 / SOP-2.5 改挂 SOP-3(`step3b_cross_species_map`)。它服务知识图谱命中率,不是 marker 发现。同日按执行顺序命名 `step3a_kg_precheck` → `step3b_cross_species_map` → `step3c_kg`。下文若仍写 `step2_ortholog` / `step2_cross_species_map` / SOP-2.5 / `step3_kg`,视为历史名,以当前文件名为准。

## Why

本 cell-annotation skill 当前的 SOP-3 假设**目标物种在 Neo4j KG 中已有完整 cell type 标注**,因此"跳过跨物种"。但这隐含一个限制:**目标物种必须在 KG 收录列表内**(目前 Neo4j 实测覆盖 29 种,Plant 18 + Animal 11)。对于不在收录内、或 KG 覆盖稀薄的非模式物种,当前 pipeline 直接退化为 0 命中,无法注释。

机会层面:Ensembl Compara REST(symbol-based,无需蛋白序列)已可查询 300+ 物种间的同源关系,**无需部署本地蛋白数据库**;生产环境对 rest.plants.ensembl.org 与 rest.ensembl.org 的访问可行。技术上,跨物种注释是 SOP-3 查图谱上的扩展点(覆盖不足时先做同源映射以提高命中率),**不应继续把它当成用户的负担,也不应挂在找 marker 的 SOP-2**。

实现此能力后,skill 的描述范围从"单物种(且必须在 KG 收录内)"扩展为"任何物种;KG 自有标注则走直接路径,KG 覆盖不足则静默调用 Ensembl Compara REST 借参考物种图谱"。**SKILL.md 的 `description` 与用户的 query 不需要任何变化**,LLM 在 13 决策点之外新增一个 `cross_species_routing` 会话级决策点静默处理。

## Capabilities

- **CAP-1 precheck**
  - **intent:** skill 可在 step3c_kg 之前对目标物种在 KG 中的 cell type 覆盖率做快速诊断,返回推荐策略与推荐参考物种列表。
  - **success:** 新工具 `step3a_kg_precheck__run` 在 0 h5ad 加载下输出 `coverage_report.json`,含 `target_species_genes_with_ct` / `target_species_unique_ct` / `coverage_tier ∈ {high, medium, low}` / `recommended_strategy ∈ {single_species, mixed, cross_species_only}` / `recommended_reference_species[]`(按 species_type 内 cell type 覆盖度 + 亲缘距离加权重排序)。覆盖率阈值(>=500/50-500/<50)与推荐排序算法有 pytest 覆盖。
- **CAP-2 cross-species map(provider abstraction)**
  - **intent:** skill 可把目标物种 marker 映射到一个或多个参考物种的等价基因,无需用户提供蛋白序列;背后使用 **pluggable provider** (默认 `ensembl_compara`,未来可加 `blast` / `diamond` / `oma` / `eggnog`),调用方不需关心 provider 实现。
  - **success:** 新工具 `step2_cross_species_map__run` 在 0 h5ad 加载下输出 `cross_species_map.json`,含每个目标 marker → 中性 `MappingRecord` 列表 (`ref_species` / `ref_gene_id` / `score` 0~100 / `score_type` / `mapping_type` / `confidence` / `raw`)。CLI 参数 `--provider` (默认 `ensembl_compara`,argparse `choices` 来自 registry)。全局统计 (命中率 / score 分布 / score_type 分布 / mapping_type 分布)。支持 `species-level 缓存` (key 含 provider name)、`--force-refresh`、`--min-score`、`--max-hits-per-gene`、Ensembl 不可达时降级为 warn + 不阻断。Adding a new provider = 1 file + 1 line in `PROVIDERS` dict; call sites (LLM-facing args, step3c_kg, SKILL.md) do not change.
  - **refactor note (2026-08-24):** Was `step2_ortholog.py` (Ensembl-Compara-hardcoded). Provider abstraction introduced per user requirement that cross-species mapping must be pluggable (BLAST/DIAMOND/OMA/eggnog are plausible future additions). Provider interface: `BaseCrossSpeciesProvider` ABC + `MappingRecord` dataclass + `PROVIDERS` registry + `@register_provider` decorator. Default provider `ensembl_compara` is the first shipped implementation; lives in subpackage `step2_xmap_providers/` (renamed from `step2_cross_species_map/` to avoid Python module/subpackage name collision with the CLI script).
- **CAP-3 step3c_kg 跨物种融合**
  - **intent:** skill 可在 step3c_kg query 时并行跑"marker 直接命中"与"marker→ortholog→参考物种命中"两条路径,候选聚合时保留 source_species 与 source_path,LLM 决策视图区分两条路径的命中数。
  - **success:** `step3c_kg.py query` 新增 `--ortholog-map` 选项。`kg_hits.json` 的 `gene_to_cts` 结构扩展为带 `source_path ∈ {direct, ortholog}` 与 `ortholog_ref_gene / ortholog_ref_species` 字段;`per_cluster` 新增 `n_direct_hits` / `n_ortholog_hits` / `n_mixed_hits` 计数。Ensembl 不可达时回退单路径,run_log.jsonl 记 warning 而非 error。现有 B1 default arm 的 strict=0.9236 不退化(parallel path 在 KG 覆盖高的物种上不应改变主导候选)。
- **CAP-4 LLM 静默路由决策**
  - **intent:** LLM 可基于 precheck 报告静默决定走单物种 / 混合 / 跨物种 / 多参考物种路径,而无需用户参与。
  - **success:** SKILL.md 新增 `cross_species_routing` 会话级决策点(13→14)。LLM 在 SOP-3 入口先调 `step3a_kg_precheck__run` 读 JSON,根据 `recommended_strategy` 与推荐参考物种列表选择 4 个 decision 之一:`routing_accept`(采用推荐) / `routing_force_single`(强制单物种) / `routing_force_cross`(强制跨物种,即便 KG 覆盖高) / `routing_multi_reference`(显式多参考物种列表,override precheck 推荐)。decision 落枚举、写 judgment 记录(必需 `inputs[]` 含 `coverage_report.recommended_strategy` / `coverage_report.recommended_reference_species`)、与 `REQUIRED_SCOPE` 在 `trajectory_schema.py` 注册。
- **CAP-5 SKILL.md 触发扩展**
  - **intent:** cell-annotation skill 的 description 触发条件覆盖跨物种场景,无需用户在 query 中显式说"跨物种"。
  - **success:** SKILL.md frontmatter `description` 在原触发短语**末尾追加**新短语(注释非模式生物数据 / 用模式生物参考 / 目标物种在 KG 中覆盖不足等),原触发词不动;`§1 角色与任务理解` 的"数据范围"小节删"不做跨物种注释",改为"默认具备跨物种注释能力"。`description` 总长不超过 1024 字符(加载器约束)。
- **CAP-6 references 文档扩展**
  - **intent:** LLM 在跨物种决策时可参考完整的 SOP / 陷阱 / 指标解读 / KG schema 说明,而无需手动浏览 handbook。
  - **success:** `references/sop.md` 新增 SOP-2.5 跨物种路由预检章节,SOP-3 拆为 3a 预检 → 3b 同源映射 → 3c organ 对齐 → 3d 候选聚合融合。`references/traps.md` 新增"跨物种专项陷阱"小节(物种命名格式 / Ensembl 限速 / 一对多 / 层级过度传播 / 扩张家族 / 参考物种覆盖不足 6 条)。`references/metrics.md` 新增 `ortholog_*` / `n_direct_hits` / `n_ortholog_hits` / `recommended_strategy` 字段的 LLM 解读。`references/kg-schema.md` 物种命名章节补 Ensembl 端点路由与跨物种查询的物种名规范化表。

## Constraints

- **C-1 加载纪律不变** — 一个子命令 = 一次 h5ad 加载(既有架构铁律)。`step3a_kg_precheck` 与 `step2_ortholog` 必须纯 JSON + 外部 REST,严禁碰 h5ad。
- **C-2 B1 评估不破** — 现有 default arm `strict=0.9236 / macroF1=0.4501 / low-conf=0` 在批 1 后不降低(parallel path 在 KG 覆盖充分的物种上不应改变主导候选);批 2 后用 evals E-1(原)E-6(新)各跑一次比对。
- **C-3 SKILL.md 描述只追加不删减** — 原 description 在拟南芥 / 有 KG 物种数据场景下行为不变;新增触发词只追加。
- **C-4 凭据归属保持当前** — Neo4j 凭据在 `skills/cell-annotation/.env`(harness 不知道 Neo4j 存在)。`step2_ortholog` 使用同 Neo4j 凭据 + 自己的 Ensembl REST 配置(无凭据,默认 rate-limit 退避;超时 / 失败降级)。
- **C-5 共享 schema single source of truth** — `cross_species_routing` 决策点的 scope 类型(session)与枚举值必须先在 `skills/cell-annotation/scripts/trajectory_schema.py:REQUIRED_SCOPE` 注册,`write_judgment.py` 与 `scripts/validate_log.py` 才接受写入与校验。
- **C-6 分两批实施** — 批 1 落地 CAP-1 / CAP-2 与工具层基础测试,**不动** SKILL.md / step3c_kg 核心 / references;批 2 接入 CAP-3 / CAP-4 / CAP-5 / CAP-6 + evals + B1 回归。两批之间 B1 评估持续通过。

## Rollout Plan(分批实施)

> 本节专门解释"批 1 / 批 2"是什么、为什么分两批、各自的范围与不变量。**这是用户决定"是否批准批 1 先上"的关键依据**。

### 为什么分两批

跨物种注释改动触及面广(新 step + SKILL.md 主体 + step3c_kg 核心 + references + 14 决策点 + evals + B1 评估)。**一次性落地风险太高**:任何一环出错都可能让 B1 三臂(strict/macroF1/low-conf)数字退化,这是项目基石。

分两批的核心原则是**让 LLM 行为在中间点保持稳定**:

- **批 1 落地后**,LLM 完全不知道新工具存在(SKILL.md / step3c_kg 不动),所以 LLM 行为 100% 与现状一致,B1 评估自动保持。
- **批 2 落地后**,LLM 才在 SKILL.md 看到 `cross_species_routing` 决策点,开始静默调用跨物种路径。

两批之间存在一段"工具就绪但 LLM 不会调"的窗口期。这是**故意设计**的:让用户/审阅者先在批 1 工具上做工程验证(Ensembl REST 集成、缓存、降级、pytest),批 2 接入 LLM 时工具已经是稳态。

### 批 1 — 工程就绪,不改变 LLM 行为

| 维度 | 内容 |
|---|---|
| 范围 | CAP-1 (`step3a_kg_precheck`) + CAP-2 (`step2_cross_species_map`) |
| 新增文件 | `skills/cell-annotation/scripts/step3a_kg_precheck.py`、`step2_ortholog.py`、`annot_harness/tests/test_step3a_kg_precheck.py`、`test_step2_ortholog.py` |
| 修改文件 | `skills/cell-annotation/scripts/common.py`(新增 `SPECIES_NAME_ALIASES` / `normalize_species_name` / `ensembl_rest_host`) |
| 不动文件 | `SKILL.md`、`references/*`、`scripts/step3c_kg.py`、`scripts/trajectory_schema.py`、`scripts/write_judgment.py` |
| 验证手段 | `python -m pytest`(89 + 新增 ≥ 4 测试全过);手动调 `step3a_kg_precheck__run --target-species=arabidopsis_thaliana --organ=root` 看 coverage_report.json;手动调 `step2_ortholog__run --target-species=arabidopsis_thaliana --reference-species=oryza_sativa` 看 ortholog_map.json;B1 default arm 三数字(strict=0.9236 / macroF1=0.4501 / low-conf=0)严格保持 |
| LLM 视角 | 完全不知道新工具存在。LLM 跑 pipeline 时行为与现状 100% 一致。 |
| 用户验收点 | ① pytest 全过 ② B1 数字不破 ③ 手动调两个新工具产物 JSON 字段符合 data-contracts.md |

### 批 2 — LLM 接入 + 文档体系 + 评估回归

| 维度 | 内容 |
|---|---|
| 范围 | CAP-3 (step3c_kg `--ortholog-map`) + CAP-4 (SKILL.md `cross_species_routing` 决策点) + CAP-5 (SKILL.md 触发词扩展) + CAP-6 (references 四文件扩小节) + evals E-6/E-7 + B1 三臂回归 + B2 真实数据回归(若有) |
| 新增/修改文件 | `skills/cell-annotation/scripts/step3c_kg.py` 增 `--ortholog-map` 选项;`SKILL.md` 决策点 13→14 + 描述追加 + §1 数据范围改;`references/sop.md` 新增 SOP-2.5;`references/traps.md` 新增跨物种专项陷阱;`references/metrics.md` 新增跨物种指标解读;`references/kg-schema.md` 新增端点路由小节;`skills/cell-annotation/scripts/trajectory_schema.py` 注册 `cross_species_routing`;`skills/cell-annotation/evals/evals.json` 加 E-6/E-7 |
| 验证手段 | `scripts/validate_log.py` 通过(14 decision 全部 enum 合规);B1 三臂数字严格保持;evals E-6 跑通(覆盖高物种走单路径不变);evals E-7 跑通(覆盖低物种走混合路径,需 mock dataset);可选非模式物种数据集 |
| LLM 视角 | 看到 `cross_species_routing` 决策点 + 新工具 + 新 references;按 references/sop.md SOP-2.5 流程静默决策 |
| 用户验收点 | ① validate_log 通过 ② B1 三臂数字保持 ③ E-6/E-7 通过 ④ (可选) 非模式物种 ≥50% cluster 有 ortholog 支持 |

### 两批的不变量

无论批 1 还是批 2 都必须满足:

- **加载次数零增量** — `step3a_kg_precheck` 与 `step2_ortholog` 都 0 h5ad 加载;`step3c_kg --ortholog-map` 仍 0 h5ad 加载。
- **现有工具行为向后兼容** — 任何 step 加新参数都不改变老调用的输出。
- **Neo4j 凭据归属不变** — 仍由 `skills/cell-annotation/.env` 提供,harness 不知道 Neo4j 存在。
- **B1 三臂数字维持** — 任何 batch 内或 batch 间评估数字都不退化。
- **现有 89 测试 + 新增测试全过** — 不允许"为通过新功能而删/改老测试"。

### 决策权

批 1 完成时,用户**单独验收**后再启动批 2。批 1 验收不通过 → 修订批 1 实现或 spec,**不**自动滑入批 2。详见 open-questions.md §决策流程。

## Non-goals

- **NG-1 不在跨物种流程中做基因 ID 转换**(TAIR locus→symbol 等)。这仍是数据处理责任,由用户在 pipeline 上游完成。跨物种工具只接受 `adata.var_names` 原样输入。
- **NG-2 ~~不实现 protein-level BLAST / diamond 比对~~**。**已撤销 (2026-09-10)**。蛋白级 BLAST 单独立项,见 `_bmad-output/specs/spec-blastp-homology/SPEC.md`。Ensembl Compara 仍是默认 provider。
- **NG-3 不在批 1 阶段重写 SKILL.md 主体或 step3c_kg.py 核心逻辑**。批 1 只新增两个独立工具 + 测试;SKILL.md / step3c_kg / references 改动属于批 2。
- **NG-4 不在批 1 阶段加 cross_species_routing 决策点到 SKILL.md**。该决策点随批 2 一次性加入,与 SKILL.md 描述扩展、references 扩展同步落地,避免 LLM 在缺 references 支撑时裸调用新工具导致判断不一致。
- **NG-5 不实现 Ensembl Compara REST 离线缓存**。仅做 species-level 缓存(同 target+reference 组合复用 `step2_ortholog/ortholog_map.json`);无 Redis / DB 缓存层。
- **NG-6 不实现 sorghum bicolor ground-truth / label_map 准备**。PRJNA935359 数据集已 `dataset/init.py` 标准化(obs 列已删除,index CSV 已抽出),但 `experiments/gt_cells_sorghum.csv` 与 `experiments/label_map_sorghum.json` 尚未生成,且 `scripts/build_gt_cells.py` 硬编码 `Seurat_clusters / Celltype / 33956` 不适配 sorghum 的 `seurat_clusters / celltype_after`。这些是**数据基板扩展任务**,需要:
  1. `build_gt_cells.py` 加 `--index-csv` / `--h5ad` / `--expected-n` 参数(已有) + 支持小写列名分支(读 `r[2]` 时按 header 找 `celltype`/`celltype_after` 列)
  2. `scripts/build_label_map.py` 为 sorghum 的 cell type 标注建映射到 KG ontology
  3. `scripts/evaluate_annotations.py` / `bootstrap_test.py` 加 `--dataset-id` 支持 sorghum
  上述 3 项属于"评估数据基板扩数据集"任务,**不开在本 spec 内**,另立 spec 跟进。批 2 阶段二验证依赖该 spec 先完成。

## Success signal

- **批 1 完成时:** 新工具 `step3a_kg_precheck__run` 与 `step2_ortholog__run` 在拟南芥数据(SRP171040)上端到端跑通,输出 `coverage_report.json`(arabidopsis_thaliana 应返回 `coverage_tier=high`, `recommended_strategy=single_species` 主导,推荐参考物种列表含 oryza_sativa 等近缘 Plant)与 `ortholog_map.json`(arabidopsis_thaliana→oryza_sativa 应有大量命中且 perc_id 中位数 >70%)。B1 default arm strict 不退化,annot_harness/tests 89+ 个测试全过。
- **批 2 完成时:** SKILL.md 新增 `cross_species_routing` 决策点后,在拟南芥数据上跑完整 7 步 + 14 决策点(含 cross_species_routing 与改写后的 candidate_gap),`run_log.jsonl` 经 `scripts/validate_log.py` 通过,judgment 的 decision 全部落在 `REQUIRED_SCOPE` 枚举内。B1 三臂(strict/macroF1/low-conf)数字维持。
- **批 2 真实价值验证(必做,使用 PRJNA935359):** 用 **PRJNA935359 (Sorghum bicolor, 高粱)** 数据集(10580 细胞,plant root,**已在 dataset/init.py 走完标准化**,但 ground-truth `gt_cells_sorghum.csv` / `label_map_sorghum.json` 尚未生成 — 见 OQ-1 决断与 NG-6)做端到端验证,分两阶段:
  - **阶段一(可立即跑,批 2 完成时):** 用 sorghum bicolor 数据跑 `step1_prepare → step2_markers → step3a_kg_precheck → step2_ortholog → step3c_kg`(跨物种路径),验证:
    1. `step3a_kg_precheck` 对 sorghum_bicolor 返回 `coverage_tier=low`(零直接命中 < 50)、`recommended_strategy=cross_species_only`、推荐 ref 物种含 oryza_sativa / zea_mays / arabidopsis_thaliana(高覆盖 Plant)— 详见 A-5(已 Neo4j 实测确认)
    2. `step2_ortholog` sorghum_bicolor → oryza_sativa 命中率 > 30%(同 Plant,Ensembl Compara 应收录)
    3. `step3c_kg` 双路径并行后,跨物种路径对至少 50% 的 cluster 给出有 ortholog 支持的候选(非 unknown)
    4. evals E-7(覆盖低物种场景,见 OQ-2 决断)在 sorghum 上跑通
    5. evals E-6(覆盖高物种对照,见 OQ-2 决断)在 SRP171040 上跑通,验证 `routing_accept single_species` 不改变现有行为
  - **阶段二(需数据基板扩展,挂独立 spec):** 跑 D-1/D-2/D-4 评估(sorghum_gt_cells.csv + sorghum_label_map.json 准备完后),验证 strict_accuracy 与 macroF1。本阶段**不在本 spec 范围**,需另开 spec(详见 NG-6)。
- 若阶段二数据集评估不达标,跨物种注释的"真实价值"标记为"框架就绪但待精度调优",**不**回退已落地的批 1 / 批 2 工程。

## Assumptions

- **A-1** 当前数据集(SRP171040 拟南芥根)在 KG 中 arabidopsis_thaliana cell type 覆盖充分(21299 genes / 195 cell types / 141497 edges,Neo4j 实测),批 2 验证时跨物种路径预期只在少部分 cluster 引入新候选,主导判断仍由直接路径给出。
- **A-2** Ensembl Compara REST 在生产环境(集群 / CI)对 `rest.plants.ensembl.org` 的 DNS 解析正常。若该域名在用户网络下也解析失败,Q5 降级策略(回退单物种 + warn)兜底。
- **A-3** Neo4j 物种命名混用(Plant 小写下划线 / Animal 大写带空格)在所有 KG 内容中保持一致。`step2_ortholog` 返回的 `target.species`(如 `homo_sapiens`)回查 KG 时需规范化映射表(`homo_sapiens` ↔ `Human`, `mus_musculus` ↔ `Mus musculus` 等);规范化表是 `references/kg-schema-extension.md` 的内容之一。
- **A-4** Ensembl Compara POST 端点限速 ~15 req/s,55 req/min,unauthenticated。`step2_ortholog` 内置指数退避(1s/2s/4s/8s,最多 4 次)+ 429 触发后 sleep Retry-After 秒。
- **A-5** PRJNA935359 (Sorghum bicolor, 高粱) **在 KG 中零覆盖**(Neo4j 实测 `MATCH (g:Gene) WHERE g.Species="sorghum_bicolor"` 返回 0,含 "sorghum" 的 species 字段也 0)。`step3a_kg_precheck` 对其返回 `coverage_tier=low`(零命中 < 50),`recommended_strategy=cross_species_only`,推荐 ref 含 oryza_sativa / zea_mays / arabidopsis_thaliana(均为 Plant 且 KG 覆盖高)。`step2_ortholog` sorghum_bicolor→oryza_sativa 命中率需 Ensembl 实测(待批 1 验证;预期 > 30%,因 Ensembl Compara 收 sorghum 且与水稻同禾本科)。批 2 阶段一验证 PRJNA935359 跑出"零直接命中 + 跨物种路径命中"为预期结果。

## Open Questions

- **OQ-1** [已决断,2026-08-24] 用 PRJNA935359 (Sorghum bicolor) 作为批 2 真实价值验证数据集。验证分两阶段:阶段一(可立即跑,仅评跨物种路径覆盖率)+ 阶段二(需数据基板扩展任务先完成,评 strict/macroF1)。详见 Success signal "批 2 真实价值验证" + NG-6。
- **OQ-2** [已决断,2026-08-24] P5 evals 跨物种场景采用方案 A — 新增 E-6 (SRP171040 覆盖高对照,验证 `routing_accept single_species` 不改变现有行为) + E-7 (PRJNA935359 sorghum_bicolor,验证 `routing_accept cross_species_only` + 跨物种路径给出候选)。E-1~E-5 不动。`evals.json` 加 2 条;E-7 用真实 sorghum 数据, 不造 mock。详见 Success signal。
- **OQ-3** [已决断,2026-08-24] 多参考物种融合采用方案 A — 候选简单 union, LLM 在 candidate_gap 读 `source_species_set` / `source_path_set` 区分优先级(优先级规则:含目标物种名 > 同 species_type ref > 远缘 ref; `direct` > `ortholog` > `mixed`)。不去重, 不加权排序。算法升级 (B/C 方案) 留作批 3 优化, 批 2 evals E-7 跑完看 LLM 实际行为再决定。
- **OQ-2** P5 evals 跨物种场景是新增 E-6 / E-7 还是扩展现有 E-1~E-5?新增会让 `evals.json` 体量增长但更聚焦;扩展更省但需重设计 `expected_decision`。
- **OQ-3** 多参考物种融合的优先级算法细节。CAP-2 输出 per-reference 的 cross_species_map;CAP-3 在聚合多个参考物种的候选时,按"亲缘距离"还是"cell type 覆盖度"还是两者加权排序?默认采用 `亲缘近 > 覆盖广`(同科 > 同 species_type > 远缘),是否合理?
