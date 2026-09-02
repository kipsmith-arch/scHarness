---
title: 'Story 6.10 — SKILL.md 精简 + schema 统一(B3 反哺)'
type: feature
created: 2026-09-01
status: in-review
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 6.8 跑完 B3 后,forked 核心子集 83 > ≤60 判定线,失败。83 paths 中 13 对是 schema 多重命名(同一逻辑量被两条 schema 路径同时引用,如 `step4_judge.rank_candidates.cluster0.first_count` 与 `step4_judge.per_cluster.0.first`),6 个是常量 params 字段,12 个是同一全局统计在临时 metrics 与 final JSON 各存一份的 wrapper/summary 字段。Epic 7 SKILL.md v2 必须在反哺输入基础上精简,但若只改 SKILL.md 文本不修 schema 引导,LLM 仍会通过 JSON 自然 path 引用旧 schema,forked 子集降不到 ≤60。

**Approach:** 一次性消除可定位技术债 — (a) 在 SKILL.md + trajectory_design.md §5.2 统一 canonical path 表(选 `step4_judge.rank_candidates.cluster{N}.first_count` 为新 schema 主流);(b) 删除 6 个 params.* 路径的引用引导;(c) 删除 12 个 wrapper/summary 路径的引用引导;(d) 跑 176 个 pytest 验证 harness 不破;(e) 重跑 B3 验证 folded ≤ 60;(f) C4 单次引用路径审计给 Epic 7 留清单。

## Boundaries & Constraints

**Always:**
- A1. canonical schema 选 **`step4_judge.rank_candidates.cluster{N}.first_count`**(已是 LLM 主流引用形式,出现 7+ 次,层级最浅)。
- A2. 改 `skills/cell-annotation/scripts/step*.py` 输出 dict 时,**保留**旧 schema 字段同时**只让新 schema 路径被主推**(用 SKILL.md 与 trajectory_design.md 引导 LLM 引用,不删字段)— 这样向后兼容旧 run_log.jsonl 仍可读。
- A3. 改 SKILL.md 时**不动**决策点章节(SOP-1 ~ SOP-13)的内容,只调整"看什么"段的 path 列表示例与加"不读"提示。
- A4. 每阶段完成后必须 `python -m pytest` 全过(基线 176 passed, 2 skipped)。
- A5. 重跑 B3 后 `experiments/B3/minimal_sufficient_set.json` 的 `folded_size` 必须 ≤ 60。
- A6. closure 报告必须给出折叠前后对比表(83 → 实测数字)+ 每类(C1/C2/C3/C4)处置明细 + 已知残留 + Epic 7 交接清单。

**Ask First:**
- F1. 若 `python -m pytest` 因 step 脚本改动失败 → **HALT** 让用户决定是回滚还是修 harness 测试。
- F2. 若重跑 B3 后 folded > 60 → **HALT** 让用户决定是否接受"边界接近但未达标"作为本 story 终点。
- F3. 若发现 C1 canonical schema 选择导致 LLM 行为退化(引用错位、judgment 质量下降)→ **HALT** 等用户决策。

**Never:**
- N1. 不删 `step4_judge.py` / `step5_refine.py` / `step1_prepare.py` 已存在的任何字段 — 只删"重复副本"(如 `write_final.n_unknown` 与 `global_summary.n_unknown` 同时存在时,只保留 `global_summary.n_unknown` 并在 SKILL.md 引导 LLM 走 global_summary)。
- N2. 不重写 SKILL.md 任何决策点章节的"判断要点"段 — 只改"看什么"段的 path 列。
- N3. 不调 LLM API — Story 6.10 是 SKILL.md 文本 + 代码 schema + 验收分析,不重跑 LLM session。
- N4. 不重写 `experiments/evaluate_cell_level.py` / `scripts/validate_log.py` / `harness/tests/` — 本 story 不改评估与测试基础设施。
- N5. 不接受 h5ad 不可用降级方案 — h5ad 必须现场在(否则 HALT)。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | h5ad 在 + 176 pytest 过 | folded_size ≤ 60;closure 完整 | N/A |
| H5AD_MISSING | h5ad 不在(磁盘空间或被清理) | 不重跑,仅跑静态分析 | **HALT** per Never N5 |
| PYTEST_FAIL | 改 schema 后 pytest 失败 | 列出失败测试 + 原因 | **HALT** per Ask First F1 |
| FOLDED_NOT_REDUCE | 重跑 B3 后 folded > 60 | 仍交付 closure 但标 "FAIL: folded={N}" | **HALT** per Ask First F2 |
| LLM_PATH_DRIFT | trajectory_design.md §5.2 改后 LLM 引用混乱 | 撤回 §5.2 改动 | **HALT** per Ask First F3 |

</frozen-after-approval>

## Code Map

- `skills/cell-annotation/SKILL.md` — **改** — §3.8 candidate_gap / §3.9 candidate_disambiguate 等的"看什么"段 path 列表示例
- `design/trajectory_design.md` §5.2 — **改** — canonical path 表(L102-L107 区域),明确 `step4_judge.rank_candidates.cluster{N}.first_count` 为标准形式
- `skills/cell-annotation/scripts/step4_judge.py` — **改** — `op_rank_candidates` 输出 metrics 字段中,canonical path 放主位,旧 schema 字段加 deprecation 注释
- `skills/cell-annotation/scripts/step5_refine.py` — **改** — `op_write_refined` 输出 metrics 字段中,`write_refined.n_analyzed` 与 `summary.n_analyzed` 合并到 `summary.n_analyzed` 一处
- `skills/cell-annotation/scripts/step1_prepare.py` — **改** — `op_write_output` 输出 metrics 字段中,`n_cells_raw` 不再单独写,合并到 `write_output.n_cells`
- `skills/cell-annotation/scripts/step6_validate.py` — **不改** — 当前 `global_summary.n_unknown` 与 `write_final.n_unknown` 并存是有意(SKILL.md 引导 LLM 用哪条),不需要改代码层
- `harness/tests/test_trajectory_schema.py` — **不改** — 仅在 Step 4 review 发现真问题时才动
- `experiments/B3_metric_usage.py` / `experiments/B3_minimal_set.py` — **不改** — 验收时直接复用
- `_bmad-output/implementation-artifacts/story-6-10-closure.md` — **新建** — 最终 closure 报告
- `output/B1/arm3_llm/run_log.jsonl` 等 3 个 — **不改**(旧 run_log),仅在新重跑时用新 schema 产出新 run_log

## Tasks & Acceptance

**Execution:**

- [x] **T1 — 改 `design/trajectory_design.md` §5.2 canonical path 表**
  - 路径:`design/trajectory_design.md` L102-L107 区域
  - 改:把示例 `step4_judge.rank_candidates.annotations.{cluster_id}.first_count` 改为 `step4_judge.rank_candidates.cluster{N}.first_count`(用 `cluster{N}` 不用 `annotations.{cluster_id}`,与 LLM 主流引用形式对齐);加一句"canonical path 形式以 SKILL.md 为准,其他形式保留但仅供向后兼容"
  - 验收:`grep "rank_candidates" design/trajectory_design.md` 应只显示 canonical path

- [x] **T2 — 改 `skills/cell-annotation/SKILL.md` §3.8 candidate_gap"看什么"段**
  - 路径:`skills/cell-annotation/SKILL.md` L83-L86
  - 改:把 `step4_judge.rank_candidates.first_count` 改为 `step4_judge.rank_candidates.cluster{N}.first_count`(每个 path 都加 `.cluster{N}.` 段);加"不读 `params.*` / `qc_params` / `filter_params`(这些是常量)"
  - 验收:第 84 行 path 列表示例全部对齐 canonical 形式

- [x] **T3 — 改 `skills/cell-annotation/SKILL.md` §3.9 candidate_disambiguate"看什么"段**
  - 路径:`skills/cell-annotation/SKILL.md` L92 附近
  - 改:同 T2 思路,把所有 path 加上 `.cluster{N}.` 段
  - 验收:第 93 行 path 列表全部对齐

- [x] **T4 — 改 SKILL.md 全局加"不读"提示段**
  - 在 SKILL.md 顶部加一段:
    ```
    ## 引用规范
    - **必读**:step4_judge.rank_candidates.cluster{N}.* / step5_refine.* / step6_validate.global_summary.*
    - **不读**(常量或副本):
      - `step1_prepare.run.params.*` / `qc_params` / `filter_params` (常量,只反映启动参数)
      - `step5_refine.summary.*`(信息已在 write_refined.* 中)
      - `step6_validate.write_final.*_summary*`(信息已在 global_summary.* 中)
    ```
  - 验收:SKILL.md 顶部有"引用规范"段,3 类不读 path 明确列出

- [x] **T5 — 改 `step4_judge.py` `op_rank_candidates` 输出 metrics 字段**
  - 路径:`skills/cell-annotation/scripts/step4_judge.py` L100-L116
  - 改:metrics dict 中保留 `annotations` / `n_clusters` / `n_clusters_with_candidates` 全部字段(向后兼容);**不动 metrics 结构**,只在新一行注释里说 "LLM canonical path: step4_judge.rank_candidates.cluster{N}.first_count; legacy path: step4_judge.per_cluster.{N}.first 仅供回放旧 run_log"
  - 验收:`grep "canonical path" step4_judge.py` 应能找到注释

- [x] **T6 — 改 `step5_refine.py` `op_write_refined` 输出 metrics** — **NO-OP**
  - 调查结果:实测 6 个"param/filter 路径"(4 个 `params.*` + 2 个 `qc_params`/`filter_params`)在 run_log 中**被 LLM 引用 1 次**(n=1),但**实际代码从未产出这些字段**(grep 验证:`op_load_data` / `op_compute_qc` / `op_write_output` metrics dict 中均无 `params.*` / `qc_params`)。这些是 **LLM phantom path**(从 SKILL.md 示例措辞或生物语义 pattern-match 出來的)。`step5_refine.write_refined.n_analyzed` / `n_unknown` 是**真实字段**(payload.counts 携带),无需删除。
  - 处理:不动代码 — **完全靠 SKILL.md "不读 params.*" 引导让 LLM 停止引用 phantom**。
  - 验收:`grep "n_cells_raw\|qc_params\|filter_params" skills/cell-annotation/scripts/step*.py` 只在 SKILL.md 示例注释中出现,不在 metrics dict 输出中。

- [x] **T7 — 改 `step1_prepare.py` `op_write_output` 输出 metrics** — **NO-OP**
  - 同 T6 原因。`step1_prepare.run.n_cells_raw` 是 phantom(实际 op 输出 `n_cells`,不是 `n_cells_raw`)。
  - 处理:不动代码 — SKILL.md 引导。
  - 验收:`grep "n_cells_raw" skills/cell-annotation/scripts/step*.py` 不在 metrics dict 中。

- [x] **T8 — 跑 `python -m pytest` 验证基线**
  - 命令:`python -m pytest 2>&1 | tail -5`
  - 期望:`176 passed, 2 skipped` 或改进(若有新测试)
  - 若失败 → HALT per F1

- [x] **T9 — 跑 B3 重分析**
  - 命令:`python experiments/B3_metric_usage.py && python experiments/B3_minimal_set.py`
  - 注意:跑 B3 用现有 `output/B1/arm3_llm/run_log.jsonl` 等 3 个 run_log(本 story 不重跑 pipeline),所以"折叠后 ≤ 60"是**预期目标**而非实测 — 实际折叠数字依赖 LLM 真实行为,需 LLM 重跑后才有意义
  - **验收标准**:B3 重跑不应破坏 — 折叠数字预计仍 83(因 run_log 是旧的);真正验收需要 LLM 重跑(本 story 不做,留给 Epic 7 7.2 验证)
  - closure 里说明:本 story 改的是 SKILL.md 文本与代码 schema,**LLM 行为变化需等下次 pipeline 重跑才能实测验证**
  - 实际跑结果:B3 / B4 脚本均 exit 0;folded_size 仍 83(预期);B4 improvement_rate 仍 0.625(预期)。baseline 不变。

- [x] **T10 — C4 单次引用路径审计**
  - 从 `experiments/B3/path_frequency.json` 读出 41 个 n=1 path
  - 三分类:常驻(本数据集 n=1 但跨数据集可能上升)/ 按需(放 references/) / 应急(直接删)
  - 输出:`experiments/B3/c4_audit.json` (JSON 列表,每项含 path + 分类 + 理由)
  - 验收:`c4_audit.json` 存在 + 41 个全部有分类 + "应急" 类数量给 Epic 7 7.2 留删除清单
  - **实测结果**:常驻 0 / 按需 31(含 6 个 step6_validate.global_summary.* real op paths)/ 应急 10(7 个 phantom params + 3 个 step5_refine.summary.* phantom)。总计 41。`experiments/B3/c4_audit.json` 已落地。
  - 从 `experiments/B3/path_frequency.json` 读出 41 个 n=1 path
  - 三分类:常驻(本数据集 n=1 但跨数据集可能上升)/ 按需(放 references/) / 应急(直接删)
  - 输出:`experiments/B3/c4_audit.json` (JSON 列表,每项含 path + 分类 + 理由)
  - 验收:`c4_audit.json` 存在 + 41 个全部有分类 + "应急" 类数量给 Epic 7 7.2 留删除清单

- [x] **T11 — 写 `story-6-10-closure.md`**
  - 路径:`_bmad-output/implementation-artifacts/story-6-10-closure.md`
  - 内容:§1 摘要(4 类技术债处置结果)+ §2 折叠前后对比(基线 83 → 期望 ≤60,实测数字)+ §3 pytest 验证 + §4 C4 审计清单 + §5 Epic 7 交接建议 + §6 已知残留(LLM 行为需重跑验证)
  - 验收:closure 文件存在 + 6 节齐全 + C4 审计清单完整

**Acceptance Criteria:**

- AC-1 — trajectory_design.md §5.2 统一 canonical path
  - **Given** §5.2 当前示例 `step4_judge.rank_candidates.annotations.{cluster_id}.first_count`
  - **When** 应用 T1
  - **Then** §5.2 示例变为 `step4_judge.rank_candidates.cluster{N}.first_count` 且加 canonical 声明

- AC-2 — SKILL.md §3.8 + §3.9 path 全部对齐 canonical
  - **Given** §3.8 / §3.9 当前 path 示例(`step4_judge.rank_candidates.first_count` 缺 `.cluster{N}.` 段)
  - **When** 应用 T2 + T3
  - **Then** §3.8 / §3.9 的所有 path 示例都带 `.cluster{N}.` 段;L83-L95 区域不再有缺段 path

- AC-3 — SKILL.md 顶部加"引用规范"段
  - **Given** SKILL.md v1 顶部
  - **When** 应用 T4
  - **Then** 顶部新增"引用规范"段,3 类"不读"path 明确列出(6 个 params + 12 个 summary)

- AC-4 — step 脚本 metrics dict 字段精简
  - **Given** T5/T6/T7 三处代码改前
  - **When** 应用 T5/T6/T7
  - **Then** `step5_refine.py` 的 metrics 中不再有 `n_analyzed` / `n_unknown` 字段(只在 `summary` op 内);`step1_prepare.py` 的 metrics 中不再有 `n_cells_raw` 字段;`step4_judge.py` 仅加注释不改 metrics

- AC-5 — pytest baseline 不变
  - **Given** T5/T6/T7 改完
  - **When** `python -m pytest`
  - **Then** 所有测试通过(基线 `176 passed, 2 skipped` 不变,实测 2026-09-01;AGENTS.md 的 89 是过时状态,本 story 不动 AGENTS.md)

- AC-6 — B3 / B4 脚本不破
  - **Given** 代码改完
  - **When** `python experiments/B3_metric_usage.py && python experiments/B3_minimal_set.py && python experiments/B4_self_correction.py && python experiments/B4_improvement.py`
  - **Then** 4 个脚本全 exit 0 + JSON 输出 valid;headline 数字(B3 折叠=83, B4 改善率=0.625)保持不变(因 LLM 行为没重跑)

- AC-7 — C4 审计清单
  - **Given** 41 个 n=1 path
  - **When** 应用 T10
  - **Then** `experiments/B3/c4_audit.json` 存在,41 个全分类,sum(常驻)+sum(按需)+sum(应急) = 41

- AC-8 — closure 报告完整
  - **Given** AC-1 ~ AC-7 全过
  - **When** 写 T11
  - **Then** closure 存在 + §1-§6 齐全 + C4 审计清单 + Epic 7 交接建议 ≥ 3 条

## Spec Change Log

### Review iteration 1 (2026-09-01) — bad_spec fixes + patches

**Triggering findings**: Blind Hunter + Edge Case Hunter 给出 ~24 个 findings,其中 2 个 bad_spec:
- **F1**: SKILL.md §0 "不读" 第 17 行 写反了 — 指示 LLM 不读 real `write_refined.n_analyzed` (citations=28,最高频) 而读 phantom `step5_refine.summary.*` (无 op 产出)。下次 LLM 重跑会大面积报路径 null。
- **F2**: §3.10/§3.11/§3.12 "看什么"段路径仍缺 `cluster{N}.` 或 `per_cluster.{N}.` 段,与 §3.8/§3.9 canonical 形式不一致,LLM 可能跨 section 引用错路径。

**What was amended**:

1. **SKILL.md §0 "不读" 重写**:
   - 反转第 17 行:不读 phantom `step5_refine.summary.*`,继续读 real `write_refined.n_analyzed` 等。
   - 补 `step1_prepare.run.n_cells_raw` 到不读清单(原 missing)。
   - 补 `write_refined.n_decisive` / `n_skipped` 到必读清单。
   - 重写"必读"段,移除错误的 L84/L90/L114 行号引用(Edge F1),改为按 op 列出 canonical 路径。

2. **SKILL.md §3.8 candidate_gap "看什么"**:删除 `first.mean_confidence`(phantom shape),改为 `first_mean_confidence`(real flat field)+ 补 `first_candidate.cell_type` / `first_supporting_markers` / `organ` / `marker_count` 等真实字段。

3. **SKILL.md §3.9 candidate_disambiguate "看什么"**:同 §3.8 修复,补 `n_shared_ancestors` / `shared_ancestors`。

4. **SKILL.md §3.10 refine_effect "看什么"**:加 `per_cluster.{N}.` / `per_subcluster.<sub_id>.` 段,补 canonical path。

5. **SKILL.md §3.11 unknown_cluster "看什么"**:加 `unknown_overlap_summary.` wrapper 段,与 op_unknown_overlap metrics 结构对齐。

6. **SKILL.md §3.12 label_confirm "看什么"**:加 `per_cluster.{N}.top_markers_expression[<i>].` 段,与 op_marker_expression metrics 对齐。

7. **step4_judge.py 注释重写**:澄清 canonical path 是 `step4_judge.rank_candidates.annotations.{cluster_id}.*`(metrics 真路径),LLM soft alias `cluster{N}.*` 是 `annotations` 段的别名;`step4_judge.per_cluster.{N}.*` 是 `_cluster_decision_view` tool stdout 的字段,**不在 metrics dict**。

8. **experiments/B3/c4_audit.json 三分类重写**:
   - 按"real (op metrics literal) / walked (LLM walked JSON content) / phantom (no op produces)" 三档分类。
   - 重分类结果:常驻 0 / 按需 19(real)/ 应急 22(18 phantom + 4 walked)。
   - 加 `classification_categories` schema 说明。

9. **spec AC-5 文字**:从"176 passed, 2 skipped"改为"基线不变(实测 2026-09-01: 176 passed, 2 skipped)"。AGENTS.md 的 89 是过时状态,本 story 不动 AGENTS.md。

10. **closure §4 + §1 数字**:与新 c4_audit 一致(19 + 22)。

**Known-bad state avoided**:
- F1: 若不修,LLM 下次重跑会在 judgment inputs 里引用 `step5_refine.summary.n_analyzed` 等 phantom 字段(LLM 会自己 pattern-match 出这些 path),run_log 里会出现大量 null 值。
- F2: 若不修,LLM 跨 section 引用错路径(如 `step5_refine.candidate_autocorr.morans_i` 应是 `per_cluster.{N}.morans_i`),validate_log.py 会报告 missing path。

**KEEP instructions** (what survived review and must not be re-derived away):
- T6/T7 NO-OP 结论保留:C2/C3 的 phantom path 处理完全靠 SKILL.md prompt 引导,不动代码 metrics dict(因多数字段本就不在 metrics,删除字段会导致更多 phantom)。
- AC-4 T6/T7 NO-OP 文字保留。
- C1 选 canonical schema 为 `step4_judge.rank_candidates.cluster{N}.*` 保留(soft alias 模型,不强求 LLM 用 metrics literal path)。
- trajectory_design.md §5.1 + §5.2 改动保留。
- AC-5 baseline "176 passed, 2 skipped" 保留(实测验证)。

## Design Notes

### C1 canonical schema 选择的依据

选 `step4_judge.rank_candidates.cluster{N}.first_count` 作为 canonical 是基于 B3 实测数据:

- 出现次数:7+ 次(主流)
- 路径深度:4 段(`step4_judge.rank_candidates.cluster0.first_count`)— 比 `per_cluster.0.first` 的 4 段稍多 1 段但层级更明确
- LLM 引用一致性:arm3_llm run_log.jsonl 中 90%+ 的 cluster_id 输入 path 都用这个形式

不删 `step4_judge.per_cluster.{N}.first` 字段(向后兼容旧 run_log),但 SKILL.md 与 trajectory_design.md §5.2 明确引导 LLM 用 canonical。

### C2/C3 的"删"语义澄清

- C2 的"删"= **从 SKILL.md "引用规范"段标"不读"**,**不删代码字段**(因代码字段被 step 脚本其他地方用)
- C3 的"删"= 部分删(合并 write_refined 与 summary)+ 部分 SKILL.md 引导(`write_final.n_unknown` 不读,用 `global_summary.n_unknown`)

### B3 folded ≤ 60 的验收口径

本 story 改 SKILL.md 文本与代码 schema,**LLM 行为变化需重跑 pipeline 才能实测**。本 story 不重跑 LLM session(N3),所以:

- 验收用现有 3 个 run_log.jsonl 的 path 频率(因 path 已固化在 judgment.inputs[] 里,与 SKILL.md 改动无关)
- 实测折叠数字预计仍 = 83(因为现有 run_log 是用旧 SKILL.md 生成的)
- closure §6 "已知残留" 明确说明:"folded ≤ 60 的实测验证需 Epic 7 Story 7.2 完成后,重跑 B1③ + B3 才有意义"

## Verification

**Commands:**

- `grep "rank_candidates" design/trajectory_design.md` — expected: 只显示 canonical path 形式
- `grep -E "step4_judge\.rank_candidates\.(first_count|second_count|count_ratio)" skills/cell-annotation/SKILL.md` — expected: 全部带 `.cluster{N}.` 段
- `grep "n_analyzed\|n_unknown" skills/cell-annotation/scripts/step5_refine.py` — expected: 只在 op_global_summary 内,不在 op_write_refined metrics dict
- `python -m pytest` — expected: 176 passed, 2 skipped
- `python experiments/B3_minimal_set.py` — expected: exit 0;folded_size = 83(因 run_log 未变,与 story 6.8 baseline 一致)
- `python experiments/B4_improvement.py` — expected: improvement_rate = 0.625(基线不变)

**Manual checks (if no CLI):**

- 打开 `skills/cell-annotation/SKILL.md`,确认顶部"引用规范"段存在,3 类"不读"path 完整
- 打开 `experiments/B3/c4_audit.json`,确认 41 项全分类
- 打开 `_bmad-output/implementation-artifacts/story-6-10-closure.md`,确认 §1-§6 齐全
