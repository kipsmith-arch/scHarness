# evals/ — skill 测试用例(P5)

本目录是 `cell-annotation` skill 的内部测试资产(对应 implementation_plan §7 与
skill-creator 的 evals 约定)。加载器不读本目录,零运行时影响。

## 用例清单(`evals.json`)

| ID | 名称 | 模式 | 测什么 |
|---|---|---|---|
| E-1 | 端到端 | e2e | 完整 SOP 走查:final_annotations 结构、run_log 完整性、评估报告 |
| E-2 | 模糊簇决策 | mini | candidate_gap:并列候选的差距判断与 decision 枚举合法性 |
| E-3 | unknown 簇 | mini | unknown_cluster:零 KG 命中时不硬贴标签,SOP-5B 处理 |
| E-4 | QC 陷阱 | mini | qc_threshold:植物 mt/cp 不套动物阈值 |
| E-5 | 日志合规 | validate | 复用 E-1~E-4 输出跑 validate_log.py,exit 0 |

## 怎么跑

**E-1 端到端**(消耗 LLM API,Ask First 确认后执行):

```
python -m annot_harness.session --skill skills/cell-annotation \
    --project-dir output/p5_evals --task "<E-1 的 prompt>"
```

前置:step1_prepare 产物就绪(processed.h5ad + obs_snapshot.csv)。

**E-2~E-4 单决策点 mini-session**(低成本,只喂该决策点指标快照):

```
python -m annot_harness.session --skill skills/cell-annotation \
    --project-dir output/p5_evals_E2 --task "<E-2 的 prompt>"
```

不跑 pipeline,只验证 LLM 的 decision 是否落在 §3.2 枚举内、reasoning 是否附依据。

**E-5 日志合规**:

```
python scripts/validate_log.py output/p5_evals/run_log.jsonl
```

## 断言判据

- 枚举合法:E-2~E-4 的 `output.decision` ∈ trajectory_design §3.2 对应决策点枚举
- run_log 完整:session_start / session_end 存在,judgment 字段齐全(decision_point/
  scope/run_ref/inputs/output/reasoning)
- 评估报告:quantitative 指标(strict/relaxed accuracy、macro-F1、聚类纯度)由
  `scripts/evaluate_annotations.py` 产出
- 定性部分人工看(conversation/report)

## 结果落点

- 会话轨迹:`output/<run>/conversation.jsonl` + `run_log.jsonl`
- 评估报告:`output/<run>/evaluation_report.json`

## 迭代

按反馈改 SKILL.md / references → 重跑 → 直到达标(implementation_plan §7.2)。
测试集扩大(P5 迭代轮)在 `evals.json` 追加用例。
