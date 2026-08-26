# KG Schema Extension — 物种命名与 Ensembl 端点路由

> SPEC cross-species-routing 的 KG schema 扩展伴侣。直接合并到 `skills/cell-annotation/references/kg-schema.md`,作为现有"## 物种过滤与命名"小节的补充。
> LLM 与 LLM 调用 step2_ortholog / step3_kg 时共享该映射表。

---

## 物种命名与 Ensembl 端点路由(扩展)

### 1. Neo4j 物种命名现状(已实测)

| species_type | 命名格式 | 示例 | 数量 |
|---|---|---|---|
| Plant | 小写 + 下划线 | `arabidopsis_thaliana` / `oryza_sativa` / `zea_mays` | 18 |
| Animal | 首字母大写 + 空格(常见名) | `Human` / `Mus musculus` / `Danio rerio` | 11 |

### 2. Ensembl Compara REST 命名空间

| 物种类型 | Ensembl 端点 | 命名格式 |
|---|---|---|
| Plant | `https://rest.plants.ensembl.org` | 小写下划线(如 `arabidopsis_thaliana`) |
| Vertebrates | `https://rest.ensembl.org` | 小写下划线(如 `homo_sapiens`) |
| Fungi | `https://rest.fungi.ensembl.org` | 小写下划线 |
| Metazoa | `https://rest.metazoa.ensembl.org` | 小写下划线 |
| Protists | `https://rest.protists.ensembl.org` | 小写下划线 |
| Bacteria | `https://rest.bacteria.ensembl.org` | 小写下划线 |

`step2_ortholog` 按 `species_type` 自动路由,详见下方映射表。

### 3. KG ↔ Ensembl 物种名规范化表(部分,实测)

| Ensembl 返回(target.species) | KG 中 Species 字段值 | species_type |
|---|---|---|
| `homo_sapiens` | `Human` | Animal |
| `mus_musculus` | `Mus musculus` | Animal |
| `danio_rerio` | `Danio rerio` | Animal |
| `arabidopsis_thaliana` | `arabidopsis_thaliana` | Plant |
| `oryza_sativa` | `oryza_sativa` | Plant |
| `glycine_max` | `glycine_max` | Plant |

**实现位置**: `skills/cell-annotation/scripts/common.py` 新增 `SPECIES_NAME_ALIASES` dict + `normalize_species_name(name, species_type)` 函数。

```python
SPECIES_NAME_ALIASES = {
    ("homo_sapiens", "Animal"): "Human",
    ("mus_musculus", "Animal"): "Mus musculus",
    ("danio_rerio", "Animal"): "Danio rerio",
    ("monopterus_albus", "Animal"): "Monopterus albus",
    ("oreochromis_niloticus", "Animal"): "Oreochromis niloticus",
    ("gasterosteus_aculeatus", "Animal"): "Gasterosteus aculeatus",
    ("astyanax_mexicanus", "Animal"): "Astyanax mexicanus",
    ("nothobranchius_furzeri", "Animal"): "Nothobranchius furzeri",
    ("oncorhynchus_mykiss", "Animal"): "Oncorhynchus mykiss",
    ("mastacembelus_armatus", "Animal"): "Mastacembelus armatus",
    ("oryzias_latipes", "Animal"): "Oryzias latipes",
    # Plant 全用小写下划线,无需映射
}

def normalize_species_name(name: str, species_type: str) -> str:
    """Ensembl 返回的 target.species → KG 中的 Species 字段值。
    若映射表中无,默认返回原名(假设 KG 与 Ensembl 命名一致)。"""
    return SPECIES_NAME_ALIASES.get((name, species_type), name)
```

### 4. Ensembl 端点路由逻辑

```python
ENSEMBL_REST_HOSTS = {
    "Plant": "https://rest.plants.ensembl.org",
    "Animal": "https://rest.ensembl.org",
    "Fungi": "https://rest.fungi.ensembl.org",
    "Metazoa": "https://rest.metazoa.ensembl.org",
    "Protists": "https://rest.protists.ensembl.org",
    "Bacteria": "https://rest.bacteria.ensembl.org",
}

def ensembl_rest_host(species_type: str) -> str:
    return ENSEMBL_REST_HOSTS.get(species_type, "https://rest.ensembl.org")
```

`step2_ortholog` 自动调用此函数;LLM 不用关心。

### 5. Ensembl 物种收录验证

`step2_ortholog` 在跑具体 marker 前,先调 `GET {host}/info/species` 验证目标物种在该端点是否收录(避免对所有 marker 一个个 404)。若不收录,直接 warning + 不跑 ortholog 查询。

### 6. species_type 推断

`step3_kg_precheck` 接收 `--target-species` 时,先在 Neo4j 查:

```cypher
MATCH (g:Gene) WHERE g.Species = $name RETURN DISTINCT g.Species_type AS st LIMIT 1
```

若返回 None(物种不在 KG),回退 `--species-type` 参数(默认 `Plant`);若仍缺,报 `precheck 失败:未知物种 + 未指定 species_type`。

### 7. 与原 kg-schema.md 关系

- 原"## 物种过滤与命名"小节保留(强调默认不设 species 过滤与物种名格式规范)。
- 本节追加"## 物种命名与 Ensembl 端点路由(扩展)"作为补充。
- LLM 通过 `references/kg-schema.md` 完整读取。
