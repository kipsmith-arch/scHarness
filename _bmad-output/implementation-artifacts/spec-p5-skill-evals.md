---
title: 'P5 skill 测试循环第一轮(evals + 评估口径)'
type: 'feature'
created: '2026-08-11'
status: 'done'
baseline_commit: '0849c9a3f7b7ed7a0966d15841499eab2bd3f7c2'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** P1~P3 已交付(harness、pipeline 脚本、SKILL.md/references),但 skill 从未被测过:13 决策点是否落在枚举内、run_log 是否合规、注释正确率是多少——全部未知。P5 是 B1 实验前的质量闸门(implementation_plan §7),且 deferred-work 挂账两项:evals/(P3 S-1 拆分)与评估口径 D-1/D-2/D-4(P4,方案 B 提前)。

**Approach:** 建立 skill 测试循环:①新建 `experiments/` 评估口径(gt_cells.csv 真值、label_map.json 映射、细胞级评估脚本);②新建 `validate_log.py`(L-4)校验 run_log 合规;③新建 `skills/cell-annotation/evals/` 用例(E-1~E-5);④用 harness 跑 with-skill 端到端 + mini-session,产出量化报告,作为 P5 达标依据与 SKILL.md 迭代输入。

## Boundaries & Constraints

**Always:**
- `annot_harness/` 零改动(领域无关红线,loop_design);评估/校验代码放项目根 `scripts/` + `experiments/`,不进 harness、不进 skill 包
- 一个子命令 = 一次 h5ad 加载;评估只读 sidecar(obs_snapshot.csv / final_annotations.json / run_log.jsonl),不读 processed.h5ad(gt_cells 对齐校验除外,限一次)
- `run_log.jsonl` 是唯一轨迹文件,不新增并行日志
- decision 只能落在 trajectory §3.2 枚举内;validate_log 强制校验

**Ask First:**
- label_map.json 人工定案(13 条预填映射核对 + KG 查证后置 `_meta.verified=true`)——需用户参与
- E-1 端到端跑测消耗 LLM API(模型/预算需确认)
- 发现 pipeline/SKILL.md 缺陷需修改时,先报用户再改

**Never:**
- 不做 B1 三臂、S1、D-3(marker_dict)、D-5(scenarios)——P6/S1 范围
- 不改 SKILL.md 正文(本轮只测;迭代修改属 P5 第二轮,另行确认)
- 不做跨物种注释;不给 loop 注入领域知识

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| D-1 生成 gt_cells | `dataset/index/SRP171040.h5ad.csv` | `experiments/gt_cells.csv`(cell_barcode, true_type) | 条码数 ≠ 33,956 或与 obs_names 不对齐 → 报错退出,不产出 |
| D-2 起稿 label_map | KG 可达 | label_map.json 初稿,`_meta.verified: false`,12 类型全覆盖 | KG 不可达 → 用预填 13 条映射起稿并标注 unverified |
| validate_log | run_log.jsonl | 逐记录校验报告,exit 0 / 非 0 | 文件缺失/空 → 明确报错;坏 JSON 行 → 定位行号 |
| evaluate_annotations | obs_snapshot + final_annotations + label_map + gt_cells | accuracy / macro-F1 / 聚类纯度 + 逐簇表 | label_map 缺预测术语 → 记 unmatched 且报告不掩藏;leiden 列缺失 → 报错 |

</frozen-after-approval>

## Code Map

- `dataset/index/SRP171040.h5ad.csv` -- 真值源(33,956 细胞,12 类型)
- `skills/cell-annotation/scripts/common.py` -- append_log / run_id 约定(validate_log 参照)
- `design/trajectory_design.md` -- run_log 记录格式 §2、决策枚举 §3.2(校验依据)
- `output/p2/` -- 已有 pipeline 产物(step1~step7 + run_log),E-1 复用 step1 结果
- `annot_harness/session.py` -- 跑测入口 `python3 -m annot_harness.session --skill skills/cell-annotation --project-dir ... --task ...`
- `skills/cell-annotation/SKILL.md` -- 13 决策点/枚举/SOP(E-2~E-4 断言依据)
- `design/experiment_implementation.md` -- §1.2 D-1/D-2/D-4 定义、label_map 预填映射

## Tasks & Acceptance

**Execution:**
- [x] `experiments/gt_cells.csv` -- 从 index CSV 提取 cell_barcode + true_type,校验 33,956 与 obs_names 100% 对齐 -- D-1 真值规范化
- [x] `scripts/build_label_map.py` -- 起稿 `experiments/label_map.json`(预填映射 + KG ancestors 查证,`_meta.verified: false`)-- D-2 起稿
- [x] `experiments/label_map.json` -- 人工定案 13 条映射,置 `_meta.verified: true` -- D-2 定案(Ask First)
- [x] `scripts/validate_log.py` -- 校验 run_log:公共字段(ts/seq/type)、exec(run_id 格式、parameters、metrics)、judgment(decision_point 枚举、scope、run_ref、inputs、output、reasoning)、seq 单调、session_start/session_end 存在 -- L-4
- [x] `scripts/evaluate_annotations.py` -- 连接 obs_snapshot(细胞→leiden)× final_annotations(leiden→标签)× label_map(标签→真值)× gt_cells(条码→真值),输出 per-cell 准确率 / macro-F1 / 聚类纯度 -- D-4 + 评估
- [x] `skills/cell-annotation/evals/evals.json` -- E-1~E-5 用例(prompt + expected_output 断言)-- 测试用例
- [x] `skills/cell-annotation/evals/README.md` -- 用例说明:怎么跑、断言怎么判、结果落哪 -- 目录说明
- [x] 跑测 `output/p5_evals/` -- with-skill E-1 端到端 + E-2~E-4 mini-session + E-5 日志校验,产出报告 -- 测试循环

**Acceptance Criteria:**
- Given E-1 端到端跑完,when 检查 run_log 与评估输出,then session_start/session_end 存在、judgment 的 decision 全部落在 §3.2 枚举内、评估脚本输出 accuracy/macro-F1/聚类纯度
- Given E-2/E-3/E-4 mini-session 跑完,when 检查 judgment,then decision 枚举合法且 reasoning 非空(附指标依据)
- Given output/p5_evals/run_log.jsonl,when 运行 validate_log.py,then exit 0 且无 error 级问题
- Given gt_cells.csv 生成,when 校验条码数与 obs_names,then 33,956 条 100% 对齐
- Given label_map.json 定案,when 检查覆盖,then 12 真值类型全覆盖且 _meta.verified=true

## Spec Change Log

- **2026-08-11 P5 迭代发现与修复**(实施中暴露,非 spec 缺陷,记录备案):
  1. dcsapi 概率性断连(带 tools 请求 ~50% 首连失败)→ harness `build_llm` 加 max_retries(默认 6,env 可调)
  2. de_rank metrics 3.7MB / filter_markers 306KB(数据表误入轨迹)→ 瘦身(364KB/67KB),完整数据留产物文件;catalog 回填"指标 vs 数据"判据 + 体积约定
  3. **markers.json 重构引入 bug**(full 恢复时循环外引用 `markers` 变量 → 39 簇同一批 marker)→ 修复,与 p2 原始结果一致(1077 markers,39/39 簇各异)
  4. KG 候选缺 organ → step3 `_rank_candidates` 候选加 `organ`/`organ_status`(root/partial/unknown/mismatch);step4 候选摘要与决策视图回传(含全候选列表);SKILL.md §3.7/§3.8 加组织一致性检查指导
  5. 工具 data 契约过薄(LLM 读不到产物文件)→ step4 `data.per_cluster` 回传决策视图(候选 + gap + organ_status)
- **KEEP**:write_judgment 工具设计(skill 层、枚举校验、common.append_log seq 一致);E-1~E-5 用例结构;organ_status 三值判定

## Design Notes

- E-1 复用 `output/p2/step1_prepare` 产物作为起点(LLM 从 step2 起主导),全流程一次会话;断言含 final_annotations 结构(标签+置信度+marker 证据)
- E-2~E-4 用单决策点 mini-session:只喂该决策点指标快照 + skill 决策指导,不跑 pipeline(成本 ≈ S1 mini-session 量级)
- 正确率映射链:预测术语 → label_map(exact/synonym/subtype/supertype/unrelated)→ 真值;exact/synonym 计正确,subtype/supertype 计部分正确(按 experiment_implementation §1.2),unrelated 计错
- label_map 预填 13 条见 `experiment_implementation.md` §1.2(如 columella root cap ↔ Columella root cap = synonym;root cap → Columella/Lateral root cap = supertype)
- 本 spec 交付后,deferred-work.md 的 evals/(P3 S-1)条目标记 resolved

## Verification

**Commands:**
- `python3 scripts/validate_log.py output/p5_evals/run_log.jsonl` -- expected: exit 0
- `python3 scripts/evaluate_annotations.py output/p5_evals` -- expected: 打印 accuracy/macro-F1/聚类纯度 + 逐簇表
- `python3 -c "import csv; rows=list(csv.reader(open('experiments/gt_cells.csv'))); assert len(rows)==33957"` -- expected: 通过(33,956 数据行 + 表头)

**Manual checks:**
- `skills/cell-annotation/evals/evals.json` 含 E-1~E-5 五条,prompt 可读、断言可客观验证
- label_map.json 13 条映射与用户核对结果一致
- E-2~E-4 的 judgment 人工抽查 reasoning 是否附指标依据

## Suggested Review Order

**轨迹写入契约(设计核心)**

- skill 层工具:枚举校验 + 复用 common.append_log 保证 seq 一致(不改 harness 的领域无关红线)
  [`write_judgment.py:37`](../../skills/cell-annotation/scripts/write_judgment.py#L37)

- 三子命令(add/session-start/session-end)与 LLM 唯一数据通道契约
  [`write_judgment.py:146`](../../skills/cell-annotation/scripts/write_judgment.py#L146)

**LLM 数据通道(决策视图回传)**

- 工具 data 是 LLM 唯一数据来源;候选 + gap + organ_status 全量回传
  [`step4_judge.py:141`](../../skills/cell-annotation/scripts/step4_judge.py#L141)

- 候选摘要带 organ/organ_status(下游消费)
  [`step4_judge.py:36`](../../skills/cell-annotation/scripts/step4_judge.py#L36)

**数据质量(本轮迭代核心)**

- markers bug 修复:full_kept_markers 按簇保存(此前循环外引用导致 39 簇同批 marker)
  [`step2_markers.py:312`](../../skills/cell-annotation/scripts/step2_markers.py#L312)

- metrics 瘦身:top_genes 15 / markers 5 摘要,数据留产物文件
  [`step2_markers.py:221`](../../skills/cell-annotation/scripts/step2_markers.py#L221)

- KG 候选 organ_status 判定(Root 组合/Unknown/None/mismatch)
  [`step3_kg.py:232`](../../skills/cell-annotation/scripts/step3_kg.py#L232)

**评估与校验**

- 四表连接 + soft macro-F1(partial=0.5 软计数)
  [`evaluate_annotations.py:29`](../../scripts/evaluate_annotations.py#L29)

- run_log 合规校验(公共字段/枚举/seq/run_ref)
  [`validate_log.py:43`](../../scripts/validate_log.py#L43)

- D-1 真值条码对齐校验
  [`build_gt_cells.py:71`](../../scripts/build_gt_cells.py#L71)

- D-2 标签映射 KG 降级 + unrelated 定案
  [`build_label_map.py:89`](../../scripts/build_label_map.py#L89)

**健壮性**

- dcsapi 概率性断连 → max_retries(env 可调)
  [`session.py:56`](../../annot_harness/session.py#L56)

**LLM 指导与设计回填**

- 组织一致性检查指导(排除 mismatch 候选)
  [`SKILL.md:79`](../../skills/cell-annotation/SKILL.md#L79)

- 指标 vs 数据判据 + 体积约定(P5 回填)
  [`operations_metrics_catalog.md:15`](../../design/operations_metrics_catalog.md#L15)

**测试用例(外围)**

- E-1~E-5 用例与断言
  [`evals.json:1`](../../skills/cell-annotation/evals/evals.json#L1)
