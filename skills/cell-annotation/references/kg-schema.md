# 知识图谱(KG)schema 与查询语义(skill references 版)

> 本文档是 `knowledge/kg_schema.md` 原文 + 查询语义说明的合集。
> 用途:理解 `step3_kg__query` 的结果(kg_hits.json)、ancestors 字段、gene_key 映射;SKILL.md §3.7 kg_match 与 §3.8/3.9 并列判断依赖本文件。

## 目录(TOC)

- [节点与关系 schema](#节点与关系-schema)
- [查询语义(step3_kg__query)](#查询语义step3_kg__query)
- [ancestors 与层级判断](#ancestors-与层级判断)
- [基因名映射 gene_key](#基因名映射-gene_key)
- [使用注意](#使用注意)

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

## 查询语义(step3_kg__query)

`step3_kg__query` 把 marker 基因 → 候选细胞类型(Ontology 节点),过程与过滤:

1. **基因匹配与映射**:var_names 为 TAIR locus ID 时,`--gene-key` 默认用 `name_map4Arabidopsis_thaliana_symbol.json`(10,963 条)映射为 symbol 后查询;也可传 JSON 文件路径或 `none`(用原始 ID)。映射失败/物种特异基因不在 KG 时,n_markers_hit 会低(见 traps.md 陷阱参考与 kg_match 决策点)。
2. **过滤条件**:
   - `--organ`(默认 root):对应 Ontology 的 `o.Organ`,与数据来源 organ 必须对齐;`--strict-organ` 打开时严格过滤(默认不区分大小写)。
   - `--species` / `--species-type`:对应 Gene 的 `g.Species` / `g.Species_type`,作为信息过滤参与查询。
   - `--min-confidence`:`marker_of.relation_confidence` 下限,默认 0(不滤)。
3. **候选聚合**:per cluster 按 marker_count → mean_confidence 排名,产出 `candidates`(cell_type / supporting_markers / marker_count / mean_confidence / min_confidence / sources)。
4. **层级查询**:`--max-ancestor-hops`(默认 3)沿 `ontology_relation` 查祖先写入 kg_hits.json 的 ancestors map;`--max-ancestor-hops 0` 表示跳过层级查询(ancestors 为空)。
5. **连通性检查**:`step3_kg__test-connection` 只查连通性,不依赖项目目录。

输出:`step3_kg/kg_hits.json`(每簇候选 + gene_to_cts + ancestors)、`step3_kg/kg_source.txt`(KG 来源)。

## ancestors 与层级判断

- `ancestors` map 形如 `{cell_type: [ancestor_name, ...]}`(≤3 跳)。
- **空列表不代表该类型没有祖先,只是 KG 本体没收录**——此时父子/同义判断要靠生物学知识,并注明依据来源。
- `step4_judge` 的 `first_second_ancestor_overlap` 直接基于它:两个候选存在父子关系时,并列是层级而非模糊(见 traps.md 陷阱 2)。
- 例:"lateral root cap" 的 ancestors 含 "root cap" → 并列时选更具体的 "lateral root cap"。

## 基因名映射 gene_key

| 取值 | 行为 |
|---|---|
| `name_map`(默认) | 用 `name_map4Arabidopsis_thaliana_symbol.json` 做 TAIR→symbol 映射后查询 |
| JSON 文件路径 | 用自定义映射文件 |
| `none` | 用原始 var_names 直接查询 |

## 使用注意

- `kg_version` 是 Neo4j 服务版本(`kg_version_source=neo4j-server`)——KG 本体无版本号,这是溯源代理值;`--species` 会作为 `g.Species` 过滤参与查询。
- `query_hierarchy` 的查询错误单独存于 `query_errors` 字段,不混入 ancestors 统计。
- 凭据优先级:CLI 参数(--uri/--user/--password)> 环境变量(NEO4J_URI/USER/PASSWORD);密码不硬编码、不写文件。
- NEO4J 不可用时先跑 `step3_kg__test-connection` 定位问题。
