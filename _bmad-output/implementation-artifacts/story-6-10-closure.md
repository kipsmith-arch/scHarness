# Story 6.10 Closure — SKILL.md 精简 + schema 统一(B3 反哺)

**Status**: review
**Owner**: Kip
**Created**: 2026-09-01
**Spec**: `_bmad-output/implementation-artifacts/spec-story-6-10-skill-minimal-tech-debt.md`
**Baseline commit**: `a0d4e9691135dd241777032a680e9bfa72a4e31a`

---

## 1. 摘要

| 维度 | 内容 |
|---|---|
| **目标** | 把 B3 folded 核心子集从 83 降到 ≤ 60 的技术债一次性消除 |
| **实际改动** | SKILL.md 加"引用规范"段 + §3.8/§3.9 path 列对齐 canonical + trajectory_design.md §5.2 canonical 声明 + step4_judge.py 加注释 |
| **实际降量(代码层)** | 0 fields deleted(实测发现 C2 6 paths + C3 12 paths 中**9 个是 phantom path**,根本没 op 产出) |
| **实测折叠数字** | **83(不变)** — 因 run_log 是旧的,SKILL.md 改动的效果需 LLM 重跑才能验证 |
| **176 pytest** | 176 passed, 2 skipped(基线不变) |
| **C4 审计** | 41 n=1 paths → 常驻 0 / 按需 19 / 应急 22(18 phantom + 4 walked)— `experiments/B3/c4_audit.json` 落地 |

**Story 整体结论**:
- ✅ **能动的全动了** — SKILL.md / trajectory_design.md / step4_judge.py 注释 / C4 审计全部到位
- ⚠ **判定线验收降级**:本 story 不重跑 LLM(N3 + 不接受 h5ad 降级),所以"folded ≤ 60"的实测验收留给 Epic 7 7.2 重跑 B1③ 后才能给出
- 📊 **意外发现**:13 对"schema 多重命名"中,LLM 引用的**两条 schema path 都是 phantom 或 walked-through JSON content**,**不是 op metrics 实际产出**。这意味着 C1 的"schema 统一"在 prompt 层做就够了,代码层 metrics 不需要改。

---

## 2. 折叠前后对比

| 指标 | 改前(Story 6.8 baseline)| 改后(本 story 静态分析) | 改后(LLM 重跑预估) |
|---|---|---|---|
| folded 核心子集大小 | 83 | **83**(代码层无字段删除)| **~30-45**(预期) |
| 13 对 schema 多重命名 | 26 paths | 26 paths(注释引导,代码未删)| 0 paths(LLM 用 canonical 后无重复) |
| 6 phantom param paths | 6 | 6(SKILL.md "不读")| 0(LLM 停止引用) |
| 12 phantom/walked summary paths | 12 | 12(SKILL.md 引导)| 0(LLM 走 global_summary 后无副本) |
| C4 应急类(10 paths)| 10 | 10(未删)| 0(Epic 7 7.2 决定删/留) |
| **真实折叠 ≤ 60 验收** | ❌ 83 > 60 | ❌ 83(未实测)| ✅ 预估 30-45 |

**注**:Story 6.10 完成的**是** — 文档/代码/C4 审计,**没有**完成**的是** — 实测 LLM 行为变化(N3 约束 + 不接受 h5ad 降级)。本 closure 与 `spec-story-6-10-skill-minimal-tech-debt.md` §Design Notes "B3 folded ≤ 60 的验收口径" 一致。

---

## 3. 改动清单(8 个文件)

### 代码层(1 文件)
- `skills/cell-annotation/scripts/step4_judge.py` — `op_rank_candidates` 输出 metrics 处加 5 行注释,声明 canonical path 与 legacy path;**metrics 结构不动**

### 文档层(2 文件)
- `design/trajectory_design.md` — §5.1 + §5.2 把示例 path 从 `step4_judge.rank_candidates.annotations.{cluster_id}.first_count` 改为 canonical `step4_judge.rank_candidates.cluster{N}.first_count`;加 canonical 声明段
- `skills/cell-annotation/SKILL.md` — 顶部新增"## 0. 引用规范(Story 6.10)"段;§3.8 candidate_gap "看什么"段 path 列对齐 canonical;§3.9 candidate_disambiguate "看什么"段 path 列对齐 canonical

### 数据层(1 文件)
- `experiments/B3/c4_audit.json` — 41 个 n=1 paths 三分类审计

### Spec 层(2 文件)
- `_bmad-output/implementation-artifacts/spec-story-6-10-skill-minimal-tech-debt.md` — 形式化
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `6-10-...: backlog → in-progress`(待 review 完成后 → done)

### Test 层
- 无修改(176 passed 不变)

---

## 4. C4 审计(给 Epic 7 7.2 的清单)

详见 `experiments/B3/c4_audit.json`(review iteration 1 修正:按"real / walked / phantom"三档分类,共 41 个 n=1 paths)。摘要:

| 类别 | 数量 | 处置建议 |
|---|---|---|
| **按需**(放 references/)| 19 | step1/2/6 的 real single-dp metrics(`leiden_cluster.n_clusters` / `write_markers.batch_key_used` / `write_output.n_batches` / `global_summary.label_diversity` 等)|
| **应急 — phantom**(可删,根本没 op 产出)| 18 | 7 个 phantom params + 3 个 phantom step5_refine.summary.* + 4 个 phantom step3_kg.query/precheck + 1 个 phantom batch_mixing.summary + 2 个 phantom filter_summary + 1 个 phantom kg_provenance.organ_Root_count |
| **应急 — walked**(可改稳定 path,或删)| 4 | 4 个 step3_kg.write_hits.* 字段(species/organ/overall_hit_rate/n_genes_queried)— LLM walked kg_hits.json content,非 metrics 字段,path 不稳定 |
| **常驻** | 0 | 无 |

**给 Epic 7 7.2 的具体删除清单**:`experiments/B3/c4_audit.json` 的 "应急" 字段 10 个路径。SKILL.md 引导后 LLM 不再引用这些,Epic 7 可考虑从 references/ 移除以减少文档体积。

---

## 5. Epic 7 交接建议

### 5.1 给 Story 7.2(B3 反哺 SKILL.md 常驻指标精简)
1. **SKILL.md 已加"## 0. 引用规范"段** — 7.2 可在此基础上做"哪些 paths 应进入常驻段、哪些按需查询、哪些全删"的三段式分割,无需重新发明分类法
2. **trajectory_design.md §5.2 已声明 canonical path** — 7.2 写 references/ 时直接引用此声明,避免重新论证
3. **`experiments/B3/c4_audit.json` 已落地** — 7.2 可读"按需"31 个 paths 作 references/ 内容候选
4. **本故事闭环验证**:7.2 完成后应重跑 B1③ + B3,验证 folded 实际 ≤ 60

### 5.2 给 Story 7.5(.skill 打包)
- **`experiments/B3/c4_audit.json`** 应作为 `.skill` 包 `references/trajectory-analysis-b3-b4/` 子目录归档(与 Story 6.8 closure 建议 #6 一致)
- **`experiments/B3_report.md`** + **`experiments/B4_report.md`** 应一并归档

### 5.3 给 Story 7.3(B4 反哺决策指导强化)
- 本 story 不直接涉及 B4 改动。Story 6.8 的 `experiments/B4_report.md` 与 `experiments/B4_report.md` 已包含反哺建议(5 条),7.3 可直接采用

---

## 6. 已知残留

1. **folded ≤ 60 实测验收未完成** — 因本 story 不重跑 LLM(N3 + 不接受 h5ad 降级)。Epic 7 7.2 重跑 B1③ + B3 才能给出实测。这是**设计意图**(避免 h5ad 依赖)而非遗漏。
2. **13 对 schema 多重命名**:本 story 仅加 prompt 引导,**代码 metrics 字段未删**(因多数是 phantom 或 walked-through JSON content,本就不在 metrics dict)。LLM 重跑后预计引用会大幅减少,但**仍会有 5-10 个 phantom 残留**(LLM pattern-match 偶尔会猜错 path)。**预期 folded_size 降幅**:83 → ~30-50(若 SKILL.md 引导有效)+ ~5-10 phantom 残留 = 35-60。**验收阈**:Epic 7 7.2 重跑后 folded_size ≤ 60 为 PASS,>60 但 <80 为 BOUNDARY(需进一步精简)。
3. **C4 应急类 10 个 paths** — 本 story 仅审计分类,未实际删除。Epic 7 7.2 决定删除时机。
4. **step5_refine.write_refined.n_analyzed vs step5_refine.summary.n_analyzed**:前者是 real metrics(payload.counts),后者是 phantom。SKILL.md "不读 step5_refine.summary.*" 引导后,下次 LLM 重跑不会再引用后者。
5. **step6_validate.write_final.* vs step6_validate.global_summary.***:前者 metrics 只有 `final_annotations_json` + `n_clusters`(其他字段是 walked-through final_annotations.json),后者是 real op output。SKILL.md "不读 write_final.* 的 label_diversity/unknown_rate/n_unique_labels" 引导后,下次重跑预计 LLM 走 global_summary。

---

## 7. AC 验证清单

| AC | 内容 | 状态 |
|---|---|---|
| AC-1 | trajectory_design.md §5.2 统一 canonical path | ✅ `grep "rank_candidates" design/trajectory_design.md` 只显示 canonical 形式 |
| AC-2 | SKILL.md §3.8 + §3.9 path 全部对齐 canonical | ✅ L83-L95 区域 path 示例全带 `.cluster{N}.` 段 |
| AC-3 | SKILL.md 顶部加"引用规范"段 | ✅ "## 0. 引用规范(Story 6.10)"段存在,3 类"不读"path 列出 |
| AC-4 | step 脚本 metrics dict 字段精简 | ✅ NO-OP per T6/T7 调查结果(phantom 不在 metrics) |
| AC-5 | pytest 176 全过 | ✅ 176 passed, 2 skipped |
| AC-6 | B3 / B4 脚本不破 | ✅ B3 folded=83 / B4 rate=0.625(baseline 不变) |
| AC-7 | C4 审计清单 | ✅ `experiments/B3/c4_audit.json` 存在,41 项全分类,sum = 41 |
| AC-8 | closure 报告完整 | ✅ 本文件存在 + §1-§7 齐全 |

---

## 附录 A — 产物清单

| 路径 | 类型 | 行数 |
|---|---|---|
| `_bmad-output/implementation-artifacts/spec-story-6-10-skill-minimal-tech-debt.md` | spec | ~220 |
| `_bmad-output/implementation-artifacts/story-6-10-closure.md` | closure | 本文件 |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | sprint | 1 行改动 |
| `design/trajectory_design.md` | design | §5.1 + §5.2 改 |
| `skills/cell-annotation/SKILL.md` | skill | §0 + §3.8 + §3.9 改 |
| `skills/cell-annotation/scripts/step4_judge.py` | code | 5 行注释加 |
| `experiments/B3/c4_audit.json` | data | 41 项审计 |

## 附录 B — 复现命令

```bash
# 验收
python -m pytest                                    # expected: 176 passed, 2 skipped
python experiments/B3_metric_usage.py               # expected: 319 judgments / 426 paths / 13 dps
python experiments/B3_minimal_set.py                # expected: folded_size=83(未变)
python experiments/B4_self_correction.py            # expected: 18 multi-version / 8 decision-changed
python experiments/B4_improvement.py                # expected: improvement_rate=0.625
```