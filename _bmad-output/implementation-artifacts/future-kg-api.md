# 未来规划：图谱改为 API，skill 不再直连 Neo4j

> 日期: 2026-09-10  
> 状态: **未排期**（独立于 BLASTP / P7；不要绑进 `spec-blastp-homology`）  
> 动机: 脚本直连 Bolt + 内嵌 Cypher 把图账号和图 schema 暴露在 skill 进程里，后续要封装 API 以提高系统安全。

skill 仍要「查参考知识」，但只调 HTTP（或同等）接口。Cypher、Bolt 凭据、节点/边类型留在服务端。LLM 侧 schema 文档改为 **API 返回字段**，不再教 `Gene` / `marker_of`。

## 现在谁在直连

| 位置 | 作用 | API 化时 |
|---|---|---|
| `skills/cell-annotation/scripts/step3c_kg.py` | driver、溯源探活、按基因查 `marker_of`、祖先 `ontology_relation*` | 换成 client：provenance / genes→cell_types / ancestors |
| `skills/cell-annotation/scripts/step3a_kg_precheck.py` | 本物种覆盖计数；另有扫同 type 其它物种的推荐 Cypher | 覆盖预检一条 HTTP。推荐排序本就会从 3a 拿掉，不必迁进 API |
| `skills/cell-annotation/scripts/step3_kg_precheck.py` | 3a 的旧文件名副本 | 删掉，勿双份维护 |
| `scripts/build_label_map.py` | 实验层按本体名查祖先 | 复用 ancestors 接口，同样禁止 Bolt |
| `skills/cell-annotation/scripts/common.py` | `neo4j_config` / `add_neo4j_args` / `NEO4J_*`；`SPECIES_NAME_ALIASES` | 改为 `KG_API_*` + token。物种别名进服务端 |

**不直连、换 API 后可不动（JSON 形状不变即可）：**

- `step5_refine.subcluster_kg`（复用 3c 的 `gene_to_cts`）
- `step3b_cross_species_map` 与所有 xmap provider（同源是基因→基因）

## 服务端应收口的查询（现为内嵌 Cypher）

1. 目标物种覆盖（基因数、带细胞类型边的基因数 / 类型数 / 边数）
2. 基因列表 → 细胞类型（过滤：物种、species_type、organ、min_confidence）
3. 细胞类型 → 祖先（跳数上限）
4. 可选：图谱/接口版本（取代 `CALL dbms.components()` 当 `kg_version`）

3c 连接时那批 `DISTINCT Species` / `Organ` / `count(marker_of)` / `labels` 是 Neo4j 运维面，**不要**再暴露给 skill。

## 配置与测试

- `skills/cell-annotation/.env.example`、`common.SKILL_DOTENV_KEYS`
- `docs/CONFIGURATION_REFERENCE.md` §3.0、`--uri/--user/--password` SUPPRESS 约定
- `annot_harness/tests/test_skill_dotenv.py`
- `annot_harness/tests/test_step3c_kg_schema.py`（测的是 argparse 隐藏项，不是图 schema）
- `annot_harness/tests/test_step3a_kg_precheck.py` 及旧 `test_step3_kg_precheck.py` 的真连测
- `experiments/run_b1.py` 的 `test-connection` preflight

## 文档（skill/LLM 将多余，服务实现方仍需要内部模型）

- `knowledge/kg_schema.md`
- `skills/cell-annotation/references/kg-schema.md`
- `_bmad-output/specs/spec-cross-species-routing/kg-schema-extension.md`
- `design/tool_design.md` §10、`assets/tools.md` 凭据表

Skill 包里改成 API 契约（cell_type / organ / confidence / source）。`mean_confidence` 仍表示图谱对 marker 边的把握，与序列比对分数无关。

## 建议落地顺序（到时候再开 spec）

1. 冻结 JSON 契约（与现有 `kg_hits.json` / `coverage_report.json` 字段对齐，减少 LLM/评估抖动）。
2. 抽出 `kg_client`：先可仍走 Bolt，测试改打 client。
3. 服务端实现同一契约；skill 只留 base URL + token。
4. 删 Bolt 参数、Cypher、skill 内 kg-schema 原文；实验脚本同步。

不要在新功能里继续堆 Cypher。本项与 BLASTP 同源映射无依赖，分开排期。
