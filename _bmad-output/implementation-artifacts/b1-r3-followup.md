# B1_r3 下一步：pseudobulk 触发条件 + strict 与把握拆开

> 日期: 2026-09-04
> 状态: 待做（主动规划，**不是** deferred-work）
> 证据: `output/B1_r3/eval/`；③ `output/B1_r3/arm3_llm/`
> 前序: `spec-measure-judge-decouple.md`（测量/判断解耦后的首轮三臂）

`deferred-work.md` 只收「非阻塞、无排期」的遗留。下面两项会改变 B1 的可比性和 headline 数字，下一轮实现/重跑以本文为准。

## 背景（B1_r3 ③ 为何 strict=0.20）

解耦后 ③ 第一次真正驱动 `de_method`。SOP 把「簇细胞占比 <5%」当成稀有，分辨率 0.8 → 34 簇时 30/34 簇触发 `--use-pseudobulk-for-rare`。5 个 SRX 库做簇内/簇外 t-test，p 值大量打平，排序退化成基因表原序（`AT1G01010`…）。这些基因进 KG 后第一候选塌掉；③ 再 `label_downgraded`。现行 strict 要求标签对 **且** confidence∈{high,medium}，降级簇对 headline 零贡献。

①② 写死 Wilcoxon，从未走这条开关。此轮不是公平的「判断力对比」。

## 1. 重新设计 pseudobulk 触发条件

**问题:** 比例阈值 5% 与簇数耦合：簇一多，几乎全员「稀有」。pseudobulk 在 n_sample=5 时检验力不足；实现上 p 值打平不回退 Wilcoxon，过滤也不看簇内表达比例。

**下一轮要定的触发（实施前冻结，不要边改边跑）:**

1. **默认仍 Wilcoxon。** 打开 pseudobulk 必须同时满足更严条件，而不是「任一簇 <5%」。
2. **稀有改绝对规模，不单靠占比。** 候选：细胞数下限（例如 <100 才考虑），或「占比 <5% **且** 细胞数 <N」。34 簇 × 3% 不再自动全开。
3. **样本维闸门。** `n_pseudobulk_samples`（本数据=5）低于阈值则 **禁止** 替换 Wilcoxon，并在测量里显式写出「未切换 / 原因」。旧 SOP 已警告 <3 检验力不足，但规则层没有回退；5 对 5 在本数据上已证实不够。
4. **失败回退。** p 值打平、前排基因呈基因组顺序、或与 Wilcoxon 标志基因 overlap≈0 时，保留 Wilcoxon 表，不得用垃圾表覆盖。
5. **SOP / SKILL / ①②③ 对照表同步。** `experiment_design.md` 里 ②「任一簇<5% → 该簇 pseudobulk」一并改；工具返回必须让 ③ 看见 `de_method` 与 top 基因，避免再 `markers_accept` 放行 `AT1G01010` 序列。

**本轮不做:** 重写 pseudobulk 统计公式本身（仍可以是按库加总 + t-test）；先把「何时允许替换 Wilcoxon」改对。

## 2. 打分：把握给人看，strict 只看标签

**问题:** `evaluate_cell_level.py`：`strict = (exact|synonym) AND confidence∈{high,medium}`。`materialize_llm_labels` 把 `label_downgraded` 写成 `confidence=low`。把握变成评估脚本的开关，而不是给用户的不确定度。① 全标 high，结构上占优。

**下一轮规则:**

1. **strict / relaxed 只由标签–对照表关系决定**（exact/synonym/subtype/…），**不再乘 confidence、不再因 low 把 strict 置 0。**
2. **confidence / status 仍写入 `final_annotations.json` 和报告**，给人机交互、抽查、降级率诊断；可单列 `low_conf_rate`，不进 accuracy 公式。
3. `label_unknown` 仍按 unknown/unmatched 计（没有细胞类型可对）。`label_downgraded` **保留原标签**，只降低展示用把握。
4. 改 `evaluate_cell_level.py` 与 `design/experiment_design.md`（或 implementation）里 B1 计分定义，冻结后再重评 `output/B1_r3`（可先对已有 `final_annotations.json` 重打分，不必立刻重跑 pipeline）。

**不要混进本项:** 对照表「预测词在 map 里即 synonym、不与该细胞 GT 对齐」是另一套口径，不在本条范围。

## 建议顺序

1. 改计分并重评 B1_r3 三臂（零成本看「去掉把握惩罚」后 ③ 的标签口径分数）。
2. 冻结并实现 §1 触发 + 回退；③ 只重跑 step2 起（或全臂），DE 与 ① 对齐后再比判断层。
3. 需要新 frozen spec 时另开，不要把这两条写进 `deferred-work.md`。
