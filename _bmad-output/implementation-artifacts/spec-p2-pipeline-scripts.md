---
title: 'P2 Pipeline scripts(cell-annotation skill 的 scripts/)'
type: 'feature'
created: '2026-08-10'
status: 'done'
review_loop_iteration: 0
baseline_commit: '24eeb7fae849de98ddcfe973056b7b7e69895fbe'
context:
  - design/tool_design.md
  - design/atomic_operations.md
  - design/trajectory_design.md
  - design/operations_metrics_catalog.md
  - design/implementation_plan.md
  - skills/echo/scripts/echo.py
  - annot_harness/skill_loader.py
  - annot_harness/dispatcher.py
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 设计文档(47 原子操作 / 247 指标 / run_log 轨迹)已冻结,但 pipeline 代码(`common.py` + 7 个 `stepN_*.py`)完全不存在,P2 是 skill 的 scripts 层,是 P3 打包的前提。

**Approach:** 在 `skills/cell-annotation/scripts/` 落地标准 CLI 脚本:每个脚本用 argparse 声明子命令、支持 `--dump-schema` 自描述(加载器派生 tool_schemas/tool_runtime 的单一事实源);每个原子操作执行后 `append_log()` 写 `run_log.jsonl`;严格"一个子命令 = 一次 h5ad 加载",全 pipeline 共 4 次加载(1 raw + 3 proc),step7_diagnose 0 加载(读 obs_snapshot.csv + JSON)。

## Boundaries & Constraints

**Always:**
- 加载纪律:step1 `run` 1×raw、step2 `run` 1×proc、step5 `run` 1×proc、step6 `run` 1×proc(backed 模式优先,失败回退全量);step3/4/7 与 step6 `report` 0 加载。recluster 1×proc。
- 工具契约:脚本 stdout **最后一行**必须是 JSON `{"status":"ok","data":{...}}` 或 `{"status":"error",...}`;`--dump-schema` 输出 `{"tools":[{subcommand,description,args[{name,type,required,default,help}]}]}`。
- 日志:只写 `<project-dir>/run_log.jsonl`(append-only,不覆盖);每原子操作完成后写 exec 记录,`run_id={step}.{op}#{attempt}`,`next_run_id` 从既有日志取最大 attempt+1;`append_log` 自动填 ts/seq。
- sidecar:`obs_snapshot.csv`/`var_snapshot.csv` 与 `processed.h5ad` 在 step1 `write_output` 同步写出;step7 与 step1 的 OBS 级操作读 sidecar 不读 h5ad。
- 植物特化:QC 计算 `pct_counts_chloroplast`(ATCG 前缀)与 `pct_counts_mt` 并列;mt/cp 基因按前缀正则标记并可 CLI 覆盖;过滤漏斗分步记录 n_lost。
- NEO4J 凭据优先级 CLI flags > env(NEO4J_URI/USER/PASSWORD)> 默认;密码绝不硬编码/写文件。
- 落点 `skills/cell-annotation/scripts/`;代码标识符与 docstring 英文。
- 每个 op 的指标块遵循 `operations_metrics_catalog.md` 的路径命名(供 LLM 以 `{step}.{op}.{metric_path}` 引用)。

**Ask First:**
- scrublet 未安装:若 `pip install scrublet` 失败,降级方案(如 doubletdetection 或基于表达的简单评分)需询问。
- 需修改设计冻结的默认阈值/参数(如 min_genes、max_mt_pct、min_pct1)时。

**Never:**
- 不新增 h5ad 加载(总加载数不得超 4+1(recluster)次)。
- 不手写 tool_schemas JSON 或重复的 schema 文件(必须由 `--dump-schema` 派生)。
- 不创建 run_log.jsonl 之外的并行日志文件。
- 不做 P3 内容(SKILL.md / references / assets / evals)。
- 不实现跨物种注释逻辑。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | `step1_prepare.py run --input dataset/h5ad/SRP171040.h5ad --project-dir output` | processed.h5ad + obs_snapshot.csv + var_snapshot.csv + qc_metrics.json;stdout 末行 JSON;run_log 追加 16 条 exec | 非零退出 + stderr 说明;stdout 末行 `{"status":"error",...}` |
| DUMP_SCHEMA | `python <script> --dump-schema` | stdout 末行合法 JSON,args 覆盖全部 CLI 参数 | 加载器报 SkillError(清晰错误) |
| RESUME | run_log.jsonl 已有 `step1_prepare.leiden_cluster#1` | 重跑写 `#2`,旧记录不覆盖 | — |
| BACKED_FALLBACK | step6 backed 模式不支持当前 h5ad | try/except 回退全量加载 | 回退并日志标注 |
| STEP7_ZERO_LOAD | step7 `run` | 只读 obs_snapshot.csv + 各 JSON;进程内无 h5ad 读取 | 缺 sidecar/JSON 时报清晰错误 |
| NO_KG_HIT | 某簇 marker 全无 KG 命中 | 候选为空,first=None;后续 unknown 分支可用 | 不崩溃 |

</frozen-after-approval>

## Code Map

- `skills/cell-annotation/scripts/common.py` -- 9 通用函数 + append_log/next_run_id(设计 tool §6、trajectory §9)
- `skills/cell-annotation/scripts/step1_prepare.py` -- run(16 op)/metrics/recluster + dump-schema
- `skills/cell-annotation/scripts/step2_markers.py` -- de_rank~write_markers(5 op)
- `skills/cell-annotation/scripts/step3_kg.py` -- connect~write_hits(5 op)+ test-connection
- `skills/cell-annotation/scripts/step4_judge.py` -- rank_candidates/write_annotations(2 op)
- `skills/cell-annotation/scripts/step5_refine.py` -- candidate_autocorr~write_refined(8 op)
- `skills/cell-annotation/scripts/step6_validate.py` -- marker_expression~write_final(5 op)+ report
- `skills/cell-annotation/scripts/step7_diagnose.py` -- hit_rate~cross_cluster(6 op)
- `knowledge/metrics_interpretation.md` -- E-1:新指标解读补全
- `dataset/init.py`、`annot_harness/skill_loader.py`、`skills/echo/scripts/echo.py` -- 契约参照(只读)
- `name_map4Arabidopsis_thaliana_symbol.json` -- step3 `--gene-key` 用 TAIR→symbol 映射(只读)

## Tasks & Acceptance

**Execution:**
- [x] `skills/cell-annotation/scripts/common.py` -- 实现 9 通用函数 + append_log/next_run_id + JSON 读写/路径解析辅助 -- 所有 step 脚本共享
- [x] `skills/cell-annotation/scripts/step1_prepare.py` -- run(16 op 单次加载+全部 Step1 指标+每 op append_log)/metrics/recluster + `--dump-schema` -- A-1
- [x] `skills/cell-annotation/scripts/step2_markers.py` -- de_rank(BH-FDR/AUC/inflation λ)+pct1_pct2+filter_markers(漏斗)+pseudobulk_de+write_markers -- B-1
- [x] `skills/cell-annotation/scripts/step3_kg.py` -- connect/query_genes(`--gene-key` name_map)/query_hierarchy(ancestors 入 kg_hits)/aggregate_candidates/write_hits + test-connection -- C-1
- [x] `skills/cell-annotation/scripts/step4_judge.py` -- rank_candidates(count_ratio/count_diff/confidence_diff/ancestor_overlap)+write_annotations -- C-2
- [x] `skills/cell-annotation/scripts/step5_refine.py` -- candidate_autocorr→subcluster→subcluster_de→subcluster_kg(缓存复用)→marker_overlap(Jaccard)→type_membership→unknown_overlap→write_refined -- C-3
- [x] `skills/cell-annotation/scripts/step6_validate.py` -- marker_expression(Cohen's d/AUC/fold_change)+violin+global_summary+report+final;backed 按列读 -- B-2
- [x] `skills/cell-annotation/scripts/step7_diagnose.py` -- hit_rate/candidate_count/first_second/batch_entropy(读 obs_snapshot)/metadata_check/cross_cluster;0 次加载 -- D-1
- [x] `knowledge/metrics_interpretation.md` -- 补全新增指标(分布扩展、过滤漏斗、BH-FDR/AUC/λ、candidate 熵、Cohen's d/Jaccard、跨簇指标)解读 -- E-1

**Acceptance Criteria:**
- Given 原始 h5ad, when `step1_prepare.py run` 执行, then 产出 processed.h5ad + obs_snapshot.csv + var_snapshot.csv + qc_metrics.json,run_log 含 step1 全部 16 op 的 exec 记录。
- Given processed.h5ad, when `step2_markers.py run` 执行, then 产出 markers.csv/markers.json,run_log 含 5 条 step2 exec 记录,de_rank 指标含 BH_adjusted_pval/AUC。
- Given 无 h5ad 输入, when `step7_diagnose.py run` 执行, then 只读 obs_snapshot.csv + JSON 产出 step7_diagnose.json + report.md,run_log 含 6 条 step7 exec 记录(0 次 h5ad 加载)。
- Given 任意 stepN 脚本, when 执行 `--dump-schema`, then stdout 末行为合法 JSON 且 `annot_harness/skill_loader.load_skill("skills/cell-annotation")` 聚合成功、覆盖全部子命令。
- Given 已存在的 run_log.jsonl, when 重跑同操作, then 追加 `#attempt+1` 记录,旧记录不被修改(append-only)。
- Given 完整数据集, when 顺序执行全 pipeline(step1→7), then 总 h5ad 加载次数 ≤ 4,47 op 全部有 exec 记录。
- Given 全部新指标, when 更新 knowledge/metrics_interpretation.md, then run_log 中出现的指标字段均有解读条目。

## Design Notes

**h5ad 加载纪律(核心不变量):** 一次加载内完成该数据层级所需全部 op。step1 `run` 在内存中串行执行 load_data→write_output 的 16 个 op 并累计指标;后续脚本只读 JSON/sidecar 或按计划再加载。**不允许**在 step3/4/7 中为任何指标加载 h5ad。

**attempt 续跑:** `next_run_id(log_path, step, op)` 扫描既有 `{step}.{op}#N` 记录取 max(N)+1;纯追加,永不覆盖。`append_log` 自动补 `ts`(ISO8601 UTC)与 `seq`(行数+1)。

**dump-schema 契约(参照 skills/echo/scripts/echo.py):** 每个子命令的 argparse action 内省为 `{name,type,required,default,help}`;`--dump-schema` 时 stdout 只打印一行 JSON。加载器(annot_harness/skill_loader.py)已实现聚合与 `{script_stem}__{subcommand}` 命名,脚本只需遵守输出格式。

**基因前缀:** 植物特化——叶绿体 `^(ATCG)`、线粒体 `^(ATMG|MT-)`(CLI 可覆盖);`pct_counts_chloroplast` 与 `pct_counts_mt` 并行计算并在 QC/过滤/指标中同等对待。

**KG 查询:** var_names 为 TAIR locus ID,`--gene-key` 默认用 `name_map4Arabidopsis_thaliana_symbol.json` 映射为 symbol 后查询;query_hierarchy 查 `ontology_relation` 祖先(≤3 跳)写入 kg_hits.json 的 ancestors map,供 step4 ancestor_overlap 使用。

## Verification

**Commands:**
- `python skills/cell-annotation/scripts/step1_prepare.py --dump-schema`(每个脚本同法)-- expected: 末行合法 JSON
- `python -c "from annot_harness.skill_loader import load_skill; s=load_skill('skills/cell-annotation'); print(len(s.tool_schemas), len(s.tool_runtime))"` -- expected: 工具数 = 全部子命令数
- `python skills/cell-annotation/scripts/step7_diagnose.py run --project-dir <dir>`(以插桩/计数方式确认 0 次 h5ad 读取)
- 端到端:`step1 run → step2 run → step3 query → step4 run → step5 run → step6 run → step7 run`(用真实 2GB 数据集,容忍较长运行时间)
- `grep -c '"type":"exec"' run_log.jsonl` -- expected: 47

**Manual checks (if no CLI):**
- run_log.jsonl 中每条 exec 的 run_id 前缀覆盖全部 47 op;seq 单调递增
- obs_snapshot.csv 行数 == processed.h5ad 细胞数;var_snapshot.csv 行数 == HVG 数
- step6 report.md 每簇一节(标签/first-second/top-3 marker 表达/refine 历史)
- metrics_interpretation.md 新增解读与 operations_metrics_catalog 指标对齐

## Suggested Review Order

**入口:加载纪律与工具契约**

- 唯一入口:`--dump-schema` 派生 + stdout 末行 JSON 契约 + 47 op 计数
  [`common.py:739`](../../skills/cell-annotation/scripts/common.py#L739)

- `emit` 严格 JSON(allow_nan=False,评审 M8/C3 修复)
  [`common.py:754`](../../skills/cell-annotation/scripts/common.py#L754)

**run_log 轨迹(append-only)**

- `append_log` 自动 ts/seq;`next_run_id` 按 max attempt 续跑(评审 L7 已记录为 defer)
  [`common.py:117`](../../skills/cell-annotation/scripts/common.py#L117)

- `next_run_id` 扫描既有 `#N` 取 max+1
  [`common.py:145`](../../skills/cell-annotation/scripts/common.py#L145)

**统计正确性(评审 B1/B2/C2 修复)**

- gini 符号修复(评审 B2,实证 0.5/-0.222 纠错)
  [`common.py:192`](../../skills/cell-annotation/scripts/common.py#L192)

- describe_distribution:NaN 常量输入 → None,JSON 合法(评审 C2)
  [`common.py:227`](../../skills/cell-annotation/scripts/common.py#L227)

- BH-FDR 改用 scanpy 全基因校正(评审 B1;真实数据 500/500/500 是强信号所致)
  [`step2_markers.py:168`](../../skills/cell-annotation/scripts/step2_markers.py#L168)

**Step1:16 op 单次加载 + 修复**

- run 主流程:QC→过滤→scrublet→归一化→HVG→PCA→聚类→UMAP→批次,全指标在内存完成
  [`step1_prepare.py:596`](../../skills/cell-annotation/scripts/step1_prepare.py#L596)

- recluster 不再覆写 qc_metrics(评审 B3,读旧文件原位更新)
  [`step1_prepare.py:714`](../../skills/cell-annotation/scripts/step1_prepare.py#L714)

- HVG 非 HVG 离散度落差(评审 M1,子集前捕获全 var 表)
  [`step1_prepare.py:322`](../../skills/cell-annotation/scripts/step1_prepare.py#L322)

- 分辨率选择:单调递增时取中间值 + auto_knee_not_applicable(评审 M2)
  [`step1_prepare.py:414`](../../skills/cell-annotation/scripts/step1_prepare.py#L414)

**Step3/4:纯 JSON 路径**

- KG 查询:name_map 符号映射、strict-organ 大小写不敏感、重复符号全挂载(评审 H4)
  [`step3_kg.py:135`](../../skills/cell-annotation/scripts/step3_kg.py#L135)

- 层级祖先查询:0 跳跳过、错误隔离 query_errors(评审 L5/L6)
  [`step3_kg.py:202`](../../skills/cell-annotation/scripts/step3_kg.py#L202)

- 候选排名:gap 指标 + ancestor_overlap
  [`step4_judge.py:52`](../../skills/cell-annotation/scripts/step4_judge.py#L52)

**Step5:细化(1× proc 加载)**

- candidate_autocorr 与 subcluster 参数一致、min_cells 预跳过(评审 M3)
  [`step5_refine.py:103`](../../skills/cell-annotation/scripts/step5_refine.py#L103)

- 单子簇 → analyzed_nosplit 降级,不裸崩(评审 M4/C7)
  [`step5_refine.py:140`](../../skills/cell-annotation/scripts/step5_refine.py#L140)

**Step6/7:验证与诊断**

- backed 按列读 + 全量回退(评审 M7/C5)
  [`step6_validate.py:41`](../../skills/cell-annotation/scripts/step6_validate.py#L41)

- 簇集 ⊆ leiden 校验,陈旧产物清晰报错(评审 H6/B2)
  [`step6_validate.py:278`](../../skills/cell-annotation/scripts/step6_validate.py#L278)

- step7 零加载:批次熵读 obs_snapshot;real run_id(评审 H3)
  [`step7_diagnose.py:133`](../../skills/cell-annotation/scripts/step7_diagnose.py#L133)

**外围:文档**

- E-1 指标解读更新(路径对齐 + 新指标 + 评审修复说明)
  [`metrics_interpretation.md:25`](../../knowledge/metrics_interpretation.md#L25)

- P2 占位 SKILL.md(供加载器派生;P3 重写)
  [`SKILL.md:1`](../../skills/cell-annotation/SKILL.md#L1)

