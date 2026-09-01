# Story 6.7 — B1 §6 辅助实验(S1 / A1 / E1 / N / C2)

**Status**: ready-for-dev  
**Owner**: Kip  
**Created**: 2026-09-01  
**Depends on**: Epic 4 (gt_cells + label_map), Epic 5 (skill), Epic 6 story 6.1–6.6 (B1 r1 main eval done)

---

## Goal

完成 B1 §6 全部辅助实验,对照 `experiment_implementation.md` §4 判定规则总表逐条形成结论,与 B1 r1 主报告互补。

**User-facing goal**(single):"完成 story 6.7"= 跑出 5 类辅助实验并产出 closure 报告,使其可单独 shippable 为 P6 阶段性收尾文档。

---

## Background

B1 r1 主评估(Story 6.6)已交付,见 `output/B1/eval/` 与 `_bmad-output/implementation-artifacts/b1-three-arm-eval.md`。本 story 补齐 §6 辅助实验。

- **AGENTS.md 状态基线**:"B1 §6 supplementary (6.7) next up"
- **预注册判定规则**:`design/experiment_implementation.md` §4
- **本数据集**:Arabidopsis thaliana root,33,956 细胞,h5ad 路径 `dataset/h5ad/SRP171040.h5ad`(注:gitignored,需现场存在)

---

## 范围(scope)

### 必须交付的 5 子实验

| # | 实验 | 设计文档 § | 预注册判定 | 主要新增产物 |
|---|---|---|---|---|
| 1 | **A1** 端到端准确率 | §3.5 | 无硬判定(补充上下文) | `output/B1/eval/A1_report.json` |
| 2 | **E1** 成本与效率 | §3.7 | 单 session ≤40万 token | `output/E1/cost_report.json` |
| 3 | **C2** KG 消融 | §3.8 | fullKG − 无KG ≥ 0.03 | `experiments/marker_dict.json` + `output/C2/` 全部 + `output/C2/eval/c2_vs_b1_3.json` |
| 4 | **S1** 合成场景 battery | §3.2 | S1-1 ③≥6/8 一致 / S1-2 反例不误触 / S1-3 ③−②≥2 | `experiments/S1/{scenarios.json, leiden_override.csv, case_*/, ground_truth.json, battery_report.json}` |
| 5 | **N 组** 笔记本消融 | §3.10–3.12 | ⑦ ≥ ⑥−0.02 / 使用率 ≥3 / N3 通过 | `output/N{1,2,3}/` |

### 范围外(out of scope,延后到 6.8)

- **B3 / B4 轨迹分析**:本 story 不做,留给 6.8(虽然 B3 输入只依赖现有 run_log.jsonl,易顺手做;但拆 story 边界清晰为先)。

---

## 任务清单(task list,按依赖排序)

### Phase 1 — 轻量、零新代码、纯分析(可立即并行)

- [ ] **T1.1 — A1 报告生成**
  - 路径:`experiments/evaluate_cell_level.py` 已经在 `output/B1/eval/evaluation_report.json` 算出 arm1/2/3 三臂细胞级评估
  - 操作:写一个 `experiments/A1_summary.py` 读取该 JSON,按 `design/experiment_implementation.md` §3.5 的"结果解读"要求生成 `output/B1/eval/A1_report.json`,包含:整体 accuracy / macro-F1 / weighted-F1 / 12×12 confusion matrix / per-type recall+precision / 失败模式 top 5 混淆对 / unknown 集中区
  - 验收:报告 JSON 存在 + 与 evaluation_report 数据自洽(同 cell 数、同 strict/relaxed 数字)
  - 复杂度:低(< 200 行)

- [ ] **T1.2 — E1 成本与效率**
  - 路径:输入是 `output/B1/arm3_llm/conversation.jsonl` + `run_log.jsonl`
  - 操作:写 `experiments/cost_count.py`,统计:
    1. 用 `tiktoken` 对 conversation.jsonl 全量 messages 计数(假设模型 cl100k_base,与本项目 gpt-4o 一致)
    2. tool-call 轮次 = 统计 tool_calls 出现次数
    3. 墙钟 = run_log 首条 `session_start.ts` 与末条 `session_end.ts` 差
    4. 每决策点平均轮次 = 按 decision_point 聚合
  - 输出:`output/E1/cost_report.json` + 一行 stdout 汇总
  - 验收:`cost_report.json` 存在 + 与预注册判定线对照(单 session ≤ 40万 token)
  - 复杂度:低(< 150 行)

### Phase 2 — 中量、新增脚本(等 Phase 1 完成后并行)

- [ ] **T2.1 — C2 KG 消融**
  - 子任务:
    - **T2.1.a** `scripts/build_marker_dict.py`:读 `skills/cell-annotation/references/kg-schema.md` + 已知的 `name_map4Arabidopsis_thaliana_symbol.json`(gitignored,本地有)+ CellMarker 公开植物 marker 子集 → 输出 `experiments/marker_dict.json`,格式 `{gene_symbol: [cell_type_1, cell_type_2, ...]}`
      - 至少覆盖 `label_map.json` 中 12 个 ground-truth 类型各 ≥ 3 个 marker 基因
      - 单元自检:对每个 ground-truth 类型,check 它的 marker 至少有 1 个出现在 root 数据集的 var_names 里(否则该类型被字典漏掉)
    - **T2.1.b** `experiments/run_no_kg_arm.py`:复用 `experiments/scripted_driver.py` 的 DAG,但把 `step3_kg.query` 这一步**替换为读 marker_dict 取 top-K 候选**(无 confidence、无 ancestor),输出 `output/C2/no_kg/step3_kg_no_kg.json` + 继续走完 step4-7
    - **T2.1.c** 跑 `experiments/evaluate_cell_level.py --arms arm3_full_kg=output/B1/arm3_llm no_kg=output/C2/no_kg --out output/C2/eval/c2_vs_b1_3.json`
    - **T2.1.d** `experiments/C2_summary.py`:对照 §3.8 判定线 `fullKG − 无KG ≥ 0.03`,输出 `output/C2/eval/c2_summary.json`
  - 验收:
    - `marker_dict.json` 存在 + 含 12 个类型 × ≥3 marker/类型
    - `output/C2/no_kg/step6_validate/final_annotations.json` 存在
    - `c2_vs_b1_3.json` 存在 + macro_f1_soft 差(③ − no_kg)被明确报告
  - 复杂度:中(预计 300-500 行)

- [ ] **T2.2 — N 组(笔记本消融与通用性)**
  - 子任务:
    - **T2.2.a — N3 笔记本通用性冒烟**
      - 用 `skills/echo`(已有,N3 P1 已冒烟过,验证仍有效;若失效则修复 echo skill)
      - 操作:写 `experiments/N3_smoke.py`,注册 notebook 工具,跑一个 echo session,write_note → 跨 session → retrieve_notes 命中;输出 `output/N3/smoke_test.log`
      - 验收:写入 + 跨 session 检索命中各 ≥ 1 次
    - **T2.2.b — N2 笔记本使用分析**
      - 操作:写 `experiments/N2_usage.py`,读 B1③ conversation.jsonl(⑥ no-notebook)的 tool_calls,统计 write_note / retrieve_notes 频次
      - 但 **⑥ = B1③ 本身就禁用了 notebook**(参考 experiment_implementation §3.10)— 所以 N2 没有 notebook-on 数据可分析
      - 解决:在 N2 步骤里**额外跑一个 notebook-on session**(用 cell-annotation skill,不换 dataset,只开 notebook 工具),输出 `output/N2/notebook_on/conversation.jsonl` + `experiments/N2_usage.py` 统计
      - 验收:使用率统计存在 + 与判定线对照
    - **T2.2.c — N1 笔记本开关消融**
      - ⑥ = B1③ 已跑(复用,无需新跑)
      - ⑦ 需要再跑 1 次 notebook-on(已由 T2.2.b 跑出),最终 ⑦×2 = (B1③ 改造版)+ T2.2.b 输出,evaluate_cell_level 对比
      - 操作:写 `experiments/N1_compare.py`,对比 ⑥ (B1③) vs ⑦ (T2.2.b 输出),输出 `output/N1/n1_report.json`
  - 验收:N1/N2/N3 三份报告齐全
  - 复杂度:中(预计 200-300 行 + 1 次额外 LLM 跑 notebook-on)

### Phase 3 — S1 合成场景 + 最终合并

- [ ] **T3.1 — S1 合成场景 battery**
  - 子任务:
    - **T3.1.a** `experiments/build_scenarios.py`:校验纯簇(占比 ≥ 90%,用 gt_cells.csv + B1③ obs_snapshot.csv)→ 选 8 用例覆盖 6 陷阱 + 正反例 + 难度梯度(§3.2 表格) → 输出 `experiments/S1/scenarios.json` + `leiden_override.csv`
    - **T3.1.b** 对每用例跑**确定性 op**:重跑 step2_markers / step3_kg / step4_judge / step5_refine,生成该决策点的指标快照,落到 `experiments/S1/case_{id}/metrics.json`
    - **T3.1.c** `experiments/run_mini_session.py`:把指标 + 决策点 + skill 决策指导喂 LLM,**单轮判断**(temperature=0),不跑整条 loop;产出 `experiments/S1/case_{id}/llm_judgment.json` × 8
    - **T3.1.d** `experiments/judge_case.py`:对每用例跑 ② (rule_judge) + ③ (T3.1.c LLM) + 确定性真值(强制 step5 subcluster,看子簇能否分离出两个原始真值类型)
    - **T3.1.e** `experiments/S1_battery_report.py`:汇总 8 用例判定表 + 命中率 + 陷阱专项 + 边界注记(报告须明示"构造伪影:合并产生人为干净的双 lobe,morans_i 天然偏高 — S1 测的是能力,不是真实弱信号的检出率")
  - 验收:`battery_report.json` 存在 + 对照 S1-1 / S1-2 / S1-3 判定线给出明确结论
  - 复杂度:重(预计 500-800 行 + 8 次 LLM 调用)

- [ ] **T3.2 — 最终 closure 报告**
  - 路径:`_bmad-output/implementation-artifacts/story-6-7-closure.md`
  - 内容模板(对应 b1-three-arm-eval.md):
    - §1 摘要(全部判定线 + 是否成立)
    - §2 各子实验产物清单 + 关键数字
    - §3 逐判定线对照表(从 §4 预注册判定规则挑出本次涉及的)
    - §4 已知局限(单数据集 / N=2 / S1 构造伪影等)
    - §5 与 B1 r1 主报告(b1-three-arm-eval.md)的呼应
  - 验收:文件存在 + §3 表格每行有明确"成立 / 不成立 / 无结论"标签

---

## 验收标准(AC,Given/When/Then)

### AC-1 — A1 报告与 B1 r1 数据自洽
- **Given** `output/B1/eval/evaluation_report.json` 已经存在(由 B1 r1 跑出,33,956 cells,12 clusters covered)
- **When** 运行 `python experiments/A1_summary.py`
- **Then** `output/B1/eval/A1_report.json` 存在,包含 arm1/2/3 三臂的 accuracy / macro-F1 / weighted-F1 / 12×12 confusion / 失败模式 top 5,且 cell 数、strict/relaxed 数字与 evaluation_report.json 完全一致(无重新计算,纯 reuse)

### AC-2 — E1 token 计数与判定线对照
- **Given** `output/B1/arm3_llm/conversation.jsonl` 存在 + 含 ≥ 1 条 session_start 与 ≥ 1 条 session_end
- **When** 运行 `python experiments/cost_count.py --conversation output/B1/arm3_llm/conversation.jsonl --run-log output/B1/arm3_llm/run_log.jsonl`
- **Then** `output/E1/cost_report.json` 存在,字段含 total_tokens / tool_call_rounds / wallclock_seconds / per_decision_point 平均轮次;stdout 一行输出"≤400k? yes/no: N tokens"

### AC-3 — C2 无 KG 臂跑通且判定线对照
- **Given** `experiments/marker_dict.json` 由 `scripts/build_marker_dict.py` 生成,覆盖 label_map 12 类型 × ≥3 marker/类型
- **When** 运行 `python experiments/run_no_kg_arm.py --project-dir output/C2/no_kg`
- **Then** `output/C2/no_kg/step6_validate/final_annotations.json` 存在;`c2_vs_b1_3.json` 存在;`fullKG_macro_f1 − no_kg_macro_f1` 字段明确(可为负)

### AC-4 — S1 8 用例 battery 跑通
- **Given** 8 用例由 `experiments/build_scenarios.py` 自动选取(纯度 ≥90%)
- **When** 跑 `experiments/S1_battery.py` 整套(含 ③ LLM 调用 × 8)
- **Then** `experiments/S1/battery_report.json` 存在,8 用例判定表全填;并对照 S1-1/S1-2/S1-3 输出明确"成立/不成立"

### AC-5 — N1/N2/N3 三件套齐全
- **Given** echo skill 可加载、notebook 工具可注册
- **When** 跑 `experiments/N3_smoke.py` + `experiments/N2_usage.py` + `experiments/N1_compare.py`
- **Then** `output/N3/smoke_test.log` 通过;`output/N2/usage_stats.json` 存在;`output/N1/n1_report.json` 存在且含 macro_f1 ⑥/⑦ 对比

### AC-6 — 最终 closure 报告
- **Given** 上述 5 子实验产物齐全
- **When** 写 `_bmad-output/implementation-artifacts/story-6-7-closure.md`
- **Then** 文件存在 + §3 判定线对照表每行有明确标签(成立/不成立/无结论)+ §5 引用 `b1-three-arm-eval.md`

---

## 已知风险与缓解

1. **Neo4j 不可用** — C2 / S1 都依赖 `step3_kg.py query`,若 Neo4j down:
   - C2 不受影响(C2 就是要绕开 KG)
   - S1 部分受影响:`build_scenarios.py` 不依赖 KG,但 case 的 `step3_kg` 会失败 → 降级为只跑 ②③ 在 markers-only 决策点(refine_effect 之前的决策点仍可跑)
2. **数据集 h5ad 不在** — Story 6.7 假定 P2 已跑通,B1 r1 产物已存在;若 h5ad 不在,B1 r1 已无产物可复用,story 6.7 整体降级为"产物缺失"模式
3. **LLM API quota** — S1 要 8 次单轮 LLM 调用 + N2 notebook-on 1 次 session,共约 9 次额外 LLM 调用;若 quota 不够,S1 部分用例降级
4. **agent judgment 一致性** — S1 用 `run_mini_session.py` 单轮判断,需 temperature=0 才能复现;若设错,S1 报告"案例间不可比"

---

## 任务分配建议(给实施者)

由于范围横跨 5 子实验 + 多文件编辑,建议**按 Phase 并行**:
- Phase 1 (A1 + E1):纯分析,可一个 worker 内串行(共享 evaluate_cell_level 输出读取模板)
- Phase 2 (C2 + N):可两个 worker 并行,但需注意 dataset lock(同一 h5ad 不能并行 load)
- Phase 3 (S1 + closure):S1 独立可单独 worker,closure 由父代理串行合成

父代理应在每个 Phase 结束后用 `python -m pytest` 验证 harness 不被破坏(89 测试基线)。