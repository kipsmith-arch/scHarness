---
title: 'B1 三臂清档重跑（③ 真关闭笔记本）'
type: 'chore'
created: '2026-09-07'
status: 'done'
baseline_commit: 'f61ba3bc913c06a7c17162f450c02c40d92ae196'
review_loop_iteration: 0
context:
  - design/experiment_implementation.md
  - _bmad-output/implementation-artifacts/spec-measure-judge-decouple.md
  - _bmad-output/implementation-artifacts/b1-r3-followup.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 解耦后的 B1 已在 `output/B1_r3/` 跑过一轮；用户要求删掉该历史并完整重跑。同时 B1 规定 ③ 禁用笔记本，但 `run_session` 每次都把 `write_note` / `retrieve_notes` 并进 tool schema，`RAG_EMBEDDING=off` 只关嵌入，任务词里的「不要调用」不是开关。

**Approach:** 先给 session 加真正的 `--no-notebook`（不注册笔记本工具、base prompt 去掉笔记本段落），再删除 `output/B1_r3` 与残留 `output/B1`，用当前代码把 ①②③ 写进空的 `output/B1/`，物化 ③ 标签后跑评估。本轮不改 pseudobulk 触发、不改 strict 计分。

## Boundaries & Constraints

**Always:**
- 删除目标仅限 `output/B1_r3/` 与 `output/B1/`（若存在）。其它 `output/`（p2、p5、C2、N1 等）不动。
- 新产物只写 `output/B1/{arm1_default,arm2_rule,arm3_llm,eval}/`。禁止往旧 `run_log.jsonl` 追加。
- 同一 raw：`dataset/h5ad/SRP171040.h5ad`，`--organ root`。①② 走 `experiments/scripted_driver.py`；③ 走 `python -m annot_harness.session --no-notebook`（或等价 `run_session(..., notebook=False)`）。
- `--no-notebook` 时：schema/runtime **不含** `write_note`/`retrieve_notes`；发给 LLM 的 loop base prompt **不含**笔记本指引。`RAG_EMBEDDING=off` 可并行，但不能替代该开关。
- ③ 每个工具 timeout ≥ 14400s；`--max-turns 800`。跑完调用 `materialize_llm_labels`，再 `evaluate_cell_level` → `bootstrap_test` → `analyze_traps`。
- 解释器沿用 B1_r3：`D:\data\programe\environment\conda\win\LM\python.exe`。

**Ask First:**
- ③ 会话结束时没有 per-cluster `label_confirm`（与 r3 首次 MiniMax 空回复同类）→ 停，不要当成功评估。
- Neo4j 或 OpenAI 网关连不上 → 停。
- 删除 `output/B1_r3` 时若磁盘占用异常或路径不是该目录 → 停。

**Never:**
- 不实现 `b1-r3-followup.md`（pseudobulk 触发重设计、strict 去 confidence 惩罚）。
- 不校准 `rule_judge` 阈值；不打包 `.skill`；不改 organ 字符串匹配。
- 不把编排脚本只放在即将删除的 `output/` 里。
- 不 resume 旧 `conversation.jsonl`。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| NB_OFF | `run_session(..., notebook=False)` 或 CLI `--no-notebook` | `tool_schemas` 无笔记本工具；system 无「笔记本」指引 | 默认（不传 flag）行为与现在完全一致 |
| NB_ON | 默认 session | 仍合并 `NOTEBOOK_TOOL_SCHEMAS` + 现有 `LOOP_BASE_PROMPT` | 不得让 flag 改变默认 |
| WIPE | `output/B1_r3` 与/或 `output/B1` 存在 | 两目录删除后再 mkdir 新 `output/B1` | 路径不对则不删 |
| ARM12 | ① `--arm default`、② `--arm rule`，新 project-dir | 各有 `session_start`、`final_annotations.json`（judge 写入）、`run_log.jsonl` | 非零退出则整轮失败 |
| ARM3 | `--no-notebook` + 任务词含路径/物种/organ/`cap_exhausted_proceed` | 有 `conversation.jsonl` + pipeline 产物；无 `write_note` 调用 | 无 `label_confirm` → Ask First |
| LABELS | ③ 已有 label_confirm | `materialize_llm_labels` 写入 `step6_validate/final_annotations.json` 的 label/confidence/status | 缺 judgment → 非零 |
| EVAL | 三臂 final 均含判断层三字段 | `output/B1/eval/{evaluation_report,bootstrap_report,traps_report}.json` | 缺文件 → 非零 |

</frozen-after-approval>

## Code Map

- `annot_harness/session.py` -- `run_session` / CLI：`notebook` 参数与 `--no-notebook`
- `annot_harness/loop.py` -- `LOOP_BASE_PROMPT` 保留默认；无笔记本变体（或由 session 在关闭时裁掉笔记本段落）
- `annot_harness/tests/test_session_notebook_flag.py` -- 新建：关/开时 schema 与 prompt 断言（不调真 LLM）
- `experiments/run_b1.py` -- 清档 + ①②③ + materialize + 三评估（勿放 `output/`）
- `experiments/scripted_driver.py` -- ①② 入口，只调用
- `experiments/judges/_common.py` -- `materialize_llm_labels`，只调用
- `experiments/evaluate_cell_level.py` / `bootstrap_test.py` / `analyze_traps.py` -- 评估，只调用
- `_bmad-output/implementation-artifacts/b1-three-arm-eval.md` -- 追加本轮数字与「未修 followup」声明

## Tasks & Acceptance

**Execution:**
- [x] `annot_harness/session.py` + `annot_harness/loop.py` -- 增加 `notebook=True` 默认；`--no-notebook` 不合并笔记本工具并去掉 base prompt 中笔记本段落 -- B1 ③ 需要真实开关
- [x] `annot_harness/tests/test_session_notebook_flag.py` -- 覆盖 NB_OFF / NB_ON -- 不依赖 h5ad/LLM
- [x] `experiments/run_b1.py` -- 删除 `output/B1_r3` 与 `output/B1`，跑 ①②③（③ `--no-notebook`、timeout 14400、max_turns=800），然后 materialize + eval -- 可重复编排
- [x] `output/B1/` -- 执行 `run_b1.py` 写出三臂与 eval -- 用户要求的清档重跑
- [x] `_bmad-output/implementation-artifacts/b1-three-arm-eval.md` -- 记录本轮 accuracy 并注明未做 followup -- 避免把本轮当成已修 DE 不公后的 killer 结论

**Acceptance Criteria:**
- Given `--no-notebook`，when dump 或检查 session 的 tool_schemas，then 不含 `write_note`/`retrieve_notes`，且默认不加 flag 时仍包含二者。
- Given 清档完成，when 开始跑臂，then `output/B1_r3` 不存在，且新 log 从 seq=1 写在 `output/B1/`。
- Given ①②③ 跑完且 ③ 有 label_confirm，when 评估，then `evaluation_report.json` 含三臂 strict/relaxed/macro-F1。
- Given ③ `conversation.jsonl`，when 搜索笔记本工具名，then 无成功的 `write_note`/`retrieve_notes` 调用。
- Given `python -m pytest`，when 全量，then 既有用例 + 新 notebook flag 测试通过。

## Spec Change Log

## Design Notes

笔记本是 loop 内置能力，不是 skill 工具，所以 skill 侧没有开关。`RAG_EMBEDDING` 只管向量检索后端。B1 与 N 组的对照要求 ③ **工具列表里就没有**笔记本，不能靠模型自觉。关闭时必须同时改 prompt，否则模型仍会点名已消失的工具。

本轮数字仍可能重现 r3 的 ③ 偏低（30/34 簇 pseudobulk）。报告必须写明，不把 R1/R2 当已修复后的结论。

③ 任务词模板见 `output/B1_r3/arm3_task.txt`（路径改为 `output/B1/arm3_llm`）；编排逻辑见同目录 `run_three_arm.py`，迁到 `experiments/run_b1.py` 后再删旧树。

## Verification

**Commands:**
- `python -m pytest annot_harness/tests/test_session_notebook_flag.py annot_harness/tests/test_measure_judge_decouple.py` -- expected: 通过
- `python experiments/run_b1.py` -- expected: 退出 0；`output/B1/eval/evaluation_report.json` 存在
- `rg "write_note|retrieve_notes" output/B1/arm3_llm/conversation.jsonl` -- expected: 无 tool 调用命中（任务词里的禁止句可以有）

**Manual checks (if no CLI):**
- ③ 若中途空回复，对照 r3 的 resume 策略：**本轮不自动 resume**，先问人。

## Suggested Review Order

**笔记本真开关**

- 默认仍合并笔记本；`notebook=False` 时工具表与 prompt 一起关掉
  [`session.py:79`](../../annot_harness/session.py#L79)

- 关笔记本用的 loop 底稿，不含 write_note 指引
  [`loop.py:41`](../../annot_harness/loop.py#L41)

- resume 时重盖 SystemMessage，避免旧会话仍教模型调已删除的工具
  [`session.py:99`](../../annot_harness/session.py#L99)

- CLI `--no-notebook`；`--dump-skill` 打印 session 合并后的工具名
  [`session.py:212`](../../annot_harness/session.py#L212)

**B1 清档重跑**

- 预检解析 Neo4j JSON，假 connected 且 provenance.error 时停
  [`run_b1.py:127`](../../experiments/run_b1.py#L127)

- 只允许删 `output/B1` 与 `output/B1_r3`
  [`run_b1.py:54`](../../experiments/run_b1.py#L54)

- ③ `run_session(..., notebook=False)`，timeout 提到 14400
  [`run_b1.py:171`](../../experiments/run_b1.py#L171)

- 评估前断言 conversation 无笔记本 tool_call，再物化 label
  [`run_b1.py:196`](../../experiments/run_b1.py#L196)

**测试与数字**

- NB_OFF/ON、resume 重盖 prompt、dump-skill 合同
  [`test_session_notebook_flag.py:35`](../../annot_harness/tests/test_session_notebook_flag.py#L35)

- 本轮三臂数字；未修 followup，不是 killer 结论
  [`b1-three-arm-eval.md:70`](./b1-three-arm-eval.md#L70)

