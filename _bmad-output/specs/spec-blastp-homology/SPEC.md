---
id: SPEC-blastp-homology
title: cell-annotation skill BLASTP 同源映射（step3b provider）
status: proposed
created: 2026-09-10
sources:
  - _bmad-output/specs/spec-cross-species-routing/SPEC.md
  - knowledge/cross-species-annotation-handbook.md
  - design/tool_design.md
  - design/atomic_operations.md
---

# BLASTP 同源映射（step3b provider）

> 状态：设计已讨论定稿，待实现。日期：2026-09-10。  
> 范围：cell-annotation skill 增加本地 BLASTP 同源映射；不改 harness；不改默认 Ensembl 路径。  
> 前置：`spec-cross-species-routing`（CAP-2 provider 抽象）。该 spec 的 **NG-2（不实现蛋白级 BLAST）在此撤销**。

## 0. 相对跨物种 spec 的修订

| 原约定（spec-cross-species-routing） | 本 spec |
|---|---|
| NG-2 不做 BLAST | 做 `blastp` provider；subject 库按需下载 |
| CAP-2 无需用户提供蛋白序列 | BLAST 路径 **必须** 用户提供 query FASTA |
| CAP-1 / CAP-4：3a 打分推荐参考物种 | 3a **只做覆盖预检**；LLM 从静态名录按亲缘点名，最多 3 个 |
| `--max-hits-per-gene` 默认 3、合并后再全局 top-3 | 每个参考物种 **best-1**；跨物种都留 |
| Ensembl 不可达 → 空 map + warning | BLAST 配置/下载失败 → **同组 ref 改道 Ensembl**，仍 `ok` |
| （无）效果闸门 | **不设数字阈值**；理不理想只由 LLM 判断 |

---

## 1. 目标

为本物种 marker 提供一条**不依赖 Ensembl REST** 的基因→参考基因映射：用户提供 query 蛋白 FASTA，skill 按需下载图谱侧 **subject BLAST 库**，一次 `blastp` 打到最多 3 个参考物种，再走现有 `step3c_kg`。

BLAST **不**直接给出细胞类型。3c 的 `confidence` 仍是图谱边 `marker_of.relation_confidence`，与 `pident` / evalue 无关。

## 2. 非目标

- 不把 BLAST 库打进 git / `.skill` 包。
- 不下载 `ncbi-blast+`（系统依赖，必须已在 PATH）。
- 不做 reciprocal best hit（用户只给 query FASTA，没有目标物种全蛋白库）。
- 不按基因 ID 做 TAIR locus → symbol 等转换（仍是 pipeline 上游责任）。
- 不设「效果不理想」的数字阈值；理不理想只由 LLM 判断。
- 不改默认 `--provider`（仍为 `ensembl_compara`），以免 B1 / 高覆盖拟南芥路径回归。
- 3a **覆盖预检留下**（本物种 KG 够不够 → 要不要做同源）；只去掉参考物种**打分推荐**。

## 3. 架构边界

| 项 | 决定 |
|---|---|
| 解剖 | 维持 `SKILL.md` / `scripts/` / `references/` / `assets/` / `evals/`。不新增 `data/`。 |
| 角色 | `step3b_xmap_providers` 的一个 provider，名 `blastp`。产出 `MappingRecord`，下游仍是 `cross_species_map.json` → `step3c_kg --ortholog-map`。 |
| 配置归属 | cell-annotation `.env`，harness 不知情。 |
| 下载时机 | 仅当本次调用 `--provider blastp` 且本地缺库或校验失败。skill 加载、Ensembl 路径、单物种 3c **不得**下载。 |
| Subject | 预构建 BLASTDB v5 蛋白库 zip（约 42MB，27 物种，路径 `blastdb/prot/{Genus_species}.*`）。title 只有基因 ID（如 `lcl\|AT1G01010`），无细胞类型 / organ。实测为蛋白组量级（每种约 2 万条），不是几十条 marker 列表。 |
| Query | 用户提供 FASTA。skill 不提供 query 序列。 |
| 参考物种 | LLM 按亲缘从静态名录点名，最多 3 个，经 `--reference-species` 传入。BLAST **不改选**。3a 不再输出 `recommended_reference_species` 排序。 |

建议环境变量（写入 `skills/cell-annotation/.env.example`，同步 `SKILL_DOTENV_KEYS`）：

| 变量 | 默认 | 用途 |
|---|---|---|
| `CELL_ANNOTATION_BLASTDB_DIR` | `~/.cache/sc-harness/cell-annotation/blastdb` | 解压后的库根（其下为 `prot/`） |
| `CELL_ANNOTATION_BLASTDB_URL` | `https://xener.dcs.cloud/api/public/download?file=blastdb.zip` | subject zip |
| `CELL_ANNOTATION_BLASTDB_SHA256` | 实现时 pin 该 zip 的 hex | 校验 |
| `CELL_ANNOTATION_QUERY_FASTA` | 无 | `--query-fasta` 的默认路径 |

`--query-fasta` 对 LLM 可见（任务数据）。仅 `provider=blastp` 时必填；Ensembl 路径忽略。

## 4. 组件

### 4.1 `ensure_blastdb`

- 若 `{BLASTDB_DIR}/prot/Arabidopsis_thaliana.pin` 等已存在且完整，直接用。
- 否则 **GET** zip（该接口 HEAD 返回 404，探测必须用 GET），写入临时文件，校验 sha256，原子解压到缓存根，使布局等于 zip 内 `blastdb/prot/`。
- zip 一次含 27 物种，只下一份，不按物种反复下。
- 并发用锁文件，避免两个进程同时写。

### 4.2 `BlastpProvider`

- `name = "blastp"`，`score_type = "percent_identity"`（`pident` 已是 0–100）。
- `available()`：`blastp` 在 PATH，且每个参考物种能对上缓存里的 `{Genus_species}` 库（必要时先 `ensure_blastdb`）。
- **禁止**按基因起 `blastp` 进程。每个参考物种：**一条** query FASTA（已过滤）对一个 `-db`，跑一次 `blastp`，tabular 输出后再按基因拆。
- 基类增加可选 `lookup_many(genes)`：Ensembl 默认内部循环现有 `lookup`（行为不变）；BLAST 覆盖为上述批量。CLI `op_query_provider` 有则走批量。
- `mapping_type`：`blastp_top_hit` / `blastp_multiple_hits`（截断前若该物种有多条 hit）。`raw` 保留 bitscore、evalue、sseqid 原文。
- subject id 去掉 `lcl|` / `dbj|` 等前缀再写入 `ref_gene_id`。`ref_species` 用 CLI 的 KG 格式（`arabidopsis_thaliana`），不用磁盘文件名。

### 4.3 一对多（3b 收，不留给 3c）

每个 `--reference-species` **只留该物种 score 最高的 1 条**。最多 3 个参考 → 最多 3 条映射进 3c。

- 不因一对多丢掉整条 DEG。
- 跨物种的多条都保留（印证，不是 paralog 噪声）。
- Ensembl 路径用同一规则（改现有「每 ref 最多 3 条再全局 top-3」：近缘 paralog 会挤掉其它物种）。
- 3c 继续管 organ / `relation_confidence`，不负责砍 paralog。3c **不读** `MappingRecord.confidence`。

### 4.4 Query FASTA 过滤（对 h5ad，不二次加载）

用户 FASTA 可能与矩阵基因不一致（整蛋白组、ID 体系不同、带 `lcl|`）。

顺序：

1. 读 `var_snapshot.csv`（与 `processed.h5ad` 同级 sidecar），得到数据集基因 ID。**禁止**为此再 load h5ad。
2. 解析 FASTA header：取第一个 token，剥 `lcl|` 等前缀。
3. 丢掉不在 `var_names` 中的序列，记 warning（条数）。
4. 3b 仍只打 `markers.json` 里的 DEG：再做 FASTA ∩ marker。零交集 → 按故障改道（见 §6），不当成「注释质量差」。

`--query-fasta` 未给或文件不可读：对 `blastp` 为配置故障 → 改道 Ensembl。

### 4.5 物种名与静态名录

CLI / KG：`arabidopsis_thaliana`。磁盘：`Arabidopsis_thaliana`。用 zip 内 27 个前缀做显式对照；未知名 fallback 为逐段首字母大写。参考物种不在 27 个库中：该 ref 空映射 + warning，其它 ref 继续。

LLM 选参考物种用 **静态表**（不打分、不推荐），写入 `skills/cell-annotation/references/reference-species.md`，SKILL.md / sop.md 在 `cross_species_routing` 处引用。表同时给出 BLAST 文件前缀与 KG/Ensembl `lower_underscore` ID。不新增「只列名」工具。

zip 内 27 个 BLAST 库（2025-11-04 构建）：

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

名录可另附 KG 中常见、但不在此 zip 的物种（仅 Ensembl 可用）。LLM 选了 zip 没有的物种：BLAST 该 ref 空 + warning；若已改道 Ensembl 则由 Compara 处理。

### 4.6 CLI / 缓存

`step3b_cross_species_map.py`：

- `--provider` 随 registry 出现 `blastp`。
- 新增 `--query-fasta`。
- `--reference-species` 仍必填；SOP 约束最多 3 个（实现可在超过 3 时 warning 并截断，或 fail；推荐 **warning + 截断为先传入的 3 个**，避免 LLM 多写一个就整步失败）。
- 现有缓存 key 只 hash `markers.json`。`blastp` 必须把 **过滤前 query FASTA 的摘要** 编进 key，否则换序列会脏命中。

## 5. 一次调用数据流

```
markers.json + 用户 FASTA + var_snapshot.csv
  → 按 var_names 过滤 FASTA → ∩ marker 基因
  → LLM 已选定 ≤3 个 reference-species（静态表 + 亲缘，非 3a 推荐）
  → provider=blastp：缺库则 GET zip
  → 每个 ref 一次 blastp → 每物种 best-1
  → cross_species_map.json
  → step3c_kg --ortholog-map（并行 direct + ortholog）
```

未选 `blastp` 时，下载 / FASTA / `blastp` 二进制全部不出现。

3a 仍只回答：本物种 KG 覆盖是否足够。`single_species` → 跳过 3b。`mixed` / `cross_species_only` → 3b，参考物种由 LLM 从静态表点名。

## 6. 故障改道与「是否理想」

分两层，不要混成一套阈值。

**A. 工具故障（代码自动改道）**

| 情况 | 行为 |
|---|---|
| 未选 `blastp` | 不下载、不找二进制、不读 FASTA |
| `blastp` 但 PATH 无 `blastp` / 无可用 FASTA / 下载或 sha256 失败 / `blastp` 进程非 0 或库打不开 | **同一组 ref 内部改走 `ensembl_compara`**，对外 `status=ok` + warnings（含改道原因）。不把 `error` 丢给 LLM 去「想起改道」。 |
| Ensembl 也失败（DNS / 全 4xx 等） | 空 map + warnings，3c 不带同源或只走直接路径（与现网 Ensembl 不可达相同） |
| 某 ref 不在 27 库 | 该 ref 空 + warning，其它 ref 继续 |
| FASTA ∩ marker ∩ var_names 为空 | 视为 BLAST 不可用，改道 Ensembl |

scripted driver 看到的是 `ok`，不会因缺 BLAST 库而整臂 `ScriptedRunError`。

**B. 注释是否理想（只由 LLM 判断）**

- 不设 hit_rate、空候选比例、unknown 占比等备用阈值。
- 映射率、`n_direct_hits` / `n_ortholog_hits`、organ 过滤、ID 对不上、warnings 只作为指标进入 JSON。
- LLM 用现有决策点（`cross_species_routing` / `kg_match` / 必要时 `global_quality`）决定：换更近的参考物种、改 provider、停下来面向用户说明「借不到可用知识」。穷尽后的「向上反映」是模型选择终止并说明，不是脚本掐断。

「簇候选为空」不是实测常见结局（B1 拟南芥高覆盖簇均有候选；该条件也不是「图谱 marker 库」假设下的合理终止线），**不作废即不采用为闸门**。

## 7. 文档与决策点

- `SKILL.md` §3.7 / `references/sop.md` SOP-3：删「跟 3a 推荐列表」；改为读覆盖档 + 静态名录，LLM 点名 ≤3 个 ref；何时选 `blastp`（有 query FASTA、Ensembl 不可达或用户要求本地比对）；库按需下载；一对多每物种 best-1。
- 不新增决策点枚举。`routing_accept` 含义改为「接受覆盖策略（是否做同源），参考物种由本判断的 action 写出」。`routing_multi_reference` 上限 3。
- `references/reference-species.md`：§4.5 表 + 亲缘选用说明（近缘优先、动植物不要混库）。
- `assets/tools.md`：随 `--dump-schema` 补 `blastp` / `--query-fasta`。

## 8. 测试

CI **不**访问 xener、**不**跑真 `blastp`、**不**下真 zip。

- 假 `blastp` 可执行文件吐固定 tabular → `MappingRecord`（pident、剥前缀、每物种 top-1）。
- 假下载器：只在 `blastp` 且缺库时 GET；校验失败拒绝安装。
- FASTA 过滤：相对假 `var_snapshot.csv` + `markers.json`。
- 物种名对照 27 前缀；缓存 key 含 FASTA 摘要；CLI 走 `lookup_many`。
- 故障改道：无 `blastp` 二进制时不返回 `error`，warnings 含改道 Ensembl（Ensembl 侧用 mock）。
- 现有 Ensembl / step3b 测试全绿；默认 provider 仍是 `ensembl_compara`。
- 真 zip + 真 `blastp`：`skip`，本机有库时可选跑。

## 9. 实现时改动的文件（清单）

- 新增：`skills/cell-annotation/scripts/step3b_xmap_providers/blastp.py`（含或拆 `ensure_blastdb`）
- 改：`base.py`（`lookup_many`）、`__init__.py` 文档、`step3b_cross_species_map.py`（批量、best-1、FASTA 过滤、缓存 key、最多 3 ref）
- 改：`step3a_kg_precheck.py`（去掉推荐排序；覆盖档与 strategy 保留）
- 改：Ensembl 合并逻辑与 BLAST 共用每物种 best-1
- 文档：`SKILL.md`、`references/sop.md`、`references/kg-schema.md`（可链到新表）、新 `references/reference-species.md`、`.env.example`
- 测试：`harness/tests/` 新增 blastp / ensure_db / FASTA 过滤；扩展现有 step3b 测试

## 10. 开放（实现期可定，不挡开工）

- zip 的 sha256 在实现时对当前 URL 计算后 pin。库更新则改 URL + 校验和 + 静态表。
- `--reference-species` 超过 3 个：推荐截断 + warning（§4.6）。
- BLAST 库若混入无 `marker_of` 的蛋白，best hit 可能在 3c 无边；这是数据质量问题，由 LLM 读 `genes_with_no_kg_entry` 判断，不在 3b 用阈值丢掉。
