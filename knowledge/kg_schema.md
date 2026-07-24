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