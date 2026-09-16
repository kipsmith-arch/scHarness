---
title: 'P3 Skill 包核心:SKILL.md + references + description(cell-annotation)'
type: 'feature'
created: '2026-08-10'
status: 'done'
review_loop_iteration: 0
baseline_commit: '3ea1db077a94faaca6038edb4b2b1fbb8669a8f7'
context:
  - design/implementation_plan.md
  - design/trajectory_design.md
  - design/experiment_design.md
  - knowledge/cell-annotation-sop.md
  - knowledge/metrics_interpretation.md
  - knowledge/kg_schema.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `skills/cell-annotation/` 只有 P2 占位 SKILL.md(仅够加载器派生工具)。13 决策点判断指导、references 按需加载、6 大陷阱、判断必附 reasoning、run_log 合规全部缺失,LLM 面对 247 指标不会读、decision 越界,B1 实验被"skill 不好用"污染。

**Approach:** 重写 `SKILL.md`(<500 行,中文 imperative)+ 新建 `references/` 4 文件(sop/metrics/traps/kg-schema)+ frontmatter description 覆盖 5+ 触发说法。assets/、evals/ 已拆分延后(deferred-work)。

## Boundaries & Constraints

**Always:**
- 只动 `SKILL.md` + 新建 `references/` 4 文件;SKILL.md < 500 行,中文、imperative、讲 why。
- **红线(S-5)**:SKILL.md 只含解读参考值 + SOP 合格标准 + 陷阱警告,**不含 if-then 决策阈值**;13 决策点 decision 枚举 ⊆ trajectory §3.2 词表。
- 素材对齐:references/sop.md ← cell-annotation-sop.md 全文;metrics.md ← metrics_interpretation.md 全文;traps.md ← 6 陷阱 + SOP 症状速查配反例;kg-schema.md ← kg_schema.md。每文件带 TOC 且被 SKILL.md"何时读哪个"明确引用。
- 日志指导按 trajectory §10:session_start/judgment/session_end 模板 + judgment 字段表(inputs/output.decision/confidence/reasoning/run_ref/scope)。
- 工具引用用加载器真实名 `{script}__{subcommand}`(11 个);frontmatter 保留 `name: cell-annotation`,`description` 覆盖 5+ 真实用户说法。

**Ask First:**
- 需修改 P2 脚本(如给 `--dump-schema` 补 tool description)时——属 P2 资产。
- references 需偏离 knowledge/ 原文时。

**Never:**
- 不改 P2 scripts/*.py;不做 assets/、evals/、P5 内容(evals.json/跑测)。
- 不实现跨物种注释(cross-species handbook 不进 references)。
- SKILL.md 不出现决策阈值句式;不新增 run_log 之外日志文件;不手写 tool_schemas JSON。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | load_skill('skills/cell-annotation') | system_prompt = 新 body;tools=11;name/description 非空 | SkillError 则修 frontmatter 重试 |
| RED_LINE | SKILL.md 出现阈值句式 | 自检 grep+人工发现即改:改写为解读参考值 | 不妥协——红线是 B1 公平性前提 |

</frozen-after-approval>

## Code Map

- `skills/cell-annotation/SKILL.md` -- P2 占位版(23 行),重写;加载器读 body 为 system_prompt
- `skills/cell-annotation/scripts/*.py` -- 只读:11 工具名/参数、47 op run_id
- `knowledge/cell-annotation-sop.md` -- sop.md 素材(SOP-1~6 + 质量检查 + 症状速查)
- `knowledge/metrics_interpretation.md` -- metrics.md 素材 + 6 陷阱来源
- `knowledge/kg_schema.md` -- kg-schema.md 素材
- `annot_harness/skill_loader.py` -- 加载契约(frontmatter;body→system_prompt)
- `design/trajectory_design.md` §3.2/§10 -- 决策枚举词表 + 日志模板
- `design/experiment_design.md` §4.1 -- "解读参考值 ≠ 决策阈值"红线依据

## Tasks & Acceptance

**Execution:**
- [x] `skills/cell-annotation/SKILL.md` -- 重写正式版:角色与任务理解 → 13 决策点流程(SOP-1~6 映射,每点"看什么"指标路径)→ 6 陷阱 → reasoning 依据 → "何时读哪个 references" → 日志指导(模板+字段表)→ 工具概览 -- S-2/S-4/S-5
- [x] `skills/cell-annotation/references/sop.md` -- SOP 全文 + TOC -- S-3
- [x] `skills/cell-annotation/references/metrics.md` -- 247 指标解读全文 + TOC -- S-3
- [x] `skills/cell-annotation/references/traps.md` -- 6 陷阱 + 症状速查按决策点组织,配反例 + TOC -- S-3
- [x] `skills/cell-annotation/references/kg-schema.md` -- KG schema + 查询语义 + TOC -- S-3

**Acceptance Criteria:**
- Given 完成后, when load_skill, then system_prompt 为新 body(含"决策点"),tools=11,name/description 非空。
- Given SKILL.md, when `wc -l`, then < 500 行。
- Given SKILL.md, when 人工+grep 扫描, then 无"若指标>X 则动作"式阈值,只有解读参考值与合格标准。
- Given SKILL.md, when 逐决策点核对, then 13 点齐全,decision 枚举 ⊆ trajectory §3.2,每点含"看什么"路径。
- Given references/, when 检查, then 4 文件均有 TOC,SKILL.md 可 grep 到对每文件引用。
- Given frontmatter, when 列举触发说法, then description 覆盖 ≥5 种真实用户说法。

## Spec Change Log

<!-- step-04 评审回环填充;初始为空 -->

## Design Notes

**系统消息 = LOOP_BASE_PROMPT + SKILL.md body**(不含 references 全文),references 由 SKILL.md"何时读哪个"指引按需 read——B3 实验落地机制。

**解读参考值 vs 决策阈值:** 参考值帮 LLM 读懂指标("AUC>0.7=判别力好的 marker";SOP"marker 10-50 个=合格"),属 SKILL.md;阈值是"若指标>X 则动作"("if AUC>0.7 then accept"),只属 B1 ② 臂 rule_judge。写法:写"看 first/second 支持 marker 数差距,明显领先才定 high",不写"first_count>2×second_count 则 high"。

**动作空间:** 13 点 decision 枚举照抄 trajectory §3.2(如 candidate_gap ∈ {first_decisive, ambiguous_parent_child, ambiguous_synonym, ambiguous_true, unknown}),明示"只在这些枚举内选"——P5 断言与 B1 可比性前提。

**6 陷阱** = experiment §4.1 ② 臂预期失效点(植物 mt/cp、层级本体并列、小样本 ratio、pct1 管家基因、连续谱 silhouette、条件特异批次);traps.md 每条给"表面读数 → 真实含义 → 反例"。

## Verification

**Commands:**
- `python -c "from annot_harness.skill_loader import load_skill; s=load_skill('skills/cell-annotation'); print(s.name, len(s.tool_schemas), len(s.system_prompt.splitlines()))"` -- expected: cell-annotation 11 <500
- `wc -l skills/cell-annotation/SKILL.md skills/cell-annotation/references/*.md` -- expected: SKILL.md < 500
- `grep -oE "references/[a-z-]+\.md" skills/cell-annotation/SKILL.md | sort -u` -- expected: sop/metrics/traps/kg-schema 全在
- `grep -nE "若.{0,20}(则|就).{0,10}(重跑|接受|拒绝|通过|失败)" skills/cell-annotation/SKILL.md` -- expected: 无输出(红线自检)

**Manual checks (if no CLI):**
- 13 决策点逐一有节;decision 枚举与 trajectory §3.2 逐条一致
- description 覆盖 ≥5 种真实触发说法;references 四文件以 TOC 开头且内容来自对应 knowledge/ 原文

## Suggested Review Order

**入口:13 决策点流程(设计意图所在)**

- 从占位版重写为正式版的核心:每决策点"何时/看什么/枚举",13 点全覆盖
  [`SKILL.md:35`](../../skills/cell-annotation/SKILL.md#L35)

**红线 S-5:无决策阈值 + 动作空间**

- 决策枚举 ⊆ trajectory §3.2(评审已做集合比对,34 值全等);candidate_disambiguate 用候选词表子集
  [`SKILL.md:39`](../../skills/cell-annotation/SKILL.md#L39)

- 6 大陷阱 = experiment §4.1 ② 臂预期失效点;SKILL 只给解读参考值,不给 if-then
  [`SKILL.md:117`](../../skills/cell-annotation/SKILL.md#L117)

**references 分块(按需加载机制)**

- SOP 全文 + TOC;与 knowledge/ 原文逐字一致(评审字节级验证)
  [`sop.md:1`](../../skills/cell-annotation/references/sop.md#L1)

- 247 指标解读全文 + TOC;SKILL.md §3 各决策点的"看什么"在此查数值解读
  [`metrics.md:1`](../../skills/cell-annotation/references/metrics.md#L1)

- 6 陷阱按决策点组织,每条"表面读数→真实含义→反例" + 症状速查
  [`traps.md:1`](../../skills/cell-annotation/references/traps.md#L1)

- KG schema + 查询语义;评审对照 step3_kg.py 核验(organ/gene_key/ancestors)
  [`kg-schema.md:1`](../../skills/cell-annotation/references/kg-schema.md#L1)

**日志合规(trajectory §10 落地)**

- session_start/judgment/session_end 模板 + 字段表;judgment 必附 reasoning 的依据
  [`SKILL.md:149`](../../skills/cell-annotation/SKILL.md#L149)

**description 触发机制(S-4)**

- frontmatter 重写,覆盖 6 种真实用户说法(≥5);加载器派生 metadata
  [`SKILL.md:3`](../../skills/cell-annotation/SKILL.md#L3)

**外围:拆分登记**

- assets/ 与 evals/ 延后原因;两处 defer(metrics.md 附 20 vs 15、trajectory §3.2 缺行)
  [`deferred-work.md:1`](../../_bmad-output/implementation-artifacts/deferred-work.md#L1)
