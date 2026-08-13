# B1 三臂决策对比 — 评估报告

> 日期:2026-08-12 · 评估框架: `experiments/{evaluate_cell_level,bootstrap_test,analyze_traps}.py`
> 产物: `output/B1/eval/{evaluation_report,bootstrap_report,traps_report}.json`

## 1. 三臂 accuracy (cell-level, n=33,762)

| 臂 | strict | relaxed | macro-F1 | purity | low_conf cells |
|---|---|---|---|---|---|
| ① default | **0.9236** | **0.9434** | 0.4501 | 0.8842 | 0 |
| ② rule | 0.1435 | 0.5434 | 0.4419 | 0.8842 | 28918 |
| ③ LLM | 0.6253 | 0.7943 | 0.4470 | 0.8842 | 11312 |

strict 是核心判据:`① > ③ >> ②`(rule 过度降级)。macro-F1 几乎相同(per-class 加权拉平)。

## 2. Bootstrap 判据(B1 §3.1 R1-R4)

| 对比 | Δ strict (95% CI) | Δ relaxed (CI) | Δ macroF1 (CI) | 判据 |
|---|---|---|---|---|
| ③ LLM → ② rule | -0.48 [-0.69, -0.28] | -0.25 [-0.37, -0.13] | -0.01 [-0.14, +0.11] | **R4** (macroF1 上③ ≈ ②;strict 上③大胜②) |
| ③ LLM → ① default | +0.29 [+0.09, +0.50] | +0.15 [+0.02, +0.28] | +0.00 [-0.13, +0.12] | macroF1 R3;strict R1(①胜) |
| ② rule → ① default | +0.78 [+0.62, +0.91] | +0.40 [+0.29, +0.49] | +0.01 [-0.12, +0.13] | macroF1 R3;strict R1(①胜) |

**核心结论**:
- B1 R1(macroF1 ≥0.03) **不满足**——三臂 macroF1 95% CI 全部跨 0
- 但 strict/relaxed 维度上 ① >> ③ >> ②,差异显著
- **解释**: macroF1 per-class 加权把 12 类的差异"拉平"了;strict(逐细胞对错)更敏感
- 当前 rule_judge 过度严苛(31/39 downgraded),LLM 适中(10/39)

## 3. R-trap 决策多样性(arm3 vs arm2 on trap-prone)

| decision_point | arm1 决策 | arm2 决策 | arm3 决策 |
|---|---|---|---|
| candidate_gap | first_decisive ×39 | first_decisive 18 / parent_child 11 / synonym 10 | parent_child 13 / true 13 / first 11 / synonym 2 |
| candidate_disambiguate | ambiguous_true ×39 | parent_child 11 / synonym 10 | parent_child 10 / true 9 / synonym 2 |
| refine_effect | refine_skipped ×39 | refine_skipped ×39 | refine_effective 10 / autocorr_low 3 / no_judgment 26 |
| label_confirm | label_confirmed ×39 | label_downgraded 31 / confirmed 8 | label_confirmed 29 / downgraded 10 |

**R-trap (③ 决策多样性 > ② on trap-prone): 2/4**
- ③ LLM 决策熵在 candidate_gap / disambiguate / label_confirm 上更高
- ② rule 在多个 dp 上几乎单一决策(refine_skipped ×39)

## 4. 与 B1 §3.1 预期判据的对照

| 规则 | 状态 |
|---|---|
| R1 (③ macroF1 − ② ≥ 0.03, CI 不含 0) | ❌ 0.01 [-0.14, +0.11] inconclusive |
| R2 (② − ① ≥ 0.03) | ❌ 0.01 [-0.12, +0.13] inconclusive |
| R3 (③ ≈ ② within ±0.01) | ✅ macroF1 触发 R3 |
| R4 (③ < ② by >0.03, REVERSED) | ⚠️ macroF1 不触发,但 strict 触发 ② < ③(语义反转) |
| Trap (③ 在 ≥4/6 陷阱上 > ②) | ⚠️ 2/4(metric 简化版);R-trap 框架已落地 |

## 5. 结论与建议

1. **当前 rule_judge 过度保守** — 31/39 簇被判 label_downgraded(陷阱4 pct1=pct2 都高的判定过宽),需要调阈值或策略。
2. **LLM 适中** — 10/39 downgraded,strict 0.63 远高于 rule 的 0.14。
3. **macro-F1 不可区分** — B1 §3.1 设计的 R1 判据在当前数据上失效;建议改用 strict/relaxed 作为更敏感的判据。
4. **R-trap 决策多样性 = 2/4** — LLM 在 cluster-level dp 上比 rule 更能分情况(rule 几乎是 if-then 硬规则),支持 B1 核心假设的弱版本。
5. **后续工作**: 重调 rule 阈值 + 用 S1 合成场景做受控验证 + 改进 evaluate 让 strict 更鲁棒(避免 confidence='low' 的0.5 权重过度惩罚)。

## 6. 文件清单

- `experiments/evaluate_cell_level.py` — 多臂 cell-level 评估(读 run_log label_confirm 决策覆盖 final_annotations.confidence)
- `experiments/bootstrap_test.py` — cluster-aware bootstrap(strict / relaxed / macroF1, 1000 draws, percentile CI)
- `experiments/analyze_traps.py` — 陷阱决策多样性分析(heuristic trap detection + decision entropy 比较)
- `output/B1/eval/evaluation_report.json` — 主报告
- `output/B1/eval/bootstrap_report.json` — bootstrap 报告
- `output/B1/eval/traps_report.json` — 陷阱分析报告
