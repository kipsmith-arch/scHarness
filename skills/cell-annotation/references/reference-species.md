# 参考物种静态名录

> 给 `cross_species_routing` 用：3a 只报告本物种 KG 覆盖，**不**打分推荐参考物种。
> LLM 按亲缘从本表点名，经 `--reference-species` 传入，最多 3 个。BLAST **不改选**。

## 选用原则

- **近缘优先**：同属 > 同科 > 同目。拟南芥优先芸薹属/十字花科，不先跳到禾本科。
- **不要混库**：植物数据集不要点名 `homo_sapiens` / `mus_musculus` 等动物；动物不要点植物。
- **上限 3**：`routing_multi_reference` 的 action 写出列表；工具对超过 3 个会 warning 并截断为先传入的 3 个。
- **BLAST vs Ensembl**：有用户 query FASTA、或 Ensembl 不可达、或用户要求本地比对时选 `--provider blastp`。默认仍是 `ensembl_compara`（高覆盖拟南芥路径不要改默认）。
- **zip 没有的物种**：BLAST 该 ref 空映射 + warning；若已改道 Ensembl 则由 Compara 处理。

CLI / KG id 为 `lower_underscore`（如 `arabidopsis_thaliana`）。BLAST 磁盘前缀为表中「BLAST 前缀」列。

## BLAST 库（27 个，2025-11-04 构建）

| BLAST 前缀 | KG / CLI id |
|---|---|
| Arabidopsis_thaliana | arabidopsis_thaliana |
| Brassica_rapa | brassica_rapa |
| Oryza_sativa | oryza_sativa |
| Zea_mays | zea_mays |
| Triticum_aestivum | triticum_aestivum |
| Glycine_max | glycine_max |
| Medicago_truncatula | medicago_truncatula |
| Solanum_lycopersicum | solanum_lycopersicum |
| Nicotiana_tabacum | nicotiana_tabacum |
| Nicotiana_attenuata | nicotiana_attenuata |
| Fragaria_vesca | fragaria_vesca |
| Manihot_esculenta | manihot_esculenta |
| Gossypium_hirsutum | gossypium_hirsutum |
| Bombax_ceiba | bombax_ceiba |
| Catharanthus_roseus | catharanthus_roseus |
| Populus_alba_var_pyramidalis | populus_alba_var_pyramidalis |
| Homo_sapiens | homo_sapiens |
| Mus_musculus | mus_musculus |
| Danio_rerio | danio_rerio |
| Oryzias_latipes | oryzias_latipes |
| Oreochromis_niloticus | oreochromis_niloticus |
| Oncorhynchus_mykiss | oncorhynchus_mykiss |
| Gasterosteus_aculeatus | gasterosteus_aculeatus |
| Astyanax_mexicanus | astyanax_mexicanus |
| Mastacembelus_armatus | mastacembelus_armatus |
| Monopterus_albus | monopterus_albus |
| Nothobranchius_furzeri | nothobranchius_furzeri |

## 仅 Ensembl 可用（不在此 zip）

KG 中常见、但不在 BLAST zip 的物种仍可用 `--provider ensembl_compara`，例如 `sorghum_bicolor`。选了 zip 没有的物种跑 BLAST 时，该 ref 空 + warning。
