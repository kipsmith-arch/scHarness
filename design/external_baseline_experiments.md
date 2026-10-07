# 外部基线对照：领域智能体与直接生成技能

摘要里的两类对照各取一个已有工作，在已有拟南芥根数据上比较。固定参数与固定规则仍由 B1 三臂承担，本文件不重做。

| 摘要中的做法 | 对照工作 | 实验 |
|---|---|---|
| 分支在设计阶段写定的领域智能体 | CASSIA（Xie 等，*Nature Communications*，2025） | E-A |
| 由模型直接生成、只排列步骤的技能 | Anthropic skill-creator 的一次成稿（不跑其评测优化环） | E-G |

两侧与 scHarness 共用同一套细胞级计分（`eval_design.md`）。判定规则在看结果之前写死。

---

## 1. 为什么是这两个工作

### 1.1 CASSIA

CASSIA 是面向细胞类型注释的多智能体：注释、校验、格式化、质量打分、报告五个角色在软件里接好，默认流程按这个顺序执行。校验不通过时回到注释，最多三轮。更细的注释、低分补救（Annotation Boost）和亚群注释（Subclustering）是用户另行打开的模块。物种、组织和标记基因表由调用者传入，不依赖参考图谱。论文报告每群使用 50 个标记基因。

这与摘要中的领域智能体对应：角色、轮次上限和可选模块在发布时已经写进框架。一次运行不会把“支持数目并列时可以细分”写成随技能更换的决策点。

文献：Xie E, Cheng L, Shireman J, et al. CASSIA: a multi-agent large language model for automated and interpretable cell annotation. *Nature Communications*, 2025. https://doi.org/10.1038/s41467-025-67084-x 。实现以 `CASSIA` Python 包的 `runCASSIA_batch` 默认路径为准。

### 1.2 skill-creator 一次成稿

skill-creator 根据任务描述写出一份 `SKILL.md`：前置元数据里的名称与描述，正文里的步骤顺序。成稿之后它还有描述优化和评测环；那些环会看到任务结果再改技能。本实验只用它的成稿步骤，停在第一份草稿。

文献与实现：Anthropic, *The Complete Guide to Building Skills for Claude*；成稿规则见 `anthropics/claude-plugins-official` 中 `plugins/skill-creator/skills/skill-creator/SKILL.md` 的 “Write the SKILL.md” 部分。仓库里的循环层走自有 API，不依赖 Claude.ai 插件界面：把该节的成稿要求原样放进与注释运行相同的模型，生成一份技能。

### 1.3 同属这两类、本次不跑的工作

| 工作 | 不放入本次实验臂的原因 |
|---|---|
| CellAgent | 期中报告用它说明全流程分析智能体。其注释工具依赖 CellTypist 参考模型，拟南芥根没有可用参考；整条流程还会重新聚类，细胞无法与现有真值对齐。 |
| GPTCelltype | 输入已选标记、输出一次名称，对应报告里的“模型直接定名”。摘要里要对照的是一份技能，而不是末端一次命名。 |
| AWS HCLS `cell-type-annotation` | 内容是人类免疫细胞上的 CellTypist / SingleR 代码步骤，物种与组织都换了，和“技能里有没有判断”混在一起。 |

---

## 2. 两侧共用的控制

数据用拟南芥根（Zhang 等，*Molecular Plant*，2019），真值、钉表与别名沿用 `experiments/gt_cells.csv`、`experiments/gt_ontology.json`、`experiments/kg_term_aliases.json`。高粱根不进入本次判定。

计分脚本用现有 `experiments/evaluate_cell_level.py` 与 `experiments/bootstrap_test.py`。主终点是严格准确率；同时报告层次得分、宏平均 F1、低置信比例。自助抽样以细胞群为单元，1000 次，95% 区间。真值不进入 CASSIA 的提示，不进入技能成稿提示，也不进入任何一次注释运行。

名称对齐只走现有别名与本体。不为 CASSIA 或生成技能另做一张预测词到手写类型的对照表。对不上的名称记为 unmatched。

模型与 scHarness 技能组同一次拟南芥运行所用的模型相同。温度两侧都设为 0。API 失败重试只重复同一请求，不改提示。

时间上各跑一轮。不按准确率回头改提示、改 CASSIA 参数或改生成技能正文。

---

## 3. E-A：scHarness 与 CASSIA

### 3.1 问题

在同一批细胞群、同一份标记基因上，设计期写定的注释智能体与承载决策点的技能，交付名称的严格准确率差多少；支持数目并列的根冠细胞群上，CASSIA 是否会先细分再定名。

比较范围停在定名。质控和聚类不进入本实验，否则两侧细胞群不一致，严格准确率不再对应同一些细胞。

### 3.2 冻结输入

取 B1 拟南芥根技能组与固定参数组已经共用的那次预处理结果（期中报告中的 34 个细胞群）。具体文件：

- `processed.h5ad` 与 `obs_snapshot.csv`（细胞条码 → leiden）
- `step2_markers/markers.csv`
- 技能组已有的 `final_annotations.json`（scHarness 侧不再重跑）

`markers.csv` 中 `status=kept` 的基因，在每个 leiden 群内按 `auc` 从高到低取前 50 个。这是 CASSIA 论文报告的标记基因数量。列映射为该包所期望的 FindAllMarkers 样式：

| CASSIA 列 | 来源 |
|---|---|
| `gene` | `gene` |
| `cluster` | `cluster` |
| `avg_log2FC` | `logfc` |
| `p_val_adj` | `pval_adj` |
| `pct.1` | `pct1` |
| `pct.2` | `pct2` |

调用参数：`species="Arabidopsis thaliana"`，`tissue="root"`。不附加实验条件、不附加预期类型名单、不附加知识图谱命中。

### 3.3 运行

主臂只走 CASSIA 默认五角色（注释、校验、格式化、打分、报告）。不调用 Subclustering。亚群标记若由本仓库的 step5 提供，等于把 scHarness 的细分结果交给对方。

预注册的敏感臂：仅当某群质量分 ≤ 75（CASSIA 论文中的低分界线）时，对该群打开 Annotation Boost，输入为该群 `markers.csv` 里的全部 kept 行（含 p 值、倍数与表达比例），而不是另外做一次聚类。敏感臂单独记分，不替换主臂。

记录每群：交付名称、质量分、校验轮次、是否进入 Boost。这些字段来自 CASSIA 自己的输出。

### 3.4 计分

CASSIA 每个 leiden 一个名称。用冻结的 `obs_snapshot.csv` 展开到细胞，写成与 `final_annotations.json` 相同的结构（`label` 为 CASSIA 名称，`confidence` 用其质量分，`status` 固定为 delivered），再走现有评价脚本。scHarness 用技能组已有交付，不从 `run_log` 另推一套标签。

另取期中报告已经点名的那一群：1872 个细胞、柱根冠与侧根冠支持数目均为 30、多数真值为柱根冠。记录两侧交付名称、与多数真值是否相符，以及 CASSIA 是否出现亚群划分。scHarness 侧同时核对已有判断记录里该群的动作是不是先细分。报告已写明细分后的子群名称尚未进入最终交付，因此这一群的行为终点是判断记录中的动作；交付名称单独列出，不把它当成细分已经生效。

### 3.5 预先注册的判定

1. 主终点：技能组严格准确率 − CASSIA 主臂严格准确率。95% 区间下界 > 0，则本数据集上技能组更高。区间含 0，则准确率差异不作结论，仍报告点估计。
2. 行为终点：该 1872 细胞群上，CASSIA 主臂给出一个细胞群一个名称。技能组判断记录中的动作为先细分。两条同时成立，则“设计期分支不会在支持数目并列时改走细分”在该群上成立。
3. 结构终点：CASSIA 输出的推理与质量分保留。这些文本不按决策点的四项要素组织，也不写入 `run_log.jsonl` 的 judgment。本项作对照清单，不计 p 值。
4. 敏感臂相对主臂的严格准确率变化单独列表。Boost 提高了分数，只说明低分补救模块有效，不改变主臂结论。

---

## 4. E-G：scHarness 与直接生成的技能

直接生成的技能是 `skills/cell-annotation-steps`。拟南芥根（SRP171040）已跑完一轮，产物在 `output/steps_arabidopsis`，启动脚本是 `experiments/ext/run_steps_skill.py`。这一轮与写有决策点的技能共用同一循环、同一批命令实现和同一数据，用户消息只给出数据路径、物种与组织。

### 4.1 问题

同一通用循环、同一批计算脚本、同一个模型，技能正文换成模型按步骤写成的第一稿之后，正文里完整决策点有多少，拟南芥根上的严格准确率差多少。

被比较的是技能正文。计算脚本保持不变。

### 4.2 成稿

生成与注释使用同一模型，温度 0，一次调用，只保留第一份草稿。不运行 skill-creator 的描述优化和评测环。成稿后允许的改动仅限：补上使脚本能够启动的路径或必填参数，并逐条写入 `patch_log.md`。不改步骤顺序，不补决策点，不补阈值。

成稿提示只用下面这一段，另附 step1–step7 的 `--dump-schema` 输出，便于它写出可调用的命令。不附带 `skills/cell-annotation/SKILL.md`、`references/`、`knowledge/` 中的陷阱与决策点说明、`design/decision_prompt_design.md`、`trajectory_schema.py`、真值文件。

```
按 Agent Skill 的格式写一份 SKILL.md，用于对植物单细胞转录组做细胞类型注释。
前置元数据必须含 name 与 description。description 写明技能做什么、在用户要求注释细胞类型时使用。
正文按顺序写出这些步骤，每步写明调用哪一个已提供的命令：
1. 预处理与质控
2. 聚类
3. 标记基因鉴定
4. 参考知识查询
5. 按细胞群定名
6. 汇总并写出最终注释
命令只使用下面 schema 里出现的脚本与参数。
不要发明新的脚本。不要规定何种指标取值对应何种动作。
```

成稿冻结在 `skills/cell-annotation-steps/SKILL.md`。

### 4.3 技能包

目录 `skills/cell-annotation-steps/` 满足 `annot_harness/skill_loader.py`：

- `SKILL.md`：冻结草稿
- `scripts/`：`step1_prepare.py` 至 `step7_diagnose.py` 各是一层包装，经 `_delegate.py` 调用 `skills/cell-annotation/scripts/` 里的同名脚本。计算实现只有一份

不放入 `write_judgment.py`。判断工具的参数枚举里含有决策点名称；放进去等于在工具清单里写上判断结构。决策点技能保留该工具。这是两份技能内容的差别，不是运行时临时删掉。

`experiments/ext/run_steps_skill.py` 用与决策点技能相同的加载方式、模型和 `notebook=False` 启动。用户消息只写拟南芥根的 h5ad、项目目录、物种与组织。该次运行写出 `output/steps_arabidopsis/step6_validate/final_annotations.json`。`run_log.jsonl` 只有 `exec` 记录，没有 `judgment`，也没有调用 step5。写不出来则准确率记为缺失，不用固定参数组的结果填补。

### 4.4 结构审核

审核对象是冻结的 `SKILL.md` 正文。十四个位置与期中报告 3.1.3 节的表一致：质控阈值、聚类分辨率、聚类质量、批次效应、差异分析方法、标记基因质量、查询途径、匹配结果、候选差距、候选消歧、细分效果、未能定名、标签确认、全流程质量。

一个位置计为完整决策点，当且仅当正文里能逐字引用出四句：

| 要素 | 引用需要满足 |
|---|---|
| 观测指标 | 点明该位置要读的具体量（分布分位、分辨率间稳定性、支持数目、表达比例等） |
| 参考判据 | 写明这些量如何阅读，从而决定取舍 |
| 候选动作 | 同时列出至少两个动作，并且没有写“指标一旦落在某区间就删去其余动作” |
| 作用范围 | 写明全数据集或单个细胞群 |

缺任何一项，该位置记 0。只写“运行某一步”记 0。审核表逐格贴引用原文；没有引文的格子保持 0。决策点技能的 `SKILL.md` 用同一标尺计一次，作为对照计数。这张表尚未写成文件；期中报告把逐格计数放在下一套数据上完成。

### 4.5 预先注册的判定

1. 主终点是结构计数：生成技能的完整决策点数为 0，scHarness 为 14。两条同时成立，则“直接生成的技能只排列步骤、判断没有单独表示”成立。生成技能计数 ≥ 1 时，逐条列出引文，结论改为“成稿中出现了若干完整决策点”，不把这些格子改判为 0。
2. 准确率是次终点，仅在生成技能写出 `final_annotations.json` 时计算。技能组严格准确率 − 生成技能严格准确率的 95% 区间下界 > 0，则本数据集上技能组更高。区间含 0 或生成技能未完成，准确率不作结论。
3. 生成技能的严格准确率若接近固定参数组，与结构终点独立报告：步骤跑完且参数保持默认时，结果会落在固定参数附近。
4. 行为记录：1872 细胞的根冠并列群上，生成技能的 `run_log.jsonl` 里有没有先细分再定名的执行。没有 judgment 工具时，以是否调用 step5 为准。此项不替代结构终点。

成稿若调用了 schema 之外的命令，或在第一步之前停止，结构终点仍然有效，准确率按缺失处理。不重新生成。

拟南芥根这一轮已经和决策点技能对照过。直接生成技能的类型名称与固定参数组相同。1872 细胞的根冠并列群上，直接生成技能直接定名，决策点技能先细分再定名。直接生成技能未定名的根毛细胞群，由决策点技能补入名称。两处是准确率差异的主要部分。严格准确率差值的 95% 区间包含 0。细分后各子群的名称没有写入这次最终注释。

---

## 5. 产出

```
skills/cell-annotation-steps/          # E-G 冻结草稿；scripts 委托到 cell-annotation
experiments/ext/run_steps_skill.py     # 启动这一轮
output/steps_arabidopsis/              # 拟南芥根已完成的运行
├── task.txt
├── run_log.jsonl                      # 仅 exec
├── conversation.jsonl
└── step6_validate/final_annotations.json
experiments/ext/cassia/                # E-A
├── markers_top50.csv
├── cassia_default/
├── cassia_boost/                      # 无低分群则写明未触发
└── evaluation_report.json
```

E-G 的十四行审核表（每格引文或 0）和与 B1 同字段的 `evaluation_report.json`（细胞数、严格准确率、层次得分、宏平均 F1、低置信比例、自助抽样区间，另加根冠群一行与决策点计数）还没有单独落成文件。对照结论写在期中报告汇报大纲。

---

## 6. 与现有实验的边界

B1 回答技能相对于固定参数、固定规则是否提高严格准确率，以及增益落在哪些细胞群。E-A 回答同一批标记上，设计期写定的注释智能体是否在并列证据处细分。E-G 回答把技能换成模型一次写成的步骤稿之后，决策点是否还在。拟南芥根上的 E-G 运行和与决策点技能的对照已经完成；十四个位置的逐格引文计数、以及在另一套数据上重复，仍按第 4.4 节与第 4.5 节做。三者的主终点不同，不合并成一个排序。

本轮不跑 CellAgent、GPTCelltype 与 HCLS 注释技能，也不在高粱根上复现。生成技能的自动优化环留在后续“由决策点图生成技能”的工作里，不提前用到这份对照上。
