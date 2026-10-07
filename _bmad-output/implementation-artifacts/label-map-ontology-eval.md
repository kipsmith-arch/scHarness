# 下一步：标签对照改为「GT 钉图谱 + 评估时算关系」

> 日期: 2026-09-11
> 状态: **已落地**（评估代码 + 钉表/别名 + 已有 `final_annotations.json` 按层次口径重评，`kg_hierarchy: used`）
> 触发: PRJNA935359（高粱）B1 重跑后 ③ strict/macro-F1 低于 ①②；对照表按数据集各维护一份，且 `hits[0]` 不按该细胞 GT 选行
> 前序: `design/experiment_implementation.md` §1.2 D-2；`experiments/evaluate_cell_level.py`；`experiments/label_map.json` / `label_map_PRJNA935359.json`

`deferred-work.md` 只收非阻塞遗留。本项会改评估可比性和 headline 数字，实现/重评以本文为准。**现行评分规范已收进 `design/eval_design.md`；本文保留迁移过程与旧 pair 对照数字。**

## 背景

Pipeline 输出的是 KG `Ontology.Name`（如 `trichoblast`、`root stele`），论文 GT 是各数据集自己的字符串（拟南芥 `Root hair`，高粱 `Root epidermis`）。D-2 用 `label_map.json` 把「预测词 × 真值词」写成 `exact / synonym / subtype / supertype / unrelated`，评估再查表。

高粱无法复用拟南芥那张表：同一 predicted `trichoblast` 在拟南芥是 Root hair 的 synonym，在高粱只能是表皮的 subtype。于是又做了 `experiments/label_map_PRJNA935359.json`。每来一套 GT 用词就复制一张 pair 表，不可扩展。

现行实现还有两处与 D-2 计分规则不一致：

1. `evaluate_cell_level.py` 对每个预测词取 `hits[0]`，**不核对该细胞的 `true_type`**。皮层细胞标成 `root stele` 仍因「该词是 Root stele 的 synonym」拿满 strict。
2. 表里没有的预测词（如 `root meristem`）整簇 `unmatched`。跑完再补行，map 继续膨胀。

`evidence` / `_meta` 不参与计分，不该成为对照表的主体。

## 已决断

1. **不再按数据集维护 predicted×true pair 表。** 计分只需要关系；关系从图谱层次当场算，不抄进 JSON。
2. **`true` 侧必须先钉到图谱节点。** 论文标签不能当全球主键。每套数据只维护很小一张 **GT 字符串 → `Ontology` 节点**。
3. **`predicted` 来自注释跑次，不提前指定。** 不要在图谱里给每个 GT「找一个最合适的词」去限制注释输出。注释照旧写 KG 名；评估再把该名落到节点。
4. **可选全球别名表**（措辞变体 → 规范 `Ontology.Name`），与数据集无关。例如 `trichoblast` → 根毛节点、`columella root cap cell` → 柱根冠节点。
5. 评估：预测词经别名 → 节点 A；该细胞 GT 经钉表 → 节点 B；用 `ontology_relation` 祖先边判定：
   - 同一节点（或别名指向同一节点）→ `exact` / `synonym`
   - A 是 B 的子孙 → `subtype`
   - A 是 B 的祖先 → `supertype`
   - 否则 / 任一侧落不到节点 → `unrelated` 或 `unmatched`
6. 钉 GT 时可以用名字在图谱里搜候选，**人工确认节点**。这一步不是规定 predicted。

## 不做

- 不限制 step3c / LLM 只能输出钉表里的词。
- 不把 relation 再手写进 pair JSON（与图谱漂移）。
- 本项不改 skill SOP、不改注释 pipeline；只改实验层对照与 `evaluate_cell_level.py`（及 bootstrap 若依赖同一 relation）。
- 不把 `Unknown` 钉到某个细胞类型节点；无 GT 类型仍 unmatched。

## 落地任务（未排期实现时按此顺序）

1. **Schema**
   - `experiments/gt_ontology.json`（或每数据集 `gt_ontology_<id>.json`）：`{ "true_label": "<Ontology.Name 或稳定 id>", ... }`，仅覆盖该数据集 `true_labels`。
   - `experiments/kg_term_aliases.json`（全库一份）：`{ "predicted_variant": "<canonical Ontology.Name>", ... }`。可从现有两张 PREBUILT 的 predicted 侧合并起稿。
2. **评估**
   - `evaluate_cell_level.py`：按「该细胞 GT」选节点 B，禁止 `hits[0]`。
   - 图谱不可达时：只接受别名 exact/synonym 的离线回退，并在报告里标明 `kg_hierarchy: skipped`；不静默沿用旧 pair 表。
3. **起稿脚本**
   - `scripts/build_label_map.py` 改为：输入 GT 词表 → 查 KG 同名/近名节点 → 写出待确认钉表（`verified: false`），不再生成 predicted×true 全对。
4. **迁移**
   - 拟南芥：`label_map.json` 的 true 侧钉到现有 12 类对应节点。
   - 高粱：`label_map_PRJNA935359.json` 的 `true_labels` 钉节点；`trichoblast` 只进别名，关系由「根毛 ⊂ 表皮」在图上算，不再手写 subtype。
   - 旧 `label_map*.json` 在新评估绿了之后停止作为计分输入（可留档，不双轨）。
5. **回归**
   - 用 `output/B1/` 与 `output/B1_PRJNA935359/` 的 `final_annotations.json` **重评、不重跑 pipeline**。headline 数字会变；报告须并列旧 pair 口径与新层次口径。
   - 单测：同一预测词、不同细胞 GT → 不同 relation；未知预测词 → unmatched；`true: null` 类（procambium）保持 unrelated。

## 落地结果（2026-09-11）

代码与资产：

- `experiments/ontology_eval.py`：按该细胞 GT 算 relation；禁止 `hits[0]`。
- `experiments/gt_ontology.json` / `gt_ontology_PRJNA935359.json` / `kg_term_aliases.json`
- `evaluate_cell_level.py` / `bootstrap_test.py` / `run_b1.py` 改为 `--gt-ontology` + `--aliases`
- `scripts/build_label_map.py` 只起稿钉表（`verified: false`），不再写 pair
- 旧 `label_map*.json` 留档，传入 `--label-map` 会退出
- 单测：`annot_harness/tests/test_ontology_eval.py` / `test_evaluate_scoring.py` / `test_build_gt_ontology.py`

重评产物（不覆盖旧 pair 报告）：`output/B1/eval_ontology/`、`output/B1_PRJNA935359/eval_ontology/`。图谱名与钉表对齐：柱根冠钉到 `columella root cap cell`（KG 无 `columella root cap`）。`Stem cell niche` / `G2/M-phase cell` 在图上无对应节点，只靠别名 exact/synonym。

`kg_hierarchy: used`。旧 pair 口径 vs 新层次口径：

| 数据集 | arm | pair strict | 新 strict | pair relaxed | 新 relaxed | pair macro-F1 | 新 macro-F1 |
|---|---|---|---|---|---|---|---|
| SRP171040 | ① | 0.9163 | 0.5893 | 0.9163 | 0.6089 | 0.4056 | 0.5009 |
| SRP171040 | ② | 0.9163 | 0.5893 | 0.9163 | 0.6089 | 0.4056 | 0.5009 |
| SRP171040 | ③ | 0.9655 | 0.7057 | 0.9655 | 0.7264 | 0.4804 | 0.5808 |
| PRJNA935359 | ① | 0.9399 | 0.3792 | 0.9578 | 0.4051 | 0.1754 | 0.2102 |
| PRJNA935359 | ② | 0.9399 | 0.3792 | 0.9578 | 0.4051 | 0.1754 | 0.2102 |
| PRJNA935359 | ③ | 0.9025 | 0.2938 | 0.9222 | 0.4015 | 0.0356 | 0.0957 |

「新 strict」列现为准确率：exact / synonym / subtype 记 1，supertype 记 0。「新 relaxed」列现为层次得分：subtype 记 1.5，supertype 记 0.5，可以大于 1。2026-09-24 按已有逐细胞关系重算，未重跑 pipeline。宏平均 F1 仍按层次得分的权重累加 TP。

高粱 1781 个 unmatched 全部是 GT=`Unknown`（不钉节点），不是别名漏了。① 对表皮细词（`root hair cell`）记 subtype；③ synonym 少、subtype 多（2278 vs ① 的 200），strict 被压低。权重改为 1.5 后，③ 的 relaxed 从 0.1862 升到 0.4015，与 ① 的 0.4051 接近。pair 表把错细胞的粗词也写成 synonym，strict 虚高。

## 成功标准

- 新增数据集只需钉 GT→节点，不必复制一张 pair 表。
- strict 表示「预测节点与**该细胞** GT 节点为同一/同义」，不再因粗词在表里有 synonym 行而对错细胞满分。
- ③ 使用更细 KG 词时，strict/relaxed 反映 subtype/supertype，而不是「词条等级运气」。

## 与其它规划的关系

- 不阻塞 P7 `.skill` 打包。
- 与 `future-kg-api.md` 正交：层次查询现在走 Bolt，将来可换 API，钉表/别名不变。
- `b1-r3-followup.md` §2（strict 不乘 confidence）保持；本项改的是 **标签对上哪一行**，不是把握。
