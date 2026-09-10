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

## 物种命名与 BLAST 库

- KG / CLI 用 `lower_underscore`（如 `arabidopsis_thaliana`）。Plant 多为这种写法；部分 Animal 历史数据可能是 `Human` / `Mus musculus`，查库时需规范化。
- BLAST subject 文件前缀是 `Genus_species`（如 `Arabidopsis_thaliana`）。对照表与 27 个已打包库见 `_bmad-output/specs/spec-blastp-homology/SPEC.md` §4.5；实现后写入 `skills/cell-annotation/references/reference-species.md`。
- `marker_of.relation_confidence` 是「该基因是该细胞类型 marker」的图谱置信度（0–1）。**不是** BLAST `pident` / evalue。step3c 聚合出的 `mean_confidence` 只平均这些边。

## 跨物种查询

同源映射（step3b）得到参考物种基因 ID 后，step3c 按该参考物种查 `marker_of`，与本物种直接命中并行。organ 必须与数据来源对齐。