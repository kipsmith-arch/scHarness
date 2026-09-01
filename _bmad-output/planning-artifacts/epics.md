# Epics — scHarness 项目里程碑

> 这是 `design/implementation_plan.md` 中 P1–P7 里程碑的 BMAD 风格 epic 拆分。
> 每个里程碑视为一个 epic,里程碑内的 task (L-/A-/B-/C-/D-/S-/I-/T-) 视为 story。
> 状态基线:`AGENTS.md` 2026-08 核查结果(P1–P6 已完成、P7 待办)。
> 总 epics:**7**;总 stories:**42**。

---

## Epic 1: P1 — Loop 宿主 + 标准 skill 加载器

**目标**:实现领域无关的 LangGraph runner,能加载任意"标准 skill 包",用最小 echo skill 完成冒烟。

**范围**:`harness/`(loop.py / dispatcher.py / skill_loader.py / session.py / conversation.py / notebook.py / config.py)+ `skills/echo/` 冒烟 + 89 pytest 全过。

**依赖**:无。

**里程碑验收(M1)**:echo skill 跑通;加载器派生三接口;subprocess/function/builtin 分发正确;笔记本读写 + BM25 兜底可用;`python -m pytest` 89 passed。

### Story 1.1: harness/loop.py + dispatcher.py — 通用循环
- 实现 LangGraph StateGraph(agent→tools→END)+ dispatcher 三型分发
- stdout 最后一行 JSON 契约
- 单元测试覆盖

### Story 1.2: harness/skill_loader.py — 标准 skill 包加载器
- 读 SKILL.md frontmatter(name/description)+ body→system_prompt
- 扫描 scripts/ → 派生 tool_schemas(LLM 面)+ tool_runtime(loop 面)
- 单一事实源 = 脚本 argparse(`--dump-schema`)

### Story 1.3: harness/session.py + conversation.py — 会话入口与对话落盘
- run_session 入口
- conversation.jsonl 写入
- 单元测试

### Story 1.4: skills/echo 冒烟 — 加载器与三型分发验证
- SKILL.md frontmatter + scripts/echo.py + `--dump-schema`
- 验证 loop 换 skill 不改代码(领域无关证明)

### Story 1.5: harness/notebook.py — 笔记本 + BM25 兜底
- write_note / retrieve_notes + notes.jsonl + 向量索引(可插拔)
- 无 embedding 时 BM25 兜底;RAG_NOTES_DIR
- N3 冒烟

### Story 1.6: loop_design.md 文档同步
- 职责 / 架构图 / State 补笔记本字段
- skill 接口改述为"从标准包派生"

---

## Epic 2: P2 — Pipeline 实现(skill 的 scripts/)

**目标**:实现 7 个 step 脚本 + 公共库,跑通 47 原子操作,4 次 h5ad 加载,每个脚本支持 `--dump-schema`。

**范围**:`skills/cell-annotation/scripts/`(common.py 1019 行 + 7 脚本 + trajectory_schema.py + write_judgment.py)。

**依赖**:P1 完成。

**里程碑验收(M2)**:47 op 全跑通;`--dump-schema` 全脚本可用;run_log 全 exec 记录;sidecars 写出;33,956-cell Arabidopsis root h5ad 上验证通过。

### Story 2.1: common.py — 通用函数库
- 9 个通用函数(describe_distribution / filter_funnel / effect_size / pairwise_overlap / batch_mixing / cluster_quality / variance_explained / resolution_stability / candidate_autocorr)
- append_log / next_run_id / current_metrics / dump_schema / arg_spec / JSON envelope
- env_or_default + load_skill_dotenv(SUPPRESS 化工具 schema)

### Story 2.2: step1_prepare.py — QC + 预处理
- subcommands:metrics(1× raw load)/ run(1× raw load,16 op + sidecars)/ recluster(1× proc load)
- 写 processed.h5ad + obs_snapshot.csv + var_snapshot.csv + qc_metrics.json

### Story 2.3: step2_markers.py — Marker 计算
- de_rank(BH-FDR/AUC/inflation λ)+ pct1_pct2 + filter_markers(漏斗)+ pseudobulk_de
- 写 markers.csv/json(enriched)

### Story 2.4: step3_kg.py — 知识图谱查询
- connect / query_genes(原始 var_names)/ query_hierarchy(ancestors)/ aggregate_candidates / write_hits
- test-connection 子命令
- SUPPRESS `--uri/--user/--password/--min-confidence/--max-ancestor-hops`(Neo4j 凭据 + KG 调优)

### Story 2.5: step4_judge.py — 候选排序(纯 JSON)
- rank_candidates(count_ratio/count_diff/confidence_diff/ancestor_overlap)
- 写 annotations.json(0 h5ad load)

### Story 2.6: step5_refine.py — 模糊簇细化解构
- candidate_autocorr → subcluster → subcluster_de → subcluster_kg(缓存复用)→ marker_overlap(Jaccard)→ type_membership → unknown_overlap
- 写 refined_annotations.json(1× proc load)

### Story 2.7: step6_validate.py — 验证 + 最终标注
- marker_expression(Cohen's d/AUC/fold_change)+ violin + global_summary + report + final
- backed 模式按列读(try/except 回退)
- 写 final_annotations.json + report.md

### Story 2.8: step7_diagnose.py — 诊断(0 h5ad load)
- hit_rate / candidate_count / first_second / batch_entropy / metadata_check / cross_cluster
- 读 obs_snapshot.csv + JSONs
- 写 step7_diagnose.json + report.md

### Story 2.9: trajectory_schema.py + write_judgment.py — 日志契约
- REQUIRED_SCOPE 单一事实源(9 session + 4 cluster)
- write_judgment.add / session-start / session-end

---

## Epic 3: P3 — SKILL.md + references + description

**目标**:把 P2 产物包成符合 skill-creator 标准的 skill 包(SKILL.md <500 行,references 按决策点组织,frontmatter description 触发词齐全)。

**范围**:`skills/cell-annotation/`(SKILL.md 243 行 + references/ 4 文件 + assets/ 2 文件 + evals/)。

**依赖**:P2 完成。

**里程碑验收(M3)**:SKILL.md < 500 行;references 有 TOC 且被引用;loop 加载器三接口派生成功;frontmatter description 覆盖 5+ 真实触发说法。

### Story 3.1: skills/cell-annotation/ 标准包骨架
- SKILL.md + scripts/(入住 P2 产物)+ references/ + assets/ + evals/
- 合规目录结构(skill-creator anatomy)

### Story 3.2: SKILL.md body
- <500 行 / 中文 / imperative
- 13 决策点流程(SOP-1~6 映射)+ 6 大陷阱警告 + 判断必附 reasoning 的依据
- "何时读哪个 references 文件"指引
- 日志指导(session_start/judgment/session_end 模板)

### Story 3.3: references/ 分块(按决策点组织)
- sop.md(全文)
- metrics.md(247 指标解读全文)
- traps.md(陷阱清单 + 反例)
- kg-schema.md(含"物种过滤与命名"section)

### Story 3.4: frontmatter description(触发机制)
- 面向"注释单细胞数据/找 marker/细胞类型判断"等真实用户说法
- 写触发词(参照 skill-creator "pushy" 原则)

### Story 3.5: 红线自检
- SKILL.md 不含 if-then 决策阈值
- 动作空间 ⊆ trajectory §3.2 枚举
- 合规检查清单

### Story 3.6: assets/ — 报告模板 + tools 清单
- 报告模板(report.md 之类)
- tools 清单(加载器派生,供人工核对)

---

## Epic 4: P4 — 实验数据底座

**目标**:把 SRP171040 真值类型与 KG 术语映射锁住,使 B1/S1/A1/E1/N1/C2 评估有共同输入。

**范围**:`experiments/gt_cells.csv`(33,956 行)+ `experiments/label_map.json`(12 类型,`_meta.verified`)。

**依赖**:无(只依赖 KG + CSV,与 P1/P2 可并行)。

**里程碑验收(M4)**:gt_cells 条码数 == 33,956 且与 h5ad obs_names 100% 对齐;label_map 12 类型全映射且人工定案;build_marker_dict 可用。

### Story 4.1: build_gt_cells.py — 真值标签抽取
- dataset/index/SRP171040.h5ad.csv → experiments/gt_cells.csv(cell_barcode, true_type)
- 33,956 行 100% 对齐验证

### Story 4.2: build_label_map.py — KG 术语映射
- 预测术语 × 12 真值标签的 exact / synonym / subtype / supertype / unrelated
- 自动查 KG ancestors 生成初稿 → **人工定案锁定**(`_meta.verified`)

### Story 4.3: build_marker_dict.py — 静态 marker 字典(C2/A3 用)
- `{gene: [cell_type]}` 静态字典
- 可用性检查

### Story 4.4: build_scenarios.py — S1 纯簇验证(可延后到 P6 前)
- 校验纯簇(占比 ≥90%)+ leiden_override.csv
- 8 用例构造

---

## Epic 5: P5 — Skill 测试循环

**目标**:用 skill-creator 方式把 skill 测到合格(P5 是 B1 的闸门),修复 r1→r2 发现的 KG 物种过滤与命名知识缺口。

**范围**:`skills/cell-annotation/evals/evals.json`(5 用例 E-1..E-5)+ `output/p5_evals_r1/` `output/p5_evals_r2/` + closure 报告。

**依赖**:P3 完成(skill 包结构合规)。

**里程碑验收(M5)**:E-1~E-5 全部断言通过;decision 枚举合法;validate_log 通过;r2 closure 报告(`_bmad-output/implementation-artifacts/p5-evals-round1-closure.md`)E-1 strict=0.9236 / relaxed=0.9434;E-5 PASS(117 judgments all compliant)。

### Story 5.1: evals/evals.json — 测试用例草稿
- E-1 端到端("注释这个数据集:output/ 里的 step1 已完成…")
- E-2 模糊簇决策("first/second 并列 8 vs 7,怎么办?")
- E-3 unknown 簇("有 3 个簇无 KG 命中")
- E-4 QC 陷阱(给植物数据 mt/cp 分布)
- E-5 日志合规(任意用例)

### Story 5.2: r1 跑测 + 评估
- with-skill 跑法:loop + skill + 测试 prompt(每用例 1 次会话)
- baseline:同一 loop + 同一 prompt,不加载 skill
- 断言核对 + 人工看输出

### Story 5.3: r1→r2 修复 — KG 物种过滤知识缺口
- 修改 SKILL.md / references/kg-schema.md"物种过滤与命名"section
- 重跑 r2 closure:全部 PASS

### Story 5.4: closure 报告 — p5-evals-round1-closure.md
- r2 评估结果记录
- KG 物种格式 vs LLM-passed `--species` 的偏差分析

---

## Epic 6: P6 — 方法学实验(B1 三臂等)

**目标**:用 skill 跑通 B1 三臂(① default / ② rule / ③ LLM)+ 辅助实验,对照预注册判定规则形成结论。

**范围**:`experiments/`(scripted_driver + evaluate_cell_level + bootstrap_test + analyze_traps + judges/) + `output/B1/{arm1_default,arm2_rule,arm3_llm}/run_log.jsonl` + `output/B1/eval/{evaluation_report,bootstrap_report,traps_report}.json`。

**依赖**:P5 达标 + P4。

**里程碑验收(M6)**:B1 三臂 + bootstrap CI + 陷阱分析;S1 battery;A1/E1/B3/B4/C2/N1/N2 产物齐全,逐条对照判定表;`b1-three-arm-eval.md` 已写入 `_bmad-output/implementation-artifacts/`。

### Story 6.1: scripted_driver.py — 确定性 DAG driver
- B1 ①/② 臂:调 dispatcher.dispatch 直接,绕开 LangGraph loop
- arm1_default = 固定默认值(无操作);arm2_rule = rule_judge;arm3_llm = 真 LLM loop

### Story 6.2: judges/default_judge.py + rule_judge.py + _common.py
- ① Fixed-default judge(no-op, all accept)
- ② Rule-based judge:oracle table → if-then(用知识文件最佳参考值转,非稻草人)
- 共享 helpers

### Story 6.3: evaluate_cell_level.py — 细胞级评估
- strict / relaxed accuracy + macro-F1 + confusion + purity
- 输入:final_annotations.json + gt_cells.csv + label_map.json

### Story 6.4: bootstrap_test.py — cluster-aware bootstrap
- 1000 resamples / 95% CI on macro-F1 delta
- B1 R1 判定:`③−② ≥ 0.03 且 CI 不含 0`

### Story 6.5: analyze_traps.py — 陷阱 oracle 比较
- B1 陷阱判定:`③ 在 ≥4/6 出现陷阱上优于 ②`
- per-trap ③ vs ② correctness

### Story 6.6: B1 r1 主评估 + report
- 跑三臂,生成 evaluation_report / bootstrap_report / traps_report
- 写 b1-three-arm-eval.md

### Story 6.7: B1 §6 辅助实验(S1 合成 / A / E / N / C)
- S1 battery:`run_mini_session.py`(③ 单轮判断, temperature=0);8 用例 × ②③ vs 确定性真值
- A1:报告(复用 B1 评估)+ count_tokens
- N 组:⑥ 复用 B1③ + 补 1;⑦ notebook-on ×2;N2 使用率 ≥3/会话前置;N3 P1 已冒烟
- C2 + A3:无 KG 臂对比,fullKG − 无KG ≥ 0.03

### Story 6.8: B3 / B4 轨迹分析
- B3:inputs[].path 频次 → 最小充分集 ≤60(反哺 SKILL.md / references 划分)
- B4:纠正对改善率 ≥50%(反哺 SKILL.md 决策指导"讲 why")

### Story 6.9: B1 r2 closure — rule_judge 阈值校准
- 收紧 DIFF_THRESH_FOR_DECISIVE / GAP_RATIO_TIED / "pct1≈pct2 → label_downgraded"
- 目标 arm2 strict ≥ 0.6;macroF1 delta arm2 vs arm1 stays inconclusive(R3 仍成立)
- 重跑 B1 arm2 + arm1

---

## Epic 7: P7 — Skill 持续迭代与交付

**目标**:基于 P5 + P6 反馈迭代 SKILL.md / references / frontmatter description,产出最终 `.skill` 包。

**范围**:SKILL.md v2 + references v2 + frontmatter v2 + `.skill` 打包件。

**依赖**:P5 + P6 完成。

**里程碑验收(M7)**:`.skill` 打包件存在;SKILL.md v2 + references v2 与 B3/B4 反哺一致;description 触发率达标;X-8 跨数据集泛化(可选)。

### Story 7.1: 扩大测试集(evals v2)
- 在 evals 中追加更多真实场景 prompt(跨决策点组合、边界案例)
- 重跑迭代

### Story 7.2: B3 反哺 — SKILL.md 常驻指标精简
- 核心子集统计 → 常驻指标精简、references 按需划分再平衡
- 写 SKILL.md v2 + references v2

### Story 7.3: B4 反哺 — 决策指导强化
- 纠正对中"改错"的决策点 → 强化 SKILL.md 决策指导(讲 why)
- 写 SKILL.md v2

### Story 7.4: description 优化
- 构造 should/should-not 触发查询集
- 用 loop 后端测触发率(替代 skill-creator 的 claude CLI)
- 迭代 frontmatter description

### Story 7.5: package_skill — .skill 打包交付
- 产出 `.skill` 文件
- 文档化安装与使用(.gitignore 忽略 *.json/csv,产物以文档 + 打包件交付)

### Story 7.6: 跨数据集复现(X-8) — skill 泛化测试(可选)
- 外部效度验证
- 非 root 数据集 portability 检查(顺便清理 deferred-work.md 4 项)

---

## 状态基线(AGENTS.md 2026-08 核查)

| Epic | Stories | 当前状态 | 备注 |
|---|---|---|---|
| Epic 1 (P1) | 6 | **done** | 89 pytest 全过 |
| Epic 2 (P2) | 9 | **done** | 47 op 全部实装并跑通;`--dump-schema` 全部脚本可用 |
| Epic 3 (P3) | 6 | **done** | SKILL.md 243 行;r2 closure 通过 |
| Epic 4 (P4) | 4 | **done** | gt_cells.csv 33,956 行;label_map.json `_meta.verified` |
| Epic 5 (P5) | 4 | **done** | r2 closure 报告:`p5-evals-round1-closure.md` |
| Epic 6 (P6) | 9 | **done**(r1 主评估)/ 部分待补 | B1 r1 已执行 + 报告已写;§6 辅助实验 + r2 closure 待跑 |
| Epic 7 (P7) | 6 | **backlog** | `.skill` 打包未做;description 优化未做 |

**Action items(来自待跑工作 + AGENTS.md "Next steps")**:
1. **Tighten `rule_judge` thresholds**(Epic 6 Story 6.9)— 优先级最高,B1 量化主轴
2. **Run B1 §6 supplementary experiments**(Epic 6 Story 6.7)— S1 + A + E + N + C
3. **B3 / B4 trajectory analysis**(Epic 6 Story 6.8)— 反哺 Epic 7
4. **SKILL.md / references iteration**(Epic 7 Stories 7.2/7.3)
5. **`.skill` packaging**(Epic 7 Story 7.5)— 最终交付
6. **Deferred-work clean-up**(Epic 7 Story 7.6)— 仅跨器官/跨数据集场景到来时清理