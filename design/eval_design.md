# 评分设计文档

## 1. 设计目标

注释 pipeline 输出的是 KG `Ontology.Name`;论文真值是各数据集自己的字符串。两者粒度、大小写、措辞都不同,不定义对照与计分就无法谈「准不准」。

本文档规定**细胞级注释性能怎么打分**,做四件事:

1. **评价对象** — 逐细胞标签 vs 该细胞 GT,不按簇 ID 配对
2. **对照** — 预测词与 GT 都落到图谱节点,用祖先边算关系,不维护 predicted×true 手写表
3. **计分** — 每个细胞一个权重;strict / relaxed 是权重的两种汇总
4. **与运行时隔离** — 评分在实验层、跑完之后;GT 不进 skill / loop / pipeline

评分回答的问题是:「这一次交付的细胞类型名,相对这篇论文的真值,对不对、偏细还是偏粗。」不评价 DEG 质量、轨迹合规、工具调用次数——那些分别见 `operations_metrics_catalog.md`、`trajectory_design.md`、P5 evals。

---

## 2. 职责划分

### 2.1 评分层的职责

| 职责 | 说明 |
|---|---|
| 展开到细胞 | `obs_snapshot.csv`(细胞 → leiden)+ `final_annotations.json`(leiden → label) |
| 对照 | 预测词 → 别名 → 节点 A;该细胞 GT → 钉表 → 节点 B |
| 关系 | 用 `ontology_relation` 祖先边判定 exact / synonym / subtype / supertype / unrelated / unmatched |
| 汇总 | 细胞权重的平均 → strict / relaxed;另报 macro-F1、纯度、low_conf_rate |
| 统计 | cluster-aware bootstrap(以簇为抽样单元) |

### 2.2 评分层不管的事

| 谁 | 不管什么 |
|---|---|
| loop | 领域无关,不知道 GT、不知道分数 |
| skill / pipeline | 只写 KG 名与 `run_log.jsonl`;禁止读 GT |
| `write_judgment` | 决策枚举与 scope,不打分 |
| P5 `evals/` | 决策点是否落在枚举、run_log 是否合规 — 轨迹测试,不是本评分 |

```
GT / 钉表 / 别名 / 评分脚本  ∈  实验层(experiments/ + scripts/)
注释产出                     ∈  skill 工具副作用(final_annotations.json)
```

### 2.3 核心原则

```
评分 = 该细胞的预测节点 vs 该细胞的 GT 节点
headline = strict(同一节点才满分)
relaxed  = 同一枝上的粗/细给部分分,始终与 strict 并列报告
```

- **禁止 `hits[0]`**:不能按「这个预测词在某张表里的第一行」给分。同一预测词、不同细胞 GT,关系必须不同。
- **不限制输出词表**:钉表只钉 GT,不规定 pipeline 只能输出哪些 `Ontology.Name`。
- **把握不进准确率**:`confidence` / `label_downgraded` 只进 `low_conf_rate` 诊断,不乘进 strict / relaxed。

---

## 3. 评价对象与数据展开

三臂(及任何两次独立 session)的 qc / 分辨率 / 聚类都可能不同,簇 ID 没有配对意义。评价单元是**细胞条码**。

```
gt_cells.csv          cell_barcode → true_type
obs_snapshot.csv      cell_id      → leiden
final_annotations.json  leiden     → {label, confidence, status}
                 │
                 ▼
            逐细胞一行: true, raw, relation, weight
```

- `label` 必须是判断层写入的交付名(`label_confirm` 之后的 materialize)。缺 `label` / `confidence` / `status` 则评估脚本非零退出,不从 `run_log` 另推一套标签。
- `label_unknown` → 预测词视为 `unknown` → unmatched。
- `label_downgraded` **保留原标签**,只把 `confidence` 写成 low。
- 一个 leiden 簇共用一个 `label`,但簇内 GT 可以混。细胞级准确率天然受**聚类纯度**上限约束;纯度是诊断,不是分数本身。

---

## 4. 对照:落到图谱节点

### 4.1 资产

| 资产 | 路径 | 范围 |
|---|---|---|
| GT 钉表 | `experiments/gt_ontology.json` 或 `gt_ontology_<数据集id>.json` | **每套数据一份**,只覆盖该数据 `true_type` |
| 别名 | `experiments/kg_term_aliases.json` | **全库一份**,预测词措辞 → 规范 `Ontology.Name` |
| 真值表 | `experiments/gt_cells.csv`(或 `gt_cells_<id>.csv`) | 条码 → 论文标签字符串 |

钉表形状:

```json
{
  "pins": {
    "Root cortex": "root cortex",
    "Root epidermis": "root epidermis"
  },
  "_meta": { "dataset": "DS001", "verified": true }
}
```

- 钉的是 **GT 字符串 → 已有 Ontology 节点**,人工确认。搜图谱可以辅助起稿(`scripts/build_label_map.py`),起稿结果不是 predicted 白名单。
- **不把 `Unknown` 钉到任何细胞类型节点。** 无类型的真值一律 unmatched。
- 别名例:`trichoblast` → 根毛节点。细标签数据集 GT 钉到根毛时这是 synonym;粗标签数据集 GT 钉到表皮时由图上「根毛 ⊂ 表皮」算成 subtype,不再手写 pair。

### 4.2 解析顺序

**节点 B(真值)**

1. `true_type` 在钉表(大小写不敏感)→ 钉到的 `Ontology.Name`
2. 否则 B 为空 → 该细胞 unmatched

**节点 A(预测)**

1. `label` 为空或 `unknown`(大小写不敏感)→ unmatched
2. 别名表命中 → 规范名,再在图上取 canonical 节点
3. 否则按 `Ontology.Name` 大小写不敏感匹配
4. 都落不到 → unmatched(报告 `unmatched_terms`,便于补别名;不静默当 synonym)

### 4.3 图谱查询

与 step3c 相同:`ontology_relation` 祖先,默认 **最多 3 hop**。只为评分中出现过的 A/B 节点拉祖先,不把全图装进评估进程。

图谱不可达(无密码、驱动失败、0 命中):`kg_hierarchy: skipped`。离线回退**只保留** exact / synonym(别名或钉点同名);不把祖先边当成 unrelated 以外的关系,也不回退到旧 pair 表。

凭据走 skill `.env` 的 `NEO4J_*`(见 `docs/CONFIGURATION_REFERENCE.md`),评分脚本与 step3c 同一套加载,loop 仍然不知道 Neo4j。

---

## 5. 关系判定

设预测节点为 A、该细胞 GT 节点为 B。

| 关系 | 判定 | 直观 |
|---|---|---|
| `exact` | A 与 B 为同一节点,**且** `label` 字符串与 `true_type` 字面相等 | 几乎不出现:GT 是论文写法,预测是 KG 写法 |
| `synonym` | A 与 B 为同一节点,字符串不同 | 「root cortex」对「Root cortex」;别名指向同一节点 |
| `subtype` | B 在 A 的祖先集合里(A 是 B 的子孙) | 预测比该细胞 GT **更细** |
| `supertype` | A 在 B 的祖先集合里(A 是 B 的祖先) | 预测比该细胞 GT **更粗** |
| `unrelated` | A、B 都有节点,但 3 hop 内不是同一点、也没有祖先边 | 邻层/错枝:表皮 vs 皮层 |
| `unmatched` | A 或 B 缺节点,或预测为 `unknown` | GT=`Unknown`;图上没有的词 |

方向以**该细胞**为准:预测 `root endodermis`、GT=`Root cortex` → 若图上内皮层是皮层后代,则 subtype;同一预测词、GT=`Root epidermis` → 通常 unrelated。

sibling / 邻层没有祖先边,就是 unrelated,不给部分分。

---

## 6. 计分

### 6.1 细胞权重

| 关系 | 权重 \(w\) |
|---|---|
| exact / synonym | 1.0 |
| subtype / supertype | 0.5 |
| unrelated / unmatched | 0.0 |

\(w = 0.5\) 是预先约定的部分分:**同一条本体枝上的粗/细既不当满分,也不当零分。** 不是 hop 数、不是图距离、不是生物学常数。1 hop 与 3 hop 同为 0.5。改权重或按 hop 衰减视为评分版本变更,须改本文档并重评,不得只改代码。

`confidence` 另有诊断权重(high/medium=1, low=0.5),**只写入 per_cell,不进入下面任何准确率或 F1。**

### 6.2 汇总(N = 可对齐的细胞数)

\[
\mathrm{strict} = \frac{1}{N}\sum_i \mathbf{1}[w_i = 1]
\]

\[
\mathrm{relaxed} = \frac{1}{N}\sum_i w_i
\]

- **headline = strict**。表示「预测节点与该细胞 GT 节点是同一/同义」。
- **relaxed 必须与 strict 并列报告**,不可只报其中一个。
- unknown / unmatched 进分母、权重 0,从而压低两条准确率;这是刻意的(无类型真值不能假装对)。可另报「去掉 `Unknown` GT 后的 strict/relaxed」作诊断,不替代 headline。

### 6.3 macro-F1(辅)

对每个真值类型 \(t\):

- 细胞权重 \(w_i>0\) 记入该类 TP(累加 \(w_i\))
- \(w_i=0\) 记入该类 FN(+1)
- 预测节点映射回某个**其它**钉住的 GT 名时,记入那一类 FP

每类算 soft F1,再对类型做算术平均。`Unknown` 作为一类时 F1 恒为 0,会系统性压低 macro-F1。**因此 macro-F1 不得单独当 headline**,只作类型均衡的辅指标。

### 6.4 必报诊断(不计 headline)

| 指标 | 定义 |
|---|---|
| `low_conf_rate` | `confidence=low` 的细胞占比 |
| `mean_cluster_purity` | 各 leiden 簇内最大 GT 类型占比的均值 |
| `unmatched_terms` | 预测词落不到节点的细胞计数 |
| per-type F1 / relation 直方图 | 失败模式:细了、粗了、还是走错枝 |
| `kg_hierarchy` | `used` 或 `skipped` |

---

## 7. 统计(比较两次运行时)

细胞不独立:同一簇共享交付标签。**禁止细胞级 McNemar。**

比较两次 session(例如 B1 三臂):

- 抽样单元 = 该次运行的 leiden 簇:有放回抽 N 个簇,抽中则带上该簇全部细胞
- 默认 1000 次,95% CI
- 每次重抽样同时算 strict、relaxed、macro-F1 的差

B1 预先注册的效应量判定线(≥ 0.03 且 CI 不含 0)写在 `experiment_implementation.md` §3.1,服务的是**实验假说**,不是本评分公式。新实验要换判定线,改实验文档,不改本节。

---

## 8. 评分不覆盖的东西

| 现象 | 本评分怎么处理 |
|---|---|
| ③ 选了更细的 KG 词 | 若是该细胞 GT 的子孙 → strict 0、relaxed 0.5;否则 unrelated 0。细不等于加分 |
| 交付锁死 `first_candidate` | 评分只看 `final_annotations.label`,不看 rank 第二名、不看 judgment 正文 |
| 聚类切得更碎 | 只通过「换了一批细胞的 label」进入分数;纯度单独报 |
| 决策点枚举 / run_log 缺字段 | P5 `validate_log.py` + evals,不是本文件 |
| pipeline 内部 QC / silhouette | `operations_metrics_catalog.md`,不是注释对错 |

---

## 9. 实现位置

评分不是 harness 的一部分,也不进 skill 包。

```
experiments/
├── ontology_eval.py          ← OntologyScorer / Hierarchy / 钉表与别名加载
├── evaluate_cell_level.py    ← 多臂细胞级评估(写 evaluation_report.json)
├── bootstrap_test.py         ← cluster-aware bootstrap(读 per_cell)
├── gt_ontology.json          ← 默认数据集钉表
├── gt_ontology_<id>.json     ← 其它数据集钉表
└── kg_term_aliases.json      ← 全球别名
scripts/build_label_map.py    ← 只起稿钉表,不再写 pair
harness/tests/test_ontology_eval.py
harness/tests/test_evaluate_scoring.py
```

CLI 要点:`evaluate_cell_level.py --gt-ontology ... --aliases ...`;传入 `--label-map` 必须退出。报告写 `kg_hierarchy`。per_cell 另存,供 bootstrap 与失败分析。

---

## 10. 与其他设计文档的关系

```
atomic_operations.md            ← pipeline 有哪些操作 (WHAT)
operations_metrics_catalog.md   ← 每个操作输出什么指标 (WHY) — 运行时度量,不是本评分
tool_design.md                  ← 怎么实现 (HOW)
trajectory_design.md            ← 指标和判断怎么记录 (LOG)
rag_design.md                   ← 通用笔记本 (MEM)
loop_design.md                  ← 通用 loop (RUN)
eval_design.md (本文档)         ← 注释对错怎么打分 (SCORE) — 实验层,GT 不进 loop/skill
experiment_design.md            ← 12 个实验问什么
experiment_implementation.md    ← 实验怎么跑、B1 判定线;计分公式以本文为准
```

落地对照与旧 pair 表迁移记录:`_bmad-output/implementation-artifacts/label-map-ontology-eval.md`。confidence 不再乘进 strict:`b1-r3-followup.md` §2。两者都是变更备忘,冲突时以本文为准。

---

## 11. 相对旧口径(已废弃,勿再用)

| 旧 | 现 |
|---|---|
| 每数据集一张 `label_map.json` predicted×true 手写关系 | 每数据集只钉 GT;关系当场算 |
| 按预测词取 `hits[0]`,不看该细胞 GT | 必须看该细胞 GT |
| strict 还要求 confidence∈{high,medium} | 把握不进准确率 |
| headline 写 macro-F1 | headline 写 strict,relaxed 并列,macro-F1 为辅 |
| 簇 ID 配对 + 细胞级 McNemar | 细胞展开 + 簇级 bootstrap |

旧 `output/*/eval/evaluation_report.json`(pair 表)可留档,不作为现行 headline。现行产物在 `eval_ontology/`。
