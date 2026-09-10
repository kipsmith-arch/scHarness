# 知识图谱(KG)schema 与查询语义(skill references 版)

> 本文档是 `knowledge/kg_schema.md` 原文 + 查询语义说明的合集。
> 用途:理解 `step3c_kg__query` 的结果(kg_hits.json)、ancestors 字段;SKILL.md §3.7 kg_match 与 §3.8/3.9 并列判断依赖本文件。

## 目录(TOC)

- [节点与关系 schema](#节点与关系-schema)
- [查询语义(step3c_kg__query)](#查询语义step3c_kg__query)
- [ancestors 与层级判断](#ancestors-与层级判断)
- [物种过滤与命名](#物种过滤与命名)

---

## 节点与关系 schema

Node properties:
- **Gene**
  - `Marker_resource`: STRING Example: "scRNA-seq"
  - `id`: STRING Example: "FvH4_2g13370"
  - `Name`: STRING Example: "FvH4_2g13370"
  - `node_id`: INTEGER Min: 33198, Max: 184120
  - `Species`: STRING Example: "fragaria_vesca"
  - `Type`: STRING Example: "Gene"
  - `NCBI_id`: STRING Example: "FvH4_2g13370"
  - `Species_type`: STRING Example: "Plant"
  - `Dataset`: STRING Example: "CRA004848"
- **Ontology**
  - `Species_type`: STRING Example: "Animal"
  - `Organ`: STRING Example: "Unknown"
  - `Name`: STRING Example: "Brachet's cleft"
  - `info_source`: STRING Example: "Zebrafish Anatomy Ontology"
  - `node_id`: INTEGER Min: 3525, Max: 167662
  - `Type`: STRING Example: "Unknown"
  - `id`: STRING Example: "ZFA:0000000"
  - `Def`: STRING Example: "The visible division between epiblast and hypoblas"
Relationship properties:
- **marker_of**
  - `relation`: STRING Example: "marker_of"
  - `relation_confidence`: FLOAT Example: "0.7"
  - `info_source`: STRING Example: "PMID:35105355"
  - `info_source_length`: INTEGER Example: "1"
- **ontology_relation**
  - `info_source_length`: INTEGER Example: "1"
  - `relation`: STRING Example: "part_of"
  - `info_source`: STRING Example: "Plant Ontology"
The relationships:
(:Gene)-[:marker_of]->(:Ontology)
(:Ontology)-[:ontology_relation]->(:Ontology)

## 查询语义(step3c_kg__query)

`step3c_kg__query` 把 marker 基因 → 候选细胞类型(Ontology 节点),过程与过滤:

**参数分类**(遵循 LLM 工具参数 vs skill 配置原则,见 `AGENTS.md`):
- **任务参数(A 类,LLM 可见)**——生物决策:`--organ`(必填)、`--species`、`--species-type`、`--strict-organ`。
- **环境参数(B 类,从 LLM schema 隐藏)**——KG 服务调优,CLI/运维临时覆盖:
  - `--min-confidence`(默认 0):`marker_of.relation_confidence` 下限。
  - `--max-ancestor-hops`(默认 3):`ontology_relation` 祖先查询上限;设为 0 跳过层级查询(ancestors 为空)。

**基因 ID 不做映射**——step3c_kg 用 `adata.var_names` 原样查 KG。若 h5ad 使用 TAIR locus ID (如 AT1G01010) 而 KG 存 symbol,需在进入 pipeline 前手动转换(可用 `name_map4Arabidopsis_thaliana_symbol.json` 或上游预处理脚本),这是数据处理责任,不在 skill 行为范围内。

1. **过滤条件**:
   - `--organ`(LLM 必填):对应 Ontology 的 `o.Organ`,与数据来源 organ 必须对齐。
   - `--species`(LLM 可选):对应 Gene 的 `g.Species`。**建议不传**——TAIR locus ID 是物种特有命名,不传即天然物种隔离;若传,必须用 KG 存储格式(小写+下划线,如 `arabidopsis_thaliana`)。
   - `--strict-organ`(LLM 可选):设为 True 时严格按 organ 过滤命中(默认 false)。
   - `--species-type`(默认 `Plant`):对应 Gene 的 `g.Species_type`(LLM 可选——不同物种切换场景下需要由 LLM 决定)。
   - `--min-confidence`(默认 0):`marker_of.relation_confidence` 下限。
2. **候选聚合**:per cluster 按 marker_count → mean_confidence 排名,产出 `candidates`(cell_type / supporting_markers / marker_count / mean_confidence / min_confidence / sources)。
3. **层级查询**:`--max-ancestor-hops`(默认 3)沿 `ontology_relation` 查祖先写入 kg_hits.json 的 ancestors map;设为 0 跳过层级查询(ancestors 为空)。
4. **连通性检查**:`step3c_kg__test-connection` 只查连通性,不依赖项目目录。

输出:`step3c_kg/kg_hits.json`(每簇候选 + gene_to_cts + ancestors)、`step3c_kg/kg_source.txt`(KG 来源)。

## ancestors 与层级判断

- `ancestors` map 形如 `{cell_type: [ancestor_name, ...]}`(≤3 跳)。
- **空列表不代表该类型没有祖先,只是 KG 本体没收录**——此时父子/同义判断要靠生物学知识,并注明依据来源。
- `step4_rank` 的 `first_second_ancestor_overlap` 直接基于它:两个候选存在父子关系时,并列是层级而非模糊(见 traps.md 陷阱 2)。
- 例:"lateral root cap" 的 ancestors 含 "root cap" → 并列时选更具体的 "lateral root cap"。

## 物种过滤与命名(重要)

- **默认不设 `KG_SPECIES`**:基因 ID(TAIR locus)本身物种特异,不设 species 过滤即天然隔离,也避免物种名格式不匹配导致 0 命中。
- 若需要设,必须用 KG 存储格式:**小写 + 下划线**(如 `arabidopsis_thaliana`),不是人类可读名(`Arabidopsis thaliana`)。KG 中 `g.Species` 为精确字符串,带空格的常见名会精确匹配失败。
- 当前数据集(SRP171040 拟南芥根)对应的 KG 物种值为 `arabidopsis_thaliana`。

## 使用注意

- `kg_version` 是 Neo4j 服务版本(`kg_version_source=neo4j-server`)——KG 本体无版本号,这是溯源代理值。
- `query_hierarchy` 的查询错误单独存于 `query_errors` 字段,不混入 ancestors 统计。
- 凭据优先级:CLI 参数(--uri/--user/--password)> 环境变量(NEO4J_URI/USER/PASSWORD);密码不硬编码、不写文件。
- NEO4J 不可用时先跑 `step3c_kg__test-connection` 定位问题。
