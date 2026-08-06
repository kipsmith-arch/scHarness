# 实验实施方案(implementation)

> 本文档把 `experiment_design.md` 的全部 12 个实验落成可直接执行的方案。每个实验按统一模板展开:**数据准备**、**实验流程**、**结果解读**、**预期结论与预先注册的判定规则**。所有判定规则在分析前定案,报告时逐条对照。

## 0. 已敲定的决策(2026-08 评审)

| 决策 | 选择 | 理由 |
|---|---|---|
| B1 评估口径 | **细胞级评估**(三臂完全独立) | 三臂的 qc/resolution/clustering 决策点必然产生不同聚类,簇 ID 配对在逻辑上不成立;细胞级评估避开该矛盾且更贴近端到端质量 |
| 数据范围 | **单数据集优先**(SRP171040) | 先把方法学实验跑通(细胞级 N=33,956,12 类型);跨数据集复现留作可选阶段 X-8 |
| 运行预算 | **精简起步** | B1 三臂各 1 session(①②确定性不耗 API)+ N 组 2+2;固定单一主力模型 |
| 结论判定 | **预先注册判定规则** | §4 的规则在跑实验前定案,防事后解释/p-hacking |

---

## 1. 共享数据底座(所有实验共用)

### 1.1 输入资产

| 资产 | 路径 | 说明 |
|---|---|---|
| raw h5ad | `dataset/h5ad/SRP171040.h5ad` | 已按 `dataset/init.py` 约定处理:X sparse、raw 重建、var_names 为基因符号 |
| ground truth | `dataset/index/SRP171040.h5ad.csv` | `Seurat_clusters` + `Celltype`,33,956 细胞,12 类型 |
| KG 环境 | `.env` 的 `NEO4J_*` | 已配置;用于 step3_kg 与标签映射表(§1.2 D-2) |

**12 个真值类型及分布**:Columella root cap 5,640 / Root cortex 4,747 / Root hair 4,743 / Non-hair 4,242 / Root endodermis 3,668 / Pericycle 3,232 / Lateral root cap 2,537 / Phloem 1,753 / Root stele 1,367 / Xylem 1,030 / Meristematic cell 570 / Stem cell niche 427。

### 1.2 数据构造步骤

**D-1 真值规范化** → 生成 `experiments/gt_cells.csv`(列:`cell_barcode`, `true_type`)
- 从 `dataset/index/SRP171040.h5ad.csv` 读入,index 即细胞条码(格式 `SRX5074330@@_AAACCTGAGACAGACC-1`)。
- 校验:条码数 == 33,956;h5ad `obs_names` 与 CSV index 完全一致(init.py 保证)。

**D-2 标签映射表** → 生成 `experiments/label_map.json`(新构造,核心资产)
- 问题:harness 输出 KG 本体术语(如 "root cap")、真值是人类命名("Columella root cap"),两者粒度/措辞不同,不定义映射就无法算准确率。
- 机制:对每个预测术语与 12 个真值标签,用 KG 本体(`step3_kg query_hierarchy` 的 ancestors)+ Plant Ontology 词汇手工核对,判定关系:
  - `exact` / `synonym` — 同义(大小写/单复数/换词,如 "columella root cap" ~ "Columella root cap")
  - `subtype` — 预测比真值更具体(预测 "columella root cap"、真值 "root cap")
  - `supertype` — 预测比真值更宽(预测 "root cap"、真值 "columella root cap")
  - `unrelated`
- 预填实例(供 D-2 起稿,须经 KG 核对后定案):
  - columella root cap ↔ Columella root cap = synonym
  - lateral root cap ↔ Lateral root cap = synonym
  - root cap → Columella/Lateral root cap = supertype
  - cortex / root cortex ↔ Root cortex = synonym
  - root hair (cell) ↔ Root hair = synonym
  - non-hair root epidermal cell ↔ Non-hair = synonym
  - endodermis ↔ Root endodermis = synonym
  - pericycle ↔ Pericycle = synonym
  - phloem ↔ Phloem = synonym
  - xylem ↔ Xylem = synonym
  - stele → Root stele = supertype
  - meristematic cell ↔ Meristematic cell = synonym
  - stem cell niche ↔ Stem cell niche = synonym
- `build_label_map.py` 生成初稿(自动查询 KG ancestors),人工定案后锁定版本(写 `_meta.built_from` / `_meta.verified`)。

**D-3 静态 marker 字典(仅 C2/A3 用)** → 生成 `experiments/marker_dict.json`
- 若 C2/A3 需要"无 KG"基线(见 §3.7):从 `knowledge/*.md` 与 KG 的 marker_resource 抽样,整理一张静态 `{gene: [cell_type,...]}` 字典作为 KG 查询的替代。
- 若素材不足,退化为"LLM 仅凭自身生物学知识 + markers 标注"(无参考字典)。

**D-4 细胞身份对齐**
- 评估时以 `cell_barcode` 为 key 连接:`h5ad obs → obs_snapshot.csv(细胞→leiden)`、`final_annotations.json(leiden→标签)`、`gt_cells.csv(条码→真值)`,展开为逐细胞预测标签。

**D-5 合成用例构造(仅 S1 用)** → 生成 `experiments/S1/scenarios.json` + `leiden_override.csv`
- 目标:把 6 个陷阱(`metrics_interpretation.md` §附)操作化为"已知真值"的合并用例,给真实数据里出现次数很少的 refine 决策点补统计力。
- 机制:取 `processed.h5ad`,选两个(或三个)**纯簇**(用 gt_cells 校验,pipeline 簇内真值类型占比 ≥90%),把其细胞 relabel 成同一簇 ID 写入 `leiden_override.csv`,跳过 step1 重聚类,step2→step7 照常读该覆盖标签。已知真值 = 合并前的两个原始类型。
- 用例清单与预期见 §3.2;`build_scenarios.py` 自动校验纯簇选取、生成覆盖文件。

### 1.3 粒度与纯度诊断(解读前置)

- pipeline 的 leiden 聚类 ≠ 原论文的 Seurat 聚类 → 一个 leiden 簇可能混入多个真值类型,细胞级准确率天然受**聚类纯度**限制。
- 必报诊断:**聚类纯度** = 每个 leiden 簇内最大真值类型占比的均值(每臂各算一次)。纯度越高说明聚类质量越好;三臂纯度差异也是 B1 结论的一部分(若 LLM 臂纯度更高,部分是"聚类更好"带来的增益,仍算系统增益,需在报告里明示)。

### 1.4 评估指标体系(细胞级,被 B1/A1/N1/C2/C4 共用)

| 层级 | 指标 | 定义 | 用途 |
|---|---|---|---|
| **主** | per-cell macro-F1 | 12 类 F1 的算术平均(对稀有类型公平) | 三臂/多条件比较的主判据 |
| **主** | per-cell accuracy | 正确细胞 / 总细胞 | 端到端直观指标 |
| 辅 | weighted-F1 | 按细胞数加权 | 反映真实分布下的端到端质量 |
| 辅 | 12×12 confusion matrix | 真值×预测 | 失败模式分析 |
| 辅 | 细分/粗命中率 | 预测落在 label_map 的 subtype/supertype 的比例 | 粒度诊断 |
| 辅 | unknown 率 | `session_end.final_summary.unknown_rate` | KG 覆盖率 |
| 统计 | cluster-aware bootstrap 95% CI | 按簇整组重抽样(1000 次),对两条件 macro-F1 差 | 显著性 |
| 统计 | per-type Wilcoxon | 12 类型配对符号秩检验 | 稳健性参考(不独立下结论) |

**预先注册的计分规则**:
1. **正确** = 预测标签与真值标签在 label_map 中为 `exact` 或 `synonym`。
2. **细分命中**(部分正确,单列) = 预测为真值的 `subtype`(比真值更细)。不算入主 macro-F1。
3. **粗命中**(部分正确,单列) = 预测为真值的 `supertype`(比真值更宽)。不算入主 macro-F1。
4. **错误** = `unrelated` 或预测为 `unknown`。unknown 按错误计入,并同时单列 unknown 率。
5. 主指标用**严格口径**(只算 1),辅助报告宽松口径(1+2+3)。

**预先注册的统计口径**:
- **不用 McNemar 于细胞级**:33,956 细胞非独立(同一簇共享标签),会伪造显著。显著性一律用 **cluster-aware bootstrap**(以簇为抽样单元)。
- 效应量:macro-F1 差。判定线 **≥ 0.03** 视为有意义(避免巨样本下统计显著但效应可忽略)。
- 报告:点估计、bootstrap 95% CI、per-type Wilcoxon p(标注仅参考)。

---

## 2. 共享实验基础设施

### 2.1 一次 session 的定义

完整 7 步 pipeline 一次跑通 + `session_start`/`session_end` 记录;约 47 exec + ~67 judgment 记录(29 簇)。

### 2.2 随机性控制(可复现)

| 源 | 控制 |
|---|---|
| Leiden 聚类 | 固定 `random_state` seed,写入 exec 参数 |
| silhouette 采样(10K) | 固定 seed(记为 `silhouette_sampled: true` + seed) |
| scrublet | 固定 seed |
| LLM 采样 | `temperature=0`(判断型任务);model/temperature 记入 `session_start` |
| 复现 | 每个 arm 的 seed 写进 `run_log` 的 `session_start`,配套 rerun 脚本 |

### 2.3 LLM 后端可插拔(loop 层,C4 依赖)

loop 的 LLM 后端通过配置切换(GPT-4o / Claude / Gemini),同一 skill/pipeline 不动(见 `loop_design.md` §9 C4)。B1/N 组默认固定一个主力模型。

---

## 3. 各实验实施方案

### 3.1 B1 — 三臂决策对比(killer experiment)

**回答**:LLM 在决策点上是否优于固定默认与阈值启发式?这是整个架构的存在理由。

#### 数据准备
- 输入:共享底座(gt_cells.csv + label_map.json)+ 三臂各自 `final_annotations.json` / `run_log.jsonl`。
- 无额外数据构造;三个 arm 跑同一 raw h5ad。

#### 实验流程
三臂执行矩阵:

| 臂 | 驱动器 | 判断层 | 是否耗 API | 产物 |
|---|---|---|---|---|
| ① Fixed-default | `scripted_driver.py` + `default_judge.py` | 无(全 accept) | 否 | `experiments/B1/arm1_default/` |
| ② Rule-based | `scripted_driver.py` + `rule_judge.py` | 规则表(oracle 表转 if-then) | 否 | `experiments/B1/arm2_rule/` |
| ③ LLM-judge | `loop.py` | LLM + skill | 是 | `experiments/B1/arm3_llm/` |

- **共享基底(三臂必须一致)**:同一 raw h5ad、同一 DAG(47 op)、同一 `dispatcher.dispatch`、同一 `run_log.jsonl` 格式。唯一差异 = 决策层。
- **预注册约束**:
  1. ③ 的动作空间必须 ⊆ ② 规则表覆盖的 decision/action 枚举(`trajectory_design.md` §3.2),否则 ③ 领先可能是"动作空间更大"而非"判断更聪明"。
   2. **③ 的 B1 主跑禁用笔记本**(notebook 是 N 组被测对象,不能混淆变量)。该 session 同时复用为 N1 的 ⑥ no-notebook 组一份(§3.10)。
- 步骤:跑 ① → ② → ③ → `evaluate_cell_level.py`(三臂逐细胞标签)→ `bootstrap_test.py`(③ vs ②、② vs ①、③ vs ①)→ `analyze_traps.py`。

#### 结果解读
- 主判据:③ vs ② 的 macro-F1 差 + cluster-aware bootstrap 95% CI。
- 支撑:② vs ①(判断层有无价值)、③ vs ①(下界)。
- 附报:12×12 confusion、每臂聚类纯度、unknown_rate、逐类型 recall(看哪些类型受益)。
- 陷阱点:6 个陷阱(`metrics_interpretation.md` §附)逐点对比 ③/② 的对错(oracle 见下表)。

#### oracle 表(② 规则表的来源,同时供 B4 判据)

> ② 的 `rule_judge.py` 把下表"正确决策"转成 if-then;③ 从 reasoning 判定其答卷。真实数据中陷阱可能不出现,未出现记 N/A;S1(§3.2)把下表操作化为合成用例以保证每个陷阱可判定。

| # | 陷阱 | 场景信号 | oracle 正确决策 |
|---|---|---|---|
| 1 | 植物 mt/cp 阈值 | mt 低或长尾、cp 主导 | 按植物组织设阈值(看 cp),不套动物规则 |
| 2 | 层级本体并列 | first≈second 且 `ancestor_overlap` | `ambiguous_parent_child`,选更具体,不进 step5 |
| 3 | 小样本 ratio | `count_ratio≥2` 但 `count_diff` 很小 | 不可判 decisive,必须看 count_diff |
| 4 | 管家基因 pct1 高 | 单看 pct1 高 | 必须看 pct2,不直接 `label_confirmed` |
| 5 | 连续谱 silhouette 低 | sil 低但数据是发育梯度 | 不盲目 recluster(可 accept) |
| 6 | 单批次真实群体 | batch_entropy 低 | 查条件注释,不判 batch_effect |

#### 预期结论与判定规则(预先注册)

| 规则 | 判定 | 结论 |
|---|---|---|
| R1 | ③ 的 macro-F1 − ② ≥ 0.03 **且** bootstrap CI 不含 0 | **③ 胜 ②** → 支持核心假设(LLM-as-judge 优于硬阈值) |
| R2 | ② − ① ≥ 0.03 且 CI 不含 0 | "读 metrics + 规则"有价值(判断层存在有意义) |
| R3 | ③ − ② ∈ [−0.01, +0.01] | ③ ≈ ② → LLM 判断未超越阈值,转向陷阱点分析判断是否部分成立 |
| R4 | ③ − ② ≤ −0.03 | **结论反转**:必须分析原因(skill 缺陷 / LLM 噪声 / 规则表太强),不能跳过 |
| 陷阱 | ③ 在 **≥4/6 出现的陷阱点**上优于 ② | 核心假设成立(LLM 在陷阱点上胜过硬编码阈值);只在实际出现的陷阱上计数,未出现记 N/A |

**报告模板**:若 R1 通过 → "LLM 判断引擎在细胞注释决策点上优于固定默认与阈值启发式,增益可归因于陷阱点的上下文弹性,而非更大的动作空间"。若 R3 → "LLM 未显著超越精心构建的阈值规则;价值集中在陷阱点;需改进 skill"。

---

### 3.2 S1 — 合成场景注入(受控能力探针)

**回答**:在**已知真值**的情形下,LLM 能否从指标读出"该簇是两群合并"并正确决定细分/不细分?这是 refine 决策点(`candidate_gap`/`refine_effect`)在真实数据里出现次数太少时的补充统计力来源。

> 定位:受控能力探针,**补充而非替代** B1。测的是"具备该能力",不代表真实 ambiguous 簇(信号往往更弱)的检出率——报告里必须明示这一边界。

#### 数据准备
- 输入:共享底座 + D-5 构造的 `scenarios.json` / `leiden_override.csv`。
- 用例矩阵(预先注册 8 个,覆盖 6 陷阱 + 正反例 + 难度梯度):

| 用例 | 构造(合并) | 对应陷阱 | oracle 决策 |
|---|---|---|---|
| S-P1 | Xylem(簇16)+ Root hair(簇1) | 无(无关类型) | `ambiguous_true` → 路由 step5 → `refine_effective` |
| S-P2 | Phloem(簇19)+ Root endodermis(簇8) | 无(无关类型) | 同上 |
| S-P3 | Meristematic cell(簇23)+ Stem cell niche(簇26)(稀有) | 陷阱3 小样本 | `ambiguous_true`,但须看 count_diff(不因 ratio 就 decisive) |
| S-N1 | Pericycle(簇4)+ Pericycle(簇6)(同类型) | 反例 | `first_decisive`,不进 step5 |
| S-N2 | Columella root cap(簇0)+ Lateral root cap(簇10) | 陷阱2 层级本体 | `ambiguous_parent_child`,选更具体,**不进** step5 |
| S-N3 | Root cortex(簇3)+ Root cortex(簇12)(同类型、不同样本) | 陷阱6 单批次 | 不判 batch_effect、不细分(`first_decisive`) |
| S-H1 | Root cortex(簇3)+ Root hair(簇1)(中间难度) | 无 | `ambiguous_true` → step5;refine 结果以确定性真值为准 |
| S-H2 | Xylem+Phloem+Root endodermis 三簇合并 | 无(超融合) | `ambiguous_true` → step5,子簇数 ≥2 应被识别 |

> 簇号是 SRP171040 的 Seurat 簇参考;实际以 `build_scenarios.py` 用 gt_cells 校验后的纯 pipeline 簇为准(占比 ≥90%)。

#### 实验流程
1. `build_scenarios.py`:校验纯簇 → 生成 `leiden_override.csv`。
2. 对每用例跑**确定性 op**(step2 DE → step3 KG → step4 rank → step5 candidate_autocorr),产出该决策点的指标快照。
3. 判 ②:把同一指标喂 `rule_judge.py`(同一张 oracle 表)。
4. 判 ③:`run_mini_session.py`——把指标 + 决策点 + skill 决策指导喂 LLM,单轮判断(`temperature=0`),不跑整条 loop(隔离决策质量、省 API)。
5. 算**确定性真值**:强制对合并簇跑 step5 subcluster,看子簇能否分离出两个原始真值类型(purity 判据)→ 这是"refine 是否有效"的客观答案。
6. 汇总 `battery_report.json`。

#### 结果解读
- 逐用例判定表:构造 | oracle | 确定性真值 | ②判定 | ③判定 | 谁对。
- 命中率:③ / ② 各自与确定性真值一致的用例数。
- **分层报告**(关键):正例命中率(能否检出杂质)与**反例命中率**(是否过度细分)——反例命中最能防"逢 merge 就 refine"的质疑。
- 陷阱专项:S-P3/S-N2/S-N3 对应陷阱 3/2/6,分别看 ③/② 是否踩 oracle。

#### 预期结论与判定规则(预先注册)

| 规则 | 判定 | 结论 |
|---|---|---|
| S1-1 | ③ 在 ≥6/8 用例上与确定性真值一致 | "LLM 具备从指标检出聚类杂质并正确决定细分"能力成立 |
| S1-2 | ③ 在**全部反例**(S-N1~N3)不误触发细分 | "不过度细分"成立 |
| S1-3 | ③ 正确数 − ② 正确数 ≥ 2 | 合成场景下 LLM 优于规则(补 B1 统计力) |
| S1-4 | ③ 正例失败率 >50% | 指标不足以支撑细分判断或 skill 决策指导缺失 → 先修 skill |

**统计注记**:8 用例共享同一数据集/KG,非独立样本,只做二项检验 + 逐用例报告,不做推断统计。**构造伪影**:合并产生人为干净的双 lobe,morans_i 天然偏高——S1 测的是能力,不是真实弱信号的检出率,报告须注明。

#### 输出
- `experiments/S1/scenarios.json`、`leiden_override.csv`
- `experiments/S1/case_{P1..H2}/`(每用例指标快照 + ②③ 判定)
- `experiments/S1/ground_truth.json`(确定性子簇真值)
- `experiments/S1/battery_report.json`(逐用例判定表 + 命中率)

---

### 3.3 B3 — 指标最小充分集

**回答**:247 个指标是否冗余?LLM 实际引用哪些?(反哺 SKILL.md 精简)

#### 数据准备
- 输入:所有 LLM session 的 `run_log.jsonl`(B1③、N1 ⑦/⑥、若做 C4 则含多模型)的 `judgment` 记录。仅需轨迹,无数据构造。

#### 实验流程
1. 聚合:`judgment.records[].inputs[].path` → 按 `{step}.{op}.{metric_path}` 计数。
2. 按 `decision_point` 分组,列出每个决策点引用的指标集合。
3. 定义**核心子集** = 被 ≥2 个决策点引用 **或** 单点引用频次 top-20 的指标。
4. 计算 **prompt 精简比** = 全量指标文本字节 / 核心子集文本字节(或近似 token)。

#### 结果解读
- 逐决策点的引用指标集合(`metric_usage_by_decision.json`)。
- 跨决策点频次排序;核心子集清单(`minimal_sufficient_set.json`)。
- 精简比:衡量 SKILL.md 里哪些指标该常驻 prompt、哪些按需查询。

#### 预期结论与判定规则(预先注册)
- 判定线:**核心子集 ≤ 60 个指标** → 判定"247 指标冗余,skill 可精简"成立(预期 ~30)。
- 若引用高度分散(核心子集 >60)→ 指标组织/prompt 结构有问题,先修 skill 再谈精简。

---

### 3.4 B4 — 自我纠正有效性(兼 D2)

**回答**:LLM 改主意(adjust→重跑→accept)对应的重跑,是否真改善质量?

#### 数据准备
- 输入:`run_log.jsonl`。同一 `(decision_point, scope)` 有多条 judgment 记录 = 改过主意。仅需轨迹。

#### 实验流程
1. 按 `(decision_point, scope)` 分组 judgment,取首版与末版(seq 排序)。
2. 逐决策点定义"改善"判据(**在脚本中显式定义,不靠 LLM 自评**):

| 决策点 | 改善判据(末版 vs 首版) |
|---|---|
| clustering_quality | silhouette_overall.mean 上升 ∧ n_singleton 下降;或末版 accept 而首版 adjust |
| marker_quality | n_markers 进入 [10,50] 区间;或末版 accept 而首版 adjust |
| refine_effect | 子簇 silhouette 上升 或 n_subclusters_with_distinct_type 增多 |
| label_confirm | 末版 decision 相对首版更明确(如 unknown→confirmed 需额外标记) |
| 其余 | 末版 decision 与首版不同且符合 oracle(用 §3.1 陷阱 oracle 表) |

3. 计算:纠正对总数、改善比例、平均改善幅度,按决策点分列。

#### 结果解读
- `self_correction_pairs.json`(首/末版 decision + run_ref + intervening runs)、`improvement_rate.json`。
- 改善率 = 改善纠正对 / 纠正对总数。

#### 预期结论与判定规则(预先注册)
- 判定线:**改善率 ≥ 50%** → "自我纠正有效"成立。
- 若 <50% → LLM 判断不稳定,回看 SKILL.md 决策指导,结论为负。

---

### 3.5 A1 — 端到端准确率

**回答**:系统在真实数据上到底注释对多少?

#### 数据准备
- 输入:B1 ③ session 的 `final_annotations.json` + `gt_cells.csv` + `label_map.json`。复用 B1 的评估产物,不新跑。

#### 实验流程
- `evaluate_cell_level.py` → accuracy / macro-F1 / weighted-F1 + 12×12 confusion + 失败模式分析。

#### 结果解读
- 整体准确率与 F1;逐类型 recall/precision。
- 失败模式:混淆集中的类型对(如 "Root cortex"↔"Root endodermis")、易注释错的类型、unknown 集中区。

#### 预期结论与判定规则
- 预期:大类(Columella root cap 等)高 recall;稀有类型(Meristematic cell / Stem cell niche)易错;unknown 若高需查 KG 覆盖。
- 无硬判定线;作为 B1 的补充上下文,与纯度诊断一起解释"准确率受什么限制"。

---

### 3.6 A3 — 与 baseline 方法对比

**回答**:本 harness 在文献中的位置。

#### 数据准备
- **Marker 硬匹配**:用 `marker_dict.json`(D-3)直接 match 每簇 markers → 类型,按命中数取 top1;无 KG、无置信度、无本体。
- **CellTypist / SingleR**:需植物参考集;核实可用性后再纳入(否则只做 Marker 硬匹配)。

#### 实验流程
- 跑 Marker 硬匹配(确定性,耗时长于 JSON 处理)→ 细胞级评估 → 与 B1 ③ 对比。
- 若参考集可得:补跑 CellTypist/SingleR,同口径评估。

#### 结果解读
- 对比表:方法 × (accuracy / macro-F1 / weighted-F1 / unknown_rate)。
- 定位:harness 是否 ≥ Marker 硬匹配、接近/超过监督工具。

#### 预期结论与判定规则
- 预期:harness ≥ Marker 硬匹配(macro-F1 差 ≥ 0.03 有意义)。
- 若 harness < Marker 硬匹配 → 说明 KG + LLM 判断层反而有害,需归因。
- 注:A3 的 Marker 硬匹配基线 **与 C2 的"无 KG"臂是同一个产物**,共享实现与结果。

---

### 3.7 E1 — 成本与效率

**回答**:实用性问题:每个 session 花多少 token / 时间 / 轮次。

#### 数据准备
- 输入:`conversation.jsonl`(LLM session)+ `run_log.jsonl`(ts 首末)。

#### 实验流程
- token 计数:用模型 tokenizer(如 tiktoken 对应模型)对 conversation 全量 messages 计数(无现成计数器,需脚本)。
- tool-call 轮次:统计 tool_calls 出现次数。
- 墙钟:run_log 首条 session_start 与末条 session_end 的 ts 差。
- 每决策点平均轮次:按 decision_point 聚合。

#### 结果解读
- 单 session 成本表 + 每决策点 token/轮次分布(找 token 大头,为 skill 精简提供数据)。

#### 预期结论与判定规则
- 判定线:单 session ≤ ~40 万 token(预算参考)。
- 超预算 → 压缩 inputs 快照(值快照只留关键指标)或缩决策点数;此实验为 skill 精简提供量化依据。

---

### 3.8 C2 — KG 消融

**回答**:知识图谱这一层值不值?(A3 的 Marker 硬匹配即为"无 KG"臂)

#### 数据准备
- **无 KG 变体**:用 `marker_dict.json`(D-3)替代 step3_kg 查询——每簇 markers 直接 match 字典 → 类型,无 confidence、无 ontology/ancestor。其余 pipeline(step1/2/4/5/6/7)不变。
- 若静态字典不可得 → 退化为"LLM 仅凭自身知识标注"(无参考),标注为降级版本,结论限定。

#### 实验流程
- 跑"无 KG"臂(确定性)→ 细胞级评估 → 与 B1 ③(full KG)对比。
- 依赖:③ 已跑通。若 Neo4j 本身不可用,则 full-KG 臂也无法跑,该实验直接降级为"观察 pipeline 如何降级",不做消融。

#### 结果解读
- macro-F1 / accuracy 差 + unknown_rate 差(无 KG 时 unknown 应显著上升,除非 LLM 用自己的知识兜底)。

#### 预期结论与判定规则(预先注册)
- 判定线:full-KG 的 macro-F1 − 无KG ≥ 0.03 → "KG 层有增益"成立。
- 若差 < 0.01 → KG 层价值存疑,需看未知率与陷阱点差异再讨论。

---

### 3.9 C4 — 多 API 模型对比(强烈建议,可选)

**回答**:LLM-as-judge 是否模型无关?

#### 数据准备
- 同一 pipeline + 同一 skill,换 LLM 后端(GPT-4o / Claude / Gemini),每模型 1 session。无数据构造。

#### 实验流程
- 跑 3 个模型 → 逐决策点比对 decision 一致性(同输入下)→ 细胞级评估 → token 成本。
- 依赖:loop 的 LLM 后端可插拔(§2.3)。

#### 结果解读
- 逐决策点 decision 一致率(量化"模型无关性")。
- 三模型准确率对比 + 成本对比。

#### 预期结论与判定规则(预先注册)
- 判定线:三模型 macro-F1 差 ≤ 0.02 **且** 主要决策点(聚类/候选/标签类)一致率 > 80% → "model-agnostic"成立。
- 若差 > 0.05 → 结论绑定具体模型,报告标注;需复查 skill 提示是否把判断锚定在模型特有能力上。

---

### 3.10 N1 — 笔记本开关消融(通用记忆)

**回答**:loop 内置笔记本对判断质量是否有增益?

#### 数据准备
- ⑦ notebook-on ×2 session;⑥ no-notebook ×2 session(**其中 1 份复用 B1③**,B1③ 即禁用笔记本跑出来的)。
- 同一 skill / pipeline / 参数,唯一差异 = 笔记本工具 + 通用提示(loop_design L-4)。

#### 实验流程
1. ⑥:不注册笔记本工具,系统提示不含用法。
2. ⑦:注册 `write_note`/`retrieve_notes` + 通用提示指导(`rag_design.md` §5)。
3. 每组跑 2 次 → `evaluate_cell_level.py` → decision 一致率(同形态指标跨 session 的 decision 一致)。

#### 结果解读
- ⑥ vs ⑦:macro-F1、unknown_rate、跨 session 判断稳定率。
- **前置条件(N2)**:⑦ 平均每 session 的 retrieve/write 调用 ≥ 3 次,否则 N1 无结论,只报使用率,先治系统提示强度。

#### 预期结论与判定规则(预先注册)
- ⑦ unknown_rate ≤ ⑥ **且** ⑦ macro-F1 ≥ ⑥ − 0.02 → "笔记本无负收益"。
- ⑦ macro-F1 ≥ ⑥ + 0.03 → "笔记本有增益"。
- 其余 → "无结论"。N=2/组,仅描述性,不做显著性。

---

### 3.11 N2 — 笔记本使用分析

**回答**:LLM 是否真用笔记本、用了是否有用?(N1 结论可信的前提)

#### 数据准备
- 输入:`conversation.jsonl`(⑦ session),只读,零额外成本。

#### 实验流程
- 调用频次与时机:write_note / retrieve_notes 出现在哪些轮次/步骤前后。
- 笔记概况:条数、平均长度、主题分布(关键词聚类)。
- 检索质量:top-k 返回是否相关(人工抽查,标注标准写进脚本)。
- 采纳率:检索后 LLM 的 judgment/reasoning 是否体现笔记内容(人工抽查)。

#### 结果解读
- `usage_stats.json`。使用率是 N1 结论可信与否的前提——若 ⑦ 几乎不调用,则 N1 差异无意义。

#### 预期结论与判定规则
- 判定线:使用率 ≥ 3 次/会话(与 N1 前置条件一致);采纳率 ≥ 50%(人工抽查 10 条)。
- 低于 → 先加强系统提示,再复跑 N1。

---

### 3.12 N3 — 笔记本通用性验证

**回答**:笔记本是 loop 级能力,与 skill 无耦合?

#### 数据准备
- 最小 skill(如 echo),不含领域知识。

#### 实验流程
- 冒烟:笔记本可注册、可读写、可跨会话检索(write_note → 新 session → retrieve_notes 命中)。

#### 结果解读
- 功能通过/失败(`N3/smoke_test.log`)。

#### 预期结论与判定规则
- 通过 = 注册成功 + 写入成功 + 跨会话检索命中 top-k。证明通用性,支撑 N1 的"loop 级增益"论述。

---

## 4. 判定规则总表(分析前定案,报告逐条对照)

| 实验 | 判定条件 | 结论 |
|---|---|---|
| B1 R1 | ③−② ≥ 0.03 且 CI 不含 0 | LLM 胜阈值 |
| B1 R2 | ②−① ≥ 0.03 且 CI 不含 0 | 判断层有价值 |
| B1 陷阱 | ③ 在 ≥4/6 出现的陷阱上优于 ② | 陷阱弹性论点成立 |
| S1-1 | ③ 在 ≥6/8 用例与确定性真值一致 | LLM 具备检出合并杂质能力 |
| S1-2 | ③ 在全部反例不误细分 | 不过度细分 |
| S1-3 | ③ 正确数 − ② 正确数 ≥ 2 | 合成场景下 LLM 优于规则 |
| B3 | 核心子集 ≤ 60(预期 ~30) | 247 指标冗余成立 |
| B4 | 改善率 ≥ 50% | 自我纠正有效 |
| A3 | harness − Marker硬匹配 ≥ 0.03 | harness ≥ 基线 |
| E1 | 单 session ≤ 40 万 token | 成本可承受 |
| C2 | fullKG − 无KG ≥ 0.03 | KG 层有增益 |
| C4 | 三模型差 ≤ 0.02 且主要决策一致率 >80% | model-agnostic |
| N1 | ⑦ ≥ ⑥−0.02(且使用率达标) | 笔记本无负收益 |
| N2 | 使用率 ≥3/会话 且采纳率 ≥50% | 笔记本被真用 |
| N3 | 冒烟通过 | loop 级通用 |

---

## 5. 目录与产物结构

```
experiments/
├── label_map.json                 ← D-2 核心资产(锁定版本)
├── marker_dict.json               ← D-3(仅 C2/A3)
├── gt_cells.csv                   ← D-1 真值
├── B1/
│   ├── arm1_default/  arm2_rule/  arm3_llm/      ← 各含 run_log.jsonl + final_annotations.json + stepN_*/
│   └── eval/
│       ├── cell_labels_arm{1,2,3}.csv
│       ├── macro_f1.json          (点估计 + bootstrap CI + per-type Wilcoxon)
│       ├── confusion_matrix.csv
│       ├── purity_diagnostic.json (聚类纯度,每臂)
│       └── trap_analysis.json     (6 陷阱逐点:③/② 对错)
├── S1/
│   ├── scenarios.json + leiden_override.csv      ← D-5
│   ├── case_{P1..H2}/            (每用例指标快照 + ②③ 判定)
│   ├── ground_truth.json         (确定性子簇真值)
│   └── battery_report.json       (逐用例判定表 + 命中率)
├── N1/  notebook_on/  notebook_off/
├── N2/  usage_stats.json
├── B3/  metric_usage_by_decision.json + minimal_sufficient_set.json + 精简比
├── B4/  self_correction_pairs.json + improvement_rate.json
├── A1/  report.md + confusion_failure_modes.json
├── A3/  baseline_compare.json     (与 C2 共享 marker_dict 结果)
├── C2/  nokg_arm/ + kg_ablation.json
├── C4/  model_consistency.json + accuracy_per_model.json
└── E1/  cost_stats.json

scripts/
├── build_label_map.py             ← D-2
├── build_marker_dict.py           ← D-3
├── build_scenarios.py             ← D-5(校验纯簇 + 生成覆盖文件)
├── run_mini_session.py            ← S1 ③ 单轮判断(temperature=0)
├── evaluate_cell_level.py         ← 展开逐细胞标签 + 计分 + purity
├── bootstrap_test.py              ← cluster-aware bootstrap
├── analyze_traps.py               ← 陷阱点 oracle 对比(真实数据 + S1)
├── run_baseline_marker_match.py   ← A3/C2 无KG臂
├── count_tokens.py                ← E1 token 统计
└── analyze_b3.py / analyze_b4.py / analyze_n1n2.py / analyze_c4.py
```

---

## 6. 执行顺序与时间线(精简起步)

```
P-1 数据构造      D-1→D-5:gt_cells + label_map(+可选 marker_dict/scenarios)  (前置,一切依赖它)
P-2 harness 实现  loop/dispatcher/dag/judges + 47 op pipeline        (tool_design Phase A~E + loop_design L-1~4)
P-3 冒烟          最小 skill 跑通 loop(N3 冒烟顺带完成,loop L-4 笔记本也在此验证)
P-4 三臂跑        arm1 → arm2 → arm3(③ 禁用笔记本)
P-5 B1 评估       evaluate + bootstrap + 陷阱分析 → B1/eval/
P-6 S1 合成用例    build_scenarios + run_battery → S1/(确定性 op + ②③ 单轮判断)
P-7 A1 + E1       A1 报告(复用 B1③)+ E1 成本(conversation + run_log)
P-8 轨迹分析      B3/B4(只读 run_log)
P-9 N 组          ⑦×2 + ⑥ 复用B1③+补1 → N1/,N2/
P-10 基线         Marker 硬匹配 → A3/C2 共享结果 → KG 消融对比
P-11(可选)        C4 多模型 / X-8 跨数据集
```

依赖关系:B1(A1/E1/B3/B4/C2/A3/N1 的数据源)→ 先跑 B1 再派生其余。S1 依赖 P-2 的确定性 op(不与 B1 的 ③ 耦合),可与 P-5 并行。

API 用量:③×1 + ⑥补×1 + ⑦×2 = **4 个全 session**,外加 S1 的 8 个单轮 mini-session(每个 ~1-2K token,可忽略)。C4 若做再 +2~3 个 session。P-1 约 1 天;P-4~P-10 在 API 可用后约 1 周。

---

## 7. 与既有设计文档的关系

```
experiment_design.md   策略层(假设/实验总览)   ← 本文档为其实施层(12 个实验全覆盖)
trajectory_design.md   13 决策点 + run_log 格式(B1/B3/B4 数据来源)
atomic_operations.md   47 原子操作(三臂跑的就是这些 op)
operations_metrics_catalog.md  247 指标(B3 分析对象)
tool_design.md         pipeline 实现(执行载体)
loop_design.md         通用 loop(B1③ + N 组载体,C4 后端可插拔)
rag_design.md          笔记本(N1/N2/N3 被测对象)
```

**对 `experiment_design.md` 的修订**(本文档 §1.4/§3.1 取代以下旧条款):
- 旧"三臂基于同一份 processed.h5ad、簇 ID 对齐、逐簇配对(McNemar)"→ 改为细胞级评估 + cluster-aware bootstrap。
- 旧"29 簇配对检验 N=29 够用"(§8)→ 12 类型 per-type 配对仅作参考,主判据走 bootstrap。
- C2 与 A3 的"Marker 硬匹配基线"合并为同一产物,避免重复实现。
- **新增 S1 合成场景注入(§3.2)**:把 6 个陷阱操作化为 8 个已知真值的合并用例,为 refine 决策点补统计力;与 B1 陷阱分析共享同一 oracle 表。此为 `experiment_design.md` 之外的新增受控探针,建议同步补入策略文档的 §3 实验总览。
