# deferred-work

本文件只收 **非阻塞、暂不排期** 的遗留（跨器官字符串、覆盖率校验等）。
**下一步要做的实验/计分改动不写这里。** B1_r3 之后的主动规划见
`_bmad-output/implementation-artifacts/b1-r3-followup.md`
（pseudobulk 触发条件重设计；strict 与 confidence 拆开）。
**图谱改为 API、skill 不再直连 Neo4j** 见
`_bmad-output/implementation-artifacts/future-kg-api.md`（未排期，与 BLASTP 无关）。

- source_spec: `_bmad-output/implementation-artifacts/spec-p2-pipeline-scripts.md`
  summary: run_log.jsonl 追加无并发锁(seq 计数与追加非原子),并发进程会冲突
  evidence: common.append_log 先整文件数行再追加;P6 阶段若并行跑多个 session 写同一 run_log 需加锁或按目录隔离
  resolved: '2026-08-11'  # 已确认并发场景不存在:各 session 独立 project_dir,单 session 内工具调用串行
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
  resolved: '2026-08-11'  # P5 已交付:evals/evals.json(E-1~E-5)+ README,且 E-1~E-5 全部跑通
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: references/metrics.md 附:max_mt_pct 默认值写成 20,与脚本实际默认 15.0(step1_prepare.py)不一致
  evidence: P2 评审遗留——该值位于 knowledge/metrics_interpretation.md 原文(verbatim 抄入 references/metrics.md);需在上游 knowledge 修正为 15 后重抄 references(冻结约束禁止单独改副本)
  resolved: '2026-08-11'  # knowledge/metrics_interpretation.md 已改 15,references/metrics.md 同步
- source_spec: `_bmad-output/implementation-artifacts/spec-p3-skill-package.md`
  summary: design/trajectory_design.md §3.2 枚举表只有 12 行,candidate_disambiguate 无专属枚举行(13 决策点缺 1)
  evidence: 上游设计文档缺口;SKILL.md 已用 candidate_gap 词表子集补位,合规;未来 validate_log.py 逐决策点枚举校验时需要该行
  resolved: '2026-08-11'  # §3.2 已补 candidate_disambiguate 行(与 SKILL.md §3.9 枚举集合相等)

- source_spec: `_bmad-output/implementation-artifacts/spec-p5-r2-organ-filter-judgment-granularity.md`
  summary: REQUIRED_SCOPE 在 write_judgment.py 与 validate_log.py 各持一份同源拷贝,跨目录无共享模块/无测试,可能漂移
  evidence: 评审发现;当前靠注释互指防漂移,未做单元测试;加 pytest golden test 或抽到独立 schema 模块后可彻底防止
  status: resolved
  resolved: '2026-08-11'  # REQUIRED_SCOPE 已抽取至 trajectory_schema.py 共享模块;write_judgment/validate_log 均导入之;harness/tests/test_trajectory_schema.py 5 项回归全绿(pytest 57 passed)
  next_action: 无

- source_spec: `_bmad-output/implementation-artifacts/spec-p5-r2-organ-filter-judgment-granularity.md`
  summary: _organ_status 的 `target.strip().title()` 归一化对多词/连字符 organ 名称不鲁棒
  evidence: title() 对 "liver-brain" → "Liver-Brain",但 KG organ 实际可能是 "liver brain"(空格);当前根数据集为单字 "root" 不触发,跨数据集时需重新评估
  status: deferred
  next_action: 支持跨数据集时补充 organ 名称规范化规则和测试

- source_spec: `_bmad-output/implementation-artifacts/spec-p5-r2-organ-filter-judgment-granularity.md`
  summary: _organ_status 的 substring 匹配 `target_norm in o` 存在 false positive("Rootstock" 含 "Root" 会误判为 root status)
  evidence: 评审发现;原始代码即有该问题,本轮未修;未来可改 anchor 匹配(如按 "Root" 或 "|Root|" 等边界)
  status: resolved
  resolved: '2026-08-31'
  resolution: |
    Reworked _organ_status with boundary-aware field matching. New helpers:
    - `_iter_organ_tokens(field)`: splits a KG organ field on `|`, drops the
      "Unknown" sentinel at both the field and token level, returns trimmed
      non-empty tokens.
    - `_field_matches_target(field, target_norm)`: returns True iff any token
      equals target as a whole word (case-insensitive). Substring matches
      like "Root" in "Rootstock" no longer pass.
    - `_organ_status`: now classifies per-field (not per-token) because a
      field like "Stem|Root|Leaf" is a single multi-organ cell type that
      applies to all three organs.
    Added harness/tests/test_step3_kg_organ_status.py with 42 tests pinning
    the new boundary contract, including the D-2 regression case
    (target="root", organ="Rootstock" -> "mismatch", was "root" pre-fix).
  next_action: 无

- source_spec: `_bmad-output/implementation-artifacts/spec-p5-r2-organ-filter-judgment-granularity.md`
  summary: validate_log 覆盖度检查仅查 candidate_gap/label_confirm;refine_effect/candidate_disambiguate 覆盖率未校验
  evidence: 评审发现;spec 当前只要求前两者,后两者为条件触发(仅 analyzed/tied 簇);后续可扩展
  status: deferred
  next_action: 明确条件触发语义后扩展 validate_log 覆盖率校验

- source_spec: `_bmad-output/implementation-artifacts/spec-p5-r2-organ-filter-judgment-granularity.md`
  summary: SKILL.md 中 organ_status 类别名仍是字面量 "root/partial/unknown/mismatch",与 prose "含目标 organ 的候选"在跨 organ 数据集下读起来易混淆
  evidence: 评审发现;代码 category 名为历史遗留(语义=匹配 target);可通过将 category 名改为 "match/partial/unknown/mismatch"(rename)解决,影响所有 kg_hits.json 输出
  status: deferred
  next_action: 仅在确定输出兼容策略后再重命名,并同步 SKILL、脚本和产物 schema

- source_spec: `_bmad-output/implementation-artifacts/spec-measure-judge-decouple.md`
  summary: 失败的 retry 仍会覆盖 processed.h5ad / JSON 产物（脚本先写盘再返回 error），log 层保留上一 ok exec，文件层无快照回滚
  evidence: ARM2_RETRY 要求「脚本非零 → 当前产物仍是上一 ok」；GB 级 h5ad 快照不在本轮任务表，且会破坏一子命令一次加载。walker 已在 dispatch status!=ok 时不更新 last_ok。
- source_spec: `_bmad-output/implementation-artifacts/spec-measure-judge-decouple.md`
  summary: qc_threshold / de_method 的 judgment 尚未映射到 step1/step2 CLI 阈值与 DE 方法参数（仅 resolution_select 已显式传 --target-resolution）
  evidence: A 组「先判再首次跑」对分辨率已落地；其余枚举→argparse 对照表未写入 frozen spec，需单独故事补参数绑定。
- source_spec: `_bmad-output/implementation-artifacts/spec-measure-judge-decouple.md`
  summary: pipeline exec_record 不写 status=ok 字段，失败 attempt 若已落盘 exec 无法从 log 区分于成功 exec
  evidence: 预存在于 common.exec_record；本轮 walker 改以 dispatcher 返回的 status==ok 为准。若要 log 层 last-ok，需给 exec 补 status。
- source_spec: `_bmad-output/implementation-artifacts/spec-b1-three-arm-rerun.md`
  summary: step3_kg test-connection 在 GraphDatabase.driver 构造成功时即 connected=true，查询失败只写入 kg_provenance.error
  evidence: localhost:7687 拒绝时仍 emit status=ok；run_b1.preflight 已在编排层解析 provenance.error / node_labels，根因在 step3_kg.op_connect / kg_provenance 的软失败。
