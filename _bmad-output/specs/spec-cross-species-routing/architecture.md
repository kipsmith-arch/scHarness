# Architecture — 跨物种路由数据流与模块拓扑

> SPEC cross-species-routing 的架构伴侣。描述新工具在 7 步 pipeline 中的位置、数据流、加载纪律、与现有 step 的接口契约。
> 内容与 `design/tool_design.md` §4(子命令结构与加载策略)同源;此处只描述本 spec 引入的扩展,不复述既有架构。

---

## 1. 跨物种路径在 7 步流程中的位置

```
                        raw h5ad
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
        step1_prepare metrics     step1_prepare run
        (1× raw)                  (1× raw)
                │                       │
                └───────────┬───────────┘
                            ▼
                   step1_prepare/
                     processed.h5ad
                     qc_metrics.json
                     obs_snapshot.csv
                            │
                            ▼
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
   step2_markers run                          (无变化)
   (1× proc)                                       │
        │                                          ▼
        ▼                                  ┌─ CAP-1 ─── step3_kg_precheck run  ← NEW (0 h5ad)
   step2_markers/                                   │     纯 Neo4j 查询
     markers.json                                   │     输出 coverage_report.json
        │                                           ▼
        │                                  ┌─ CAP-4 ─── LLM 决策 cross_species_routing
        │                                           │     (session 级)
        │                                           │     decision: accept / force_single / force_cross / multi_ref
        │                                           ▼
        │                                  ┌─ (conditional) step2_cross_species_map run  ← NEW (0 h5ad)
        │                                           │     调 provider (default Ensembl Compara REST)
        │                                           │     输出 cross_species_map.json
        │                                           │     species-level 缓存 + --force-refresh
        ▼                                           ▼
   step3_kg query   ← CAP-3 扩展 (0 h5ad)         ──┘
   - --ortholog-map 选项(可选)
   - 直接路径 + 跨物种路径并行跑
   - 候选聚合保留 source_species / source_path
        │
        ▼
   step3_kg/kg_hits.json
   - n_direct_hits / n_ortholog_hits / n_mixed_hits
   - 候选 candidates[] 增 source_species 字段
        │
        ▼
   (后续 step4 / step5 / step6 / step7 不变,但 LLM 在
    candidate_gap / candidate_disambiguate / global_quality
    中读新字段)
```

**关键改动:**
- SOP-3 入口前插 CAP-1 precheck + CAP-4 决策 + 可选 CAP-2 ortholog
- SOP-3 内 step3_kg 接收 `--ortholog-map` 后并行跑两条路径
- step4 ~ step7 工具层不变,LLM 决策视图多读几个字段

## 2. 新工具清单

| 工具名 | 加载次数 | 输入 | 输出 | 用途 |
|---|---|---|---|---|
| `step3_kg_precheck__run` | 0 h5ad | `--target-species` `--organ` `--species-type` `--project-dir` | `step3_kg_precheck/coverage_report.json` | 目标物种在 KG 中的 cell type 覆盖率评估 + 参考物种推荐 |
| `step2_cross_species_map__run` | 0 h5ad | `--provider`(默认 `ensembl_compara`)+ `--target-species` + `--reference-species[]` + `--species-type` + `--input markers.json` + `--project-dir` + `--min-score` + `--max-hits-per-gene` + `--force-refresh` + provider tuning (`--provider-timeout/--provider-max-retries/--provider-concurrency`) | `step2_cross_species_map/cross_species_map.json` | Cross-species gene mapping via pluggable provider (Ensembl Compara default; future BLAST/DIAMOND/OMA/eggnog) |

`step3_kg__query` 增 `--ortholog-map` 选项(默认 None)。

## 3. 加载次数影响

| 场景 | 现有 | 加本 spec 后 | 增量 |
|---|---|---|---|
| 完整 pipeline(含 precheck + ortholog) | 4 (1 raw + 3 proc) | **4**(0 + 0 + 1 raw + 3 proc) | **+0** |
| 仅 precheck(独立调) | 0 | 0 | 0 |
| 仅 ortholog(独立调) | 0 | 0 | 0 |
| recluster 一次 | 1 | 1 | 0 |

零加载次数增量,与 C-1 一致。

## 4. 模块边界

- **`step3_kg_precheck__run` 是 step3_kg 的前置诊断,不是替代。** 它不查 cell type,只查目标物种的"基因覆盖度"。
- **`step2_cross_species_map__run` 是 step2_markers 的下游、step3_kg 的上游。** 它消费 markers.json,产出 `cross_species_map.json`;不进 h5ad。Provider 是 pluggable(默认 Ensembl Compara);CLI 通过 `--provider` 参数 dispatch,argparse `choices` 从 `step2_xmap_providers.PROVIDERS` registry 读取。新增 provider = 新建 `step2_xmap_providers/<name>.py` 实现 `BaseCrossSpeciesProvider` 接口 + `@register_provider` 装饰器;无需改 CLI / LLM-facing / step3_kg。
- **`step3_kg` 不直接调 provider。** Cross-species mapping 是独立工具(单一职责、便于单独缓存 / 测试 / 降级 / 切换 provider)。
- **降级策略**: `step2_cross_species_map` provider 不可达时(超时 / 429 / DNS 失败 / unavailable division)返回 `{"status": "ok", "data": {"cross_species_map": {}, "warnings": [...]}}`,而非 error;`step3_kg` 看到空 cross_species_map 自动回退单路径。

## 5. 与现有决策点的接口

| 现有决策点 | 改动 | 说明 |
|---|---|---|
| `kg_match`(session) | 读 `kg_hits.query_stats.n_direct_hits` / `n_ortholog_hits` | 命中率低时区分"直接路径低"还是"跨物种路径低",判断方向不同 |
| `candidate_gap`(cluster) | 候选的 `source_species` 字段影响排序 | 候选优先级 = organ 匹配 → 物种亲缘 → marker_count → confidence |
| `candidate_disambiguate`(cluster) | 第一候选 vs 第二候选的 `source_species` 一致性 | 同源到同一参考物种的并列 ≠ 真模糊;同源到不同参考物种的并列才是 |
| `global_quality`(session) | 新增 `cross_species_ortholog_rate` / `mixed_candidate_fraction` | 整体看跨物种路径是否带来新候选、占比是否合理 |
| 新增 `cross_species_routing`(session) | 决策点本身 | LLM 读完 precheck 报告后必走 |

## 6. 工具 schema 派生

按既有约定,新工具通过 `--dump-schema` 自动派生 `tool_schemas` / `tool_runtime`(见 `skills/cell-annotation/scripts/common.py:dump_schema`)。`step3_kg.py` 改 `--ortholog-map` 不需要手动维护 schema。

`step3_kg_precheck.py` 与 `step2_cross_species_map.py` 同样实现 `--dump-schema`,loader 在 `harness/skill_loader.py` 自动识别。

## 7. 凭据与环境

- **Neo4j 凭据**: `step3_kg_precheck` 复用现有 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`(从 `skills/cell-annotation/.env` 读)。
- **Ensembl REST**: 无凭据。环境变量新增 `ENSEMBL_REST_HOST`(默认根据 species_type 路由),可在 `.env` 覆盖(默认 `https://rest.ensembl.org`)。
- **超时**: `--ensembl-timeout`(默认 10s),`--ensembl-max-retries`(默认 4)。
- **缓存目录**: `step2_cross_species_map/cache/cross_species_map__{target}__{ref}___{provider}__{md5(markers.json)}.json`(species-level;`md5(markers.json)` 防止不同 marker 集合复用旧映射;cache key 含 provider name 让不同 provider 独立缓存)。

## 8. 失败模式

| 失败 | 检测点 | 行为 |
|---|---|---|
| Neo4j 不可达 | precheck 连不上 | precheck 返回 `{"status": "error"}`,LLM 走单物种路径 + warn |
| Ensembl 不可达 | ortholog POST/GET 全部超时 | ortholog 返回空 map + warning,step3_kg 走单路径 |
| Ensembl 返回 429 | 响应状态 | 指数退避(Retry-After)+ 最终失败降级 |
| Ensembl 返回 200 但 body 无 homologies | `data[0].homologies == []` | 视为该 marker 无 ortholog,记入 `unmapped_genes[]` |
| 目标物种不在 Ensembl Compara | Ensembl 404 | 跳过该 marker,记 unmapped,继续 |
| 物种名规范化失败 | KG 中查不到 normalized name | 跳过该 reference 的候选,在 run_log 记 warn |
| cross_species_map.json 文件损坏 | JSON parse fail | 重跑(不读 cache,`--force-refresh` 自动) |
| Provider crash (Exception in __init__) | get_provider() raises | CLI 返回 `status: error`,而不是 graceful degrade(这是 init-time error,不能安全忽略) |
