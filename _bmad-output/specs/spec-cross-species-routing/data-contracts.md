# Data Contracts — 新工具 I/O schema

> SPEC cross-species-routing 的数据契约伴侣。定义 `step3_kg_precheck` / `step2_ortholog` / `step3_kg --ortholog-map` 的输入输出 JSON schema;供 loader、tests、evals、validate_log 引用。

---

## 1. step3_kg_precheck — coverage_report.json

### 1.1 输入参数(工具层 argparse)

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `--target-species` | string | 是 | — | Ensembl / KG 物种名格式(小写下划线,如 `arabidopsis_thaliana`) |
| `--organ` | string | 是 | — | 目标 organ,与 step3_kg 一致 |
| `--species-type` | string | 否 | `Plant` | `Plant` / `Animal` / `Fungi` / `Protists`,与 step3_kg 一致 |
| `--project-dir` | string | 否 | `output` | 与其它 step 一致 |
| `--high-threshold` | int | 否 | 500 | coverage_tier=high 的下限(genes_with_ct ≥ 此值) |
| `--low-threshold` | int | 否 | 50 | coverage_tier=low 的上限(genes_with_ct < 此值) |

### 1.2 输出 JSON

```json
{
  "status": "ok",
  "data": {
    "target_species": "arabidopsis_thaliana",
    "target_species_type": "Plant",
    "target_organ": "root",
    "computed_at": "2026-08-24T15:00:00",
    "kg_query_stats": {
      "n_target_genes_in_kg": 24371,
      "n_target_genes_with_ct": 21299,
      "n_target_unique_ct": 195,
      "n_target_marker_edges": 141497
    },
    "coverage_tier": "high",
    "recommended_strategy": "single_species",
    "recommended_reference_species": [
      {
        "species": "oryza_sativa",
        "species_type": "Plant",
        "kg_genes": 25397,
        "kg_unique_ct": 187,
        "n_ortholog_edges_kg": 0,
        "ensembl_available": true,
        "score": 0.92,
        "score_breakdown": {
          "ct_coverage_norm": 0.96,
          "phylogenetic_distance_norm": 0.85,
          "ensembl_divisible": 1.0
        }
      },
      ...
    ],
    "strategy_rationale": "目标物种 arabidopsis_thaliana 在 KG 中 21299 genes 有 cell type 标注(高覆盖),直接路径足以支持注释。推荐 oryza_sativa 作为辅助参考(同 Plant + 高覆盖 + 近缘)。",
    "warnings": []
  }
}
```

### 1.3 字段语义

| 字段 | 语义 |
|---|---|
| `coverage_tier` | `high` (≥ high_threshold) / `medium` (low_threshold ≤ x < high_threshold) / `low` (< low_threshold) |
| `recommended_strategy` | `single_species` (high) / `mixed` (medium) / `cross_species_only` (low) |
| `score` | 0~1 越大越适合做 reference;`ct_coverage_norm * 0.5 + phylogenetic_distance_norm * 0.4 + ensembl_divisible * 0.1` |
| `n_ortholog_edges_kg` | KG 中已有"目标物种↔参考物种"的 ortholog 边数(若有;目前为 0,后续可灌库) |

### 1.4 边缘 case

- KG 不可达: `{"status": "error", "error": "无法连接 Neo4j: ..."}`,LLM 走"无 precheck"默认单物种路径 + warn
- 目标物种在 KG 中完全不存在: `coverage_tier=low`, `recommended_strategy=cross_species_only`, `recommended_reference_species` 默认填 KG 收录最多的 Plant/Animal 物种
- 推荐列表为空(无同 species_type 物种): `recommended_reference_species: []`,strategy 强制 `cross_species_only` + warn 让 LLM 显式选 ref

---

## 2. step2_ortholog — ortholog_map.json

### 2.1 输入参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `--target-species` | string | 是 | — | 小写下划线格式 |
| `--reference-species` | string[] | 是(可重复) | — | 至少 1 个;同样小写下划线;可重复传多个 |
| `--species-type` | string | 否 | `Plant` | 用于路由端点 |
| `--input` | path | 是 | — | `step2_markers/markers.json` 路径 |
| `--project-dir` | string | 否 | `output` | |
| `--min-identity` | float | 否 | 30.0 | 过滤 identity < 此值的 ortholog 对 |
| `--max-hits-per-gene` | int | 否 | 3 | 限制一对多(取 perc_id 最高的 N 个) |
| `--force-refresh` | flag | 否 | False | 忽略 cache 重打 Ensembl |
| `--ensembl-rest-host` | string | 否 | 自动路由 | 可手动 override |
| `--ensembl-timeout` | int | 否 | 10 | 单个请求超时(秒) |
| `--ensembl-max-retries` | int | 否 | 4 | |

### 2.2 输出 JSON

```json
{
  "status": "ok",
  "data": {
    "target_species": "arabidopsis_thaliana",
    "reference_species": ["oryza_sativa"],
    "species_type": "Plant",
    "ensembl_host_used": "https://rest.plants.ensembl.org",
    "computed_at": "2026-08-24T15:30:00",
    "cache_hit": false,
    "ortholog_map": {
      "AT1G31340": [
        {
          "ref_species": "oryza_sativa",
          "ref_gene_id": "Os06g0650100",
          "identity": 99.34,
          "coverage": 100.0,
          "ortholog_type": "ortholog_one2one",
          "source": "ensembl_compara"
        }
      ],
      "AT2G39730": [
        {
          "ref_species": "oryza_sativa",
          "ref_gene_id": "Os02g0168800",
          "identity": 92.5,
          "coverage": 98.0,
          "ortholog_type": "ortholog_one2one",
          "source": "ensembl_compara"
        }
      ]
    },
    "unmapped_genes": ["AT5G12345"],
    "summary": {
      "n_input_genes": 234,
      "n_mapped": 198,
      "n_unmapped": 36,
      "hit_rate": 0.846,
      "identity_distribution": {
        "min": 35.2,
        "median": 78.4,
        "max": 99.9,
        "p10": 42.1,
        "p90": 95.3
      },
      "type_distribution": {
        "ortholog_one2one": 180,
        "ortholog_one2many": 18
      },
      "n_ref_genes_per_target_mean": 1.1,
      "ensembl_request_count": 12,
      "ensembl_elapsed_seconds": 8.4
    },
    "warnings": []
  }
}
```

### 2.3 缓存文件

```
<project-dir>/step2_ortholog/cache/
  ortholog_map__{target_species}__{ref_species_comma_joined}__{md5(markers.json)[0:8]}.json
```

`ref_species_comma_joined` 排序后拼接(如 `oryza_sativa,zea_mays`)。`md5(markers.json)` 防 marker 集合变化导致旧映射误导。

`--force-refresh` 时跳过 cache 读,跑完仍写 cache。

### 2.4 边缘 case

- Ensembl 完全不可达: `cache_hit=false, ortholog_map={}, summary={hit_rate: 0, ensembl_request_count: 0, ensembl_elapsed_seconds: 0}, warnings: ["Ensembl REST 不可达: <error>"]`。**不视为 error**,LLM 看 warning 决定是否走单物种路径。
- Ensembl 返回空(目标物种无 Compara 收录): `ortholog_map={}, unmapped_genes=<全部>, warnings: ["<target_species> 不在 Ensembl Compara"]`
- 单个 marker Ensembl 报错: 记入 `unmapped_genes`,不阻断
- 一对多超过 `--max-hits-per-gene`: 按 `identity` 降序截断,记 `n_truncated_by_max_hits` 到 summary

---

## 3. step3_kg — kg_hits.json 扩展

### 3.1 新增 CLI 选项

```
--ortholog-map PATH    path to step2_ortholog/ortholog_map.json (optional)
```

### 3.2 kg_hits.json 结构改动

**`gene_to_cts`**(从 `Dict[gene → List[hit]]` 改为):

```json
{
  "AT1G31340": [
    {
      "cell_type": "Lateral Root Cap",
      "organ": "Root",
      "ontology_id": "CL:0000043",
      "species_type": "Plant",
      "ontology_type": "cell_type",
      "confidence": 0.95,
      "source": "PanglaoDB",
      "source_path": "direct",
      "ortholog_ref_gene": null,
      "ortholog_ref_species": null
    }
  ],
  "AT2G39730": [
    {
      "cell_type": "root cap cell",
      "organ": "Root",
      "ontology_id": "PO:0000058",
      "species_type": "Plant",
      "ontology_type": "cell_type",
      "confidence": 0.85,
      "source": "EnsemblPlants",
      "source_path": "ortholog",
      "ortholog_ref_gene": "Os02g0168800",
      "ortholog_ref_species": "oryza_sativa"
    }
  ]
}
```

**`per_cluster`**(新增字段):

```json
{
  "0": {
    "n_markers": 28,
    "n_markers_hit": 22,
    "n_markers_direct_hit": 18,
    "n_markers_ortholog_hit": 14,
    "n_markers_mixed_hit": 10,
    "candidates": [
      {
        "cell_type": "Lateral Root Cap",
        "supporting_markers": [...],
        "marker_count": 18,
        "source_species_set": ["arabidopsis_thaliana", "oryza_sativa"],
        "source_path_set": ["direct", "ortholog"],
        "mean_confidence": 0.93,
        ...
      }
    ]
  }
}
```

**`query_config`** 增字段:

```json
{
  "ortholog_map_path": "step2_ortholog/ortholog_map.json",
  "ortholog_map_ref_species": ["oryza_sativa"],
  "ensembl_used": true
}
```

**`query_stats`** 增字段:

```json
{
  "n_direct_hits": 412,
  "n_ortholog_hits": 198,
  "n_mixed_hits": 87,
  "n_genes_with_only_ortholog": 23,
  "ortholog_unavailable": false,
  "ortholog_warnings": []
}
```

### 3.3 兼容性

未传 `--ortholog-map` 时,行为与现状 100% 一致;`source_path` 全部为 `direct`,新增字段缺失(不写)。B1 评估可直接比对 strict/macroF1。

---

## 4. cross_species_routing 决策点 — trajectory_schema.py 扩展

### 4.1 REQUIRED_SCOPE 注册

```python
REQUIRED_SCOPE = {
    ...  # 现有 13 个决策点
    "cross_species_routing": "session",
}
```

### 4.2 decision 枚举

```
routing_accept
routing_force_single
routing_force_cross
routing_multi_reference
```

### 4.3 judgment 必填字段(沿用既有 schema)

| 字段 | 跨物种 routing 取值 |
|---|---|
| `decision_point` | `"cross_species_routing"` |
| `scope` | `{"type": "session"}` |
| `run_ref` | `step3_kg_precheck.precheck#1`(取自 precheck 工具 stdout run_id) |
| `inputs[]` | 必含 `{path: "step3_kg_precheck.coverage_report.recommended_strategy", value: "..."}` 与 `{path: "step3_kg_precheck.coverage_report.recommended_reference_species", value: [...]}` |
| `output.decision` | 枚举之一 |
| `output.action` | 如 `run_step2_ortholog --target-species=X --reference-species=Y,Z` 或 `run_step3_kg --species-type=Plant --organ=root` |
| `reasoning` | 自然语言解释为什么选这条路径 |

`routing_multi_reference` 必须在 `output.action` 显式列出 reference species 列表(可覆盖 precheck 推荐)。

---

## 5. 缓存清理

`step2_ortholog --force-refresh` 或 marker 集合变化(md5 不同)自动重打。手动清理:

```bash
rm -rf <project-dir>/step2_ortholog/cache/
```

LLM 不需要关心;但 `write_judgment.py` 与 `step7_diagnose.metadata_check` 应记录 `ortholog_map.json` 的 md5 与 mtime 便于审计。
