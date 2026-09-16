# annotHarness 项目文档(简历撰写参考)

> 本文档基于项目设计文档与代码整理,目的是便于根据不同岗位 JD 挑选合适的素材编写简历。
> 项目仍在实施过程中,文档反映设计规划与已完成部分的现状。

---

## 1. 一句话项目介绍

**annotHarness**（细胞注释 harness）面向 scRNA-seq 细胞类型注释：把判断拆成 **47 个原子操作 + 247 个量化指标 + 13 个决策点**，让大语言模型在 pipeline 关键节点上做出可追溯、可审计、可用于微调训练的智能判断。

---

## 2. 项目解决的科学/工程问题

### 2.1 核心问题

单细胞 RNA-seq 注释的传统方法要么是 hardcoded 阈值启发式(脆、上下文不灵活),要么是监督学习(需要标注数据,植物领域几乎无可用参考集)。本项目研究的核心问题是:

> **LLM 作为 pipeline 的判断引擎,比固定启发式/默认参数更好吗?**

### 2.2 四点科学贡献

1. **Loop/Skill/Pipeline 三层分离架构** — 通用 Agent 宿主 + 领域规范 + 执行流水线,换 skill 可跑完全不同的任务
2. **LLM 作为 13 个决策点的判断引擎** — 贯穿 pipeline 全程,而非只在末端给标签
3. **轨迹日志设计**(`run_log.jsonl`)— 记录判断的输入(看了哪些指标) + 推理 + 输出,可导出为微调训练对
4. **Loop 级通用记忆(笔记本)** — 跨会话复用的经验库,与 skill 解耦

---

## 3. 整体架构

### 3.1 三层分离架构

```
┌─────────────────────────────────────────┐
│  Loop(领域无关通用层)               │ ← LangGraph 实现
│  - 加载 skill / 跑 LLM↔tool 循环 │
│  - 记录 conversation.jsonl           │
│  - 内置笔记本(write_note/retrieve) │
└─────────────────┬───────────────────────┘
                  │ 加载 skill
                  ▼
┌─────────────────────────────────────────┐
│  Skill(领域规范)                   │ ← SKILL.md + scripts/
│  - 领域知识 / SOP                   │
│  - tool_schemas + tool_runtime      │
│  - decision 枚举 / 阈值参考          │
└─────────────────┬───────────────────────┘
                  │ 提供工具
                  ▼
┌─────────────────────────────────────────┐
│  Pipeline(执行层)                  │ ← 47 原子操作
│  - 7 步 CLI 脚本                    │
│  - 单子命令 = 一次 h5ad 加载       │
│  - 每 op 写 run_log.jsonl exec 记录  │
└─────────────────────────────────────────┘
```

### 3.2 7 步 Pipeline

| 步骤 | 作用 | 决策点 |
|---|---|---|
| **step1_prepare** | QC + 过滤 + 归一化 + HVG + PCA + kNN + Leiden 聚类 | qc_threshold / resolution_select / clustering_quality / batch_effect |
| **step2_markers** | DE 分析 + marker 基因发现 | de_method / marker_quality |
| **step3c_kg** | Neo4j 知识图谱查询(基因→细胞类型) | kg_match |
| **step4_judge** | 簇级别 first/second 候选排名 | candidate_gap / candidate_disambiguate |
| **step5_refine** | 模糊簇子聚类与判断 | refine_effect / unknown_cluster |
| **step6_validate** | top marker 表达验证 + 最终标签 | label_confirm |
| **step7_diagnose** | 全局诊断报告 | global_quality |

### 3.3 数据流关键约束

- **最小化 h5ad 加载次数**:h5ad 文件 GB 级,完整 pipeline 仅 4 次加载(1 raw + 3 proc);step3c_kg / step4_judge / step7_diagnose 0 次加载
- **sidecar 中间文件**:`obs_snapshot.csv`(5 MB)、`var_snapshot.csv` 替代完整 h5ad
- **环境变量管外部依赖**:Neo4j 凭证经 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` 环境变量配置,优先级 CLI > env > 默认
- **植物特化**:QC 同时看线粒体(`MT-` 前缀)与叶绿体(`ATCG` 前缀)基因,不用动物参考阈值

---

## 4. 关键技术设计

### 4.1 Loop 设计(`annot_harness/`)

**核心原则**:**极简通用,不做上下文管理,只记轨迹**。

- LangGraph StateGraph 实现 `agent → tools → END` 循环
- 工具分发器(`dispatcher.py`)支持三种执行方式:`subprocess`(CLI 脚本)、`function`(Python 函数)、`builtin`(loop 内置笔记本)
- 标准 skill 包加载器(`skill_loader.py`):从 SKILL.md YAML frontmatter + scripts/`--dump-schema` 自动派生 `system_prompt` / `tool_schemas` / `tool_runtime`,避免手工 JSON 漂移
- 内置笔记本(`notebook.py`):Chroma 向量库持久化 + BM25 兜底,跨会话检索经验
- 测试套件 57 个测试通过(`pytest.ini` + `annot_harness/tests/`)

**关键接口契约**:subprocess 工具 stdout 最后一行输出 JSON `{"status":"ok","data":{...}}` 或 `{"status":"error",...}`,loop 原样透传给 LLM。

### 4.2 Skill 设计(`skills/cell-annotation/`)

**核心原则**:**标准 skill 规范,可移植,可被任意 skill-aware agent 加载**。

- SKILL.md(< 500 行,中文,imperative 句式)含:角色定义 + 13 决策点流程 + 6 陷阱警告 + references 引用指引 + 日志指导
- `references/` 按需加载的文档(SOP / 247 指标解读 / 陷阱 / KG schema),不进入系统消息
- `scripts/` 47 个原子操作的标准实现,每个实现 `--dump-schema` 自描述
- `evals/` 测试用例与断言(`evals.json`)
- `assets/` 输出用文件(报告模板、工具清单)

**触发机制**:YAML frontmatter `description` 字段覆盖"注释单细胞数据 / 找 marker / 细胞类型判断"等真实触发说法。

### 4.3 轨迹日志设计(`run_log.jsonl`)

**核心原则**:**单一文件、append-only、不覆盖**。

- 4 类记录:`session_start` / `exec` / `judgment` / `session_end`
- `run_id` 格式:`{step}.{op}#{attempt}`,如 `step1_prepare.leiden_cluster#1`
- "当前值"语义:同 `run_id` 前缀中 `seq` 最大的记录
- 因果链自然形成:judgment 记录的 `run_ref` 指向基于的 exec 记录
- 共享 schema:`skills/cell-annotation/scripts/trajectory_schema.py` 拥有 `REQUIRED_SCOPE` 字典,skill 端工具与 eval 端验证脚本共同引用,避免分叉

**微调数据导出**:从 run_log 提取 judgment 记录 → 当前有效判断(每 decision_point+scope 取末版)+ 自我纠正对(同点多次判断取首末版),产出 `fine_tune_pairs.jsonl` / `self_correction_pairs.jsonl`。

### 4.4 13 个决策点

| 决策点 | 阶段 | 粒度 | 枚举 |
|---|---|---|---|
| qc_threshold | step1 | session | threshold_set / threshold_default |
| resolution_select | step1 | session | resolution_chosen |
| clustering_quality | step1 | session | clustering_accept / clustering_adjust |
| batch_effect | step1 | session | batch_effect / condition_specific / well_mixed |
| de_method | step2 | session | wilcoxon / pseudobulk_all / pseudobulk_rare |
| marker_quality | step2 | session | markers_accept / markers_adjust_filter / markers_fail |
| kg_match | step3 | session | id_match_ok / id_mismatch_gene_key / id_mismatch_organ |
| candidate_gap | step4 | cluster | first_decisive / ambiguous_parent_child / ambiguous_synonym / ambiguous_true / unknown |
| candidate_disambiguate | step4 | cluster | ambiguous_parent_child / ambiguous_synonym / ambiguous_true |
| refine_effect | step5 | cluster | refine_effective / refine_ineffective / refine_skipped / refine_autocorr_low |
| unknown_cluster | step5 | session | single_unknown_type / multiple_unknown_types |
| label_confirm | step6 | cluster | label_confirmed / label_downgraded / label_unknown |
| global_quality | step7 | session | quality_good / quality_acceptable / quality_poor |

每条 judgment 必填 `inputs[]`(实际读取的指标 path + 值快照)、`output.decision`、`output.confidence`、`reasoning`(自然语言推理链)。

### 4.5 六大陷阱(LLM 必须警惕)

| 陷阱 | 简述 |
|---|---|
| 1 | 植物 mt/cp 与动物不同,`MT-` 前缀匹配不到植物基因 |
| 2 | 层级本体下的并列不是模糊(root cap ⊃ lateral root cap 共享 marker) |
| 3 | 小样本时 count_ratio 骗人,必须配合 count_diff |
| 4 | pct1 高不等于好 marker,必须看 pct2(管家基因陷阱) |
| 5 | 发育连续谱 silhouette 天然低,不代表聚类错 |
| 6 | 批次熵低不一定是批次效应,突变体/条件特异群体天然单批次 |

---

## 5. 实验设计(方法论验证)

### 5.1 B1 三臂决策对比(killer experiment)

同一条 pipeline,三组条件,唯一变量是决策方式:

| Arm | 决策方式 | 是否耗 API |
|---|---|---|
| ① Fixed-default | 全 accept,默认参数 | 否 |
| ② Rule-based | 阈值启发式(知识文件参考值转 if-then) | 否 |
| ③ LLM-judge | 本 harness Agent | 是 |

**评估口径**(2026-08 修订):细胞级评估(per-cell macro-F1 / accuracy)+ cluster-aware bootstrap 95% CI。

**预注册判定规则**:
- R1:③ − ② ≥ 0.03 且 CI 不含 0 → LLM 胜阈值(支持核心假设)
- R2:② − ① ≥ 0.03 → "读 metrics + 规则"有价值
- 陷阱:③ 在 ≥4/6 出现的陷阱上优于 ② → 陷阱弹性论点成立

**实现要点**:三臂共享 pipeline 工具与 dispatcher,差异仅在决策层;③ 的动作空间必须 ⊆ ② 规则表覆盖的决策/动作枚举。

### 5.2 其他实验

- **B3**:247 指标最小充分集 — 统计 judgment.inputs[].path 频次,预期 ~30 个核心子集(精简 SKILL.md)
- **B4**:自我纠正有效性 — adjust → recluster → accept 循环是否真改善质量(改善率 ≥ 50%)
- **A1**:端到端准确率(细胞级)
- **A3**:与 Marker 硬匹配 / CellTypist / SingleR 等基线对比
- **E1**:成本与效率(token / 轮次 / 墙钟时间)
- **C2**:KG 消融 — full KG vs 无 KG 臂,差 ≥ 0.03 则 KG 层有增益
- **C4**:多 API 模型对比(GPT-4o / Claude / Gemini)— 验证 model-agnostic
- **N1/N2/N3**:笔记本(loop 通用记忆)消融 — ⑦ notebook-on vs ⑥ no-notebook
- **S1**:合成场景注入 — 把 6 陷阱操作化为 8 个已知真值的簇合并用例,为 refine 决策点补统计力

---

## 6. 技术栈与依赖

### 6.1 核心库

- **LangGraph / LangChain**:Agent 循环 + function calling
- **OpenAI API / Anthropic API / Google API**(可插拔):LLM 后端
- **Scanpy / AnnData**:单细胞分析核心
- **Leidenalg / igraph**:聚类算法与模块度
- **Scikit-learn**:轮廓系数、AUC、Cohen's d 等指标
- **SciPy / Statsmodels**:统计检验(BH-FDR 等)
- **Sentence-transformers / Chroma**:向量检索(笔记本)
- **Neo4j**:知识图谱(195K 节点 / 466K `marker_of` 边)

### 6.2 测试与工程

- **pytest**:57 个测试覆盖 loop / dispatcher / skill_loader / conversation / notebook / trajectory schema
- **Conftest fixtures**:FakeEmbedder 替代真实 embedding 模型,确保测试离线可跑

---

## 7. 已完成 / 进行中 / 未开始

### 7.1 已完成 ✅

**设计层**
- 全部设计文档(`design/` 8 份 + `knowledge/` 4 份,共 47 op / 247 指标 / 13 决策点 / 12 实验)
- 数据入库:`dataset/h5ad/SRP171040.h5ad`(33,956 细胞 Arabidopsis 根 scRNA-seq)
- 真值准备:`dataset/index/SRP171040.h5ad.csv`(12 真值类型)
- Neo4j 知识图谱在线(基因 ID 标记)
- 基因 ID 映射资源(可选上游使用):`name_map4Arabidopsis_thaliana_symbol.json`(10,963 条)——不再被 skill 调用

**Loop 宿主代码**(`annot_harness/`,6 文件)
- `loop.py` / `dispatcher.py` / `skill_loader.py` / `session.py` / `conversation.py` / `notebook.py`
- 89 个 pytest 全部通过(`annot_harness/tests/`,含 step3c_kg schema-discipline 测试)

**Skill 包**(`skills/cell-annotation/`)
- SKILL.md(< 500 行,中文,imperative 句式,含 13 决策点 + 6 陷阱 + 日志指导)
- `scripts/` 完整实现:7 个 stepN 脚本 + common.py + trajectory_schema.py + write_judgment.py,**共 4,092 行**
- 每个脚本支持 `--dump-schema` 自描述(加载器自动派生 tool_schemas / tool_runtime)
- `references/` 4 个分块文件(mop.md / metrics / trap / kg-schema)
- `assets/` 报告模板
- `evals/` evals.json

**Pipeline scripts**(`skills/cell-annotation/scripts/`,7 步)

| 脚本 | 行数 | 子命令(各含 `--dump-schema`) | h5ad 加载 |
|---|---|---|---|
| `step1_prepare.py` | 777 | metrics / run / recluster | 1× raw(proc 可选) |
| `step2_markers.py` | 453 | run(wilcoxon / AUC / pseudobulk) | 1× proc |
| `step3c_kg.py` | 485 | query / test-connection | 0 |
| `step4_judge.py` | 214 | run(纯 JSON,LLM 决策视图) | 0 |
| `step5_refine.py` | 411 | run(自相关 → 子聚类 → 子簇 DE/KG) | 1× proc |
| `step6_validate.py` | 417 | run / report(backed 模式按列读) | 1× proc(可 0) |
| `step7_diagnose.py` | 221 | run(读 sidecar,0 加载) | 0 |

`common.py`(840 行):9 个通用函数(`describe_distribution` / `filter_funnel` / `effect_size` / `pairwise_overlap` / `batch_mixing` / `cluster_quality` / `variance_explained` / `resolution_stability` / `candidate_autocorr`) + `append_log` / `next_run_id` / `current_metrics` / `dump_schema` / `arg_spec`

**共享 schema 模块**(`skills/cell-annotation/scripts/trajectory_schema.py`)
- `REQUIRED_SCOPE` 字典:decision point → 必填 scope 类型(13 项,9 session + 4 cluster)
- 同时被 skill 端 `write_judgment.py` 与 eval 端 `scripts/validate_log.py` 引用(单一事实源)

**echo skill 冒烟**:标准 skill 格式验证 Loop 通用性(无关 cell-annotation 业务)

### 7.2 进行中 ⚠️

- 实验代码(`scripts/` 下的 `build_label_map.py` / `build_gt_cells.py` / `validate_log.py` / `evaluate_annotations.py` 已完成)
- 评估基础设施(bootstrap / 陷阱分析 / token 统计)尚需补全
- B1 三臂驱动 + cell-aware bootstrap 实跑

### 7.3 未开始 ❌

- B1 三臂跑通 + cell-level 评估(B1 评估口径已敲定,代码未跑)
- A1/A3/C2/C4/N 组实跑
- B3/B4/S1 分析脚本
- SKILL.md/references 迭代优化(基于 evals 实跑反馈)
- `.skill` 打包交付

---

## 8. 项目里程碑

| 里程碑 | 验收标准 | 状态 |
|---|---|---|
| M1 Loop | echo skill 跑通 + 加载器派生三接口 + 笔记本读写 | ✅ |
| M2 scripts | 7 脚本代码完成 + `--dump-schema` 全可用 + shared schema | ✅ |
| M3 Skill 包 | 标准 anatomy 合规 + 三接口派生 + SKILL.md <500 行 | ✅ |
| M4 数据底座 | gt_cells 对齐 + label_map 12 类型 | ✅(代码完成,数据生成待跑) |
| M5 Skill 测试 | 5 个 evals 断言通过 | ❌(evals.json 已起草) |
| M6 实验 | B1 三臂 + bootstrap CI + 陷阱分析 | ❌ |
| M7 交付 | SKILL v2 + description 优化 + `.skill` 打包 | ❌ |

**关键路径当前状态**:P1(Loop)+ P2(scripts)+ P3(SKILL)+ P4(数据底座代码)已完成;P5(skill 测试)与 P6(实验)待跑。

---

## 9. 简历关键词矩阵(按岗位方向)

### 9.1 LLM / Agent 工程师方向

**可用关键词**:Agent 架构设计、LangGraph、function calling、tool dispatcher、prompt engineering、trajectory logging、LLM-as-judge、skill-driven agent、multi-turn reasoning

**可用项目描述模板**:
- 设计并实现了基于 LangGraph 的领域无关 Agent Loop,支持从标准 skill 包自动派生 system prompt / tool schemas / tool runtime,避免 schema 漂移
- 提出"Loop / Skill / Pipeline"三层分离架构,实现通用 Agent 宿主 + 可移植领域规范 + 复用流水线
- 设计了 append-only NDJSON 轨迹日志,记录 LLM 判断的 inputs / reasoning / output,可导出为微调训练对

### 9.2 单细胞 / 生信工程师方向

**可用关键词**:scRNA-seq、Scanpy、cell type annotation、marker discovery、Leiden clustering、knowledge graph (Neo4j)、QC pipeline、batch effect correction、doublet detection

**可用项目描述模板**:
- 设计并实现了 7 步 / 47 原子操作 / 247 量化指标的单细胞注释 pipeline,完整流程仅 4 次 h5ad 加载
- 将植物特化(线粒体 + 叶绿体 QC)与通用聚类(Leiden 多分辨率)结合,支持单细胞数据预处理到类型判断全流程
- 集成 Neo4j 知识图谱(195K 节点)做 marker → 细胞类型候选查询,结合本体遍历处理层级关系

### 9.3 数据 / ML 工程师方向

**可用关键词**:pipeline optimization、I/O optimization、sidecar data layout、metric catalog、bootstrap evaluation、statistical metrics(silhouette / Cohen's d / AUC / ARI)、sidecar pattern

**可用项目描述模板**:
- 通过 sidecar 中间数据(`obs_snapshot.csv`)设计,把 48% 的 pipeline 操作从全量 h5ad 加载降到 0 次
- 设计了 247 个结构化指标的 catalog,每个指标有明确路径与解读,支撑后续 LLM 判断
- 实现 cluster-aware bootstrap 1000 次重抽样,解决细胞非独立问题,正确评估三臂决策的统计显著性

### 9.4 评测 / 实验设计方向

**可用关键词**:LLM-as-judge evaluation、ablation study、A/B testing、pre-registered analysis、trap analysis、controlled probe、fine-tuning data generation

**可用项目描述模板**:
- 设计 12 个方法学实验(B/A/C/D/E/N/S 组),含 ① 默认 / ② 规则 / ③ LLM 三臂对比
- 预注册判定规则(避免 p-hacking),含效应量阈值(macro-F1 差 ≥ 0.03)
- 设计 8 个合成场景(S1)把 6 类陷阱操作化为已知真值的簇合并探针,为出现频率低的决策点补统计力
- 把轨迹日志导出为微调训练对,含当前有效判断 + 自我纠正对两种格式

### 9.5 知识工程 / RAG 方向

**可用关键词**:RAG、vector store(Chroma)、BM25 fallback、cross-session memory、retrieval-augmented decision making、experience replay

**可用项目描述模板**:
- 设计了 loop 级通用笔记本(write_note / retrieve_notes),与 skill 完全解耦
- Chroma 持久化向量库 + BM25 兜底,在离线 / embedding 不可用场景下保持可用
- 笔记本操作本身在 conversation 中自然记录,与 run_log.jsonl 独立不混淆

### 9.6 工程 / 工具链方向

**可用关键词**:CLI tool design、argparse-driven schema、single source of truth、environment variable config、graceful degradation、test design

**可用项目描述模板**:
- 把工具 schema 与 argparse 绑定为单一事实源,杜绝 schema 与代码漂移
- 外部依赖(Neo4j)统一通过环境变量配置,优先级 CLI > env > 默认
- 设计了 graceful degradation:scrublet 缺失时降级 doublet detection;backed 模式失败时回退全量加载
- 57 个 pytest 覆盖 loop / dispatcher / skill loader / notebook / conversation / trajectory schema

---

## 10. 关键数字(便于在简历里加量化指标)

| 指标 | 数值 |
|---|---|
| Pipeline 阶段数 | 7 步 |
| 原子操作数 | 47 个 |
| 量化指标数 | 247 个(117 核心 + 130 扩展) |
| LLM 决策点数 | 13 个 |
| 实验组数 | 12 个(B1/B3/B4/A1/A3/E1/C2/C4/N1/N2/N3/S1) |
| 完整 h5ad 加载次数(设计后) | 4 次(1 raw + 3 proc) |
| 不需 h5ad 的操作占比 | 48% |
| Pipeline 数据集 | 33,956 细胞 × 53,678 基因(Arabidopsis 根 scRNA-seq) |
| 真值类型 | 12 个 |
| KG 规模 | 195,322 节点 / 466,606 `marker_of` 边 |
| 单 session 预期 judgment 数 | ~67 条(29 簇 × 2 + 9 + 8) |
| 单 session 预期 token 上限 | ≤ 40 万 |
| 测试覆盖 | 61 个 pytest 通过(`annot_harness/tests/`) |
| Cell-annotation scripts 代码量 | 4,092 行(`common.py` 840 + 7 个 stepN 脚本 2,978 + trajectory_schema 22 + write_judgment 252) |
| Pipeline 脚本子命令数(每脚本 `--dump-schema` 派生) | step1: 3 / step2: 1 / step3: 2 / step4: 1 / step5: 1 / step6: 2 / step7: 1 |
| common.py 通用函数数 | 9(describe_distribution / filter_funnel / effect_size / pairwise_overlap / batch_mixing / cluster_quality / variance_explained / resolution_stability / candidate_autocorr) |
| 共享 schema 字段 | REQUIRED_SCOPE(13 项,9 session + 4 cluster,被 skill 端与 eval 端共同引用) |
| Cell-annotation SKILL.md 行数 | < 500 行(渐进披露) |
| Loop 代码文件数 | 6(loop/dispatcher/skill_loader/session/conversation/notebook) |
| 决策层与执行层文件 | decision 枚举字典 + REQUIRED_SCOPE 共享 schema(单一事实源) |

---

## 11. 项目文件结构速查

```
annotHarness/
├── design/                          ← 全部设计文档(8 份)
│   ├── loop_design.md               ← 通用 Loop 设计
│   ├── tool_design.md               ← 47 op + 4 次 h5ad 加载
│   ├── atomic_operations.md         ← 47 操作清单
│   ├── operations_metrics_catalog.md ← 247 指标 catalog
│   ├── trajectory_design.md         ← run_log.jsonl 设计
│   ├── rag_design.md                ← 笔记本(通用记忆)
│   ├── implementation_plan.md       ← 全项目推进计划 P1~P7
│   ├── experiment_design.md         ← 12 实验方法学设计
│   └── experiment_implementation.md ← 实验实施方案 + 预注册判定
├── knowledge/                       ← 领域知识(给 SKILL.md references 用)
├── annot_harness/                         ← Loop 宿主代码(已完成)
│   ├── loop.py / dispatcher.py / skill_loader.py
│   ├── session.py / conversation.py / notebook.py
│   └── tests/                       ← 57 测试
├── skills/
│   ├── echo/                        ← 标准 skill 冒烟
│   └── cell-annotation/             ← 主 skill(进行中)
│       ├── SKILL.md                 ← < 500 行
│       ├── scripts/                 ← 待实现
│       ├── references/              ← SOP / metrics / traps / kg-schema
│       ├── assets/                  ← 报告模板
│       └── evals/                   ← evals.json
├── dataset/
│   ├── init.py                      ← h5ad 入库规范化
│   ├── h5ad/SRP171040.h5ad         ← Arabidopsis 根 scRNA-seq
│   └── index/SRP171040.h5ad.csv     ← 真值(12 类型)
├── scripts/                         ← 评估 / 导出脚本(待实现)
├── experiments/                     ← 实验输出(待实现)
├── docs/                            ← 本文档目录
├── pytest.ini
└── readme.md
```

---

## 12. 简历写作建议

### 12.1 篇幅控制

- 简历项目描述 3~5 行,挑最有岗位匹配度的方向展开
- 不需要把所有架构细节都写上,挑能体现"工程能力 / 研究能力 / 架构能力 / 评测能力"的具体点

### 12.2 强调什么

- **设计文档完备性**:8 份设计文档覆盖架构 / 实现 / 实验 / 评测,体现工程严谨度
- **架构创新点**:三层分离 / 自动派生 schema / append-only 轨迹日志 / 共享 schema 模块
- **量化指标**:47 op / 247 指标 / 13 决策点 / 12 实验 / 61 测试 / 4,092 行 scripts
- **方法论严谨**:预注册判定规则 + 细胞级评估 + cluster-aware bootstrap
- **已完成部分**:Loop 宿主 + 7 步 pipeline 完整实现 + 测试套件 + 设计文档体系(可以作为"已交付"成果展示)

### 12.3 规避什么

- 不要把"未跑通"的部分写成"已验证"(如"三臂实验已验证 LLM 胜阈值"应改为"已设计 12 个预注册判定规则的实验,待跑")
- 不要夸大 LLM 实际效果(尚未跑实验,不应声称"性能优于 X")
- 项目仍在过程,描述应强调"已设计 / 已完成 / 进行中"分阶段表述

### 12.4 现成可写的成果句式(可直接套用)

- "**独立设计并实现了一套领域无关 Agent Loop**(LangGraph + 标准 skill 加载器 + 进程/函数/内置三型 dispatcher),通过 61 个 pytest 覆盖"
- "**主导了一套完整的 scRNA-seq 细胞类型注释 skill 包**:7 步 CLI pipeline(4,092 行 Python)、13 个 LLM 决策点、Neo4j 知识图谱集成,采用 argparse 单源 schema 自动派生加载器接口"
- "**设计了一种 append-only NDJSON 轨迹日志格式**(run_log.jsonl),统一记录指标与 LLM 判断,通过 shared schema 模块(`trajectory_schema.py`)消除 skill 端与 eval 端漂移,可导出为微调训练对"
- "**预注册了一套方法学实验体系**(12 个实验 / 预注册判定规则 / cluster-aware bootstrap),包含三臂决策对比与 6 类陷阱 oracle 表,严谨支撑后续结论"

---

## 附录 A:术语表

| 术语 | 含义 |
|---|---|
| scRNA-seq | single-cell RNA sequencing,单细胞 RNA 测序 |
| h5ad | AnnData 格式,单细胞数据的标准存储 |
| AnnData | 单细胞数据结构(X + obs + var + obsm + obsp + uns) |
| Leiden | 基于模块度优化的图聚类算法 |
| HVG | Highly Variable Genes,高变基因 |
| PCA | Principal Component Analysis,主成分分析 |
| kNN | k-Nearest Neighbors,k 近邻 |
| UMAP | Uniform Manifold Approximation and Projection,降维可视化 |
| DE / DEG | Differential Expression / Differentially Expressed Gene,差异表达 / 差异表达基因 |
| pct1 / pct2 | 目标基因在某簇 / 其他簇细胞中的表达细胞百分比 |
| Marker | 标记基因,用于识别细胞类型的特征基因 |
| KG | Knowledge Graph,知识图谱(本项目用 Neo4j 存基因↔细胞类型关系) |
| Macro-F1 | 各类 F1 的算术平均,对稀有类型公平 |
| ARI / NMI | 调整兰德指数 / 归一化互信息,聚类相似度指标 |
| Silhouette | 轮廓系数,聚类质量指标 |
| Bootstrap | 重抽样统计推断方法 |
| Sidecar | 与主文件同目录的辅助文件(如 obs_snapshot.csv) |
| Backed mode | AnnData 的只读模式,可按列读取而不全量加载 |
| SFT | Supervised Fine-Tuning,监督微调 |
| RAG | Retrieval-Augmented Generation,检索增强生成 |
| BM25 | 经典信息检索排序算法 |