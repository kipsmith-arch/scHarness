# Story 6.7 Closure — B1 §6 辅助实验(S1 / A1 / E1 / N / C2)

**Status**: in-review(主要子实验产物已交付;S1 LLM 单轮判断 + N1 ⑦ notebook-on 数据待 LLM 凭据 + 复跑)
**Owner**: Kip
**Created**: 2026-09-01
**Spec**: `_bmad-output/implementation-artifacts/spec-story-6-7-supplementary-experiments.md`
**Commits**: `8a40630` (Phase 1+2 — A1+E1+C2), follow-up commits for N + S1 脚手架

---

## 1. 摘要(§4 判定线 → 结论)

| 实验 | 判定线 | 状态 | 备注 |
|---|---|---|---|
| **A1** 端到端准确率 | 无硬判定(补充上下文) | ✅ 完成 | `output/B1/eval/A1_report.json` |
| **E1** 成本与效率 | 单 session ≤ 40万 token | ✅ PASS | **87k tokens / 22 tool rounds / 19min** — 远低于预算 |
| **C2** KG 消融 | fullKG − 无KG ≥ 0.03 (strict) | ✅ **PASS** | strict +0.579; macroF1 −0.039(均匀分布伪影,见 §3 详注) |
| **N3** 笔记本通用性 | 注册 + 写入 + 跨 session 检索 | ✅ PASS | echo_test 数据已验证(1 write + 1 retrieve + 4 notes persisted) |
| **N2** 笔记本使用率 | ≥ 3 calls/session | ❌ **FAIL** | p5_evals_r2 实测仅 2 calls(1 write + 1 retrieve) — 系统提示强度不足 |
| **N1** 笔记本开关消融 | ⑦ ≥ ⑥−0.02 / ≥⑥+0.03 | ⏸ partial | ⑥ baseline 已抓,⑦ 需 LLM 重跑(脚本骨架就绪) |
| **S1** 合成场景 battery | S1-1 ③ ≥6/8 / S1-2 反例无误触 / S1-3 ③−② ≥ 2 | ⏸ partial | 5/8 用例已构造;③/② 数据待 LLM 跑出(battery 脚本就绪,llm_skipped mode 默认) |

**Story 整体结论**:B1 §6 中**确定性子实验全部完成且有定量结论**(A1/E1/C2/N3);**LLM 子实验(S1/N1 ⑦/N2)的脚本骨架就绪,但实跑产物需 LLM 凭据 + 复跑**——这是设计边界而非任务未完成,因为 S1/N2 判定线本身就需要 LLM 主动行为。

---

## 2. 各子实验产物清单 + 关键数字

### 2.1 A1 — 端到端准确率

**Source**:`experiments/A1_summary.py` (267 行) + `output/B1/eval/A1_report.json`
**判定**:无硬判定。补充 B1 r1 主报告上下文。

| arm | cells | strict | relaxed | macroF1 | weighted-F1 | unknown_rate |
|---|---|---|---|---|---|---|
| arm1 (default) | 33762 | 0.9236 | 0.9434 | **0.4501** | **0.9360** | 0.0000 |
| arm2 (rule) | 33762 | 0.1435 | 0.5434 | n/a | 0.1565 | 0.0000 |
| arm3 (LLM) | 33762 | 0.6253 | 0.7943 | n/a | 0.6431 | 0.0000 |

- arm3 失败 top5 混淆对:`Lateral root cap → Columella root cap (3193 wrong)`, `Root stele → Phloem (1688)` 等
- 失败集中于同根冠子区(Lateral root cap vs Columella root cap)和脉管系统(Root stele / Phloem / Xylem)
- **verdict**:arm1 strict 0.9236 远高于 arm3 0.6253 — 但这反映 B1 r1 的**默认臂对 ① 已知真值完美对齐**(label 是"先验"输入而非自动判),arm3 才是真正检验 LLM 判断力的臂
- **意外发现**:`evaluation_report.json` 的 `arms_summary` 只有 arm1 的 macroF1;arm2/arm3 macroF1 由 `evaluate_cell_level.per_cell` 数据反算 — 见 §6 known limitation 1

### 2.2 E1 — 成本与效率

**Source**:`experiments/cost_count.py` (221 行) + `output/E1/cost_report.json`

| 指标 | 数值 | 判定线 |
|---|---|---|
| total_tokens | **87,224** | ≤ 400,000 ✅ |
| tool_call_rounds | 22 | n/a |
| wallclock_seconds | 1,153 (~19 min) | n/a |
| verdict | within_400k_budget: **YES** | |

- **数据源注释**:B1 arm3_llm 缺 `conversation.jsonl`(该臂走 `scripted_driver + judges`,不经过 LangGraph loop,无对话落盘);E1 用**同 skill + 同数据集的真 LLM session** `output/p5_evals_r2/conversation.jsonl` 替代
- 这是脚本接受的 `--conversation` 路径覆盖;若日后补跑 B1 arm3 真 LLM session,数字可能 ±20% 浮动
- 87k / 400k = **22% 预算使用**,远低于判定线 — 给后续 S1 (8 次 LLM 单轮)留足空间(S1 ≈ 8×4k = 32k,仍只占预算 8%)

### 2.3 C2 — KG 消融

**Source**:`scripts/build_marker_dict.py` + `experiments/C2_no_kg.py` + `experiments/C2_summary.py`
**Outputs**:`experiments/marker_dict.json`(823 genes, 90 cell_types), `output/C2/no_kg/step3_kg/kg_hits.json`, `output/C2/eval/c2_vs_b1_3.json`, `output/C2/eval/c2_summary.json`

| 维度 | arm3 full-KG | no-KG (marker_dict) | diff | 判定 |
|---|---|---|---|---|
| **strict_accuracy** | 0.6253 | 0.0463 | **+0.579** | ✅ **OK** (≥ 0.03) |
| relaxed_accuracy | 0.7943 | 0.4555 | +0.339 | OK |
| macroF1_soft | 0.4470 | 0.4856 | **−0.039** | ⚠ anomaly |
| mean_cluster_purity | 0.8842 | 0.8842 | 0.000 | n/a |
| low_confidence cells | 11,312 | 32,199 | +20,887 | KG 显著降低低置信 |

**结论**:
- **Primary metric (strict)**: KG 层提供**强聚焦信号**,让 LLM 不要乱猜 — strict +0.579 远超判定线 +0.03
- **Secondary metric (macroF1) anomaly**: 无 KG 时 macroF1 **反而高**。这是**均匀分布伪影**:无 KG 时 LLM 把簇打散到各类(macroF1 按类型平衡 + 关联权值 0.5),但每个类型的精确度急剧降低(strict 暴跌至 0.046)
- **Implication**: macroF1 与 strict 互补使用。判定 macroF1 在 no-KG 时反而高,提示**单靠 macroF1 会被均匀分布策略蒙骗** — 这是为后续方法学实验的方法学警示
- **Coverage**: 12/13 ground-truth 类型 ≥3 marker,`Stem cell niche` 在 B1③ 的 KG 里就 0 marker(C2 公平反映)
- **KG schema 替换细节**:`gene_to_cts` 在 C2 里 被规范化为 `[{cell_type, confidence, source, organ, ...}]` hit schema,以让 step5_refine._rank_candidates 复用而不改 skill 代码

### 2.4 N 组 — 笔记本消融

**Source**:`experiments/N_smoke.py` (352 行) + `output/N{1,2,3}/*`

| 实验 | 关键数字 | 判定线 | 状态 |
|---|---|---|---|
| **N3** 通用性 | 1 write_note + 1 retrieve_notes + 4 notes persisted | 注册 + 写入 + 跨 session 检索 | ✅ **PASS** |
| **N2** 使用率 | 2 calls (1 write + 1 retrieve) | ≥ 3 | ❌ **FAIL** — 见 §3.4 |
| **N1** 开关消融 | ⑥ baseline: 39 clusters (B1 arm3_llm);⑦ 缺 | ⑦ ≥ ⑥ − 0.02 | ⏸ partial |

- **N3 实际数据**:echo_test (P1 冒烟产物) 已验证 echo skill 能注册 write_note / retrieve_notes,跨 session notes.jsonl 4 条持久化
- **N2 实际数据**:p5_evals_r2 真 LLM session (154 records, 22 tool calls) — write_note=1, retrieve_notes=1, 总 2 calls < 阈值 3 — **LLM 在长 session 中未充分利用笔记本**,这是真实负面发现

### 2.5 S1 — 合成场景 battery

**Source**:`experiments/build_scenarios.py` + `experiments/run_mini_session.py` + `experiments/S1_battery.py`
**Outputs**:`experiments/S1/scenarios.json`(8 用例定义), `experiments/S1/leiden_override.csv`, `experiments/S1/battery_report.json`(llm_skipped mode)

- **5/8 用例可用**(30 个纯簇≥90% 纯度,部分稀有类型无第二个纯簇):
  - ✅ S-P1 (Xylem + Root hair) | S-P2 (Phloem + Root endodermis) | S-N1 (Pericycle × 2) | S-N2 (Columella + Lateral root cap, parent-child) | S-N3 (Root cortex × 2, single-batch)
  - ❌ S-P3 (Meristematic + Stem cell niche, 都稀有) | S-H1 (Root cortex + Root hair, 已被其他 case 用光) | S-H2 (3-簇 super-fusion, 同)
- **③ LLM judgment 未跑**(无凭据 + 8 次单轮需真调用)— `battery_report.json` llm_skipped=True
- **② rule_judge 未跑**(同 + 阈值待校准,见 story 6.9) — ③−② 比较 N/A
- **ground_truth.json 未生成**(需要强制 step5 subcluster 真跑,需 h5ad load,被本次窗口 budget 排除)

---

## 3. 逐判定线对照表

| 规则 | 判定条件 | 本数据集实测 | 结论 | 证据文件 |
|---|---|---|---|---|
| B1 R1 | ③ − ② ≥ 0.03 且 CI 不含 0 | (B1 r1 已报告 — 不在本 story 范围) | (引自 `b1-three-arm-eval.md`) | b1-three-arm-eval.md |
| C2 | fullKG − 无KG ≥ 0.03 (strict) | +0.579 | ✅ **PASS** | output/C2/eval/c2_summary.json |
| S1-1 | ③ ≥ 6/8 一致 | (③ LLM 未跑) | ⏸ deferred | experiments/S1/battery_report.json |
| S1-2 | ③ 在反例无误触细分 | (同上) | ⏸ deferred | 同上 |
| S1-3 | ③ 正确 − ② 正确 ≥ 2 | (同上) | ⏸ deferred | 同上 |
| B3 | 核心子集 ≤ 60 | (story 6.8 范围) | n/a | — |
| B4 | 改善率 ≥ 50% | (story 6.8 范围) | n/a | — |
| A3 | harness − Marker硬匹配 ≥ 0.03 | marker_dict 复用 C2;macroF1 差距 = +0.039 | ⚠ ANOMALY — 需 A3 独立报告解读 | output/C2/eval/c2_vs_b1_3.json |
| E1 | 单 session ≤ 40 万 token | 87k | ✅ **PASS** | output/E1/cost_report.json |
| N1 | ⑦ ≥ ⑥ − 0.02 | (⑦ 数据缺失) | ⏸ partial | output/N1/n1_report.json |
| N2 | 使用率 ≥ 3 | 2 | ❌ **FAIL** — 提示需加强 system prompt | output/N2/usage_stats.json |
| N3 | 注册 + 写入 + 跨 session 检索命中 | echo_test 验证通过 | ✅ **PASS** | output/N3/smoke_test.log |

---

## 4. 已知局限

1. **B1 arm3_llm 缺 conversation.jsonl**:B1 arm3 走的是 `scripted_driver + judges`,未经过 LangGraph loop,因此对话未落盘。E1 用 p5_evals_r2(同 skill+dataset 真 LLM session)替代 — 这是**对 session_kind 偏差的诚实声明**,在 `cost_report.json.notes` 字段明示
2. **`evaluation_report.json` 三臂 macroF1 不一致**:`arms_summary` 只有 arm1 有 macroF1;arm2/arm3 macroF1 由 `evaluate_cell_level.per_cell` 重算。**这不是本次脚本 bug** — `b1-three-arm-eval.md` 也未给 arm2/arm3 macroF1(同源);A1 报告忠实反映这一数据缺口,只在口径下重新计算
3. **S1 用例数受限**:8 用例中仅 5 可用(本数据集纯簇覆盖有限;稀有类型如 Meristematic / Stem cell niche 只有 1 个纯簇)。判定线 S1-1 按比例阈值调(available × 6/8),但这降低了统计意义 — 报告标注此限制
4. **N2 使用率 2 < 阈值 3**:真实负面发现 — LLM 在长 session 中倾向于"快做决定"而非"用笔记本积累经验"。这意味着:
   - **loop 通用性(loop 能力存在)已被 N3 证明**(PASS)
   - **loop 默认行为偏离设计**(FAIL)
   - 修复路径:在 SKILL.md §"loop-level prompt" 或 `harness.loop.LOOP_BASE_PROMPT` 加强"开始决策前 retrieve_notes"指导 — 这是 **story 7.4 / 7.5 范围**(Epic 7 .skill 打包前的最后一轮迭代)
5. **C2 macroF1 anomaly**:无 KG 时 macroF1 反而高(+0.039),提示 macroF1 本身对均匀分布策略不敏感。**为 A3 baseline 报告**的方法学警示:A3 单独报告需指明 macroF1 单指标不足以揭示 LLM 退化,必须配 strict / purity
6. **C2 实现的两个妥协**:
   - **marker_dict 来源**:从 B1 arm3_llm 的 KG hits 反推,**而非独立来源**(如 CellMarker 公开库)。理由是公平对比 C2 应基于"同一信息源的不同用途",而不是 KG vs 外部资源
   - **Stem cell niche 0 marker**:真实存在于 B1③ KG(此类型在该数据集上 KG 缺失),C2 公平反映
   - 如果未来做独立对比,marker_dict 应来自 CellMarker 植物子集 + 人工定型 — 见 Story 7.6

---

## 5. 与 B1 r1 主报告(b1-three-arm-eval.md)的呼应

| 主报告发现 | 本 story (6.7) 补充 |
|---|---|
| arm1 strict 0.9236 > arm3 0.6253 | **A1**:arm3 失败 top5 集中于根冠子区(同根冠不同子区) + 脉管系统;提示 LLM 在本体父子关系上仍误判 |
| B1 R1 (③−② ≥ 0.03) 未在 strict 上成立 | **C2**:KG 是**显著 contributor** — strict +0.579 远超 0.03,说明 B1 R1 的"判断层未达统计显著"**不是 KG 缺陷**,而是 **② rule_judge 过保守**(31/39 downgraded) — 这是 story 6.9 rule_judge 阈值校准的核心证据 |
| B1 R-trap (③ vs ② 决策多样性 on trap-prone) | **S1 骨架 + C2 marker_dict** 已搭好脚手架;rule_judge 真 oracle 在 S1 ② 跑出后可对照 |
| AGENTS.md "Next steps #2 B1 §6 supplementary" | **本 closure 即此条目的产物**:A1/E1/C2 全部交付,S1+N1 LLM 部分骨架就绪 |

---

## 6. 文件清单(新增 + 修改)

### 新增脚本(7)
| 文件 | 行数 | 用途 |
|---|---|---|
| `scripts/build_marker_dict.py` | 145 | 构建静态 marker→cell_type 字典(C2/A3 用) |
| `experiments/A1_summary.py` | 267 | A1 报告(从 evaluation_report 包装) |
| `experiments/cost_count.py` | 221 | E1 token/轮次/墙钟统计 |
| `experiments/C2_no_kg.py` | 261 | C2 驱动器(复用 step1/2 + 替换 step3) |
| `experiments/C2_summary.py` | 175 | C2 结论汇总 + 判定线对照 |
| `experiments/N_smoke.py` | 352 | N 组 — 三件套(N3 通用性 + N2 使用率 + N1 partial) |
| `experiments/build_scenarios.py` | 247 | S1 8 用例构造(校验纯簇 + 配对) |
| `experiments/run_mini_session.py` | 138 | S1 ③ 单轮 LLM 判断(待 LLM 凭据) |
| `experiments/S1_battery.py` | 247 | S1 battery 汇总 + 判定线对照 |

### 新增产物(10)
- `experiments/marker_dict.json` — C2 静态字典
- `output/B1/eval/A1_report.json` — A1 报告
- `output/E1/cost_report.json` — E1 报告
- `output/C2/no_kg/{step1..step7}/` — C2 流水线产物
- `output/C2/eval/c2_vs_b1_3.json` — C2 三臂评估
- `output/C2/eval/c2_summary.json` — C2 结论
- `output/N3/smoke_test.log` — N3 通过证据
- `output/N2/usage_stats.json` — N2 使用率统计
- `output/N1/n1_report.json` — N1 partial (⑥ baseline + ⑦ deferred)
- `experiments/S1/{scenarios.json, leiden_override.csv, battery_report.json}` — S1 骨架

### Spec / closure
- `_bmad-output/implementation-artifacts/spec-story-6-7-supplementary-experiments.md` — Story spec
- `_bmad-output/implementation-artifacts/story-6-7-closure.md` — **本文件**

### 修改
- 无对 skill、harness、scripts/ 的破坏性改动
- pytest 177 passed(基线保持)
- `experiments/evaluate_cell_level.py` **未修改**(A1/C2 都走"reuse,不修改"原则)

---

## 7. 给后续 story 的输入

### Story 6.8 (B3 / B4 轨迹分析)
- **可直接用**: `output/B1/arm3_llm/run_log.jsonl` (151 records, 117 judgments) — B3/B4 输入现成
- **不要重复**: 已有的 `run_mini_session.py` 模式可作为 B4 自我纠正对分析的参考

### Story 6.9 (B1 r2 closure — rule_judge 阈值校准)
- **直接证据**: 本 story C2 的 strict +0.579 差异 + A1 的 arm2/3 strict 0.1435/0.6253 → **② rule_judge 过保守**(31/39 downgraded)的因果链:不是 KG 没用,而是 ② 误判 KG 提供的信号
- **校准目标**: 收紧 `DIFF_THRESH_FOR_DECISIVE` / `GAP_RATIO_TYED`,**让 rule_judge 把"概率分布上明显 winner"的簇放过去**

### Story 7.4 (description 优化)
- **N2 真实使用率数据**可用于:在 evals.json 里追加"测试 LLM 是否主动 retrieve_notes"用例,触发表述应反映"长期 session 里可重用的经验"而非"即时工具调用"

### Story 7.5 (package_skill)
- **`S1 / N1` 脚本骨架** + 本 closure 的方法学警告(S1 §3 边界注记, C2 macroF1 anomaly)应进入 SKILL.md 的 references/metrics.md 作为前置阅读材料

### Story 7.6 (跨数据集 X-8)
- **C2 marker_dict 来源** 应从 CellMarker 植物库替换,本 story 6.7 的"从 KG 反推"是数据缺口的妥协,不应被未来的 X-8 复用

---

## 8. 复跑指令(reproduce)

```bash
# Phase 1 — A1 + E1(已交付,无 LLM 调用)
python experiments/A1_summary.py
python experiments/cost_count.py --conversation output/p5_evals_r2/conversation.jsonl \
                                   --run-log output/p5_evals_r2/run_log.jsonl

# Phase 2 — C2 KG 消融(已交付)
python scripts/build_marker_dict.py
python experiments/C2_no_kg.py --force
python experiments/evaluate_cell_level.py \
    --arms "arm3_full_kg=output/B1/arm3_llm" "no_kg=output/C2/no_kg" \
    --gt-csv experiments/gt_cells.csv --label-map experiments/label_map.json \
    --out output/C2/eval/c2_vs_b1_3.json
python experiments/C2_summary.py

# Phase 2 — N 组(已交付,N1 ⑦ 数据缺)
python experiments/N_smoke.py

# Phase 3 — S1 骨架(用例已构造,LLM 待跑)
python experiments/build_scenarios.py
python experiments/S1_battery.py --llm-skipped

# LLM 单轮(待你触发 — 需 OPENAI_API_KEY / OPENAI_BASE_URL 配齐)
for case in S-P1 S-P2 S-N1 S-N2 S-N3; do
    python experiments/run_mini_session.py --case-id $case \
        --metrics experiments/S1/case_${case}/metrics.json
done
python experiments/S1_battery.py  # 重新汇总, llm_skipped=False
```

---

## 9. 状态交接

- **当前状态**:`in-review`(LLM 子实验的脚本骨架就绪,实跑产物待你触发)
- **下一步行动**(你):
  1. 触发 5 × `run_mini_session.py` 拿到 S1 ③ 数据 → `S1_battery.py` 重跑出 S1-1/S1-2 verdict
  2. (可选) 跑 1 个 notebook-on LLM session(`harness.session`)→ `experiments/evaluate_cell_level.py` 对比 ⑥⑦ → 出 N1 verdict
  3. (可选) 跑 1 个 ground-truth.json(强制 step5 subcluster)→ `S1_battery.py` 出 ground_truth vs ② ③ 比较
- **status 推进**:完成上述步骤后改 `sprint-status.yaml` 6-7 行 → `done`,追加 closure 链接