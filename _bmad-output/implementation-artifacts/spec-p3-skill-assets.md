---
title: 'P3 补做:cell-annotation assets/ 目录(tools 总览 + 交付报告模板)'
type: 'feature'
created: '2026-08-11'
status: 'done'
review_loop_iteration: 0
route: 'one-shot'
---

# P3 补做:cell-annotation assets/ 目录(tools 总览 + 交付报告模板)

## Intent

**Problem:** P3 主 spec 拆分的 deferred 项中,`skills/cell-annotation/assets/` 目录(S-6)未交付:加载器派生的 11 工具总览(供人工核对)与交付报告模板缺失。

**Approach:** 新建 `assets/tools.md`(11 工具总览:派生契约、参数默认值、op 映射、核对清单,内容整理自 `--dump-schema` 实况与 `design/atomic_operations.md`)与 `assets/report-template.md`(交付报告骨架,字段绑定 `final_annotations.json` / `step7_diagnose.json` / `run_log.jsonl`),并在 `deferred-work.md` 对应条目标记 resolved。加载器不读 assets/,零运行时影响。

## Suggested Review Order

**入口:11 工具总览的准确性(本补做的核心价值)**

- 工具名/数量与参数默认值均按 `--dump-schema` 实况核对;op 映射去重后 == 47
  [`tools.md:1`](../../skills/cell-annotation/assets/tools.md#L1)

- 逐工具参数表:与 7 个脚本的 `--dump-schema` 输出逐一对照(默认值、必填、说明)
  [`tools.md:47`](../../skills/cell-annotation/assets/tools.md#L47)

- op 映射表 + 核对清单:run_id 格式、`last_exec_run_id`、复用 op、h5ad 加载纪律
  [`tools.md:169`](../../skills/cell-annotation/assets/tools.md#L169)

**交付报告模板与真实输出对齐**

- 各字段来源标注:final_annotations.json `_meta`/`_summary`/`annotations`、step7_diagnose.json、session_end final_summary
  [`report-template.md:1`](../../skills/cell-annotation/assets/report-template.md#L1)

**外围:deferred 登记**

- assets/ 条目已标记 resolved;其余 deferred 项(evals/、metrics 20 vs 15、trajectory §3.2 缺行)保持不变
  [`deferred-work.md:1`](../../_bmad-output/implementation-artifacts/deferred-work.md#L1)
