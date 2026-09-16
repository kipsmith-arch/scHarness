---
title: '测量与判断解耦（判断层驱动 DAG）'
type: 'refactor'
created: '2026-09-04'
status: 'done'
baseline_commit: '1e18311a17b0fa5b650929381f5a37fd5827d363'
review_loop_iteration: 0
context:
  - design/experiment_design.md
  - design/experiment_implementation.md
  - knowledge/metrics_interpretation.md
  - design/trajectory_design.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 设计要求 pipeline 只出测量值、三种判断策略（①default / ②rule / ③LLM）作为唯一变量驱动 13 个决策点。实现是「线性跑完 step1–7，再事后改 label」：step3 按 organ 优先级排好 first/second，step5 用 `first_count > second_count` 自路由，step6 写 `label`+`high/medium/low`；①② 走裸 subprocess、不调 `dispatcher.dispatch`，judge 不能 adjust / 重试 / 跳过 step5；③ 的 `write_judgment.action` 只进日志、loop 不执行。三臂在 A/B 组塌成 ①。

**Approach:** 抽出领域无关的 DAG 驱动器（decision_after → judge → accept/adjust/skip）；pipeline 只算测量并交出完整候选列表；`step4_judge` 改名为 `step4_rank`；①② 当场判并驱动下一步（与 ③ 共用 `dispatcher.dispatch`）；③ 仍走 loop，但工具输出不再带预判枚举。重试只服务 B 组质量闸门；每 op 最多 5 次重试，触顶有成功产物则放行并记 `cap_exhausted_proceed`，一次成功 exec 都没有才任务失败。允许 breaking，重构后重跑 B1。

## Boundaries & Constraints

**Always:**
- pipeline 可排序、计数、算 gap / 表达量；**不得**写出 `high|medium|low`、`first_decisive|ambiguous_*`、`label_confirmed|downgraded` 等决策枚举，也不得按这些枚举自行路由。
- `has_candidates` / `no_candidates`（候选是否为空）视为测量，可保留。step3 必须交出**完整** `candidates[]` + 排序键（organ_status / marker_count / mean_confidence）；**谁是 winner 由判断层选**，下游不得把 `candidates[0]` 当已决标签。
- `op_choose_resolution` 只接受判断层（或 ① 的固定默认）给出的显式 resolution；禁止用 cluster-count knee 静默选定作为三臂差异。
- 三臂共用 `dispatcher.dispatch` 与 `run_log.jsonl`（①② 不得再裸 `subprocess` 调脚本）。判断层是唯一变量。动作空间 ⊆ `trajectory_design.md` §3.2。
- 重试语义（代码执法，不靠提示词）：
  - **为何重复：** 只为 B 组质量闸门（`clustering_quality` / `marker_quality` / 需重跑才能换测量的同类点）。A 组是「先判再首次跑」，C 组是路由，都不是重试。重试 = 同一 `{step}.{op}` 换参再测，让判断层再看一眼（B4 的 adjust→重跑→accept）。
  - **上限：** 首次 `#{1}` + 最多 5 次重试 `#{2}`–`#{6}`。`#{7}` 由 `common.next_run_id`/`exec_record` 拒绝（③ 再调同一工具同样命中）；①② 的 retry 循环不再 dispatch。不新增 decision 枚举。
  - **当前产物：** 下游读该 op **最后一次 status=ok 的 exec**（失败 attempt 留在 log 里供 B4，但不覆盖产物）。不自动回滚到更早「看起来更好」的 attempt。
  - **触顶仍想 adjust：** 只要存在 ≥1 次 ok exec → **放行**该次产物，写 judgment：`decision` 用已有 accept 枚举（如 `clustering_accept` / `markers_accept`），`action=cap_exhausted_proceed`，`reasoning` 写清 attempt 数与「闸门未满足、用最后一次成功测量继续」。session **不中止**（B1 臂必须有可评估产物）。
  - **一次 ok exec 都没有**（脚本全失败或从未跑成）→ **该 op 任务失败**：不再往下游走，`session_end` 带 error；不捏造 label。这与「闸门不满意」不是一类事。
- SKILL.md 可说明触顶应 `cap_exhausted_proceed` 并继续 SOP；执法仍是脚本拒绝 `#7`。loop 不解析 `action` 字符串。
- harness **不知道** cell-annotation：`annot_harness/dag.py` 只编码「节点 / 依赖 / decision_after / 重试」；细胞注释 DAG 实例与 oracle 表不进 harness。
- 一个子命令一次 h5ad 加载。重试必须走已有子命令（`step1_prepare recluster`、带新参的 `step2_markers run` 等），不新开加载路径。
- `final_annotations.json` 的 `label` / `confidence` / `status` **只由判断层写入**。`evaluate_cell_level.py` 只读这三项；**删除**现行 `load_arm_judgment_confidence` / `load_arm_unknown_label` 对 `run_log` 的第二套覆盖。计分公式不变。
- 改名必须锁步：脚本、产物目录、`run_id` 前缀、SKILL/references/tests 中的 `step4_judge` → `step4_rank`。
- ①② 必须写 `session_start`（与 ③ 对称）。

**Ask First:**
- `marker_quality` adjust 若无法用现有 `step2_markers run` 重跑完成（需要新的「只过滤」子命令或额外 h5ad 加载）。
- 通用 DAG 驱动器若必须改 `annot_harness/dispatcher.py` 契约才能挂 judge 钩子。

**Never:**
- 不把 if-then 阈值写进 pipeline 或 SKILL.md。
- 不把 oracle 表 / 细胞类型知识放进 harness。
- 不把「事后 rewrite `final_annotations`」继续当作三臂差异的来源（可留作把已写入的 judgment 物化成 JSON 的最后一步）。
- 不重校准 `rule_judge` 阈值（Story 6.9 已完成）。
- 不改 strict/relaxed 计分定义；不做 P7 打包；不新造并行日志文件。
- 不改 organ_status 字符串匹配（属 deferred-work）。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ARM1_NO_RETRY | ① + 同一 raw h5ad | 各决策点当场 accept；不调 `recluster`；`candidate_gap=first_decisive` → **不跑 step5**；judge 写 label=first、confidence=high | 缺 exec 指标 → 仍 accept，reasoning 标明盲判 |
| ARM2_RETRY | ② 在 `clustering_quality` 给出 `clustering_adjust` 且 `#1` ok | driver 经 dispatch 调 recluster，写 `leiden_cluster#2`，再判 | 脚本非零 → 该 attempt 记 error exec，当前产物仍是上一 ok |
| CAP_PROCEED | `#1`–`#6` 均已发生，末次 judge 仍 adjust，且至少一次 exec ok | 不发 `#7`；judgment `action=cap_exhausted_proceed` + accept 枚举；DAG 用最后 ok 产物继续 | 不得把 cap 写成新 decision 枚举 |
| CAP_NO_OK | 该 op 没有任何 status=ok 的 exec | session 失败；`session_end` error；不写 final labels | evaluate 缺产物 → 非零退出 |
| RETRY_CAP | 已有 `#1`–`#6` 时 ③ 再调同一工具 | 返回 `status=error`，不追加 `#7` exec | 提示词要求再试也无效 |
| RES_EXPLICIT | 判断层已写 `resolution_select` | `step1_prepare run` 使用该显式 resolution，不跑 knee 自动选 | 未给显式值且非 ① 默认 → 拒绝静默 knee |
| ARM2_REFINE | ② 对簇 C 判 `ambiguous_true` | 仅对 C 调 step5；decisive / parent_child 簇不进 refine | step5 缺 `--clusters` 且无 judgment 路由 → 拒绝跑全量 |
| PIPELINE_NO_ENUM | 只跑 step4–6，不跑 judge | `annotations.json` 有 first/second/gap；`final` 测量物无 confidence/label 决策字段 | 旧代码读 `confidence` 应失败或为空，由 judge 补齐 |
| RENAME | 任意脚本写/读 step4 产物 | 路径为 `step4_rank/`，`run_id` 以 `step4_rank.` 开头 | 读 `step4_judge/` → 明确错误（不静默回退） |
| EVALUATE | 三臂各有 judge 写入的 `final_annotations.json` | `evaluate_cell_level.py` 按原 strict/relaxed 计分 | 缺 label/confidence/status → 退出并说明「判断层未写入」 |

</frozen-after-approval>

## Code Map

- `annot_harness/dag.py` -- 新建：通用 DAG（nodes / deps / `decision_after` / 每 op 最多 5 次重试）
- `skills/cell-annotation/scripts/common.py` -- `next_run_id`/`exec_record`：`attempt > 6` 拒绝写入（③ 也走这里）
- `annot_harness/scripted_driver.py` -- 新建：`run_scripted(dag, judge, tool_runtime, project_dir)`，与 loop 共用 `dispatcher.dispatch`
- `annot_harness/dispatcher.py` -- 只读，确认不必改契约
- `experiments/cell_annotation_dag.py` -- 新建：47 op + 13 个 `decision_after` 绑定（skill 外的实验 DAG 实例）
- `experiments/scripted_driver.py` -- 改为薄 CLI，委托 `annot_harness.scripted_driver` + 上表 DAG
- `experiments/judges/_common.py` -- judge 协议：`decide(dp, exec_record, history) -> judgment`；`commit_labels()` 只物化已写入的 judgment
- `experiments/judges/default_judge.py` -- 改为当场回调：永不 adjust、永不路由 step5
- `experiments/judges/rule_judge.py` -- 改为当场回调：可读 metrics，可 adjust / 路由；阈值数字不改
- `skills/cell-annotation/scripts/step3_kg.py` -- 保留排序键为测量；文档化 sort，不把 `[0]` 当已决 winner
- `skills/cell-annotation/scripts/step1_prepare.py` -- `op_choose_resolution` 只消费显式 resolution
- `skills/cell-annotation/scripts/step4_judge.py` -- 重命名为 `step4_rank.py`；只算 gap，不指定标签
- `skills/cell-annotation/scripts/common.py` -- `STEP_DIRS` 等目录键 `step4_judge` → `step4_rank`
- `skills/cell-annotation/scripts/step5_refine.py` -- 删除 `first_count > second_count` 自路由；只处理判断层给出的簇列表
- `skills/cell-annotation/scripts/step6_validate.py` -- 删除 `_confidence_evidence` 与自动 `label=`；测量物不含决策枚举
- `skills/cell-annotation/scripts/step7_diagnose.py` -- 改读 `step4_rank/`；`top_strictly_ahead` 仅作测量布尔
- `skills/cell-annotation/SKILL.md` -- path 改为 `step4_rank`；枚举只来自 `write_judgment`；③ 的 `action` 必须再调工具才生效
- `annot_harness/tests/test_trajectory_schema.py` -- `run_ref` 前缀更新
- `experiments/evaluate_cell_level.py` -- 断言 label 来自判断层；去掉对 pipeline `_confidence_evidence` 的兜底叙述
- `design/experiment_design.md` -- 文件结构段与实现对齐（driver 在 harness，DAG 实例在 experiments）

## Tasks & Acceptance

**Execution:**
- [x] `annot_harness/dag.py` + `annot_harness/scripted_driver.py` -- 实现通用 walk / `decision_after` / adjust 重试（每 op 最多 5 次） -- ①② 与 loop 共用派发
- [x] `skills/cell-annotation/scripts/common.py` -- `next_run_id` 在 attempt>6 时失败 -- ③ 再调工具也被硬限制
- [x] `experiments/cell_annotation_dag.py` -- 绑定 13 决策点到对应 op -- DAG 实例不进 harness
- [x] `experiments/scripted_driver.py` -- 改为 CLI 包装 -- 保留现有入口路径
- [x] `experiments/judges/_common.py` + `default_judge.py` + `rule_judge.py` -- 改为 `decide()` 当场回调；写 `session_start`；`commit_labels` 只物化 judgment -- 判断层驱动 DAG
- [x] `skills/cell-annotation/scripts/step3_kg.py` -- 输出完整 `candidates[]` + 排序键说明 -- winner 交给判断层
- [x] `skills/cell-annotation/scripts/step1_prepare.py` -- `choose_resolution` 只吃显式值 -- 禁止 knee 静默当决策
- [x] `skills/cell-annotation/scripts/step4_judge.py` → `step4_rank.py` + `common.py` 目录键 -- 去掉 judge 语义
- [x] `skills/cell-annotation/scripts/step5_refine.py` -- 以判断层簇列表为唯一入口 -- 去掉 pipeline 内 routing
- [x] `skills/cell-annotation/scripts/step6_validate.py` -- 删除 `_confidence_evidence` 与自动 label -- 测量与判断分离
- [x] `skills/cell-annotation/scripts/step7_diagnose.py` + SKILL.md + `references/metrics.md` + `assets/tools.md` -- path 锁步改名；SKILL 写明 ③ 须再调工具执行 action
- [x] `annot_harness/tests/test_measure_judge_decouple.py` -- 覆盖 I/O 矩阵（fake dispatch，不加载 h5ad）-- 回归不依赖 2GB 数据
- [x] `annot_harness/tests/test_trajectory_schema.py` + 引用 `step4_judge` 的测试 -- 更新前缀
- [x] `experiments/evaluate_cell_level.py` -- 删除 run_log 第二套覆盖；缺判断层字段则失败
- [x] `design/experiment_design.md` -- 更正「judges 在 harness / ①② 已走 dispatch」的过时结构图

**Acceptance Criteria:**
- Given 只跑 pipeline 不跑 judge，when 查看 step4/step6 产物，then 没有 `confidence` / `label_confirmed` / `first_decisive` 等决策枚举，且 step6 不写 `label`。
- Given step3 产出，when 判断层未选 winner，when 下游读 `candidates[0]`，then 不得据此跳过 step5 或写入最终 label。
- Given ① driver + default_judge，when 跑完 DAG，then 不出现 `recluster` exec，且没有 `step5_refine.*` exec；`run_log` 含 `session_start`。
- Given ② 对 `clustering_quality` 返回 `clustering_adjust`，when driver 处理该 judgment，then 出现 `step1_prepare.leiden_cluster#2`（或等价 recluster run_id）。
- Given 同一 `{step}.{op}` 已有 `#1`–`#6` 且至少一次 exec ok，when 判断层仍要 adjust，then 不出现 `#7`，出现 `action=cap_exhausted_proceed` 的 judgment，下游继续用最后一次 ok 产物。
- Given 该 op 没有任何 ok exec，when 无法再试，then session 以 error 结束，不写 `final_annotations` 的 label。
- Given ② 仅簇 C 为 `ambiguous_true`，when 进入 refine，then step5 只处理 C。
- Given 任意 step4 产物路径，when 脚本读写，then 只使用 `step4_rank/`；打开 `step4_judge/` 失败。
- Given `final_annotations.json` 缺少 judge 写入的 label/confidence/status，when 跑 `evaluate_cell_level.py`，then 非零退出。
- Given `python -m pytest`，when 全量执行，then 既有用例 + 新 I/O 测试全部通过。

## Spec Change Log

## Design Notes

重试不是「同一判断再喊一遍」，是「换参再测」。A 组参数在首次 exec 前一次选定；只有 B 组闸门在看到测量之后才可能 `*_adjust`。

触顶分叉：有成功测量 → 放行（否则 B1 缺臂、细胞级评估无法比）；没有成功测量 → 失败（没有可恢复状态）。`action=cap_exhausted_proceed` 让 B4 能把「被迫 accept」与真正的 `clustering_accept` 分开，而不改 §3.2 枚举。

① 不读 metrics，一律 `proceed` / 空 `refine`。② 读 metrics 套现有阈值（含补写 `resolution_select`）。③ loop 不执行 `action` 字符串；SKILL 要求再调工具，触顶 error 后应写 `cap_exhausted_proceed` 并继续 SOP。

```python
def decide(dp, exec_record, history) -> dict:
    # action: proceed | retry:{op, params} | refine:{cluster_ids} | skip:{op}
    #         | cap_exhausted_proceed
```

step1 已有 `metrics` / `run` / `recluster`：先 metrics → 判 `qc_threshold`/`resolution_select` → 再带显式参 `run`；必要时 `recluster`。step3 的 organ 优先级排序是**测量约定**（键公开），不是 `candidate_gap`。QC 过滤 / marker `kept|grey|dropped` / 双细胞剔除仍是「应用已选参数」，不是新决策点。

## Verification

**Commands:**
- `python -m pytest` -- expected: 全部通过（含新 `test_measure_judge_decouple.py`）
- `python skills/cell-annotation/scripts/step4_rank.py --dump-schema` -- expected: 工具名/目录为 `step4_rank`
- `rg -n "step4_judge|_confidence_evidence|first_count > .*second_count|load_arm_judgment_confidence" skills/cell-annotation/scripts experiments` -- expected: 无命中（测试夹具除外）

**Manual checks (if no CLI):**
- 本轮只交 spec，不重跑 2GB h5ad。实现阶段用假 dispatch 单测 DAG；批准后的实现再重跑 B1 三臂。

## Suggested Review Order

**DAG 驱动器（harness 领域无关）**

- 入口：`run_scripted` 走 topo、`decision_after`、retry/skip/cap
  [`scripted_driver.py:206`](../../annot_harness/scripted_driver.py#L206)

- 通用节点 / 钩子 / 每 op `#1`+5 次上限
  [`dag.py:26`](../../annot_harness/dag.py#L26)

- 触顶必须用钩子上的 `accept_decision`，不写细胞注释枚举
  [`scripted_driver.py:301`](../../annot_harness/scripted_driver.py#L301)

**细胞注释 DAG 实例（不进 harness）**

- 47 op + 13 决策点绑在 experiments
  [`cell_annotation_dag.py:116`](../../experiments/cell_annotation_dag.py#L116)

- 薄 CLI：`--arm default|rule` 委托 walker + skill `tool_runtime`
  [`scripted_driver.py:25`](../../experiments/scripted_driver.py#L25)

**判断层当场回调**

- ① 永不 adjust、不填 `--clusters`，从而跳过 step5
  [`default_judge.py:28`](../../experiments/judges/default_judge.py#L28)

- ② 读 metrics；`ambiguous_true` 才把簇写入 step5
  [`rule_judge.py:55`](../../experiments/judges/rule_judge.py#L55)

**Pipeline 只出测量**

- `choose_resolution` 拒绝缺省 knee
  [`step1_prepare.py:414`](../../skills/cell-annotation/scripts/step1_prepare.py#L414)

- step4 改名为 rank；first/second 是排序测量
  [`step4_rank.py:184`](../../skills/cell-annotation/scripts/step4_rank.py#L184)

- step5 唯一入口是判断层 `--clusters`
  [`step5_refine.py:299`](../../skills/cell-annotation/scripts/step5_refine.py#L299)

- step6 不写 label/confidence；无 step5 exec 则 passthrough
  [`step6_validate.py:288`](../../skills/cell-annotation/scripts/step6_validate.py#L288)

- `#7` 由 `next_run_id` 硬拒绝；旧目录名抛错
  [`common.py:176`](../../skills/cell-annotation/scripts/common.py#L176)

**评估与 ③ 面**

- 缺判断层三字段则评估退出
  [`evaluate_cell_level.py:55`](../../experiments/evaluate_cell_level.py#L55)

- ③ 的 `action` 须再调工具；触顶写 `cap_exhausted_proceed`
  [`SKILL.md:230`](../../skills/cell-annotation/SKILL.md#L230)

**测试**

- I/O 矩阵用假 dispatch，不加载 h5ad
  [`test_measure_judge_decouple.py:1`](../../annot_harness/tests/test_measure_judge_decouple.py#L1)

