- source_spec: `_bmad-output/implementation-artifacts/spec-p2-pipeline-scripts.md`
  summary: run_log.jsonl 追加无并发锁(seq 计数与追加非原子),并发进程会冲突
  evidence: common.append_log 先整文件数行再追加;P6 阶段若并行跑多个 session 写同一 run_log 需加锁或按目录隔离
- source_spec: `_bmad-output/implementation-artifacts/spec-p2-pipeline-scripts.md`
  summary: append_log 每次追加 O(n) 数行(47 记录量级可忽略),且中途失败会留下无收尾 op 的半截 exec 序列(设计按 run_id 前缀查询可容忍)
  evidence: trajectory_design §4.2 明确"按 seq 排序,最新=当前",残缺序列不影响查询;P6 导出脚本前可加失败标记
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: P3 的 assets/ 目录(加载器派生的 11 工具总览 tools.md + 交付报告模板 report-template.md,S-6)
  evidence: 与 SKILL.md/references 无耦合——SKILL.md 只给工具概览表、加载器不读 assets/;独立补做不影响主交付,从主 spec 拆分以压缩 token
  resolved: '2026-08-11'  # assets/tools.md + assets/report-template.md 已交付
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: P3 的 evals/ 目录骨架(README 说明,用例内容归 P5,S-1)
  evidence: 纯目录 + 说明、无实质内容;P5 写 evals.json 时一并创建更自然,从主 spec 拆分
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: references/metrics.md 附:max_mt_pct 默认值写成 20,与脚本实际默认 15.0(step1_prepare.py)不一致
  evidence: P2 评审遗留——该值位于 knowledge/metrics_interpretation.md 原文(verbatim 抄入 references/metrics.md);需在上游 knowledge 修正为 15 后重抄 references(冻结约束禁止单独改副本)
  resolved: '2026-08-11'  # knowledge/metrics_interpretation.md 已改 15,references/metrics.md 同步
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: design/trajectory_design.md §3.2 枚举表只有 12 行,candidate_disambiguate 无专属枚举行(13 决策点缺 1)
  evidence: 上游设计文档缺口;SKILL.md 已用 candidate_gap 词表子集补位,合规;未来 validate_log.py 逐决策点枚举校验时需要该行
  resolved: '2026-08-11'  # §3.2 已补 candidate_disambiguate 行(与 SKILL.md §3.9 枚举集合相等)
