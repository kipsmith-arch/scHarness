---
name: cell-annotation
description: 单细胞 RNA-seq 细胞类型注释技能:从预处理(step1_prepare)到 marker 发现(step2_markers)、知识图谱查询(step3_kg)、簇判断(step4_judge)、细化(step5_refine)、验证(step6_validate)与诊断(step7_diagnose)的完整流水线。当用户要求"注释单细胞数据/找 marker/细胞类型判断/跑 pipeline"时触发。
---

# Cell Annotation(占位版 — P3 将重写正式 SKILL.md)

> 本文件是 P2 阶段的占位 SKILL.md,仅用于让标准 skill 加载器从 `scripts/`
> 派生工具接口(implementation_plan.md §4.3 的验证标准)。正式指令、
> references 指引与决策点流程将在 P3(S-2/S-3)写入。

## 工具

本技能的 11 个 CLI 工具由加载器从 `scripts/` 的 `--dump-schema` 自动派生
(`{script}__{subcommand}` 命名),覆盖 47 个原子操作:

| 脚本 | 子命令 | 加载次数 |
|---|---|---|
| `step1_prepare` | `metrics` / `run` / `recluster` | 1× raw / 1× raw / 1× proc |
| `step2_markers` | `run` | 1× proc |
| `step3_kg` | `query` / `test-connection` | 0 |
| `step4_judge` | `run` | 0 |
| `step5_refine` | `run` | 1× proc |
| `step6_validate` | `run` / `report` | 1× proc(backed 可选)/ 0 |
| `step7_diagnose` | `run` | 0 |

工具契约:stdout 最后一行 JSON `{"status":"ok","data":{...}}`;
每个原子操作完成后自动追加 `exec` 记录到 `<project-dir>/run_log.jsonl`。
