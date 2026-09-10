# Open Questions — 跨物种路由未决问题

> SPEC cross-species-routing 的未决问题伴侣。这些问题影响实施细节,但不阻塞 spec 通过。
> 每个问题给出"当前默认决策"、"需要何时决断"、"决断影响的文件"。

---

## OQ-1 跨物种路径在拟南芥数据上的精度边界

- **当前默认**: ~~批 2 完成时,只在拟南芥数据上证明"不回归"(strict ≥ 0.92, macroF1 ≥ 0.45)。~~ → **已决断 (2026-08-24)**: 用 PRJNA935359 (Sorghum bicolor) 作为批 2 真实价值验证数据集。验证分两阶段:阶段一(可立即跑,仅评跨物种路径覆盖率)+ 阶段二(需数据基板扩展任务先完成,评 strict/macroF1)。详见 SPEC.md Success signal "批 2 真实价值验证" + NG-6。
- **何时决断**: ✅ 2026-08-24 (user)
- **决断影响**: 详见 SPEC.md
- **决策者**: 用户

## OQ-2 P5 evals 跨物种场景是新增 E-6/7 还是扩展 E-1~E-5

- **当前默认**: ~~新增 E-6(覆盖高物种单路径不变)+ E-7(覆盖低物种混合路径),`evals.json` 加 2 条。~~ → **已决断 (2026-08-24)**: 采用方案 A — 新增 E-6 (用 SRP171040, 验证 `routing_accept single_species` 不改变现有行为, 作为负向对照) + E-7 (用 PRJNA935359 sorghum_bicolor, 验证 `routing_accept cross_species_only` + 跨物种路径给出候选)。E-1~E-5 不动。
- **何时决断**: ✅ 2026-08-24 (user)
- **决断影响**: `evals.json` 加 2 条;`evals/README.md` 加章节;E-7 直接用真实 sorghum 数据, 不造 mock。
- **决策者**: 用户

## OQ-3 多参考物种融合的优先级算法

- **当前默认**: ~~`亲缘近 > 覆盖广`(同科 > 同 species_type > 远缘);多个 ref 的候选在聚合时简单 union,不去重。~~ → **已决断 (2026-08-24)**: 采用方案 A — 候选简单 union, LLM 在 candidate_gap 读 `source_species_set` / `source_path_set` 区分优先级(优先级规则:含目标物种名 > 同 species_type ref > 远缘 ref; `direct` > `ortholog` > `mixed`)。不去重, 不加权排序。
- **何时决断**: ✅ 2026-08-24 (user)
- **决断影响**: 实现简单, 不引入排序算法参数; 默认 routing_accept 走单 ref(precheck 推荐 score 最高), routing_multi_reference 少见场景; 算法升级 (B/C 方案) 留作批 3 优化, 批 2 evals E-7 跑完看 LLM 实际行为再决定。
- **决策者**: 用户

## OQ-4 reference species 列表的更新机制

- **当前默认**: `step3a_kg_precheck` 每次跑实时从 Neo4j 取物种列表,实时排序
- **何时决断**: 批 1 实施后第一次跑预检时(看性能)
- **决断影响**:
  - 实时跑 → 简单但慢(每次约 1s)
  - 缓存 → 维护成本,KG 内容更新时缓存过期
- **决策者**: 实施者

## OQ-5 扩张家族反向计数的工程实现

- **当前默认**: `step3b_cross_species_map` 在 summary 输出 `n_ref_genes_per_target_mean`,LLM 看到 > 2.0 警戒
- **何时决断**: 批 2 step5_refine 评估时
- **决断影响**:
  - 当前默认 → 不强制处理扩张家族
  - 替代方案: `step3b_cross_species_map` 检测 1 ref gene 被 ≥ 5 target gene 命中时,自动把该 ref gene 标记为扩张家族,候选聚合时降权
- **决策者**: 实施者

## OQ-6 cross_species_routing 决策点的 action 字段格式

- **当前默认**: 自由文本(如 `run_step3b_cross_species_map --target-species=X --reference-species=Y,Z`)
- **何时决断**: 批 2 trajectory_schema.py 注册时
- **决断影响**:
  - 自由文本 → 灵活但 LLM 输出不稳定
  - 结构化 → 必填字段 `{step: "step3b_cross_species_map__run"|"step3c_kg__query", args: {...}}`,validate_log 校验
- **决策者**: 设计层

---

## 决策流程

实施过程中遇到这些问题:
1. 若能在 ≤ 30 分钟内决断 → 实施者直接决断,在 `.memlog.md` 追加一条 `--type decision` 记录
2. 若需要用户参与 → 暂停当前任务,问用户,得到回答后追加 `--type direction` 记录,继续实施
3. 若影响 spec 主线(影响 CAP / Constraint / Non-goal) → 更新 SPEC.md 与对应 companion,在 `.memlog.md` 追加 `--type event` 记录"spec update"
