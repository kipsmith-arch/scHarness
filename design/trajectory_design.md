# 运行日志设计

## 1. 设计目标

pipeline 有 47 个原子操作,产出 247 个统计指标;LLM 在 13 个决策点做判断。所有指标和判断需要:

1. **统一存储** — 一个文件,append-only,不覆盖
2. **时间线** — 时间戳 + 序号,自然区分新旧
3. **可追溯** — 任何判断能回溯到它基于的指标;任何指标能查到它被哪些判断引用
4. **可导出** — 从日志提取微调训练对

### 与旧设计的对比

| 旧设计 | 新设计 |
|---|---|
| `trajectory/session.json` + `executions/` + `snapshots/` + `judgments/` + `exports/`(~20 文件) | `run_log.jsonl`(1 个文件) |
| 同一文件被不同子命令覆盖(qc_metrics.json 问题) | append-only,永远不覆盖 |
| `supersedes`/`superseded_by` 版本链 | 按 `seq` 排序,最新 = 当前 |
| `variable_registry.json` 契约文件 | 路径直接写在记录的 inputs 里 |
| snapshot 目录保留旧版 JSON | 旧指标在 log 里,按 seq 查 |
| exec 记录有 run_id + step + subcommand + op 四个字段 | exec 记录只有 run_id(含 step+op+attempt)+ metrics |

---

## 2. 文件格式

**`<project-dir>/run_log.jsonl`** — NDJSON,每行一条记录,append-only。

### 2.1 记录类型

| type | 含义 | 谁写 | 什么时候写 |
|---|---|---|---|
| `session_start` | 会话开始 | LLM | 开局 |
| `exec` | 原子操作执行完毕 | pipeline 脚本 | 每个原子操作跑完 |
| `judgment` | LLM 做完判断 | LLM | 每个决策点判断完毕 |
| `session_end` | 会话结束 | LLM | 收尾 |

### 2.2 公共字段

所有记录都包含:

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | ISO 8601 | 时间戳 |
| `seq` | int | 自增序号(文件内唯一,从 1 开始) |
| `type` | string | 记录类型(见上表) |

### 2.3 `session_start` 记录

```json
{
  "ts": "2026-07-23T14:00:00Z",
  "seq": 1,
  "type": "session_start",
  "session_id": "sess-20260723-SRP171040",
  "dataset": {
    "id": "SRP171040",
    "h5ad_path": "dataset/h5ad/SRP171040.h5ad",
    "n_cells_raw": 33956,
    "n_genes_raw": 53678,
    "organism": "Arabidopsis thaliana",
    "organ": "root",
    "tissue": "root tip",
    "genotypes": ["Col-0", "rhd6", "gl2"],
    "batch_key": "sample",
    "n_batches": 5
  }
}
```

### 2.4 `exec` 记录

一个原子操作执行完毕后追加。每条 exec 记录有唯一的 `run_id`。

`run_id` 格式:`{step}.{op}#{attempt}`

- `step` — 步骤名,如 `step1_prepare`、`step2_markers`
- `op` — 原子操作短名,如 `leiden_cluster`、`de_rank`
- `#attempt` — 该 step.op 的第几次执行(从 1 开始,重跑递增)

示例:`step1_prepare.leiden_cluster#1` = 第一次执行 step1_prepare 的 leiden_cluster 操作。

需要解析时:`step, rest = run_id.rsplit(".", 1); op, attempt = rest.split("#")`

```json
{
  "ts": "2026-07-23T14:03:01Z",
  "seq": 5,
  "type": "exec",
  "run_id": "step1_prepare.leiden_cluster#1",
  "parameters": {"resolution_list": "0.4,0.6,0.8,1.0,1.2"},
  "metrics": {
    "resolution_cluster_counts": {"0.4": 18, "0.6": 24, "0.8": 29, "1.0": 34, "1.2": 38},
    "silhouette_overall": {"mean": 0.35, "std": 0.15},
    "silhouette_per_cluster": {"0": {"mean": 0.45, "median": 0.48}, "1": {"mean": 0.32}},
    "n_clusters_negative_mean_silhouette": 2,
    "modularity": 0.72,
    "cluster_size_distribution": {"min": 50, "max": 5000, "median": 800, "gini": 0.4, "n_rare": 3, "n_singleton": 0}
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | string | `{step}.{op}#{attempt}`,如 `step1_prepare.leiden_cluster#1`。每条 exec 记录唯一 |
| `parameters` | object | 该操作使用的参数 |
| `metrics` | object | 该操作产出的统计指标(见 operations_metrics_catalog.md)。**体积约定:单条 < 50KB,上限 ~200KB**;只记指标摘要,不记 per-gene/per-cluster 数据表(数据表进产物文件)——见 catalog 卷首判据 |

### 2.5 `judgment` 记录

LLM 做完判断后追加。`run_ref` 指向该判断基于的 exec 记录的 `run_id`。

```json
{
  "ts": "2026-07-23T14:05:30Z",
  "seq": 8,
  "type": "judgment",
  "decision_point": "clustering_quality",
  "scope": {"type": "session"},
  "run_ref": "step1_prepare.leiden_cluster#1",
  "inputs": [
    {"path": "step1_prepare.leiden_cluster.silhouette_overall.mean", "value": 0.15},
    {"path": "step1_prepare.leiden_cluster.n_clusters_negative_mean_silhouette", "value": 5},
    {"path": "step1_prepare.leiden_cluster.cluster_size_distribution.n_singleton", "value": 3}
  ],
  "output": {
    "decision": "clustering_adjust",
    "confidence": "high",
    "action": "recluster_at_0.6"
  },
  "reasoning": "silhouette=0.15 is very low, 5 clusters have negative mean silhouette, 3 singleton clusters. Resolution 0.8 is too high. Try 0.6."
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `decision_point` | string | qc_threshold ~ global_quality 之一 |
| `scope` | object | `{type: "session"}` 或 `{type: "cluster", cluster_id: "0"}` |
| `run_ref` | string | 该判断基于哪个 exec 记录的 run_id |
| `inputs` | array | LLM 实际读取的变量列表,每项含 `path` + `value`(值快照) |
| `output.decision` | string | 判断结果(枚举值,见 §3.2) |
| `output.confidence` | string | high / medium / low |
| `output.action` | string | 后续动作指令 |
| `reasoning` | string | 自然语言推理链 |

### 2.6 `session_end` 记录

```json
{
  "ts": "2026-07-23T15:30:00Z",
  "seq": 95,
  "type": "session_end",
  "final_summary": {
    "n_clusters": 29,
    "n_unknown": 0,
    "unknown_rate": 0.0,
    "n_unique_labels": 27,
    "run_count": 12,
    "judgment_count": 67
  }
}
```

---

## 3. 判断点与决策枚举

### 3.1 判断点清单

| 判断点 | 何时判断 | scope 类型 | 每数据集条数 |
|---|---|---|---|
| qc_threshold | step1_prepare compute_qc + qc_distribution 后 | session | 1 |
| resolution_select | step1_prepare leiden_cluster 后 | session | 1 |
| clustering_quality | step1_prepare leiden_cluster 后 | session | 1 |
| batch_effect | step1_prepare batch_mixing 后 | session | 1 |
| de_method | step2_markers 前 | session | 1 |
| marker_quality | step2_markers filter_markers 后 | session | 1 |
| kg_match | step3c_kg query_genes 后 | session | 1 |
| candidate_gap | step4_judge rank_candidates 后 | cluster | N(簇数) |
| candidate_disambiguate | step4_judge rank_candidates 后 | cluster | ≤N(仅并列簇) |
| refine_effect | step5_refine candidate_autocorr + subcluster + marker_overlap 后 | cluster | ≤N(仅 analyzed 簇) |
| unknown_cluster | step5_refine unknown_overlap 后 | session | 1 |
| label_confirm | step6_validate marker_expression 后 | cluster | N(簇数) |
| global_quality | step7_diagnose cross_cluster 后 | session | 1 |

一个 29 簇数据集约产出 **29×2 + 9 + 8 = 67 条** judgment 记录。

### 3.2 decision 枚举词汇表

| 判断点 | decision 枚举值 | 含义 |
|---|---|---|
| qc_threshold | `threshold_set` / `threshold_default` | 设定阈值 / 用默认值 |
| resolution_select | `resolution_chosen` | 选择分辨率 |
| clustering_quality | `clustering_accept` / `clustering_adjust` | 接受 / 需要调参 |
| batch_effect | `batch_effect` / `condition_specific` / `well_mixed` | 批次效应 / 条件特异 / 混合良好 |
| de_method | `wilcoxon` / `pseudobulk_all` / `pseudobulk_rare` | DE 方法选择 |
| marker_quality | `markers_accept` / `markers_adjust_filter` / `markers_fail` | 接受 / 调参 / 失败 |
| kg_match | `id_match_ok` / `id_mismatch_gene_key` / `id_mismatch_organ` | ID 匹配诊断 |
| candidate_gap | `first_decisive` / `ambiguous_parent_child` / `ambiguous_synonym` / `ambiguous_true` / `unknown` | 候选差距判断 |
| candidate_disambiguate | `ambiguous_parent_child` / `ambiguous_synonym` / `ambiguous_true` | 并列候选消歧(仅并列簇;candidate_gap 词表子集) |
| refine_effect | `refine_effective` / `refine_ineffective` / `refine_skipped` / `refine_autocorr_low` | 子聚类效果(含 candidate_autocorr 预判) |
| unknown_cluster | `single_unknown_type` / `multiple_unknown_types` | Unknown 簇判断 |
| label_confirm | `label_confirmed` / `label_downgraded` / `label_unknown` | 逐簇标签确认 |
| global_quality | `quality_good` / `quality_acceptable` / `quality_poor` | 全局质量 |

---

## 4. 时间线与版本管理

### 4.1 原理

日志是 append-only。同一操作重跑只是在文件末尾追加新记录,`#attempt` 递增。不需要 `supersedes`/`superseded_by` 字段——**按 `seq` 排序,最新 = 当前**。

### 4.2 时间线示例

```
seq=1   session_start                         dataset=SRP171040
seq=2   exec  step1_prepare.compute_qc#1       n_cells=33956, distributions={mt: p99=12.3}
seq=3   exec  step1_prepare.qc_distribution#1  bimodality=0.3
seq=4   judgment qc_threshold                  decision=threshold_set, max_mt_pct=15
seq=5   exec  step1_prepare.filter_cells#1    n_before=33956, n_after=33952
seq=6   exec  step1_prepare.pca#1              variance_explained=[0.08,...]
seq=7   exec  step1_prepare.leiden_cluster#1   silhouette=0.15, n_singleton=3            ← 太低
seq=8   judgment resolution_select             decision=resolution_chosen, res=0.8
seq=9   judgment clustering_quality            decision=clustering_adjust              ← 不满意
seq=10  exec  step1_prepare.leiden_cluster#2   silhouette=0.32, n_singleton=0            ← 重跑
seq=11  judgment clustering_quality            decision=clustering_accept              ← 满意了
seq=12  exec  step2_markers.de_rank#1          de_distribution={logfc_mean: 1.2}
...
seq=95  session_end                            n_clusters=29, unknown_rate=0.0
```

### 4.3 查询当前值

查"当前 silhouette"= 找 `run_id` 以 `step1_prepare.leiden_cluster` 开头的记录中 `seq` 最大的:

```python
log = read_log("run_log.jsonl")
leiden_records = [r for r in log if r.get("run_id", "").startswith("step1_prepare.leiden_cluster#")]
current = max(leiden_records, key=lambda r: r["seq"])
sil = current["metrics"]["silhouette_overall"]["mean"]  # 0.32
```

### 4.4 查询历史

查"leiden_cluster 的全部历史":

```python
leiden_records = sorted(
    [r for r in log if r.get("run_id", "").startswith("step1_prepare.leiden_cluster#")],
    key=lambda r: r["seq"]
)
# [seq=7 silhouette=0.15 (#1), seq=10 silhouette=0.32 (#2)]
```

### 4.5 查询判断的因果链

查"clustering_quality 为什么从 adjust 变成 accept":

```python
records = [r for r in log if r.get("decision_point") == "clustering_quality"]
# [seq=9 decision=adjust run_ref=step1_prepare.leiden_cluster#1,
#  seq=11 decision=accept run_ref=step1_prepare.leiden_cluster#2]

# seq=11 的判断基于 leiden_cluster#2 的输出:
run_ref = records[1]["run_ref"]  # "step1_prepare.leiden_cluster#2"
exec_record = [r for r in log if r.get("run_id") == run_ref][0]
# silhouette=0.32 (从 #1 的 0.15 提升)
```

### 4.6 同一操作多次执行的触发原因

judgment 记录的 `output.action` 字段自然记录了"为什么重跑":

```
seq=9  judgment clustering_quality  decision=clustering_adjust  action="recluster_at_0.6"
                                                         ↓ 触发了 leiden_cluster 重跑
seq=10 exec    step1_prepare.leiden_cluster#2  silhouette=0.32
seq=11 judgment clustering_quality  decision=clustering_accept  action="proceed_to_step2_markers"
```

不需要额外的 `triggered_by` 字段——因果链就是时间线上的前后关系。

---

## 5. 变量路径

### 5.1 格式

判断记录的 `inputs[].path` 用以下格式:

```
{step}.{op}.{metric_path}
```

其中 `{step}.{op}` 与 exec 记录的 `run_id` 前缀(去掉 `#attempt`)一致。

| 示例 | 含义 |
|---|---|
| `step1_prepare.compute_qc.n_cells` | step1_prepare 的 compute_qc 产出的 n_cells |
| `step1_prepare.leiden_cluster.silhouette_overall.mean` | step1_prepare 的 leiden_cluster 产出的 silhouette 均值 |
| `step2_markers.de_rank.de_distribution.logfc_mean` | step2_markers 的 de_rank 产出的 logfc 均值 |
| `step4_judge.rank_candidates.cluster0.count_ratio` | step4_judge 的 rank_candidates 对 cluster 0 的 count_ratio (canonical) |

### 5.2 簇级变量

簇级变量在路径中包含 cluster_id。**canonical path 形式**(Story 6.10):

```
step4_judge.rank_candidates.cluster{N}.first_count
step6_validate.marker_expression.cluster{N}.top_markers_expression[0].cohen_d
```

canonical 形式以 SKILL.md 与 trajectory_design.md §5.2 为准;其他形式(如 `step4_judge.per_cluster.{N}.first`、`step4_judge.rank_candidates.annotations.{cluster_id}.first_count`)**仅供回放旧 run_log.jsonl** 兼容,新生成的 judgment 应使用 canonical path(用 `cluster{N}` 而不是 `annotations.{cluster_id}`,因为 LLM 主流引用形式是前者)。

### 5.3 值快照

`inputs[].value` 是判断时的值快照。即使后续重跑改变了指标,判断记录中的值不变——能回溯"当时 LLM 看到的是什么数据,做了什么判断"。

### 5.4 无需变量注册表

路径直接由 `step` + `op` + `metrics` 中的 JSON 路径组合,与 `operations_metrics_catalog.md` 中定义的指标一一对应。pipeline 脚本保证产出这些路径,LLM 直接引用,无需中间注册文件。

---

## 6. 与数据文件的关系

### 6.1 两类输出

| 类别 | 内容 | 存储 | 谁读 |
|---|---|---|---|
| **指标** | 统计测量值(silhouette, n_markers, hit_rate...) | `run_log.jsonl` | LLM 读,做判断 |
| **数据** | 实际列表/表(marker 基因列表, 候选列表, annotations...) | 各步 JSON 文件 | 下游 pipeline 脚本读 |
| **二进制** | h5ad | processed.h5ad | 脚本读 |

### 6.2 数据文件(不变)

```
step1_prepare/processed.h5ad, obs_snapshot.csv, var_snapshot.csv
step2_markers/markers.csv, markers.json          ← 基因列表,供 step3c_kg 读
step3c_kg/kg_hits.json                             ← 候选列表,供 step4_judge 读
step4_judge/annotations.json                      ← first/second 候选,供 step5_refine 读
step5_refine/refined_annotations.json             ← 细化结果,供 step6_validate 读
step6_validate/final_annotations.json, report.md  ← 最终标签
step7_diagnose/diagnostics.json, report.md        ← 诊断报告
```

数据文件被重跑覆盖时,旧版指标仍在 `run_log.jsonl` 中(按 seq 可查)。数据文件本身的旧版不保留(太大且不是 LLM 判断的主要依据)。

### 6.3 LLM 读什么

大多数判断只需读 `run_log.jsonl` 的指标。少数需要看具体数据(如 candidate_gap 需要看 supporting_markers 基因列表)时,读对应的 JSON 文件。

---

## 7. 微调数据导出

### 7.1 当前有效训练对

每个判断点+scope 的最新判断 = 一条训练对:

```python
log = read_log("run_log.jsonl")
judgments = [r for r in log if r["type"] == "judgment"]

# 按 decision_point + scope 分组,取每组 seq 最大的
groups = {}
for r in judgments:
    key = (r["decision_point"], json.dumps(r.get("scope", {})))
    if key not in groups or r["seq"] > groups[key]["seq"]:
        groups[key] = r

# 导出
with open("fine_tune_pairs.jsonl", "w") as f:
    for r in groups.values():
        pair = {
            "pair_id": f"{r['decision_point']}-{r.get('scope',{}).get('cluster_id','session')}",
            "task": r["decision_point"],
            "input": {inp["path"]: inp["value"] for inp in r["inputs"]},
            "output": r["output"],
            "reasoning": r["reasoning"],
            "run_ref": r["run_ref"],
            "ts": r["ts"],
            "seq": r["seq"]
        }
        f.write(json.dumps(pair, ensure_ascii=False) + "\n")
```

### 7.2 自我纠正训练对

同一判断点+scope 有多条判断 = LLM 改了主意。取首版和末版成对:

```python
from collections import defaultdict

by_key = defaultdict(list)
for r in judgments:
    key = (r["decision_point"], json.dumps(r.get("scope", {})))
    by_key[key].append(r)

corrections = []
for key, records in by_key.items():
    if len(records) < 2:
        continue
    records.sort(key=lambda r: r["seq"])
    corrections.append({
        "pair_id": f"correction-{key[0]}-{key[1]}",
        "v1": {"decision": records[0]["output"]["decision"], 
               "reasoning": records[0]["reasoning"],
               "run_ref": records[0]["run_ref"]},
        "v2": {"decision": records[-1]["output"]["decision"],
               "reasoning": records[-1]["reasoning"],
               "run_ref": records[-1]["run_ref"]},
        "intervening_runs": [r["run_ref"] for r in records[1:-1] if r["type"] == "exec"]
    })
```

### 7.3 跨数据集聚合

```bash
# 聚合多个数据集的 run_log.jsonl
python scripts/aggregate_logs.py --inputs proj1/run_log.jsonl proj2/run_log.jsonl --output aggregated.jsonl

# 按判断点分拆
python scripts/split_by_decision.py --input aggregated.jsonl --output-dir by_decision/
```

---

## 8. 完整目录结构

```
<project-dir>/
│
├── step1_prepare/                    ← pipeline 数据文件(不变)
│   ├── processed.h5ad
│   ├── obs_snapshot.csv
│   └── var_snapshot.csv
├── step2_markers/
│   ├── markers.csv
│   └── markers.json
├── step3c_kg/
│   ├── kg_hits.json
│   └── kg_source.txt
├── step4_judge/
│   └── annotations.json
├── step5_refine/
│   └── refined_annotations.json
├── step6_validate/
│   ├── final_annotations.json
│   ├── report.md
│   └── figures/
├── step7_diagnose/
│   ├── diagnostics.json
│   └── report.md
│
├── run_log.jsonl                      ← 统一运行日志(唯一轨迹文件)
│
└── exports/                           ← 导出目录(可选,脚本生成)
    ├── fine_tune_pairs.jsonl
    └── self_correction_pairs.jsonl
```

---

## 9. 通用函数

### 9.1 append_log(pipeline 脚本用)

```python
import json, datetime, os

def append_log(log_path, record: dict):
    """追加一条记录到 run_log.jsonl。自动填充 ts 和 seq。"""
    record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            record["seq"] = sum(1 for _ in f) + 1
    else:
        record["seq"] = 1
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

### 9.2 pipeline 脚本调用方式

每个原子操作完成后调一次。`run_id` 由脚本生成,格式 `{step}.{op}#{attempt}`:

```python
# step1_prepare.py, leiden_cluster 完成后
append_log(paths.run_log, {
    "type": "exec",
    "run_id": "step1_prepare.leiden_cluster#1",
    "parameters": {"resolution_list": "0.4,0.6,0.8,1.0,1.2"},
    "metrics": {
        "resolution_cluster_counts": cluster_counts,
        "silhouette_overall": {"mean": 0.35, "std": 0.15},
        # ... 其他指标
    }
})
```

### 9.3 LLM 调用方式

判断完成后调一次:

```python
append_log("output/run_log.jsonl", {
    "type": "judgment",
    "decision_point": "clustering_quality",
    "scope": {"type": "session"},
    "run_ref": "step1_prepare.leiden_cluster#1",
    "inputs": [
        {"path": "step1_prepare.leiden_cluster.silhouette_overall.mean", "value": 0.15},
        {"path": "step1_prepare.leiden_cluster.n_clusters_negative_mean_silhouette", "value": 5}
    ],
    "output": {"decision": "clustering_adjust", "confidence": "high", "action": "recluster_at_0.6"},
    "reasoning": "silhouette=0.15 is very low, 5 clusters negative. Try lower resolution."
})
```

### 9.4 run_id 生成

每次执行原子操作时,按 `{step}.{op}` 维度维护一个计数器:

```python
# 脚本启动时,从 run_log.jsonl 中查找该 step.op 的最大 attempt
def next_run_id(log_path, step, op):
    prefix = f"{step}.{op}#"
    existing = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                rid = r.get("run_id", "")
                if rid.startswith(prefix):
                    existing.append(int(rid.split("#")[1]))
    attempt = max(existing) + 1 if existing else 1
    return f"{step}.{op}#{attempt}"
```

---

## 10. SKILL.md 中的指导

```markdown
## 运行日志

每次 pipeline 步骤执行后和每次判断完成后,你必须将记录追加到
`<project-dir>/run_log.jsonl`(NDJSON,每行一条)。

### pipeline 执行后

运行 pipeline 命令后,脚本会自动追加 exec 记录。你无需手动写 exec 记录。
但你需要知道 run_id(从脚本输出中获取,格式如 `step1_prepare.leiden_cluster#1`),
以便在 judgment 记录中引用。

### 判断后

完成每个判断点(qc_threshold~global_quality)的判断后,追加一条 judgment 记录:

```python
python -c "
import json, datetime, os
log = 'output/run_log.jsonl'
seq = sum(1 for _ in open(log, encoding='utf-8')) + 1 if os.path.exists(log) else 1
record = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'seq': seq,
    'type': 'judgment',
    'decision_point': 'clustering_quality',
    'scope': {'type': 'session'},
    'run_ref': 'step1_prepare.leiden_cluster#1',
    'inputs': [
        {'path': 'step1_prepare.leiden_cluster.silhouette_overall.mean', 'value': 0.15},
    ],
    'output': {'decision': 'clustering_adjust', 'confidence': 'high', 'action': 'recluster'},
    'reasoning': 'silhouette too low, need to adjust resolution'
}
with open(log, 'a', encoding='utf-8') as f:
    f.write(json.dumps(record, ensure_ascii=False) + '\n')
"
```

### 会话开始和结束

- 开局:追加 `type: "session_start"` 记录(含 dataset 元数据)
- 收尾:追加 `type: "session_end"` 记录(含 final_summary)

### 判断记录字段

| 字段 | 必填 | 说明 |
|---|---|---|
| inputs | 是 | [{path, value}] — 你实际读取并用于判断的变量 |
| output.decision | 是 | 枚举值(见 trajectory_design.md §3.2) |
| output.confidence | 是 | high / medium / low |
| reasoning | 是 | 你的推理链(自然语言) |
| run_ref | 是 | 你基于哪个 exec 记录的 run_id 做判断 |
| scope | 是 | {type:"session"} 或 {type:"cluster", cluster_id:"0"} |
```

---

## 11. 验证

```
python scripts/validate_log.py --project-dir ./output

检查:
- run_log.jsonl 存在且每行是合法 JSON
- seq 单调递增,无重复
- 每条 exec 记录有 run_id, metrics
- 每条 judgment 记录有 decision_point, scope, run_ref, inputs, output, reasoning
- run_ref 指向的 run_id 在 log 中存在
- 每个 decision_point + scope 至少有一条 judgment 记录
- (candidate_disambiguate/refine_effect 条件存在除外)
- output.decision 在对应判断点的枚举值中
- run_id 格式为 {step}.{op}#{attempt}
- ts 格式为 ISO 8601
```

---

## 12. 一次完整 session 的日志样例

```jsonl
{"ts":"2026-07-23T14:00:00Z","seq":1,"type":"session_start","session_id":"sess-001","dataset":{"id":"SRP171040","n_cells_raw":33956,"organism":"Arabidopsis thaliana","organ":"root","batch_key":"sample","n_batches":5}}
{"ts":"2026-07-23T14:00:01Z","seq":2,"type":"exec","run_id":"step1_prepare.compute_qc#1","parameters":{"organ":"root"},"metrics":{"n_cells":33956,"n_genes":53678}}
{"ts":"2026-07-23T14:00:01Z","seq":3,"type":"exec","run_id":"step1_prepare.qc_distribution#1","parameters":{},"metrics":{"pct_counts_mt":{"mean":2.1,"p99":12.3,"bimodality":0.3},"n_genes_by_counts":{"median":3200,"bimodality":0.8}}}
{"ts":"2026-07-23T14:02:00Z","seq":4,"type":"judgment","decision_point":"qc_threshold","scope":{"type":"session"},"run_ref":"step1_prepare.qc_distribution#1","inputs":[{"path":"step1_prepare.qc_distribution.pct_counts_mt.p99","value":12.3},{"path":"step1_prepare.qc_distribution.n_genes_by_counts.bimodality","value":0.8}],"output":{"decision":"threshold_set","confidence":"high","action":"set_max_mt_pct_15"},"reasoning":"p99=12.3, set max_mt_pct=15 to trim tail. n_genes bimodality=0.8 suggests mixed population, set min_genes=300 at valley."}
{"ts":"2026-07-23T14:03:00Z","seq":5,"type":"exec","run_id":"step1_prepare.filter_cells#1","parameters":{"min_genes":300,"max_mt_pct":15},"metrics":{"n_before":33956,"n_after":33940,"funnel":{"min_genes":{"n_lost":10},"max_mt_pct":{"n_lost":6}}}}
{"ts":"2026-07-23T14:03:01Z","seq":6,"type":"exec","run_id":"step1_prepare.pca#1","parameters":{"n_comps":50},"metrics":{"variance_explained":[0.08,0.05,0.03],"n_pcs_for_80pct":25}}
{"ts":"2026-07-23T14:03:02Z","seq":7,"type":"exec","run_id":"step1_prepare.leiden_cluster#1","parameters":{"resolution_list":"0.4,0.6,0.8,1.0,1.2"},"metrics":{"resolution_cluster_counts":{"0.4":18,"0.6":24,"0.8":29},"silhouette_overall":{"mean":0.35},"n_singleton":0}}
{"ts":"2026-07-23T14:05:00Z","seq":8,"type":"judgment","decision_point":"resolution_select","scope":{"type":"session"},"run_ref":"step1_prepare.leiden_cluster#1","inputs":[{"path":"step1_prepare.leiden_cluster.resolution_cluster_counts","value":{"0.4":18,"0.6":24,"0.8":29}}],"output":{"decision":"resolution_chosen","confidence":"medium","action":"target_resolution_0.8"},"reasoning":"cluster count plateaus around 24-29 between res 0.6-0.8. Choosing 0.8 for 29 clusters."}
{"ts":"2026-07-23T14:05:30Z","seq":9,"type":"judgment","decision_point":"clustering_quality","scope":{"type":"session"},"run_ref":"step1_prepare.leiden_cluster#1","inputs":[{"path":"step1_prepare.leiden_cluster.silhouette_overall.mean","value":0.35},{"path":"step1_prepare.leiden_cluster.n_singleton","value":0}],"output":{"decision":"clustering_accept","confidence":"medium","action":"proceed_to_step2_markers"},"reasoning":"silhouette=0.35 is reasonable (0.25-0.5 range), no singleton clusters. Accepting resolution 0.8."}
{"ts":"2026-07-23T14:10:00Z","seq":10,"type":"exec","run_id":"step2_markers.de_rank#1","parameters":{"min_pct1":0.5},"metrics":{"de_distribution":{"logfc_mean":1.2,"pval_inflation_lambda":1.1}}}
{"ts":"2026-07-23T14:10:01Z","seq":11,"type":"exec","run_id":"step2_markers.filter_markers#1","parameters":{"min_pct1":0.5,"min_pct1_pct2":0.25},"metrics":{"n_before_filter":500,"n_markers":30,"filter_efficiency":0.06,"n_grey_zone":15}}
{"ts":"2026-07-23T14:12:00Z","seq":12,"type":"judgment","decision_point":"marker_quality","scope":{"type":"session"},"run_ref":"step2_markers.filter_markers#1","inputs":[{"path":"step2_markers.filter_markers.n_markers","value":30},{"path":"step2_markers.filter_markers.filter_efficiency","value":0.06}],"output":{"decision":"markers_accept","confidence":"high","action":"proceed_to_step3c_kg"},"reasoning":"30 markers per cluster, filter efficiency 6% is reasonable. Accepting."}
...
{"ts":"2026-07-23T15:30:00Z","seq":95,"type":"session_end","final_summary":{"n_clusters":29,"n_unknown":0,"unknown_rate":0.0,"n_unique_labels":27,"run_count":8,"judgment_count":67}}
```

---

## 13. 实施计划

| Phase | 内容 | 产出 |
|---|---|---|
| **L-1** | 在 `common.py` 中实现 `append_log()` + `next_run_id()` | 通用日志函数 |
| **L-2** | 在每个 pipeline 脚本的每个原子操作后调用 `append_log()` | 各脚本产出 exec 记录 |
| **L-3** | 在 SKILL.md 中增加运行日志指导 | LLM 知道何时、怎么写 judgment 记录 |
| **L-4** | 实现 `validate_log.py` | 验证日志完整性 |
| **L-5** | 实现 `export_log.py` | 导出微调训练对 + 自我纠正对 |
| **L-6** | 实现 `aggregate_logs.py` | 跨数据集聚合 |

Phase L-1~L-2 可与 `tool_design.md` 的 Phase A~E 并行。L-3~L-6 在有第一批日志数据后实施。

---

## 14. 与其他设计文档的关系

```
atomic_operations.md            ← pipeline 有哪些操作 (WHAT)
operations_metrics_catalog.md   ← 每个操作输出什么指标 (WHY)
tool_design.md                  ← 怎么实现,加载策略 (HOW)
trajectory_design.md            ← 指标和判断怎么记录 (LOG)
```

数据流:

```
atomic_operations.md
    │ defines 46 ops
    ▼
operations_metrics_catalog.md
    │ defines 247 metrics per op
    ▼
tool_design.md
    │ implements ops, computes metrics
    ▼
pipeline scripts
    │ each op calls append_log() with run_id = {step}.{op}#{attempt}
    ▼
run_log.jsonl
    │ LLM reads metrics, makes judgments, appends judgment records
    │
    ├──→ export_log.py → fine_tune_pairs.jsonl (当前有效判断)
    └──→ export_log.py → self_correction_pairs.jsonl (判断改主意前后对比)
```

`run_log.jsonl` 是唯一枢纽:pipeline 脚本往里写指标,LLM 往里写判断,导出脚本从里读训练对。一个文件,append-only,时间线自然。
