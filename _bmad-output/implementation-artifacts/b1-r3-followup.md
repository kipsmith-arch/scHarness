# B1_r3 下一步：de_method 交给 LLM + strict 与把握拆开

> 日期: 2026-09-04（§1 方案 2026-09-07 改定）
> 状态: §1 SKILL 已改，2026-09-07 全量重跑验证通过；§2 计分已改并随本轮重评
> 证据: `output/B1_r3/eval/`（已删，数字见当时报告）；2026-09-07 复现 `output/B1/`
> 前序: `spec-measure-judge-decouple.md`（测量/判断解耦后的首轮三臂）

`deferred-work.md` 只收「非阻塞、无排期」的遗留。下面两项会改变 B1 的可比性和 headline 数字，下一轮实现/重跑以本文为准。

## 背景（B1_r3 / 2026-09-07 ③ 为何 strict 塌）

解耦后 ③ 第一次真正驱动 `de_method`。当时 SKILL §3.5 把「簇细胞占比 <5%」写成稀有定义，并写「应切 pseudobulk」。分辨率 0.8 → 三十余簇时绝大多数簇被标成稀有，LLM 打开 `--use-pseudobulk-for-rare`。5 个 SRX 库做簇内/簇外 t-test，p 值大量打平，排序退化成基因表原序（`AT1G01010`…）。这些基因进 KG 后第一候选塌掉；③ 再 `label_downgraded`。旧 strict 要求标签对 **且** confidence∈{high,medium}，降级簇对 headline 零贡献（§2 已改为只看标签）。

2026-09-07 全量重跑（`output/B1/arm3_llm`）再次出现同一路径：`de_method=pseudobulk_rare`，推理明确按 SKILL 的 5% 走（最小簇 211 细胞仍 <5%）。①② 写死 Wilcoxon，从未走这条开关。此轮不是公平的「判断力对比」。

`knowledge/` 与 `design/` 不进 LLM 上下文；运行时只有 `skills/cell-annotation/SKILL.md`（system_prompt）和脚本 argparse schema。

## 1. 稀有定义从 SKILL 拿掉，由 LLM 判断（已改文档）

**问题:** Wilcoxon vs pseudobulk 本身是 `de_method` 决策点，应由 LLM 根据簇大小等证据判断。旧 SKILL 把「怎么才算稀有」（占比 <5%）写成 if-then，模型是在执行文档，不是在判断。把更严的绝对细胞数 / 样本维闸门冻进 pipeline，会把判断层再收回路规则，与解耦目标相反。

**方案（2026-09-07 改定，取代原「冻结代码触发」稿）:**

1. **稀有簇可用 pseudobulk 保留。** 决策枚举仍是 `wilcoxon` / `pseudobulk_rare` / `pseudobulk_all`；默认 Wilcoxon，只有 LLM 显式传 `--use-pseudobulk-for-rare` 才切。
2. **「怎么才算稀有」不写进 SKILL。** 看簇数与 `cluster_size_distribution`（min / max / 各簇规模）；不把预计算的 `n_rare_clusters` 当稀有定义；不写占比阈值、不写「应切」。
3. **pseudobulk 是否适用（如聚合后样本是否够）也由 LLM 依证据判断**，不在 pipeline 里禁止切换。
4. **只改 `skills/`。** `knowledge/`、`design/` 是项目指导与备忘录，不同步改阈值。`references/` 当前 loop 无读文件工具，不作为本项必改。

**已做:** `skills/cell-annotation/SKILL.md` §3.5（2026-09-07）。

**本轮不做:** 重写 pseudobulk 统计公式；不在 `step2_markers` 里加绝对 n / `n_pseudobulk_samples` 闸门 / p 值打平自动回退；不改 `experiment_design.md` 里 ② 的对照表条文（② 仍走 `rule_judge` 写死 Wilcoxon，与 ③ 对照的是判断层，不是文档阈值）。

**重跑核对（2026-09-07 全量 `output/B1`）:** ③ `de_method=wilcoxon`。推理看最小簇 211 细胞（约 0.62%）、34 簇均 >100，判定无需 pseudobulk——没有再套 5%「应切」。①② 仍写死 Wilcoxon。工具 schema 里 `--rare-threshold` 默认 0.05 仍在 argparse，不在本项范围内。

## 2. 打分：把握给人看，strict 只看标签

**问题:** `evaluate_cell_level.py`：`strict = (exact|synonym) AND confidence∈{high,medium}`。`materialize_llm_labels` 把 `label_downgraded` 写成 `confidence=low`。把握变成评估脚本的开关，而不是给用户的不确定度。① 全标 high，结构上占优。

**规则:**

1. **strict / relaxed 只由标签–对照表关系决定**（exact/synonym/subtype/…），**不再乘 confidence、不再因 low 把 strict 置 0。**
2. **confidence / status 仍写入 `final_annotations.json` 和报告**，给人机交互、抽查、降级率诊断；单列 `low_conf_rate`，不进 accuracy 公式。
3. `label_unknown` 仍按 unknown/unmatched 计（没有细胞类型可对）。`label_downgraded` **保留原标签**，只降低展示用把握。
4. 实现处：`experiments/evaluate_cell_level.py`（`bootstrap_test.py` / `calibrate_thresholds.py` 同步）。`design/` 备忘录不改。

**已做:** 公式与回归测试（`annot_harness/tests/test_evaluate_scoring.py`）。随 2026-09-07 全量重跑写入 `output/B1/eval/`：

| arm | strict | relaxed | macro-F1 | low_conf cells |
|---|---|---|---|---|
| ① default | 0.9163 | 0.9163 | 0.4056 | 1733 |
| ② rule | 0.9163 | 0.9163 | 0.4056 | 15476 |
| ③ LLM | 0.9655 | 0.9655 | 0.4804 | 5878 |

①=② 说明 rule_judge 与 default 标签相同，差距只在把握。③ 选 Wilcoxon 后标签口径高于 ①；`low_conf_rate` 仍单列。Bootstrap ③−① strict CI 仍穿 0（簇级重抽样方差大）。

**不要混进本项:** 对照表「预测词在 map 里即 synonym、不与该细胞 GT 对齐」是另一套口径，不在本条范围。

## 建议顺序

1. §2 已改，数字见上表。
2. §1 已用全量三臂重跑验证（③ 自选 Wilcoxon）。
3. 需要新 frozen spec 时另开，不要把这两条写进 `deferred-work.md`。
