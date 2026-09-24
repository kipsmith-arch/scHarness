# 实验设计文档

## 1. 设计目标

本工程的科学贡献有四点,实验即围绕其验证:

1. **Loop/Skill/Pipeline 三层分离** — 通用 agent harness(换 skill 可跑别的任务)
2. **LLM 作为 14 个决策点的判断引擎** — 贯穿 pipeline 做 accept/adjust 判断,而非只在末端给标签。第 14 个是 `cross_species_routing`(覆盖预检之后、查图谱之前)
3. **轨迹日志设计** — `run_log.jsonl` 记录判断的 inputs(看了哪些指标)+ reasoning + output,可导出训练对
4. **247 个结构化指标 → LLM 判断** — 把统计测量"翻译"成决策
5. **Loop 级通用记忆(笔记本)** — loop 内置、完全被动、与 skill 解耦的经验库;跨会话复用"上次类似情况怎么判的",验证其对判断质量的增益(见 §4.4 N 组)

**核心问题**:LLM 在 pipeline 决策点上,比固定启发式/默认参数更好吗?

这是整个架构的存在理由。本文档定义验证该问题的实验集。

---

## 2. 约束

| 约束 | 内容 | 影响 |
|---|---|---|
| 算力 | 仅商业 API(GPT/Claude 等),无 GPU | 砍掉微调实验(D1),轨迹价值改用模式挖掘体现 |
| 模型 | 可调用多 API 模型 | C4 多模型对比可行,反而支撑"model-agnostic"论点 |
| 数据 | 当前仅一套已入库数据 | B1 走细胞级评估 + cluster-aware bootstrap(计分见 `eval_design.md`);跨数据集复现需补充数据集(见 §8) |
| 论述侧重 | 方法论:LLM-as-judge 有效性 | B 组为核心,A 组为基线,C/D/E 为支撑 |

---

## 3. 实验总览

| 编号 | 名称 | 组 | 核心度 | 回答什么 |
|---|---|---|---|---|
| B1 | 三臂决策对比(LLM vs 规则 vs 默认) | 方法论 | ★★★ | LLM 判断的智能性是否优于非默认参数与阈值启发式 |
| B3 | 指标最小充分集 | 方法论 | ★★★ | 247 指标是否冗余,LLM 实际引用哪些 |
| B4 | 自我纠正有效性 | 方法论/轨迹 | ★★★ | adjust→recluster→accept 循环是否真改善质量 |
| A1 | 端到端准确率 | 基线 | ★★ | 系统能用 |
| A3 | 与 baseline 方法对比 | 基线 | ★★ | 定位本方法在文献中 |
| E1 | 成本与效率 | 工程 | ★★ | 实用性 |
| C2 | KG 消融 | 消融 | ★ | 知识图谱这一层值不值 |
| C4 | 多 API 模型对比 | 泛化 | ★(建议) | LLM-as-judge 是否模型无关 |
| N1 | 笔记本开关消融 | 记忆 | ★★ | 通用经验库(笔记本)对判断质量是否有增益 |
| N2 | 笔记本使用分析 | 记忆 | ★★ | LLM 是否真用笔记本、用了是否有用 |
| N3 | 笔记本通用性验证 | 记忆/工程 | ★ | 记忆能力是否与 skill 无关(loop 级通用) |
| S1 | 合成场景注入(簇合并探针) | 方法论 | ★★ | 已知真值下 LLM 能否检出"两群被合成一群"并正确决定细分/不细分 |

**砍掉**:D1(微调,无 GPU)。轨迹价值由 B4/D2 的模式挖掘体现。

> S1 为新增受控能力探针:把 6 个陷阱操作化为 8 个已知真值的簇合并用例,为真实数据里出现较少的 refine 决策点补统计力。实施细节见 `experiment_implementation.md` §3.2。

---

## 4. 核心实验

### 4.1 B1 — 三臂决策对比(killer experiment)

#### 假设
LLM 在 14 个决策点(`qc_threshold`~`global_quality`,含 `cross_species_routing`,见 `trajectory_design.md` §3.1)的判断,能让 pipeline 产出比"固定默认参数"和"阈值启发式"更准确的细胞类型注释。

#### 方案
同一条 7 步 pipeline(47 原子操作),三组条件,唯一变量是决策方式:

| Arm | 决策方式 | 说明 |
|---|---|---|
| ① Fixed-default | 全部 `accept`,参数走默认 | 下界基线 |
| ② Rule-based | 阈值启发式 | 公平对照,排除"非默认就更好"的混淆 |
| ③ LLM-judge | 本 harness 的 agent | 被测对象 |

#### 三臂的逐决策点配置(14 个决策点)

**关键背景**:`metrics_interpretation.md §原则1` 明确"pipeline 产出测量值,LLM 产出判断……**不写进代码**"。即整个项目的论点是反对硬编码决策阈值。先厘清两个易混术语:

| 术语 | 是什么 | 该在哪 | 例子 |
|---|---|---|---|
| **解读参考值 / 合格标准** | 帮人/LLM 读懂一个数字的锚点(知识) | 知识文件 → ③ 的 SKILL.md | "AUC>0.7=好 marker";SOP "marker 10-50 个=合格" |
| **决策阈值** | `if 指标>X then 动作`(规则) | ② 的 `rule_judge.py`;**③ 的 SKILL.md 不该有** | "if AUC>0.7 then accept" |

- **③ SKILL.md 不含决策阈值**:它含的是解读参考值 + SOP 合格标准 + 陷阱警告——这些是"知识"(帮 LLM 读懂原始指标),不是"触发动作的开关"。LLM 读原始指标 + 这些知识 + 上下文,自己判 accept/adjust,可在陷阱点偏离参考值(如"sil=0.18<0.2 锚点,但发育连续谱低 sil 正常,accept")。
- **② Rule 臂 = 把知识文件里的解读参考值/合格标准强制转成决策阈值**(因为代码需要分支条件)。这步"锚点→if-then"的强制转换正是 ② 变脆的根源——参考值本是"读取辅助",塞成"触发条件"就丢了上下文弹性。
- 知识文件 `§附:常见陷阱` 列出了硬编码阈值翻车的具体场景——这些就是 **③ 预期胜过 ② 的预测点**(实验假设的落点)。
- 所以 ② vs ③ 比的是:**同一批数值锚点,② 强制成规则 vs ③ 当解读辅助来推理**。② 用知识文件给出的**最佳**参考值构建(非稻草人);③ 仍胜才证明"让 LLM 判"的论点成立。

三臂按决策点的**行为类别**分三组:

**A 组 — 参数供给型**(决策产出 pipeline 参数;① 固定值 / ② metric 派生)

| 决策点 | 读取指标(出处) | ① Default 固定值 | ② Rule(metric 派生,数值出处) | ② 预期失效(陷阱,出处) |
|---|---|---|---|---|
| `qc_threshold` | `qc_distribution`:p99/p90 mt、bimodality_coefficient、valley_detection(SOP-1、metrics §compute_qc) | max_mt_pct=15, min_genes=300, max_cp_pct=20 | p99_mt≫p90(长尾)→max_mt_pct≈p99;n_genes bimodality>0.555→min_genes=valley | 植物组织天然高/低 mt 与动物不同(陷阱1)——根不是叶,cp 阈值与 mt 不可套用同套规则 |
| `resolution_select` | `leiden_cluster`:resolution_cluster_counts、n_clusters_derivative、adjacent_resolution_ARI(metrics §choose_resolution) | resolution=0.8 固定 | 选拐点:n_clusters_derivative≈0 的平台期,且相邻 ARI>0.9 | 平台不明显时拐点检测不稳定;生物学预期簇数与拐点冲突时规则无上下文 |
| `de_method` | `leiden_cluster`:frac_smallest_cluster / n_rare_clusters(<5%)(SOP-2、metrics §pseudobulk_de) | wilcoxon 全用 | 任一簇<5% 细胞→该簇 pseudobulk(其余仍 wilcoxon) | pseudobulk 需样本维度;若 n_pseudobulk_samples<3 检验力不足,规则不知该回退 |

**B 组 — 重试触发型**(质量闸门,adjust 触发同 op 重跑;① 永不重试 / ② 阈值触发)

| 决策点 | 读取指标(出处) | ① Default | ② Rule(阈值出处) | ② 预期失效(陷阱,出处) |
|---|---|---|---|---|
| `clustering_quality` | `leiden_cluster`:silhouette_overall.mean、n_singleton、n_clusters_negative_silhouette、frac_largest_cluster(metrics §leiden_cluster) | accept(永不 recluster) | sil>0.2 AND n_singleton==0 AND n_neg_sil<2 AND frac_largest<0.5 → accept;否则 adjust(降分辨率重跑) | **连续谱 silhouette 天然低**(陷阱5)——发育梯度簇 sil 本就低,② 误判为"需重聚类",反而破坏真实结构 |
| `marker_quality` | `filter_markers`:n_markers、n_grey_zone、filter_funnel、filter_efficiency(SOP-2、metrics §filter_markers) | accept(永不重过滤) | n_markers∈[10,50] AND grey_zone_rate<0.3 → accept;否则 adjust(调 pct1/pct2 阈值重过滤) | 稀有簇 marker 天然少(<10),规则误判"质量差"反复重过滤;管家基因主导时需类别知识而非阈值 |
| `kg_match` | `query_genes`:overall_hit_rate、genes_with_no_kg_entry(metrics §query_genes) | id_match_ok(假定匹配) | hit_rate>80%→ok;10-30%→id_mismatch_gene_key(上游 ID 预处理未完成);<10%→id_mismatch_organ | 物种特异性基因本就不在 KG(陷阱2)——低命中不总是 organ 错 |

**C 组 — 标志/路由型**(不改参不重跑,只设标志或路由;① 固定标志 / ② metric 派生标志)

| 决策点 | 读取指标(出处) | ① Default 标志 | ② Rule(阈值出处) | ② 预期失效(陷阱,出处) |
|---|---|---|---|---|
| `batch_effect` | `batch_mixing`:batch_graph_autocorr、per_cluster_batch_entropy、per_cluster_max_batch_fraction(metrics §batch_mixing) | well_mixed(假定无批次) | autocorr>0.3→batch_effect;单簇单批次且对应基因型→condition_specific;否则 well_mixed | **突变体/条件特异群体天然单批次**(陷阱6)——② 易误判 batch_effect 去做批次校正,抹掉真实群体 |
| `cross_species_routing` | `step3a_kg_precheck` 的 `coverage_tier`、`recommended_strategy`(SOP-3;同源用来提高 KG 命中率) | `routing_accept`,跟随预检策略:`single_species` 跳过 step3b。本点要读覆盖档,不能固定成「永不映射」,否则零覆盖物种跑不通 | 同①:接受预检策略,不另点参考物种 | 覆盖档没有亲缘。参考物种按名录点名,最多 3 个。③ 还可 `routing_force_single` / `routing_force_cross` / `routing_multi_reference` |
| `candidate_gap` (cluster) | `rank_candidates`:first/second_count、count_ratio、count_diff、first_second_ancestor_overlap(metrics §rank_candidates、§4 LLM 判断逻辑) | first_decisive(全跳过 step5) | ratio>2 AND count_diff≥3→decisive;ancestor_overlap→ambiguous_parent_child(选更具体,不入 step5);first≈second 无 overlap→ambiguous_true(路由 step5);first=None→unknown | **小样本 ratio 骗人**(陷阱3,2 vs 1 ratio=2 但只差 1)——须 count_diff 配合;**层级本体并列不是模糊**(陷阱2,root cap ⊃ lateral root cap 共享 marker)——② 若不查 ancestor_overlap 会误入 step5 |
| `candidate_disambiguate` (cluster,仅并列簇) | `rank_candidates`:first_second_ancestor_overlap、first/second_mean_confidence | (① 无并列簇,此点不存在) | overlap→parent_child/synonym;无 overlap→ambiguous_true | 同义词 vs 父子类需本体遍历,阈值易把"同义"判成"真模糊" |
| `refine_effect` (cluster,仅 analyzed) | `candidate_autocorr`:morans_i;`subcluster`:n_subclusters_with_distinct_type;`marker_overlap`:Jaccard(SOP-5、metrics §5) | (① 不进 step5,此点不存在) | morans_i>0.3 AND sub_distinct≥1 AND max_Jaccard<0.5→effective;morans_i<0.1→autocorr_low(skip);否则 ineffective | 子簇类型与父候选"完全无关"才是假分裂(SOP-5),阈值只看 Jaccard 无法判"无关" |
| `unknown_cluster` | `unknown_overlap`:avg_overlap、Jaccard(metrics §unknown_overlap) | single_unknown_type | avg_overlap>0.5→single;否则 multiple | unknown 簇 marker 普遍稀疏,overlap 统计不稳 |
| `label_confirm` (cluster) | `marker_expression`:top-3 pct1/pct2、Cohen's d、AUC(SOP-6、metrics §marker_expression) | confirmed(全信) | mean_top3_pct1>0.5 AND pct2 低 AND cohen_d>0.5 AND AUC>0.7→confirmed;部分不达标→downgraded;first=None→unknown | **管家基因 pct1 也高**(陷阱4)——必须看 pct2,纯 pct1 阈值会把管家基因当好 marker 放行 |
| `global_quality` | `global_summary`:unknown_rate、label_diversity、mean_first_count_gap、cross_cluster_marker_reuse(SOP-6、metrics §global_summary) | quality_good | unknown_rate<30% AND label_diversity 合理 AND marker_reuse 低→good;30-50%→acceptable;>50%→poor | unknown_rate 高可能只是 KG 覆盖窄(陷阱:先查 organ/ID),② 直接判 quality_poor 会掩盖可修的 ID 问题 |

**两臂的本质区别(三轴)**

| 轴 | ① Default | ② Rule | ③ LLM |
|---|---|---|---|
| 读 metrics? | 否(盲) | 是(阈值) | 是(推理) |
| 能触发重试/改参? | 否(静态) | 是(A/B 组规则触发) | 是(LLM 触发) |
| 处理"陷阱"上下文? | 否 | **否(阈值无上下文)** | 是(读知识 + 上下文) |

- ① = 盲 + 静态(完全无判断层);②③ 都读 metrics 且能重试,差别在"阈值 vs 推理"。例外:`cross_species_routing` 上 ①② 都读预检策略再决定是否跳过同源。
- **① vs ②**:加"读 metrics + 规则"比"完全不判"强吗?(判断层有无价值)
- **② vs ③**:在 6 个陷阱点上,LLM 推理能否胜过硬编码阈值?(智能判断的价值——**核心假设,有具体预测落点**)

**塌缩控制**:② 的规则必须**实际读 metrics 并可能触发重试/路由**(A 组派生参数、B 组重试、C 组路由到 step5),否则退化成 ①。关键不可省的点:`resolution_select`/`de_method`(派生)、`clustering_quality`/`marker_quality`(重试)、`candidate_gap`(路由 step5)——这几条是 ② 存在的意义。

**② 的公平性**:② 必须用知识文件给出的**最佳**阈值(上表"阈值出处"列),不能设成稻草人。否则 ③ 胜 ② 无意义。若 ③ 在 6 个陷阱点仍胜过一个**精心构建**的 ②,项目论点(反硬编码、主 LLM 判断)才站得住。

#### 实现 — 三臂如何共享与分离

**原则**:决策层(谁判断、判断什么)是被测变量,**必须不同**;pipeline + 工具 + 日志格式是受控基底,**必须相同**。强行让 ①② 跑在 LLM loop 上,要么塞个假 LLM(扭曲),要么用 LLM 跟随规则(污染)——都不对。loop 天然是 LLM↔tool 循环,①② 没有 LLM 可循环,应走独立的确定性 DAG 驱动器,但复用同一份 dispatch 与日志。

**组件职责**

| 组件 | ① Default | ② Rule | ③ LLM | 共享? |
|---|---|---|---|---|
| pipeline 子进程脚本(47 op) | 同 | 同 | 同 | **是** |
| `dispatcher.run_subprocess`(工具执行) | 同 | 同 | 同 | **是** |
| `run_log.jsonl` 格式(exec/judgment) | 同 | 同 | 同 | **是** |
| DAG 顺序(依赖关系) | 同 | 同 | 同 | **是** |
| **决策/判断层**(谁判、何时重试、改什么参) | `default_judge` | `rule_judge` | LLM(loop) | **否(被测变量)** |
| `loop.py`(LangGraph LLM↔tool 循环) | 不用 | 不用 | 用 | 否 |
| skill.system_prompt / tool_schemas(LLM 面) | 不用 | 不用 | 用 | 否 |

> "相同 skill 和 tool"的准确答案:**相同的 pipeline 工具与 tool_runtime 派发**,是;**相同的 LLM 面 skill(prompt+schemas)**,否——那两样本来就只对 LLM 有意义,①② 无 LLM 谈不上用它们。被对比的恰恰是"决策层",它**必须**不同。

**文件结构**

```
annot_harness/
├── loop.py              ← ③ 专用(LLM↔tool 循环)
├── dispatcher.py        ← run_subprocess / run_function(三臂共用)
├── scripted_driver.py  ← ①② 专用(走 DAG,确定性;与 loop 共用 dispatch)
└── dag.py               ← 通用 DAG(nodes / deps / decision_after / 重试上限)
experiments/
├── cell_annotation_dag.py  ← 47 op + 14 个 decision_after(skill 外的 DAG 实例)
├── scripted_driver.py      ← 薄 CLI,委托 annot_harness.scripted_driver
└── judges/
    ├── default_judge.py ← ①(当场 decide(),永不 adjust,不路由 step5)
    └── rule_judge.py    ← ②(当场 decide(),可读 metrics,可 adjust / 路由)
```

**`scripted_driver` 骨架**(与 loop 共用 `dispatcher.dispatch`,不走 LangGraph)

```python
def run_scripted(dag, judge, tool_runtime, project_dir, log_path):
    history = {}  # op -> 最新 exec 记录
    for node in topological_order(dag):
        # 1. 解析参数:默认值,或被前序 judgment 的 action 覆盖
        params = resolve_params(node, judge, history)
        # 2. 派发工具(与 loop 调同一个 dispatch)
        spec = tool_runtime[tool_for(node["op"])]
        exec_record = dispatch(spec, {"subcommand": node["op"], **params}, state)
        append_log(log_path, exec_record)           # 写 exec 记录(同格式)
        history[node["op"]] = exec_record
        # 3. 在决策点判:default/rule/llm 三选一(此处 default 或 rule)
        for dp in node.get("decision_after", []):
            judgment = judge(dp, exec_record, history)
            append_log(log_path, judgment)           # 写 judgment 记录(同格式)
            if judgment["output"]["decision"].endswith("_adjust"):
                # 4. 重试:同一 op 用新参数重跑,attempt 递增
                history[node["op"]] = retry_with(node, judgment, tool_runtime, log_path)
```

**judge 函数骨架**

```python
# ① default:永不判,全 accept,不读 metrics
def default_judge(dp, exec_record, history):
    return {
        "type": "judgment", "decision_point": dp, "scope": {"type": "session"},
        "run_ref": exec_record["run_id"], "inputs": [],
        "output": {"decision": ACCEPT[dp], "confidence": "high",
                    "action": f"proceed_to_{next_op(dp)}"},
        "reasoning": "default: accept without inspection"
    }

# ② rule:读 metrics,套规则表
def rule_judge(dp, exec_record, history):
    m = exec_record["metrics"]
    if dp == "clustering_quality":
        sil = m["silhouette_overall"]["mean"]
        n_singleton = m.get("n_singleton", 0)
        if sil > 0.2 and n_singleton == 0:
            return accept(dp, "proceed_to_step2_markers",
                          inputs=[sil_path(sil), n_singleton_path(n_singleton)])
        return adjust(dp, "recluster_at_0.6", inputs=[...])  # 触发 driver 重跑
    # ... 其余决策点按规则表
```

**有效性控制(三臂可比的前提)**

1. **动作空间一致**:② 的规则表覆盖的 decision/action 枚举,必须与 ③ SKILL.md 允许 LLM 用的相同(`trajectory_design.md` §3.2 的枚举)。即 ③ 不许干规则表之外的动作,否则 ③ 领先可能是"动作空间更大"而非"判断更聪明"。
2. **② 须确定性代码**:不用 LLM 跟随规则 prompt——LLM 采样非确定性,会让 ② 不可复现且混入 LLM 方差。
3. **工具执行路径字面相同**:①②③ 都调 `dispatcher.dispatch(spec, args)` 执行同一 pipeline 脚本,唯一差异是"下一步调哪个 op / 带什么参 / 要不要重试"——这正是被测的决策层。
4. **评估口径**:三臂按**细胞级评估**(计分见 `eval_design.md`,实验用法见 `experiment_implementation.md` §1.4)——逐细胞标签 vs 该细胞 GT,不再要求簇 ID 对齐。原"三臂同一份 processed.h5ad、簇 ID 配对"与 qc/resolution/clustering 三个决策点必然改变聚类的设计自相矛盾,已废弃。

#### 指标(细胞级,公式见 `eval_design.md`)
- **headline**:per-cell strict(exact/synonym);**并列** relaxed(subtype/supertype=0.5)
- **辅**:macro-F1(不得单独当 headline;`Unknown` 会压低)、unknown_rate、relation 直方图、聚类纯度
- 统计检验:cluster-aware bootstrap(按簇整组重抽样)对两臂差做 95% CI;per-type Wilcoxon 作参考(不用细胞级 McNemar)

#### 执行
1. 跑 ① 固定默认 pipeline,得 `output_arm1/` + run_log
2. 跑 ② Rule-based pipeline,得 `output_arm2/` + run_log
3. 跑 ③ LLM-judge pipeline,得 `output_arm3/` + run_log(已是系统主路径)
4. 三个 arm 各导出 `final_annotations.json`,经 obs_snapshot.csv(细胞→leiden)展开为逐细胞标签
5. 逐细胞对真值(`experiments/gt_cells.csv`),按实施文档 §2 口径算指标 + bootstrap CI

#### 预期
③ 显著优于 ② > ①。若 ③≈②,说明 LLM 判断未超越简单阈值,需反思 skill 设计;若 ③≈①,说明判断本身无价值。

#### 输出
- `experiments/B1/confusion_matrix_{arm}.csv`(类型×类型,细胞级)
- `experiments/B1/eval/macro_f1.json`(macro-F1 点估计 + bootstrap CI + per-type Wilcoxon)
- `experiments/B1/eval/cell_labels_arm{1,2,3}.csv`(逐细胞标签)

---

### 4.2 B3 — 指标最小充分集

#### 假设
LLM 在 14 决策点实际高频引用的指标,只是 247 个中的一小部分(~30)。

#### 方案
从所有 `run_log.jsonl` 的 `judgment.records` 提取 `inputs[].path`,按 `{step}.{op}.{metric_path}` 聚合:

```python
from collections import Counter
paths = Counter()
for r in log:
    if r.get("type") == "judgment":
        for inp in r.get("inputs", []):
            paths[inp["path"]] += 1
# 按 decision_point 分组,看每组引用的指标
```

#### 指标
- 每个决策点引用的指标集合
- 跨决策点的指标频次排序
- "核心子集":被 ≥2 个决策点引用 或 单点引用频次 top-20 的指标

#### 输出
- `experiments/B3/metric_usage_by_decision.json`(逐决策点引用的指标 + 频次)
- `experiments/B3/minimal_sufficient_set.json`(核心子集清单)
- 结论:skill 可精简的 token 量(全量 → 核心子集的字节比)

#### 价值
反过来指导 SKILL.md:哪些指标该常驻 prompt,哪些按需查询。这是 skill 设计的可操作发现。

---

### 4.3 B4 — 自我纠正有效性(兼 D2)

#### 假设
LLM 改主意(`clustering_adjust`→重跑→`clustering_accept` 这类循环)对应的重跑,确实改善了质量指标。

#### 方案
从 `run_log.jsonl` 提取同一 `{decision_point, scope}` 有多条 judgment 的记录(见 `trajectory_design.md` §7.2),取首版与末版:

```python
from collections import defaultdict
by_key = defaultdict(list)
for r in log:
    if r.get("type") == "judgment":
        key = (r["decision_point"], json.dumps(r.get("scope", {})))
        by_key[key].append(r)

corrections = []
for key, records in by_key.items():
    if len(records) >= 2:
        records.sort(key=lambda r: r["seq"])
        corrections.append({
            "decision_point": key[0],
            "v1_decision": records[0]["output"]["decision"],
            "v1_run_ref": records[0]["run_ref"],
            "v_last_decision": records[-1]["output"]["decision"],
            "v_last_run_ref": records[-1]["run_ref"],
            "intervening_runs": [r["run_ref"] for r in records[1:-1]]
        })
```

对每个纠正对,比较 `v1_run_ref` 与 `v_last_run_ref` 两版 exec 记录的指标差:
- `clustering_quality`:silhouette_overall.mean 是否上升、n_singleton 是否下降
- `marker_quality`:n_markers 是否进入 [20,50] 区间
- `refine_effect`:子簇 silhouette 是否提升

#### 指标
- 纠正对总数(最多 14 决策点 × 簇数)
- 其中"改善"的比例(末版指标优于首版)
- 平均改善幅度

#### 输出
- `experiments/B4/self_correction_pairs.json`
- `experiments/B4/improvement_rate.json`(改善比例 + 幅度)

#### 价值
证明轨迹设计能复现"改主意=变好"。若改主意后变差的占多数,说明 LLM 判断不稳定,需回看 SKILL.md 的决策指导。

---

### 4.4 N 组 — 笔记本消融与使用(通用记忆)

> 被测对象是 loop 内置笔记本(`write_note` / `retrieve_notes`,见 `rag_design.md`)。本组不绑定那 14 个决策点——记忆是 loop 级通用能力,评估也随之解耦。

#### 假设
loop 内置笔记本能让 LLM 跨会话复用经验,提升判断质量;且该能力与 skill 无关。

#### N1 — 笔记本开关消融

同一 skill、同一 pipeline,两组唯一差别是笔记本可用与否:

| 组 | 笔记本 | 说明 |
|---|---|---|
| ⑥ no-notebook | 不注册笔记本工具,系统提示不含用法 | 基线 |
| ⑦ notebook | 注册 write_note / retrieve_notes + 通用提示指导 | 被测对象 |

比较(每组多个 session):
- 端到端质量:细胞级 strict/relaxed(见 `eval_design.md`)、unknown_rate
- 判断稳定性:相同指标形态跨 session 的 decision 一致率(⑥ 应天然不稳定,⑦ 靠笔记收敛)

#### N2 — 使用分析(零额外成本,只读 conversation.jsonl)

- 调用频次与时机:write_note / retrieve_notes 出现在哪些轮次/步骤前后
- 笔记概况:条数、平均长度、主题分布(人工或关键词聚类)
- 检索质量:top-k 返回是否相关(人工抽查)
- 采纳率:检索后 LLM 的判断 / reasoning 是否体现笔记内容(人工抽查)

N2 的使用率是"N1 结论是否可信"的前提:若 ⑦ 几乎不调用笔记本,则 N1 差异无意义,先治系统提示强度。

#### N3 — 通用性验证

换最小 skill(如 echo),验证笔记本可注册、可读写、可跨会话检索——证明它是 loop 级能力,与 skill 无耦合。

#### 指标
- N1:细胞级准确率/macro-F1、unknown_rate、decision 一致率(⑥ vs ⑦)
- N2:调用频次、笔记主题分布、top-k 相关性、采纳率
- N3:功能通过/失败

#### 输出
- `experiments/N1/accuracy_compare.json`(⑥ vs ⑦ 配对)
- `experiments/N2/usage_stats.json` + 抽查样本
- `experiments/N3/smoke_test.log`

#### 执行
1. 跑 ⑥ 基线(笔记本禁用)与 ⑦(启用)各若干 session
2. 从 conversation.jsonl 统计 N2 指标
3. 最小 skill 冒烟测试验证 N3

---

## 5. 支撑实验

### 5.1 A1 — 端到端准确率

对比 `step6_validate/final_annotations.json` 与该数据集真值表的细胞类型列,经 obs_snapshot.csv 展开为逐细胞标签对齐(口径见 `eval_design.md`)。产出 strict / relaxed + 按类型失败模式。识别哪些类型易注释、哪些易错。

### 5.2 A3 — 与 baseline 方法对比

| baseline | 来源 | 说明 |
|---|---|---|
| Marker 硬匹配 | step3 无 KG 替代 | 用 markers.json 直接 match 已知类型 marker,不查图谱 |
| CellTypist | 若有植物参考集 | 通用自动注释工具(需确认目标类群可用性) |
| SingleR | 若有植物参考集 | 同上,基于参考集 |

对比最终细胞级准确率,定位本 harness 在文献中的位置。若无可用的植物参考集,只做 Marker 硬匹配对比。

### 5.3 E1 — 成本与效率

从 `conversation.jsonl` 统计:
- 总 token 数(系统侧计)
- tool-call 轮次
- 墙钟时间(从 `run_log.jsonl` 的首末 `ts`)
- 每决策点平均轮次

零成本,加工程严谨度。

---

## 6. 可选消融与泛化

### 6.1 C2 — KG 消融

用 marker 直匹配替代 step3c_kg,其余 pipeline 不变,对比 B1 的 ③ 组准确率。判断知识图谱这一层是否带来增益。
- **依赖**:Neo4j 可用(`.env` 中 `NEO4J_*` 已配)
- **若 KG 不可用**:此实验降级为"无 KG"作为唯一可跑条件,无法消融,改为观察 KG 不可用时 pipeline 如何降级。

### 6.2 C4 — 多 API 模型对比(强烈建议)

同一 pipeline 同一 skill,换不同 API 后端(GPT-4o / Claude-3.5 / Gemini 等),比:
- 14 决策点的判断一致性(同输入下 decision 是否一致)
- 最终细胞级准确率
- token 成本

**无 GPU 即可做**,且直接支撑"LLM-as-judge 模型无关"论点。对方法论定位是加分项,建议不砍。

---

## 7. 与其他设计文档的关系

```
atomic_operations.md            47 原子操作 — B1 三臂跑的就是这些 op
operations_metrics_catalog.md   247 指标   — B3 分析 LLM 引用了哪些
trajectory_design.md            14 决策点  — B1/B3/B4 的数据来源
tool_design.md                  pipeline 实现 — 实验的执行载体
loop_design.md                  通用 loop   — C4 换模型只改 loop 的 LLM 后端
rag_design.md                   通用笔记本  — N1/N2/N3 的被测对象
eval_design.md                  注释对错怎么打分 — B1/A/N/C 共用 SCORE
experiment_implementation.md    实验怎么跑、B1 判定线
```

```
run_log.jsonl
    │ exec 记录(指标) + judgment 记录(LLM 判断 + inputs + reasoning)
    ▼
B1: 细胞级 strict/relaxed + cluster-aware bootstrap(② vs ③;公式见 `eval_design.md`)
B3: inputs[].path 频次统计 → 最小充分集
B4: 同 decision_point 多 judgment → 自我纠正对 → 改善幅度

conversation.jsonl
    │ 工具调用序列(含 write_note / retrieve_notes)
    ▼
N2: 笔记本调用频次/时机 → 使用分析
```

实验分析全部基于 `run_log.jsonl` 的现有字段,无需新增日志格式。

---

## 8. 单数据集缓解策略

当前仅一套数据。B1 细胞级评估(该数据的真值类型 + cluster-aware bootstrap)已可独立成立,但跨数据集复现会让结论外部效度更强。

### 策略
1. **细胞级评估(已有数据集可立即做)**:按 `eval_design.md` 计分 + `experiment_implementation.md` 的 cluster-aware bootstrap,无需额外数据。细胞级评估避免簇 ID 对齐问题。
2. **跨数据集复现(需补充)**:找 1-2 个同物种同组织的 scRNA-seq 数据集(控制变量),复跑 B1 三臂。候选数据的公共库访问号与 h5ad 可得性待核实,本文不列出。
3. **若无法补充数据集**:B1 结论限定为"在该数据集上",并在讨论部分说明单数据集局限;加强 B3/B4 的分析深度作为补偿。

---

## 9. 实施时间线

| Phase | 内容 | 依赖 | 产出 |
|---|---|---|---|
| **X-1** | 实现 loop + skill + pipeline(见 `loop_design.md` §10 / `tool_design.md` 实施计划) | — | 可跑的 harness |
| **X-2** | 跑 ③ LLM-judge 主路径,产出 `run_log.jsonl` | X-1 | 主路径轨迹 |
| **X-3** | 实现 Rule-based 启发式开关 + 跑 ①② | X-1 | 三臂轨迹 |
| **X-4** | B1 细胞级评估 + cluster-aware bootstrap 分析 | X-3 | `B1/` 结果 |
| **X-5** | B3 指标频次统计 + B4 自我纠正挖掘 | X-2 | `B3/`、`B4/` 结果 |
| **X-6** | A1 准确率 + A3 baseline + E1 成本 | X-2 | `A/`、`E/` 结果 |
| **X-7** | C2 KG 消融 + C4 多模型(可选) | X-1 | 消融结果 |
| **X-8** | (可选)跨数据集复现 B1 | 补充数据集 | 外部效度 |
| **X-9** | N1/N2/N3 笔记本实验 | loop 笔记本(L-4) | 记忆增益结论 |

X-1~X-3 与系统实现并行;X-4~X-6 在有第一批轨迹后即可启动;X-7 视算力/API 预算;X-8 视数据集可得性;X-9 依赖 `loop_design.md` L-4(笔记本内核),不依赖多数据集(单数据集多 session 即可对比)。

---

## 10. 验证

### 10.1 B1 的有效性验证
- 三臂使用同一份 Rule-based 规则表(②③ 共享),否则 ② 与 ③ 比较的是参数空间而非判断智能。
- 评估口径:细胞级(见 `eval_design.md`)。三臂聚类必然不同,不以簇 ID 配对;以逐细胞标签对真值计算,统计用 cluster-aware bootstrap(避免细胞非独立的伪显著)。
- 报告效应量(strict / relaxed 差,macro-F1 辅报)而非仅 p 值;B1 判定线预先注册在 `experiment_implementation.md` §3.1。

### 10.2 轨迹分析的可复现性
- B3/B4 的分析脚本只读 `run_log.jsonl`,不依赖任何中间状态,可重跑复现。
- B4 的"改善"判定阈值(silhouette 上升、n_singleton 下降等)在脚本中显式定义,不靠 LLM 自评。

### 10.3 因果链完整性
- 每条 B1 的逐簇判断结论,能从 `final_annotations.json` → `run_ref`(judgment)→ `exec`(metrics)回溯,验证 `trajectory_design.md` §4.5 的因果链可追溯(评估口径虽为细胞级,但判断单元仍是簇级,故逐簇回溯仍然成立)。

### 10.4 N 组实验的有效性
- ⑥ vs ⑦ 除笔记本外完全同构(同一 skill / pipeline / 参数,仅重复跑),否则比的是 skill 而非记忆。
- N2 统计只读 `conversation.jsonl`,可重跑复现;抽查样本人工标注,标注标准写进脚本。
- N3 用最小 skill,不依赖领域数据,可独立复现。
