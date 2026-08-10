# 全项目推进计划(实施路线图 · skill 优先版)

> 本版依据 skill-creator 规范重排:**skill = 标准可移植产物**(SKILL.md + 打包资源),不是 harness 私有格式。
> 项目目标 = 构建一个**符合标准 skill 规范的 cell-annotation skill**(带 scripts 的完整包),并用它跑通方法论实验。
> 状态约定:`[x]` 已完成 / `[ ]` 未开始 / `[~]` 进行中。

---

## 0. 现状盘点(2026-08 核查)

| 资产 | 状态 | 说明 |
|---|---|---|
| 设计文档 | ✅ 完备 | `design/` 8 份 + `knowledge/` 4 份:47 原子操作 / 247 指标 / 13 决策点 / 12 实验 |
| 数据入库 | ✅ 完成 | `dataset/h5ad/SRP171040.h5ad`(33,956 细胞)、`dataset/index/SRP171040.h5ad.csv`(12 真值类型) |
| 知识图谱 | ✅ 在线 | `NEO4J_*` 可连:195,322 节点 / 466,606 条 `marker_of` 边 |
| 基因名映射 | ✅ 就绪 | `name_map4Arabidopsis_thaliana_symbol.json`(10,963 条) |
| **skill 包** | ❌ 不存在 | `skills/` 为空(SKILL.md / scripts / references / evals 全无) |
| **loop 代码** | ❌ 不存在 | `harness/`(loop / dispatcher / skill 加载器 / notebook)未写 |
| **pipeline 代码** | ❌ 不存在 | `common.py` + 7 个 `stepN_*.py` 未写 |
| **实验代码** | ❌ 不存在 | `scripts/`、`experiments/` 未建 |
| 依赖环境 | ⚠️ 基本齐 | langgraph/scanpy/neo4j/sentence-transformers/sklearn/statsmodels/leidenalg 均可用;**scrublet 未安装** |

**结论:设计冻结、产物为零。一切围绕一个目标——做出一个标准、可测、可迭代的 cell-annotation skill。**

---

## 1. Skill 的标准形态(设计基准,来自 skill-creator)

```
cell-annotation/                        ← skill 目录(最终产物)
├── SKILL.md                            ← YAML frontmatter(name/description)+ 指令(<500 行理想)
├── scripts/                            ← 确定性/重复任务的执行代码(= pipeline,47 op)
├── references/                         ← 按需加载的文档(SOP / 指标解读 / 陷阱 / KG schema)
├── assets/                             ← 输出用文件(报告模板、工具清单)
└── evals/                              ← 测试用例 + 断言(evals.json)
```

**三级渐进加载**:

| 层级 | 内容 | 何时进入上下文 |
|---|---|---|
| 1. metadata | name + description(~100 词) | 常驻,决定触发 |
| 2. SKILL.md body | 指令(<500 行) | 触发后 |
| 3. scripts / references / assets | 无限 | 按需执行 / 读取 |

**对本项目的四条硬推论**:

1. **SKILL.md 只写"怎么干 + 何时读什么"**。247 指标解读全文(~545 行)、SOP 全文放 `references/`,按决策点按需读;SKILL.md 保持精简。→ B3 实验("哪些指标常驻 prompt、哪些按需查询")的结论天然落地,无需事后重构。
2. **`tool_schemas` / `tool_runtime` 是从 `scripts/` 派生的视图,不是手写顶层文件**。脚本的 argparse 是工具声明的单一事实源;加载器聚合生成两样东西,杜绝"schema 与 argparse 漂移"。
3. **description 是触发机制**。skill 不绑定 scHarness——任何 skill-aware agent 都能加载;loop 只是"能加载任何标准 skill 的宿主"(用 echo skill 证明)。
4. **skill 的开发走 skill-creator 循环**:草稿 → 测试 prompt(evals/evals.json)→ with-skill vs baseline → 迭代 → description 优化 → 打包。

---

## 2. 阶段总览:里程碑与依赖

```
P1 Loop 宿主 ──┐
               ├─→ P3 SKILL.md+references ──┐
P2 scripts ────┘(pipeline)                  ├─→ P5 skill 测试循环(evals)──→ P6 方法学实验(B1 等)
P4 数据底座(D-1~D-5)───────────────────────┘             │                   │
                                                         └── P7 迭代/description/打包
```

| 阶段 | 名称 | 对应 skill-creator 环节 | 关键产出 | 里程碑 |
|---|---|---|---|---|
| **P1** | Loop 宿主 + 标准 skill 加载器 | —(宿主) | `harness/` 6 文件(含加载器)+ 笔记本 | M1:echo skill(标准格式)跑通 |
| **P2** | Pipeline = skill 的 scripts/ | scripts 层 | `common.py` + 7 脚本 + `--dump-schema` 自描述 | M2:47 op 全跑通、schema 可派生 |
| **P3** | SKILL.md + references + description | 写草稿 | 标准 skill 包(scripts 入住) | M3:包结构合规、三接口派生成功 |
| **P4** | 实验数据底座 | 测试前置 | `experiments/gt_cells.csv` + `label_map.json` | M4:评估口径就绪 |
| **P5** | skill 测试循环(第一轮) | 跑测试用例 + 评估 | `evals/` 跑通、断言达标 | M5:skill 通过内部测试 |
| **P6** | 方法学实验(B1 三臂等) | 定量评估(大尺度) | B1/S1/A/E/B3/B4/N/C 全部结果 | M6:预注册判定表对照 |
| **P7** | skill 持续迭代与交付 | 迭代循环 + 优化 + 打包 | 测试集扩大、description 优化、`.skill` 包 | M7:方法论闭环 + 可交付 skill |

**关键路径**:P1 → P2 → P3 → P5(内循环:P3→P5→改→重跑)→ P6(B1 为主轴)。
**P5 是新增的关键闸门**:skill 质量不达标不进 B1,否则 B1 结论混入"skill 本身不好用"的混淆。

---

## 3. P1 — Loop 宿主 + 标准 skill 加载器(约 4~6 天)

> 依据 `loop_design.md` §5/§10、`rag_design.md` §8、skill-creator 的 skill anatomy。loop 仍然领域无关,但**格式感知**:它理解"标准 skill 包"长什么样。

### 3.1 任务清单

| # | 任务 | 产出 |
|---|---|---|
| L-1 | `harness/loop.py`(LangGraph StateGraph:agent→tools→END)+ `dispatcher.py`(subprocess/function/builtin 分发,stdout 最后一行 JSON 契约) | 通用循环 |
| L-2 | **`harness/skill_loader.py`**:按标准格式加载 skill——读 frontmatter(name/description)、body→system_prompt;扫描 `scripts/` 发现工具;聚合各脚本的 schema 声明 → 派生 `tool_schemas`(LLM 面)+ `tool_runtime`(loop 面) | 标准 skill 加载器 |
| L-3 | `harness/session.py`(run_session 入口)+ `conversation.py`(conversation.jsonl) | 会话入口 |
| L-4 | **最小 echo skill 冒烟(标准格式)**:`skills/echo/`(SKILL.md frontmatter + scripts/echo.py + `--dump-schema`)验证加载器、三型分发、通用性——loop 换 skill 不改代码 | 通用性验证 |
| L-5 | `harness/notebook.py`:write_note / retrieve_notes + notes.jsonl + 向量索引(可插拔)+ **BM25 兜底**;loop 注册内置工具 + base_prompt 拼接 + `RAG_NOTES_DIR` | 笔记本(含 N3 冒烟) |
| L-6 | 同步修订 loop_design.md(职责/架构图/State 补笔记本字段;skill 接口改述为"从标准包派生") | 文档一致 |

### 3.2 加载器派生规则(核心设计)

```
SKILL.md frontmatter → name / description(metadata)
SKILL.md body        → system_prompt(与 loop base_prompt 拼接)
scripts/*.py         → 每个脚本执行 `--dump-schema` 输出 {subcommand, args[{name,type,required,default,help}]}
                       → 加载器聚合为 tool_schemas(OpenAI function-calling 格式)
                       → 同时生成 tool_runtime:{name: {type:"subprocess", script, subcommand}}
```

- **单一事实源**:工具声明写在脚本 argparse 里,派生自动对齐,无手工 JSON 漂移(取代旧计划的 S-3/S-4 手写任务)
- 新增工具类型(function/builtin)只需加载器扩展,不动 skill 格式

### 3.3 验证标准

- [ ] echo skill:frontmatter 可读、body 进 system message、scripts 工具可调、tool_calls 循环正常 END
- [ ] 笔记本:write_note → 新 session → retrieve_notes 命中;无 embedding 时 BM25 兜底可用
- [ ] 加载器对"缺 frontmatter / 缺 scripts"的 skill 报清晰错误

---

## 4. P2 — Pipeline 实现(即 skill 的 `scripts/`,约 2~3 周)

> 依据 `tool_design.md` §4/§5/§8、`trajectory_design.md` §13。**硬约束:一个子命令 = 一次 h5ad 加载;完整 pipeline 4 次加载(1 raw + 3 proc);step7_diagnose 0 次加载。落地位置:`skills/cell-annotation/scripts/`。**

### 4.1 任务清单

| # | 任务 | 设计出处 | 产出 |
|---|---|---|---|
| A-0 | `common.py`:9 个通用函数(`describe_distribution`/`filter_funnel`/`effect_size`/`pairwise_overlap`/`batch_mixing`/`cluster_quality`/`variance_explained`/`resolution_stability`/`candidate_autocorr`)+ `append_log`/`next_run_id` | tool §6、trajectory §9 | 函数库 |
| A-1 | `step1_prepare.py`:`run`(16 op 单次加载 + 全部 Step1 扩展指标 + 每 op append_log)/`metrics`/`recluster`;**每脚本实现 `--dump-schema`** | tool §5.1 | processed.h5ad + **obs_snapshot.csv + var_snapshot.csv** + qc_metrics.json |
| B-1 | `step2_markers.py`:de_rank(BH-FDR/AUC/inflation λ)+ pct1_pct2 + filter_markers(漏斗)+ pseudobulk_de + write_markers | tool §5.2 | markers.csv/json(enriched) |
| B-2 | `step6_validate.py`:marker_expression(Cohen's d/AUC/fold_change)+ violin + global_summary + report + final;**backed 模式按列读**(try/except 回退全量) | tool §5.6 | final_annotations.json + report.md |
| C-1 | `step3_kg.py`:connect / query_genes(`--gene-key` 用 name_map)/ query_hierarchy(ancestors 入 kg_hits)/ aggregate_candidates / write_hits + `test-connection` | tool §5.3 | kg_hits.json(enriched) |
| C-2 | `step4_judge.py`:rank_candidates(count_ratio/count_diff/confidence_diff/ancestor_overlap)+ write_annotations(纯 JSON) | tool §5.4 | annotations.json |
| C-3 | `step5_refine.py`:candidate_autocorr → subcluster → subcluster_de → subcluster_kg(缓存复用)→ marker_overlap(Jaccard)→ type_membership → unknown_overlap → write_refined | tool §5.5 | refined_annotations.json |
| D-1 | `step7_diagnose.py`:hit_rate/candidate_count/first_second/batch_entropy(**读 obs_snapshot,0 次加载**)/metadata_check/cross_cluster | tool §5.7 | step7_diagnose.json + report.md |
| E-1 | 更新 `knowledge/metrics_interpretation.md` 补新指标解读(最终成为 skill 的 references 素材) | tool §8 Phase E | 知识文件完备 |

### 4.2 关键实现要点(设计冻结)

- **过滤漏斗**:分步过滤 + 记录每步 n_lost;silhouette >10K 采样 10K + 固定 seed
- **植物特化**:QC 用 `pct_counts_chloroplast`(ATCG)与 mt 并列;`MT-` 匹配不到植物基因
- **sidecar 一致性**:obs_snapshot/var_snapshot 与 processed.h5ad 同步写出
- **环境变量**:NEO4J_* 优先级 CLI > env > 默认,密码不硬编码

### 4.3 验证标准

- [ ] 4 次 h5ad 加载跑通 47 op;run_log.jsonl 有全部 `stepN.op#attempt` exec 记录
- [ ] step7_diagnose 0 次 h5ad 加载;断点续跑只追加不覆盖;130 扩展指标 0 额外加载
- [ ] 每个脚本 `--dump-schema` 输出合法;加载器聚合结果覆盖全部子命令

---

## 5. P3 — SKILL.md + references + description(约 1 周,项目重点)

> 依据 skill-creator 写作规范(progressive disclosure、imperative、讲 why 不讲 MUST)、`knowledge/` 4 文件、`trajectory_design.md` §10、`experiment_design.md` §4.1(③ 的 SKILL 不含决策阈值)。

### 5.1 任务清单

| # | 任务 | 素材 | 产出 |
|---|---|---|---|
| S-1 | 建标准包骨架:`skills/cell-annotation/`(SKILL.md + scripts/ + references/ + assets/ + evals/),scripts/ 直接入住 P2 产物 | skill-creator anatomy | 合规目录 |
| S-2 | **SKILL.md body(<500 行,中文,imperative)**:角色与任务理解 → 13 决策点流程(SOP-1~6 映射)→ 6 大陷阱警告 → 判断必附 reasoning 的依据 → **"何时读哪个 references 文件"指引** → 日志指导(session_start/judgment/session_end 模板) | knowledge/cell-annotation-sop.md、metrics_interpretation.md、trajectory §10 | SKILL.md |
| S-3 | **references/ 分块**(按决策点组织,配合 SKILL.md 的"看什么"清单):`sop.md`(全文)、`metrics.md`(247 指标解读全文)、`traps.md`(陷阱清单 + 反例)、`kg-schema.md` | knowledge/ 4 文件 | references/ |
| S-4 | **frontmatter description**(触发机制):面向"注释单细胞数据/找 marker/细胞类型判断"等真实用户说法,写触发词;参照 skill-creator 的"pushy"原则 | — | frontmatter |
| S-5 | 红线自检:**SKILL.md 不含 if-then 决策阈值**(只含解读参考值 + SOP 合格标准);动作空间 ⊆ trajectory §3.2 枚举 | experiment §4.1 | 合规检查 |
| S-6 | assets/:报告模板、tools 清单(加载器派生的工具总览供人工核对) | — | assets/ |

### 5.2 渐进披露设计要点(与 B3 的关系)

- SKILL.md 里每个决策点给**指标路径清单**("看什么"),具体数值解读在 references/metrics.md——**LLM 判断时按需 read references**,而不是全量注入
- 这是 B3(指标最小充分集)的实验对象与落地机制:测试循环里统计 LLM 实际读哪些 reference、引用哪些指标路径,反哺 SKILL.md 常驻内容与 references 划分
- 系统消息 = loop base_prompt + SKILL.md body(**不含 references 全文**),与 rag_design §5 一致

### 5.3 验证标准

- [ ] SKILL.md < 500 行;references 有目录(TOC)且被 SKILL.md 明确引用
- [ ] loop 加载器从该包成功派生 system_prompt + tool_schemas + tool_runtime
- [ ] frontmatter description 覆盖 5+ 种真实触发说法(见 evals)

---

## 6. P4 — 实验数据底座(约 1~2 天,与 P1/P2 并行)

> 依据 `experiment_implementation.md` §1。D-1/D-2 是 B1/A1/N1/C2/C4 的评估前置,与 pipeline 无耦合。

| # | 任务 | 产出 | 验证 |
|---|---|---|---|
| D-1 | `dataset/index/SRP171040.h5ad.csv` → `experiments/gt_cells.csv`(cell_barcode, true_type) | gt_cells.csv | 条码数 == 33,956 且与 h5ad obs_names 100% 对齐 |
| D-2 | `scripts/build_label_map.py`:预测术语 × 12 真值标签的 exact/synonym/subtype/supertype/unrelated,自动查 KG ancestors 生成初稿 → **人工定案锁定**(_meta.verified) | experiments/label_map.json(核心资产) | 12 类型全覆盖 |
| D-3 | `scripts/build_marker_dict.py`:`{gene:[cell_type]}` 静态字典(C2/A3 无 KG 臂用) | experiments/marker_dict.json | 可用性检查 |
| D-5 | (S1 前置,可延后)`build_scenarios.py`:校验纯簇(占比 ≥90%)+ leiden_override.csv | S1/scenarios.json | 8 用例构造 |

---

## 7. P5 — skill 测试循环(skill-creator:草稿 → 测试 → 评估 → 迭代)

> **这是本版计划新增的闸门**。B1 之前,先用 skill-creator 的方式把 skill 本身测到合格,避免方法学实验被"skill 不好用"污染。

### 7.1 测试用例(evals/evals.json)

起草 3~5 个真实测试 prompt + expected_output(覆盖 skill 的能力面):

| 用例 | prompt 形态 | expected_output(断言方向) |
|---|---|---|
| E-1 端到端 | "注释这个数据集:output/ 里的 step1 已完成…" | 完整走完 SOP;final 有标签 + 置信度 + marker 证据 |
| E-2 模糊簇决策 | "簇 3 first/second 并列 8 vs 7,怎么办?" | 判断逻辑正确(查 ancestor_overlap / 生物学知识),decision 枚举合法 |
| E-3 unknown 簇 | "有 3 个簇无 KG 命中" | 不硬贴标签,按 SOP-5B 处理,写 judgment |
| E-4 QC 陷阱 | 给植物数据 mt/cp 分布 | 不套动物阈值(陷阱 1),decision 枚举合法 |
| E-5 日志合规 | 任意用例 | run_log 每判断点有 judgment 记录、字段齐全 |

断言写成可客观验证项(枚举合法、run_log 字段完整、final_annotations 结构、SKILL 未越界动作等);定性部分人工看。

### 7.2 跑法与迭代循环

```
1. with-skill 跑法:loop + skill + 测试 prompt(每用例 1 次会话;可用 mini-session/单决策点用例控成本)
2. baseline:同一 loop + 同一 prompt,不加载 skill(或旧版 skill)——对比"skill 是否带来增量"
3. 评估:断言自动核对 + 人工看输出(conversation/report);参照 skill-creator 的 viewer/反馈流程(本环境无浏览器时在对话中呈现)
4. 迭代:按反馈改 SKILL.md / references → 重跑 → 直到达标(参照 skill-creator:改"讲 why"、去不干活的指令、重复工具调用→收进 scripts)
```

**成本控制**:E-2~E-4 用单决策点 mini-session(只喂该决策点指标 + skill 决策指导,不跑全 loop),API 成本 ≈ S1 的 mini-session 量级。

### 7.3 达标线(进入 B1 的前置条件)

- [ ] E-1 端到端跑通,run_log 完整,session_end 有 final_summary
- [ ] E-2~E-4 的 decision 全部落在 trajectory §3.2 枚举内,reasoning 附指标依据
- [ ] E-5 日志合规(validate_log.py 通过)

---

## 8. P6 — 方法学实验(约 1~2 周,API 预算 ≈ 4 全 session + 若干 mini-session)

> 依据 `experiment_implementation.md` §3、§6。skill 已过 P5 闸门;B1③ 用的就是这份 skill(禁用笔记本,兼作 N1⑥)。

| 序 | 实验 | 依赖 | 关键动作 |
|---|---|---|---|
| 1 | **B1 三臂** | P5 达标 + P4 | `scripted_driver.py` + `dag.py` + `judges/default_judge.py`、`rule_judge.py`(用知识文件最佳参考值转 if-then,非稻草人)→ 跑 ①② → `evaluate_cell_level.py` + `bootstrap_test.py`(cluster-aware,1000 次)+ `analyze_traps.py` |
| 2 | **S1 合成场景** | P2 确定性 op + D-5 | `run_mini_session.py`(③ 单轮判断,temperature=0);8 用例 × ②③ vs 确定性真值 → battery_report;可与 B1 并行 |
| 3 | **A1 + E1** | B1③ | A1 报告(复用 B1 评估)+ `count_tokens.py`(conversation 全量 token) |
| 4 | **B3 + B4** | B1③ 轨迹 | `analyze_b3.py`(inputs[].path 频次 → 最小充分集 ≤60)→ **反哺 SKILL.md/references 划分**;`analyze_b4.py`(纠正对改善率 ≥50%) |
| 5 | **N 组** | P1 笔记本 + P5 | ⑥ 复用 B1③ + 补 1;⑦ notebook-on ×2;N2 使用率 ≥3/会话前置;N3 冒烟已在 P1 完成 |
| 6 | **C2 + A3** | P4 D-3 | `run_baseline_marker_match.py`(无 KG 臂,与 A3 共享)→ fullKG − 无KG ≥ 0.03 判定 |
| 7 | **C4(可选)** | loop 后端可插拔 | 换 2~3 个模型各 1 session,决策一致率 + macro-F1 差 |

**判定规则速查**(已预注册,experiment_implementation §4):B1 R1 `③−② ≥ 0.03 且 CI 不含 0`;B1 陷阱 `③ 在 ≥4/6 出现陷阱上优于 ②`;S1 `≥6/8 一致 ∧ 反例全不误细分`;B3 `核心子集 ≤60`;B4 `改善率 ≥50%`;A3/C2 `差 ≥0.03`;E1 `≤40 万 token`;N1 `⑦ ≥ ⑥−0.02 且使用率达标`。

---

## 9. P7 — skill 持续迭代与交付(对应 skill-creator 后半)

| # | 任务 | 输入 → 输出 |
|---|---|---|
| I-1 | **扩大测试集**:在 evals 中追加更多真实场景 prompt(跨决策点组合、边界案例),重跑迭代 | evals v2 |
| I-2 | **B3 反哺**:核心子集统计 → SKILL.md 常驻指标精简、references 按需划分再平衡 | SKILL.md v2 + references v2 |
| I-3 | **B4 反哺**:纠正对中"改错"的决策点 → 强化 SKILL.md 决策指导(讲 why) | SKILL.md v2 |
| I-4 | **description 优化**:构造 should/should-not 触发查询集 → 用 loop 后端(替代 skill-creator 的 claude CLI)测触发率 → 迭代 description | frontmatter v2 |
| I-5 | **打包交付**:`package_skill` 产出 `.skill` 文件;文档化安装与使用(本仓库 .gitignore 忽略 *.json/csv,产物以文档+打包件交付) | 可交付 skill |
| I-6 | (可选)跨数据集复现(X-8) = skill 泛化测试 | 外部效度 |

---

## 10. 关键路径、工作量与并行

### 10.1 关键路径

```
P1 Loop+加载器(4~6d) → P2 scripts(10~15d) → P3 SKILL.md+references(3~5d)
   → P5 skill 测试循环(3~5d,含 1~2 轮迭代) → P6 B1(3~5d) → 结论
```

### 10.2 并行窗口

| 并行组 | 说明 |
|---|---|
| P4(D-1/D-2) ‖ P1 | 只依赖 KG + CSV,无耦合 |
| P3 ‖ P2 后段 | SKILL.md/references 文本编写不依赖 pipeline 代码;schemas 派生验证需 P2 完成 |
| S1 ‖ B1 | S1 只依赖确定性 op,不与 ③ 耦合 |
| P5 的 E-2~E-4 mini-session ‖ P6 准备 | 单决策点用例成本极低,可先行 |

### 10.3 总工作量(单人,不含等待)

| 阶段 | 估算 |
|---|---|
| P1 | 4~6 天 |
| P2 | 10~15 天(A 段最重 ~5 天) |
| P3 | 3~5 天 |
| P4 | 1~2 天(不含 label_map 人工核对等待) |
| P5 | 3~5 天(含迭代) |
| P6 | 5~10 天(B1 为主;S1/B3/B4 并行压缩) |
| P7 | 持续,首轮 2~3 天 |
| **合计** | **约 5~7 周(关键路径),并行压缩后 ~4 周** |

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **scrublet 未安装** | detect_doublets 无法跑 | `pip install scrublet`;装不上则降级 doubletdetection/简单 score,run_log 标注 de_method |
| skill 测试成本 | P5 每用例 1 会话 API | E-2~E-4 用单决策点 mini-session;E-1 端到端保留 1 次 |
| LLM 不按 SKILL 走 / 动作越界 | B1 可比性失效 | P3 红线(动作空间 ⊆ ② 枚举)+ P5 断言(E-2~E-4 枚举合法)+ 人工抽查 |
| rule_judge 设成稻草人 | ③ 胜 ② 无意义 | ② 必须用知识文件最佳参考值转 if-then(experiment §4.1) |
| SKILL.md 膨胀 >500 行 | 渐进披露失效 | 内容外移 references;P5 迭代时检查 |
| 加载器派生与 argparse 漂移 | schema 过期 | `--dump-schema` 单一事实源;P5 断言覆盖工具可用性 |
| backed 模式兼容性 | step6 内存爆 | try/except 回退全量加载 |
| `.gitignore` 忽略 *.json/csv/h5ad | 产物无法入库 | 汇总报告/图表入库;原始轨迹留本地;skill 打包件(.skill)可入库 |
| 单数据集局限 | 结论外部效度不足 | 细胞级评估 + cluster-aware bootstrap 已独立成立;X-8 跨数据集为 skill 泛化测试 |
| LLM 不用笔记本 | N1 无结论 | N2 前置使用率 ≥3/会话;不足加强 base_prompt 提示强度 |
| description 优化依赖 claude CLI | skill-creator 工具链不可用 | 用 loop 后端适配触发率测试,或跳过(I-4 标注为可选) |

---

## 12. 里程碑验收清单

| 里程碑 | 验收标准 |
|---|---|
| M1 Loop | echo skill(标准格式)跑通;加载器从标准包派生三接口;subprocess/function/builtin 分发正确;笔记本读写 + BM25 兜底可用 |
| M2 scripts | 47 op 全跑通;4 次 h5ad 加载;`--dump-schema` 全脚本可用;run_log 全 exec 记录;step7 0 加载;sidecars 写出 |
| M3 Skill 包 | **标准 anatomy 合规**(SKILL.md frontmatter + scripts/references/assets/evals);SKILL.md <500 行;references 有 TOC 且被引用;三接口派生成功 |
| M4 数据底座 | gt_cells 100% 对齐;label_map 12 类型全映射且人工定案 |
| M5 Skill 测试 | E-1~E-5 全部断言通过;decision 枚举合法;validate_log 通过 |
| M6 实验 | B1 三臂 + bootstrap CI + 陷阱分析;S1 battery;A1/E1/B3/B4/C2/N1/N2 产物齐全,逐条对照判定表 |
| M7 交付 | SKILL v2 + references v2;description 优化完成;`.skill` 打包件;文档与代码一致 |

---

## 13. 与 skill-creator 流程的对应关系(自检)

| skill-creator 环节 | 本项目落点 |
|---|---|
| 捕获意图 / 访谈 | `readme.md` + 设计文档(已完成) |
| 写 SKILL.md 草稿 | P3(S-2,含 frontmatter description) |
| 打包资源 scripts/references/assets | P2(scripts)+ P3(S-3/S-6) |
| 测试用例 evals/evals.json | P5(§7.1) |
| with-skill vs baseline 跑测 | P5(§7.2:loop+skill vs 裸 loop/旧版) |
| 评估(viewer/反馈 + 断言) | P5(§7.2:断言核对 + 人工看;本环境在对话中呈现) |
| 迭代改进 | P5 §7.2 + P7(I-1~I-3) |
| description 优化 | P7(I-4,适配 loop 后端) |
| 打包交付 | P7(I-5,`.skill`) |

> 注:本项目 skill 在 loop 内是"显式加载"而非"触发竞争",故 description 的触发价值主要体现在**可移植性**(其他 agent 也能用);P5 的断言(而非触发率)是 skill 质量的主判据。
