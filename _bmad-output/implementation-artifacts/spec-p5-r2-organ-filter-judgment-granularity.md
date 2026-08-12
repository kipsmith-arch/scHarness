---
title: 'P5 第二轮:organ 候选重排序 + judgment 粒度规范'
type: 'feature'
created: '2026-08-11'
status: 'done'
baseline_commit: '6ea3929d35c49ed340ffe1f06eba833dcb7993dd'
review_loop_iteration: 1
context:
  - '{project-root}/design/trajectory_design.md'   # §3.1 决策点 scope/条数契约(事实源)
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** P5 第一轮把 organ 一致性排除留给 LLM(SKILL.md "mismatch 必须排除/降级"),每轮都靠 LLM 自觉;更实质的问题是 step3 排名函数只按 `marker_count` 排,导致 KG 跨组织污染的高-marker mismatch 候选(mesophyll/pollen/bundle sheath)霸占决策视图 top-k,LLM 被迫花注意力"无视"明显错的 top。E-1 实测每簇 44 候选中 ~一半 mismatch。judgment 粒度不受控——E-1 中 `refine_effect`/`label_confirm` 被写成 session 级(应逐簇),逐簇可追溯性丢失,违背 trajectory_design §3.1 契约。

**Approach:** ① organ 候选**结构化重排序**(不改生物领域知识、不维护白名单):`_rank_candidates` 排序键前置 `organ_status` 优先级(匹配目标 organ > partial > unknown > mismatch),同类内仍按 marker_count → confidence;决策视图 top-k 自然清洁,LLM 仍以生物学常识做最终判断,换 KG/物种/organ 无需改代码;② judgment 粒度规范化:`write_judgment.py` 按决策点强制 scope 类型,`validate_log.py` 同规则 + 簇覆盖度检查,SKILL.md §7 明示粒度表。

## Boundaries & Constraints

**Always:**
- organ 排序逻辑只放 `step3_kg._rank_candidates` 排序键;step5_refine 复用同一实现 + 同一 gene_to_cts,自动一致
- organ 优先级映射按目标 organ 计算:`match(0) < partial(1) < unknown(2) < mismatch(3)`,其中 "match" 表示候选 organ 字段包含目标 organ;目标 organ **从 `--organ` 透传,不在代码中硬编码任何 organ 名称**(根数据 `root`、脑数据 `brain`、叶片数据 `leaf` 等同理);同类内 marker_count → mean_confidence → cell_type 保持原序
- `_organ_status(organs, target)` 参数化:函数体用 `target` 替代字面量 `"Root"`(大小写按 KG 标注惯例 title-case 归一化,如 `"root"→"Root"`);**`target` 无默认值,调用方必须显式传入**(防止隐式硬编码)
- `_priority(status, target)` 与 `_rank_candidates(per_cluster_genes, gene_to_cts, target)` 同理:`target` 无默认值
- step5 `op_subcluster_kg` 从 `kg["query_config"]["organ"]` **必须读到非空值**才传入 `_rank_candidates`;若 `query_config` 缺失或为空,`op_subcluster_kg` 应直接 `common.fail` 报错退出(不允许静默 fallback 到任何 organ 字面量)
- kg_hits.json 的 `per_cluster.candidates` 结构不变;每个候选保留完整 `organ_status` 字段供 LLM 决策
- 决策点→scope 类型映射以 trajectory_design §3.1 为唯一事实源(9 session / 4 cluster),双向强制
- step3/step4 保持 0 次 h5ad 加载;run_log.jsonl 格式不变;不改 harness

**Ask First:** 无(设计分叉已确认:重排序非排除、粒度强制、合并 spec)

**Never:**
- 不维护 organ allow-list / 白名单 / 任何生物术语集合(领域知识归 SKILL.md)
- 不在代码中硬编码任何 organ 名称(目标 organ 来自 `--organ` 参数)
- 不在代码中硬编码"xylem 属于根"等生物学事实
- 不扩展 scope 结构(不做批量 multi-cluster 记录);不新增/删除决策点
- 不改 harness;不改 run_log schema;不重跑 LLM E-1 会话

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 重排序生效 | step3 query,簇有 root 候选与 mismatch 候选 | 决策视图 first/second 为 organ_status=root 的最高 marker_count 候选;mismatch 排在 root/partial/unknown 之后 | 无 root 候选时,mismatch 仍出现,LLM 生物学判断 |
| 同类内排序 | root 候选 A(marker=8) vs root 候选 B(marker=5) | A 排在 B 前(同 organ 内仍按 marker_count) | — |
| 唯一候选为 mismatch | 簇只有 bundle sheath(mismatch, marker=10) | 仍排首位,LLM 可见并自行判断 | — |
| 粒度违规写入 | write_judgment add(label_confirm, scope_type=session) | 校验报错,不写入 run_log | 错误信息指明期望 cluster |
| 合规写入 | write_judgment add(candidate_gap, scope_type=cluster, cluster_id 有效) | 写入成功 | cluster_id 缺失/越界 → 报错 |
| 旧日志校验 | validate_log on output/p5_evals/run_log.jsonl(E-1) | label_confirm/refine_effect 报 scope 类型 ERROR | exit 非 0,报告逐行定位 |

</frozen-after-approval>

## Code Map

- `skills/cell-annotation/scripts/step3_kg.py` -- `_rank_candidates` 排序键前置 organ_status;ORGAN_ORDER 常量
- `skills/cell-annotation/scripts/step5_refine.py` -- `op_subcluster_kg` 调 `_rank_candidates` 处继承默认(零改动)
- `skills/cell-annotation/scripts/write_judgment.py` -- `_validate_add` 加 `REQUIRED_SCOPE` 强制 scope 类型
- `scripts/validate_log.py` -- judgment 分支加 scope 类型 ERROR + cluster_id 范围 + 簇覆盖度 warning
- `skills/cell-annotation/SKILL.md` -- §3.7/§3.8/§7 更新;§3.7 删"必须排除/降级"LLM 职责、改为"决策视图已按 organ_status 排序";§7 加粒度表

## Tasks & Acceptance

**Execution:**
- [x] `skills/cell-annotation/scripts/step3_kg.py` -- `_organ_status(organs, target)` 加 `target` 参数(**无默认值,调用方必传**),函数体内所有 `"Root"` 字面量替换为 `target` 归一化形式(`target.strip().title()`);`_rank_candidates(per_cluster_genes, gene_to_cts, target)` 同理 target 无默认;按 target 计算 `_priority(status, target)`:`match=0 / partial=1 / unknown=2 / mismatch=3`(`target` 不允许默认值);排序键改为 `(priority, -marker_count, -mean_confidence, cell_type)`;`op_aggregate_candidates(markers, gene_to_cts, log_path, params, target)` 与 `cmd_query` 透传 `args.organ`;docstring 注明排序语义 + target 必传约束
- [x] `skills/cell-annotation/scripts/step5_refine.py` -- `op_subcluster_kg` **显式**从 `kg["query_config"]["organ"]` 读 target(无 `.get` 默认值);若 target 为空/缺失,直接 `common.fail("kg_hits.json 缺少 query_config.organ")` 报错退出(不允许任何 organ 字面量 fallback);传入 `_rank_candidates`
- [x] `skills/cell-annotation/scripts/write_judgment.py` -- `REQUIRED_SCOPE = {qc_threshold/resolution_select/clustering_quality/batch_effect/de_method/marker_quality/kg_match/unknown_cluster/global_quality: "session", candidate_gap/candidate_disambiguate/refine_effect/label_confirm: "cluster"}`;`_validate_add` 校验 `scope_type == REQUIRED_SCOPE[decision_point]` 否则报错;docstring 更新
- [x] `scripts/validate_log.py` -- 同 `REQUIRED_SCOPE` 表(与 write_judgment 注释互指防漂移);scope.type 不符 → ERROR;cluster 级 dp 的 cluster_id 不在 `[0, n_clusters)`(n_clusters 取最新 `step4_judge.rank_candidates` exec metrics)→ ERROR;candidate_gap/label_confirm 的 distinct cluster_id 数 < n_clusters → WARNING
- [x] `skills/cell-annotation/SKILL.md` -- §3.7:删"组织一致性检查(必做)…必须排除/降级";改述"决策视图已按 organ_status 优先级排序(含目标 organ>partial>unknown>mismatch),同类内按 marker_count;请结合生物学常识判断,器官匹配的候选优先但非强制";§3.8:删"先排除 mismatch 再比较",改述"top 候选已按 organ 排序,无需手动排除";§7:加「决策点→scope 类型→每数据集条数」粒度表 + 逐簇决策每簇一条说明

**Acceptance Criteria:**
- Given E-1 markers.json + Neo4j 可达,when 重跑 step3_kg query,then 检查 5 个样本簇:每个簇的 first_candidate 的 organ_status 为 root/partial/unknown(不再被 mismatch 凭高 marker_count 顶替)
- Given 同簇的 match 候选(低 marker_count)与 mismatch 候选(高 marker_count),when 排序后,then match 候选排在 mismatch 前(同类内仍按 marker_count)
- Given `--organ=leaf` 重跑,then 决策视图第一候选的 organ_status=root/partial 的项不再被优先(改为 organ 包含 leaf 的候选优先),证明 target 透传生效
- Given write_judgment add(label_confirm, scope_type=session),then 报错且 run_log 不追加
- Given write_judgment add(label_confirm, scope_type=cluster, cluster_id=0),then 写入成功
- Given output/p5_evals/run_log.jsonl(E-1),when validate_log.py,then label_confirm/refine_effect 报 scope 类型 ERROR(证明旧违规可捕获)
- Given output/p5_evals_E2/run_log.jsonl(E-2 candidate_gap cluster),when validate_log.py,then 通过

## Spec Change Log

- **术语对齐(实施期发现)**:spec I/O 矩阵/AC 里以 `organ_status=root/partial/unknown` 描述根数据集场景;而优先级描述里使用的术语是 `match(0)`。二者指的是同一概念:organ_status 类别名 `"root"` 在历史代码中语义为"含目标 organ 的候选"。为了避免触动 frozen-after-approval 内容且不破坏既有 kg_hits.json/工具现状,实施期保留 `root` 作为 organ_status 类别名(priority dict 用 `{"root": 0, ...}`)。后续 iteration 可考虑重命名为 `match` 以使语义更明确。
- **评审期修补(P5 R2 review loop)**:Blind Hunter/Edge Case Hunter 评审发现 6 处偏差,均修复并验证:
  1. **HIGH**: `--organ` CLI argparse `default="root"` 硬编码 organ 名 → 改为 `default=None`,cmd_query 缺参则 `common.fail` 退出(原 spec "Never" 边界明确禁止任何 organ 名硬编码)
  2. **MEDIUM**: `_organ_status` 未校验 target 类型 → 加 `ValueError` 防御(non-string/empty);并补 docstring 备注 `title()` 归一化限制
  3. **MEDIUM**: step5 `query_config.organ` guard 位置靠后(在 h5ad read + 3 个 op 之后)→ 移到 h5ad 加载之前,避免缺 target organ 时产生孤立 exec 记录
  4. **MEDIUM**: SKILL.md §3.7 同类内排序描述缺降序限定 → 补 "marker_count(降序) → mean_confidence(降序) → cell_type(升序)"
  5. **MEDIUM**: validate_log 覆盖度检查把字符串簇名也算入 distinct → 改为只统计 `isdigit()` 的 numeric cluster_id(避免与非数字簇名集合混算)
  6. **MEDIUM**: spec Verification 里对 E-2/E-3 "均通过" 的描述不准 → 改为"无与本轮粒度相关的 ERROR;mini-session 缺 session_start/session_end/exec 是其正常状态"
  - KEEP: `_organ_status` 的 substring 匹配逻辑(原本代码已含),属历史 bug,本轮不动(已记 deferred-work)
  - KEEP: organ_status 类别名 `root/partial/unknown/mismatch`(历史命名,改动影响 kg_hits.json 输出兼容性;已记 deferred-work)

## Design Notes

- **为何重排序而非排除**:E-1 实测每簇 ~50 候选,mismatch ~一半,KG 跨组织污染严重;按 marker_count 排时高-marker 的 bundle sheath / leaf pavement 顶替 root 候选,污染决策视图 top-k。结构化优先级是普适规则("优先展示器官匹配的候选"),非领域知识;生物学判断仍归 LLM,排序只是给 LLM 一个清洁信号。
- **为何不用白名单**:白名单 `{xylem, metaxylem}` 是领域知识(生物学共识);换 KG/物种/organ 全部失效,维护成本逆天;即便做配置文件,本质仍是领域知识,只是搬家。
- **为何 target 透传而非硬编码 root**:`_organ_status` 原本硬编码 `"Root" in o`,只适用于根数据;改 `target` 参数从 `--organ` 透传后,根/脑/叶/任何 organ 数据同套代码可用,`step5` 从 `query_config.organ` 读出与 step3 保持一致。**target 不允许默认值**——一旦 `target="root"` 写在函数签名里就等于把 organ 名硬编码到代码;调用方忘传参也是 bug 而非 silently fallback。
- **step5 自动一致**:`op_subcluster_kg` 复用 `_rank_candidates` + 同一 `gene_to_cts`,排序规则一处生效,无需复制逻辑。
- **REQUIRED_SCOPE 事实源**:trajectory_design §3.1(9 session / 4 cluster);write_judgment 与 validate_log 各持一份同源拷贝(跨目录,不引共享模块,注释互指防漂移)。

## Verification

**Commands:**
- 复制 `output/p5_evals/{step1_prepare,step2_markers}` 到 `output/p5_evals_r2/`(避免污染 E-1 run_log);`python skills/cell-annotation/scripts/step3_kg.py query --project-dir output/p5_evals_r2` -- expected: exit 0;抽样检查 5 个簇的 first/second 的 organ_status 为 root/partial/unknown
- `python skills/cell-annotation/scripts/step4_judge.py run --project-dir output/p5_evals_r2` -- expected: 决策视图 first/second 器官匹配优先
- `python scripts/validate_log.py output/p5_evals/run_log.jsonl` -- expected: label_confirm/refine_effect 报 scope 类型 ERROR(捕获旧违规)
- `python scripts/validate_log.py output/p5_evals_E2/run_log.jsonl output/p5_evals_E3/run_log.jsonl` -- expected: 无与本轮粒度相关的 ERROR(scope 类型/cluster_id/coverage 三项检项);mini-session 文件因缺 session_start/session_end/exec 仍会报 ERROR,属 mini-session 正常状态,不在本轮覆盖范围
- write_judgment 拒绝测试:用临时 project-dir 测 `add(label_confirm, scope_type=session)` 应报错;`add(label_confirm, scope_type=cluster, cluster_id=0)` 应成功(勿污染现有 run_log)

**Manual checks:**
- SKILL.md §3.7/3.8 不再要求 LLM 手动排除 mismatch;§7 粒度表与 trajectory_design §3.1 一致;无任何 allow-list / 白名单字样
- validate_log 与 write_judgment 的 REQUIRED_SCOPE 表内容一致
- _rank_candidates 排序键含 ORGAN_ORDER 在最前位
## Suggested Review Order

**organ 排序下沉 + target 透传（核心设计入口）**

- organ_status 类别与优先级映射；`root` 是历史类别名，语义=匹配 target organ
  [`step3_kg.py:33`](../../skills/cell-annotation/scripts/step3_kg.py#L33)

- `_organ_status` 收 target 参数并防御 non-string/empty；`title()` 归一化限制写明
  [`step3_kg.py:36`](../../skills/cell-annotation/scripts/step3_kg.py#L36)

- 排序优先级辅助函数；organ-agnostic dict 映射
  [`step3_kg.py:79`](../../skills/cell-annotation/scripts/step3_kg.py#L79)

- `_rank_candidates` 排序键前置 organ_status priority；同类内保留 marker_count→confidence→cell_type
  [`step3_kg.py:293`](../../skills/cell-annotation/scripts/step3_kg.py#L293)

- `op_aggregate_candidates` 透传 target；与 cmd_query 端到端串通
  [`step3_kg.py:348`](../../skills/cell-annotation/scripts/step3_kg.py#L348)

- cmd_query 缺 --organ 时 fail-fast；与 step5 的 query_config fail-fast 镜像一致
  [`step3_kg.py:407`](../../skills/cell-annotation/scripts/step3_kg.py#L407)

- step5 `op_subcluster_kg` 收 target；cmd_run 在 h5ad read 前 fail-fast 防孤立 exec 记录
  [`step5_refine.py:201`](../../skills/cell-annotation/scripts/step5_refine.py#L201)
  [`step5_refine.py:302`](../../skills/cell-annotation/scripts/step5_refine.py#L302)

**judgment 粒度强制**

- write_judgment REQUIRED_SCOPE 表（9 session + 4 cluster；事实源 trajectory_design §3.1）
  [`write_judgment.py:59`](../../skills/cell-annotation/scripts/write_judgment.py#L59)

- `_validate_add` 校验 scope_type 与 REQUIRED_SCOPE 双向一致；非法直接拒写入
  [`write_judgment.py:121`](../../skills/cell-annotation/scripts/write_judgment.py#L121)

- validate_log 同源 REQUIRED_SCOPE；scope-type 不符报 ERROR
  [`validate_log.py:43`](../../scripts/validate_log.py#L43)
  [`validate_log.py:153`](../../scripts/validate_log.py#L153)

**簇覆盖度检查**

- `n_clusters_latest` 从 step4_judge.rank_candidates exec metrics 跟踪；cluster_id 越界报 ERROR
  [`validate_log.py:71`](../../scripts/validate_log.py#L71)

- 覆盖度检查只统计 numeric cluster_id；candidate_gap/label_confirm 不足时 WARNING
  [`validate_log.py:204`](../../scripts/validate_log.py#L204)

**SKILL.md 用户面指导**

- §3.7 organ 优先级排序说明 + 删 LLM 排除职责；同类内排序含降序限定
  [`SKILL.md:75`](../../skills/cell-annotation/SKILL.md#L75)

- §3.8 top 候选已排序，无需手动排除 mismatch
  [`SKILL.md:82`](../../skills/cell-annotation/SKILL.md#L82)

- §7 粒度表（决策点→scope 类型→条数）+ 逐簇决策每簇一条说明
  [`SKILL.md:183`](../../skills/cell-annotation/SKILL.md#L183)
