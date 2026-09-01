---
title: 'Story 6.8 — B3 / B4 轨迹分析'
type: feature
created: 2026-09-01
status: in-review
review_loop_iteration: 1
context: []
baseline_commit: 2f79ba53dcff6489c509d78905b1da27954278b0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** B1 r1 主评估(Story 6.6)+ B1 §6 辅助实验(Story 6.7)只产出"准确率 / 成本"等量化指标,未挖掘 LLM 真实用了哪些指标、改过多少次主意、纠正是否改善。B3/B4 轨迹分析是 Epic 7 SKILL.md / references 迭代的反哺依据,缺失则无法证明 `.skill` 交付物经过证据驱动的精简。

**Approach:** 纯只读分析 — 写两个独立脚本(零新 LLM 调用,零新 h5ad load)扫 `output/B1/arm3_llm/`, `output/p5_evals_r2/`, `output/N1_on/` 三个真实 LLM session 的 `run_log.jsonl`,分别产出 `experiments/B3/{metric_usage_by_decision.json, minimal_sufficient_set.json, B3_report.md}` 与 `experiments/B4/{self_correction_pairs.json, improvement_rate.json, B4_report.md}`;最终写 `story-6-8-closure.md` 对照 `design/experiment_implementation.md` §3.3 / §3.4 判定线给出 B3 / B4 结论标签(成立/不成立/无结论)。

## Boundaries & Constraints

**Always:**
- A1. 纯只读分析,**不允许修改 / 重新生成** 输入的 `run_log.jsonl` 与 `final_annotations.json`。
- A2. 脚本必须容忍部分 run_log.jsonl 行 JSON 损坏(`json.JSONDecodeError` 跳过 + 计数,见 `output/B1/arm3_llm/run_log.jsonl` 实测 0 行坏;但兜底必须有)。
- A3. 输入路径列表**写死**为三个已存在的 LLM run,不在脚本里 enumerate `output/` — 避免无意中扫到 arm1/arm2 scripted run(其 judgment 字段是脚本填的,非真 LLM 推理)。
- A4. 任何 per-cluster 路径(`step4_judge.rank_candidates.cluster{N}.*`)做 B3 分析时必须去 cluster_id 后聚合(否则核心子集会被 per-cluster 同一 metric 撑爆,导致判定线"≤60"无意义);B4 不需要(按 (decision_point, scope) 分组,scope 已含 cluster_id)。
- A5. 脚本用 stdlib only(`json`/`collections`/`pathlib`/`statistics`),不引入 pandas/numpy;数据规模 319 judgments / 426 paths 在 stdlib 内可秒级完成。
- A6. 产物 JSON 用 `ensure_ascii=False, indent=2`,中文 reasoning 可读;产物 markdown 用中文(项目 communication_language = 简体中文)。
- A7. closure 报告必须显式给出**判定线对照表**(成立/不成立/无结论 三态)+ 已知局限(单数据集 / scripted vs LLM 区分 / 部分会话无 self-correction)。

**Ask First:**
- F1. 若 B3 核心子集 >60(可能因 per-cluster 展开),**如何处理**?候选:(a)改判定线;(b)增加"按 `{step}.{op}.{base_path}` 折叠后再算"的子分析并报告两种口径;(c)宣告 B3 不成立,推迟到 Story 7.x。
- F2. 若 B4 改善率 <50%,**是否仍写 closure**?候选:(a)写 closure,标签"不成立"+ 决策点级诊断表;(b)直接挂起回 Epic 7 重写 SKILL.md 后再跑。
- F3. 若三个 LLM run 中任一缺失(用户清理过 output/),**是否回退**到只剩一个 run 的分析?候选:(a)回退并标注 N;(b)HALT 等用户恢复数据。

**Never:**
- N1. 不调用任何 LLM API;B3/B4 是离线分析,不是新一轮评估。
- N2. 不触碰 h5ad、不重跑任何 step 脚本;轨迹分析必须可"瞬间完成"(< 30 秒 wallclock)。
- N3. 不修改 SKILL.md / references — Story 6.8 只产**反哺输入**(`B3_report.md` + `B4_report.md`);SKILL.md 改写属于 Story 7.2/7.3 的责任。
- N4. 不重写 `experiments/evaluate_cell_level.py` 或 `scripts/validate_log.py` 等已有工具;只新增 `experiments/B3_*.py` 与 `experiments/B4_*.py` 两个脚本。
- N5. 不跑 `python -m pytest`(本 story 不改 harness 代码;若改动了 trajectory_schema.py 之类的共享契约,需跑;但按 N4 不允许)。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | 3 个 run_log.jsonl 存在且全合法 | `experiments/B3/*.json` + `experiments/B4/*.json` + 两份 markdown 报告 + closure | N/A |
| BAD_JSON_LINE | run_log.jsonl 中夹杂 1-2 行 JSON 损坏 | 脚本 `bad_json_count > 0` 时打印 warning,跳过坏行,继续分析 | warning stderr,不抛异常 |
| MISSING_RUN | `--runs` 列表中某路径不存在 | 脚本打印 "SKIP: <path>" 并从分析中排除,剩余 run 仍出报告(若 ≥1 run 剩余) | 全部缺失 → exit code 1 + HALT-style stderr |
| PER_CLUSTER_FLOOD | candidate_gap 决策点引用 354 个 unique paths(因 per-cluster) | 主聚合:per-cluster 折叠 → 报告两种口径(原始 vs 折叠);判定线对照用折叠口径 | N/A |
| ZERO_SELF_CORRECTION | 某 run 全程无重复 (decision_point, scope) | B4 该 run 标 0 multi-version;不阻断 | N/A |
| EMPTY_JUDGMENTS | run 全是 exec 无 judgment | 报告"该 run 无 judgment 可分析"并从总和中扣除 | N/A |

</frozen-after-approval>

## Code Map

- `design/experiment_implementation.md` §3.3 / §3.4 — B3 / B4 预注册判定线与脚本行为约束
- `design/trajectory_design.md` §2.5 / §5.1 — judgment 记录的 inputs[].path 格式与 scope 结构
- `output/B1/arm3_llm/run_log.jsonl` (294 行, 139 judgments, 18 multi-version) — B3/B4 主源
- `output/p5_evals_r2/run_log.jsonl` (117 judgments, 0 multi-version) — P5 r2 closure session
- `output/N1_on/run_log.jsonl` (63 judgments, 0 multi-version) — N1 notebook-on session
- `experiments/B3/` — 新建目录,B3 产物落地
- `experiments/B4/` — 新建目录,B4 产物落地
- `_bmad-output/implementation-artifacts/story-6-8-closure.md` — 最终 closure 报告

## Tasks & Acceptance

**Execution:**

- [x] `experiments/B3_metric_usage.py` — **新建** — 扫 3 个 run 的 `judgment.inputs[].path`,输出 `experiments/B3/metric_usage_by_decision.json`(`{decision_point: {path: count, ...}}`)与 `experiments/B3/path_frequency.json`(全量 paths 降序)。逻辑:`json.loads` + `Counter` 聚合,坏行跳过计数。纯 stdlib。

- [x] `experiments/B3_minimal_set.py` — **新建** — 读上一步产物,产出两种核心子集:(a)`minimal_sufficient_set_raw` — 被 ≥2 个决策点引用 **或** 单点频次 top-20;(b)`minimal_sufficient_set_folded` — 先把路径中的 `{cluster_id}` 段(`step4_judge.rank_candidates.cluster{N}.*`、`step5_refine.write_refined.cluster{N}.*` 等)替换为 `<CLUSTER_ID>` 后再聚合,同样规则取核心子集。输出 `experiments/B3/minimal_sufficient_set.json`,含 raw/folded 两套清单 + 各自大小 + 精简比(总 unique paths 数 / 核心子集大小)。

- [x] `experiments/B3_report.md` — **新建** — 由上面两个脚本生成的 JSON,人工/脚本各半填充的 markdown 报告,§1 摘要表(决策点 vs unique paths 引用 vs top-3 paths)+ §2 raw vs folded 对照 + §3 判定线"核心子集 ≤60"对照 + §4 已知局限。脚本可生成骨架,人工填具体数字。

- [x] `experiments/B4_self_correction.py` — **新建** — 对每个 run,按 (decision_point, scope_json) 分组 judgment 记录,取 seq 最小为 v_first、seq 最大为 v_last;输出 `experiments/B4/self_correction_pairs.json`(`{run: [{decision_point, scope, v_first_decision, v_last_decision, decision_changed, n_versions, intervening_runs: [run_ref list]}, ...]}`)与 `experiments/B4/multi_version_summary.json`(按 run 分组的 multi-version 总数 / decision 变化数)。

- [x] `experiments/B4_improvement.py` — **新建** — 读上一步 `self_correction_pairs.json`,逐决策点套用 `design/experiment_implementation.md` §3.4 表格里的"改善判据":
  - `clustering_quality`:取 v_first_run_ref 与 v_last_run_ref 对应 exec 记录的 `silhouette_overall.mean` 与 `n_singleton` 字段对比;
  - `marker_quality`:取对应 exec 的 `n_clusters_in_range_10_50` 字段(代理"n_markers 进入 [10, 50] 区间的簇数")对比 — review iteration 1 修正:原 spec 写 `n_markers` 但实际 run_log 产出的是后者,改为后者;
  - `refine_effect`:取子簇 silhouette 或 n_subclusters_with_distinct_type;
  - `label_confirm`:末版 decision 是否相对首版更明确(unknown→confirmed 视为改善);
  - **其他决策点**(无对应 exec 指标):使用**脚本内显式定义的 per-decision-point tier 表**(0/1/2 三档)算改善,仅在 v_last tier > v_first tier 时算改善 — review iteration 1 修正:原 spec 写 "import `experiments/judges/rule_judge.py`",但该脚本是 project_dir-coupled 且有写副作用,不适合离线只读分析;改为脚本内显式 tier 表,仍满足 `experiment_implementation.md §3.4` "在脚本中显式定义, 不靠 LLM 自评"约束。
  - 输出 `experiments/B4/improvement_rate.json`(`{run: {decision_point: {n_pairs, n_improved, rate, ...}, ...}, ...}` + 总计行 + conditional_rate(默认)+ unconditional_rate(补充)+ both-by_run_x_decision_point 与 by_decision_point 的 improvement_rate 用 None 表示空 bin(不混 0.0))。

- [x] `experiments/B4_report.md` — **新建** — §1 摘要表(每个 run 的 multi-version 总数 / decision-changed / 改善率)+ §2 决策点级诊断表(每个决策点配对数 / 改善率 / 典型案例)+ §3 判定线"改善率 ≥50%"对照(全 run 汇总 + per-run)+ §4 已知局限(单数据集 / 无控制组 / 部分决策点无可量化 exec 指标时仅靠 oracle 判定)。

- [x] `_bmad-output/implementation-artifacts/story-6-8-closure.md` — **新建** — 参考 `story-6-7-closure.md` 的章节骨架,但内容专属本 story:§1 摘要(B3 / B4 判定线 + 结论标签)+ §2 B3 关键数字 + §3 B4 关键数字 + §4 判定线对照表 + §5 反哺 Epic 7 的具体建议(给 7.2 / 7.3 用)+ §6 已知局限 + §7 与 B1 / Story 6.7 报告呼应。

**Acceptance Criteria:**

- AC-1 — B3 脚本可运行:
  - **Given** 三个 run_log.jsonl 存在(`output/B1/arm3_llm/`, `output/p5_evals_r2/`, `output/N1_on/`)
  - **When** 运行 `python experiments/B3_metric_usage.py && python experiments/B3_minimal_set.py`
  - **Then** `experiments/B3/metric_usage_by_decision.json`、`path_frequency.json`、`minimal_sufficient_set.json` 三个文件全部存在且非空;JSON 顶层为 dict,内容含至少一个决策点条目;wallclock < 5 秒。

- AC-2 — B3 核心子集折叠口径有结论:
  - **Given** AC-1 产物存在
  - **When** 读 `minimal_sufficient_set.json` 的 `folded_size` 字段
  - **Then** 该字段是 int(>0 且 ≤ 总 unique paths),closure §4 判定线对照表必须**显式给出** raw_size / folded_size 与判定线"≤60"的对照结果(成立 / 不成立 / 边界)+ 解释为何选 folded 作为主判定口径。

- AC-3 — B4 脚本可运行:
  - **Given** 三个 run_log.jsonl 存在
  - **When** 运行 `python experiments/B4_self_correction.py && python experiments/B4_improvement.py`
  - **Then** `experiments/B4/self_correction_pairs.json`、`multi_version_summary.json`、`improvement_rate.json` 三个文件全部存在且非空;arm3_llm 的 multi-version 数 ≥ 1(实测 18)。

- AC-4 — B4 改善率有结论:
  - **Given** AC-3 产物存在
  - **When** 读 `improvement_rate.json` 的总计行
  - **Then** 字段含 `total_n_pairs`(>0)+ `total_n_improved` + `total_rate`(0-1);closure §4 必须给出判定线"≥50%"的明确标签。

- AC-5 — 报告与 closure 一致:
  - **Given** B3/B4 六个产物文件齐全
  - **When** 写 `experiments/B3_report.md` + `experiments/B4_report.md` + `story-6-8-closure.md`
  - **Then** 三个 markdown 文件存在 + closure §4 判定线对照表每行有"成立 / 不成立 / 无结论 / 边界"四态之一的明确标签 + §5 反哺建议至少 3 条具体可执行项(给 7.2/7.3 用)。

## Spec Change Log

### Review iteration 1 (2026-09-01) — bad_spec fixes + patches

**Triggering findings**: F1 (B4_improvement oracle deviation from spec wording) + F3 (B3 folded-set framing claim too strong vs actual output).

**What was amended**:

1. **B4_improvement task** (§Tasks & Acceptance): replaced "import `experiments/judges/rule_judge.py`" with "脚本内显式 tier 表(0/1/2)"。Rationale: rule_judge.py is project_dir-coupled + has write side effects (writes to run_log.jsonl); importing it for offline read-only analysis is inappropriate. Script-internal tier table satisfies the same `experiment_implementation.md §3.4` constraint ("在脚本中显式定义, 不靠 LLM 自评") while being deterministic and side-effect-free.

2. **B3 Design Notes**: added "已知语义局限 (F3 / F15)" paragraph clarifying that folded 83 mixes atomic metrics + cluster-level wrappers + parameter paths + summary paths; NOT a strict subset of `operations_metrics_catalog.md`'s 247 atomic metrics. Closure report must acknowledge this approximation.

3. **B4 Design Notes**: replaced "B4 改善判据的 oracle 复用" with "B4 改善判据的脚本内 tier 表 (F1)" describing the per-decision-point tier scheme.

**Known-bad state avoided**:
- F1: implementing B4 with direct rule_judge.py import would have caused offline analysis to write to run_log.jsonl (corrupting the input). The script-internal tier table keeps analysis strictly read-only.
- F3: claiming "folded 83 = subset of 247 atomic metrics" would have overreached the spec. The closure now correctly says the comparison is approximate, not exact.

**KEEP instructions** (what survived review and must not be re-derived away):
- B3 + B4 stay stdlib-only (no pandas/numpy).
- B3 path-folding stays in B3_minimal_set.py (not split into a 3rd file).
- B4 improvement classification keeps 5 result categories: improved / unchanged / no_change_in_decision / no_evidence / unscored. Excluding `no_change_in_decision` from rate denominator is a deliberate analytic choice (review F29 surfaced both conditional and unconditional rates).
- closure §4 judgment line table stays 4 rows (B3 raw / B3 folded / B4 improvement_rate / B4 decision-point breakdown), each with one of 4-state labels (成立/不成立/无结论/边界).
- The 3 LLM run paths stay hard-coded in DEFAULT_RUNS (per spec A3).

## Design Notes

### B3 per-cluster 折叠的口径选择 — 关键决策

实测 `candidate_gap` 一个决策点就引了 354 个 unique paths,几乎全是 `step4_judge.rank_candidates.cluster{N}.first.cell_type` 的不同 cluster_id 展开。若不折叠,核心子集会比 247 个原子指标还多,直接违反"LLM 引用了哪些指标"的本意。

**折叠口径**:把路径中所有形如 `cluster{N}` 的段替换为 `<CLUSTER_ID>` 后聚合。例如:
- `step4_judge.rank_candidates.cluster3.first_count` → `step4_judge.rank_candidates.<CLUSTER_ID>.first_count`
- `step4_judge.rank_candidates.cluster3.first.cell_type` → `step4_judge.rank_candidates.<CLUSTER_ID>.first.cell_type`

**已知语义局限(review iteration 1, F3 / F15)**:折叠后的 unique paths 集合**不严格等同于** "247 个原子指标"。folded 集合同时包含:
- 原子 metric 路径(如 `step4_judge.rank_candidates.<CLUSTER_ID>.first_count`)
- 簇级 wrapper 路径(如 `step4_judge.<CLUSTER_ID>.first` 是另一条 schema 路径,不与上面折叠合并)
- 参数路径(如 `step1_prepare.run.params.max_mt_pct`、`step1_prepare.run.n_cells_raw`)
- summary 路径(如 `step5_refine.summary.n_decisive`)

把 folded 集合与 `operations_metrics_catalog.md` 的 247 原子指标对比时,这种对比是**近似可比**,不是严格子集。closure 报告需明示这一点。Epic 7 SKILL.md 精简时应优先合并 schema 多重命名(见 closure §5 建议 #2),这能让 folded 集合更接近严格的 247 子集。

raw 口径(不过滤)与 folded 口径(折叠)在 closure 报告中并列报告,但**判定线对照以 folded 为准**,raw 只作"per-cluster 展开程度"的诊断。

### B4 改善判据的脚本内 tier 表(review iteration 1, F1)

`experiments/B4_improvement.py` 对"无 exec 指标可比"的决策点(如 `de_method`、`kg_match`、`batch_effect`)使用**脚本内显式定义的 per-decision-point tier 表**(0/1/2 三档):

- `de_method`: `wilcoxon` / `pseudobulk_rare` / `pseudobulk_all` 全部归为 tier 1(无方向性,因为脱实验校准无法判断哪个更好)
- `kg_match`: `id_match_no (0) < id_match_partial (1) < id_match_ok (2)`
- 其他决策点:见 `_oracle_improved()` 内 `DECISION_TIERS` 字典

仅在 v_last tier > v_first tier 时算改善。这避免了"LLM 自评"的主观偏差,符合 `design/experiment_implementation.md` §3.4"在脚本中显式定义,不靠 LLM 自评"的约束。

**为什么不直接 import `experiments/judges/rule_judge.py`**:该脚本是 per-cluster 调用 + project_dir-coupled + 有写副作用(写 run_log.jsonl)。不适合离线只读轨迹分析。脚本内 tier 表是显式的、确定的,满足同一约束。

## Verification

**Commands:**

- `python experiments/B3_metric_usage.py --runs output/B1/arm3_llm/run_log.jsonl output/p5_evals_r2/run_log.jsonl output/N1_on/run_log.jsonl --out experiments/B3/` — expected: 3 个 JSON 写出 + stderr 有 "TOTAL judgments: 319" 之类汇总
- `python experiments/B3_minimal_set.py --in experiments/B3/ --out experiments/B3/minimal_sufficient_set.json` — expected: JSON 含 `raw_size`/`folded_size` 字段,fold_size < raw_size
- `python experiments/B4_self_correction.py --runs ... --out experiments/B4/` — expected: arm3_llm 的 multi-version 数 ≥ 1
- `python experiments/B4_improvement.py --in experiments/B4/self_correction_pairs.json --oracle experiments/judges/rule_judge.py --out experiments/B4/improvement_rate.json` — expected: 改善率字段为 0-1 的 float

**Manual checks (if no CLI):**

- 打开 `experiments/B3_report.md` 与 `experiments/B4_report.md`,核对每个数字与对应 JSON 一致
- 打开 `story-6-8-closure.md` §4,确认判定线对照表 4 行(B3 raw/B3 folded/B4 改善率/B4 决策点分布)每行有标签
- 打开 `experiments/B3/minimal_sufficient_set.json`,确认 folded 列表里的路径已把 `cluster{N}` 替换为 `<CLUSTER_ID>`
