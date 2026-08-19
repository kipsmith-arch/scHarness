"""Step 3 — knowledge-graph query (tool_design.md §5.3, atomic_operations.md stage 3).

Subcommands:
    query           — connect -> query_genes -> query_hierarchy -> aggregate_candidates
                      -> write_hits. [0× h5ad — pure JSON + Neo4j]
    test-connection — connect only, report provenance.

Credentials: CLI flags > NEO4J_URI/USER/PASSWORD env > defaults (password never
hardcoded). Gene names are mapped to symbols via the JSON file pointed to by
``--gene-key`` (default: ``name_map4Arabidopsis_thaliana_symbol.json`` at
project root); set to ``"none"`` to skip mapping.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  -- also triggers load_skill_dotenv()

# organ_status 分类(语义)
#   root     — 候选 organ 字段包含目标 target organ(由 --organ 透传,代码中无 organ 字面量)
#   partial  — 同时含 target 与非 target organ
#   unknown  — 无 organ 标注或仅 Unknown
#   mismatch — 仅非 target organ
# _priority 把这些 category 映射成排序优先级(root/含 target 排最前),与 target 无关。
ORGAN_STATUS_PRIORITY = {"root": 0, "partial": 1, "unknown": 2, "mismatch": 3}


def _organ_status(organs, target):
    """Classify candidate by organ consistency with the target organ.

    ``target`` is the dataset's target organ name, passed through from ``--organ``
    on the step3_kg query subcommand. **No default value** — callers must pass it
    explicitly so no organ name is hardcoded in this function. KG Organ strings
    are normalised via ``target.strip().title()`` to match the title-case
    convention used in the KG (e.g. ``"root" → "Root"``).

    Returned categories are organ-agnostic:
      - ``"root"``: candidate organs contain the target organ (category name is
        historical; the semantic is "matches target organ")
      - ``"partial"``: candidate organs include both target and non-target
      - ``"unknown"``: no organ label or only "Unknown"
      - ``"mismatch"``: candidate organs are all non-target

    Caveats:
      - ``target`` must be a non-empty string; non-string/empty raises ValueError
        (callers are responsible for fail-fast at the CLI layer; this function
        defends against accidental None propagation).
      - ``target`` is normalised via ``strip().title()``; KG organ strings use
        pipe-or-space-separated titles ("Stem|Root|Leaf"), so single-word
        targets work, but multi-word or hyphenated targets may need explicit
        casing. Round-2 scope accepts this limitation.
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"_organ_status: target must be a non-empty string, got {target!r}")
    target_norm = target.strip().title()
    orgs = {str(o).strip() for o in organs if o is not None and str(o).strip()}
    if not orgs:
        return "unknown"
    known = {o for o in orgs if o != "Unknown"}
    if not known:
        return "unknown"
    has_target = any(target_norm in o for o in known)
    has_other = any(target_norm not in o for o in known)
    if has_target and has_other:
        return "partial"
    if has_target:
        return "root"
    return "mismatch"


def _priority(status):
    """Ordinal priority of an organ_status category for candidate ranking.

    Organ-agnostic: depends only on the category name returned by
    ``_organ_status``. ``root`` (matching target) ranks highest; ``mismatch``
    (non-target only) ranks lowest.
    """
    return ORGAN_STATUS_PRIORITY.get(status, ORGAN_STATUS_PRIORITY["mismatch"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="step3_kg.py",
        description="Step 3 知识图谱查询:基因→细胞类型映射、本体层级、候选聚合(0 次 h5ad 加载)",
    )
    parser.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="subcommand")

    p_q = sub.add_parser("query", help="查询 KG 并聚合候选")
    # A 类(任务/生物决策):LLM 可见
    p_q.add_argument("--organ", default=None, help="组织过滤(对应 o.Organ),必填;不提供则 fail")
    p_q.add_argument("--species", default=None, help="物种(信息性,对应 g.Species)")
    p_q.add_argument("--species-type", default="Plant", help="物种类型过滤(对应 g.Species_type,默认 Plant)")
    p_q.add_argument("--strict-organ", action="store_true", help="严格按 organ 过滤命中")
    # B 类(环境/资源):SUPPRESS 隐藏,LLM 不可见,CLI/运维可临时 override
    p_q.add_argument("--min-confidence", type=float, default=argparse.SUPPRESS,
                     help=argparse.SUPPRESS)
    p_q.add_argument("--gene-key", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_q.add_argument("--max-ancestor-hops", type=int, default=argparse.SUPPRESS,
                     help=argparse.SUPPRESS)
    # step3_kg: --project-dir / --input 都属 B(部署/路径),LLM 不可见。
    # 不调用 add_common_args,直接以 SUPPRESS 声明。
    p_q.add_argument("--project-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_q.add_argument("--input", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_neo4j_args(p_q)

    p_t = sub.add_parser("test-connection", help="测试 Neo4j 连接与来源信息")
    common.add_neo4j_args(p_t)
    p_t.add_argument("--project-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return parser


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------

def _driver(cfg: dict):
    from neo4j import GraphDatabase

    if not cfg.get("password"):
        raise ValueError("缺少 NEO4J_PASSWORD(环境变量或 --password),不硬编码密码")
    return GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))


def kg_provenance(driver) -> dict:
    """Sample the KG: node label counts, species, species types, marker resources."""
    prov = {}
    try:
        with driver.session() as s:
            res = s.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC LIMIT 10")
            prov["node_labels"] = {r["label"]: r["n"] for r in res}
            res = s.run("MATCH (g:Gene) RETURN DISTINCT g.Species AS x LIMIT 20")
            prov["species"] = [r["x"] for r in res if r["x"] is not None]
            res = s.run("MATCH (g:Gene) RETURN DISTINCT g.Species_type AS x LIMIT 10")
            prov["species_types"] = [r["x"] for r in res if r["x"] is not None]
            res = s.run("MATCH (g:Gene) RETURN DISTINCT g.Marker_resource AS x LIMIT 50")
            prov["marker_resources"] = [r["x"] for r in res if r["x"] is not None]
            res = s.run("MATCH (g:Gene) RETURN DISTINCT g.Dataset AS x LIMIT 20")
            prov["datasets"] = [r["x"] for r in res if r["x"] is not None]
            res = s.run("MATCH (:Gene)-[r:marker_of]->(:Ontology) RETURN count(r) AS n")
            prov["n_marker_of_edges"] = int(res.single()["n"])
            res = s.run("MATCH (o:Ontology) RETURN DISTINCT o.Organ AS organ, count(*) AS n ORDER BY n DESC LIMIT 15")
            prov["organ_vocabulary"] = {str(r["organ"]): r["n"] for r in res}
    except Exception as exc:
        prov["error"] = str(exc)
    return prov


def _load_gene_map(path: str) -> dict:
    """Load a gene {raw_name: query_name} mapping from a JSON file at ``path``.

    ``path`` semantics:
        - "none" / "" / None : no mapping (identity transform; raw names queried)
        - any other string    : filesystem path to a JSON dict; the dict must
                                map raw gene IDs to query-side names
                                (typically TAIR locus → symbol).

    File-not-found fails loudly — silently falling back to no mapping would
    hide a real misconfiguration from the operator.
    """
    if path in (None, "", "none"):
        return {}
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"基因名映射文件不存在:{path}（设 'none' 跳过映射）"
        )
    mapping = json.load(open(path, encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise TypeError(f"基因名映射文件必须是 dict(当前 {type(mapping).__name__})")
    return mapping


# ---------------------------------------------------------------------------
# ops
# ---------------------------------------------------------------------------

def op_connect(cfg, log_path, params) -> dict:
    driver = _driver(cfg)
    prov = kg_provenance(driver)
    m = {"kg_provenance": prov,
         "kg_source": "neo4j",
         "kg_version": None,
         "kg_date": common.now_iso()[:10]}
    # kg_version: no KG-native version field exists; record the Neo4j server
    # version as a provenance proxy so metadata_check can be satisfied honestly
    try:
        with driver.session() as s:
            res = s.run("CALL dbms.components() YIELD name, versions RETURN name, versions LIMIT 5")
            comps = {r["name"]: r["versions"] for r in res}
            if comps:
                m["kg_version"] = str(comps.get("Neo4j Kernel", list(comps.values())[0]))
                m["kg_version_source"] = "neo4j-server"
    except Exception:
        pass
    common.exec_record(log_path, "step3_kg", "connect", params, m)
    return driver, m


def op_query_genes(driver, genes, config, log_path, params) -> dict:
    gene_map = _load_gene_map(config["gene_key"])
    query_names = [gene_map.get(g, g) for g in genes]  # mapped symbol or raw
    # also query raw names to catch KG entries stored under locus ids
    query_names = list(dict.fromkeys([g for pair in zip(query_names, genes) for g in pair]))

    where = ["g.Name IN $names"]
    if config["species_type"]:
        where.append("g.Species_type = $species_type")
    if config["species"]:
        where.append("g.Species = $species")
    if config["min_confidence"] > 0:
        where.append("r.relation_confidence >= $min_conf")
    cypher = (
        "MATCH (g:Gene)-[r:marker_of]->(o:Ontology) "
        f"WHERE {' AND '.join(where)} "
        "RETURN g.Name AS gene, o.Name AS cell_type, o.Organ AS organ, o.id AS ontology_id, "
        "o.Species_type AS species_type, o.Type AS ontology_type, "
        "r.relation_confidence AS confidence, r.info_source AS source"
    )
    gene_to_cts = {g: [] for g in genes}
    hit_rows = []
    with driver.session() as s:
        for i in range(0, len(query_names), 500):
            batch = query_names[i:i + 500]
            res = s.run(cypher, names=batch, species_type=config["species_type"],
                        species=config["species"], min_conf=config["min_confidence"])
            for r in res:
                hit_rows.append(dict(r))

    for r in hit_rows:
        conf = r["confidence"]
        if conf is None:
            conf = 1.0  # missing confidence treated as unconstrained
        if config["min_confidence"] > 0 and conf < config["min_confidence"]:
            continue
        if config["strict_organ"] and (r["organ"] or "").lower() != str(config["organ"]).lower():
            continue
        # attach to EVERY raw gene whose query name matched (duplicated symbols
        # must not lose hits — no break)
        for g in genes:
            mapped = gene_map.get(g, g)
            if mapped == r["gene"] or g == r["gene"]:
                gene_to_cts.setdefault(g, []).append({
                    "cell_type": r["cell_type"], "organ": r["organ"],
                    "ontology_id": r["ontology_id"], "species_type": r["species_type"],
                    "ontology_type": r["ontology_type"], "confidence": float(conf),
                    "source": r["source"],
                })

    with_hits = [g for g in genes if gene_to_cts.get(g)]
    mult = [len(gene_to_cts[g]) for g in with_hits]
    m = {
        "overall_hit_rate": float(len(with_hits) / max(len(genes), 1)),
        "n_unique_genes_queried": len(genes),
        "n_genes_with_hits": len(with_hits),
        "n_genes_without_hits": len(genes) - len(with_hits),
        "genes_with_no_kg_entry": [g for g in genes if not gene_to_cts.get(g)][:50],
        "mapping_multiplicity_per_gene": common.describe_distribution(mult) if mult else None,
        "mean_candidates_per_gene": float(np.mean(mult)) if mult else None,
        "strict_organ": config["strict_organ"],
        "organ_filter": config["organ"],
    }
    common.exec_record(log_path, "step3_kg", "query_genes", params, m)
    return gene_to_cts, m


def op_query_hierarchy(driver, cell_types, max_hops, log_path, params) -> dict:
    ancestors = {}
    query_errors = {}
    # hop count must be a literal in Cypher variable-length patterns;
    # 0 hops means "no hierarchy query" (design: 0~3 跳)
    hops = max(int(max_hops), 1)
    if int(max_hops) <= 0:
        m = {"n_cell_types_queried": len(cell_types),
             "n_cell_types_with_ancestors": 0,
             "skipped": True}
        common.exec_record(log_path, "step3_kg", "query_hierarchy", params, m)
        return ancestors, m
    with driver.session() as s:
        for ct in cell_types:
            cypher = (
                "MATCH (o:Ontology {Name: $name})-[:ontology_relation*1..%d]->(a:Ontology) "
                "RETURN DISTINCT a.Name AS ancestor" % hops
            )
            try:
                res = s.run(cypher, name=ct)
                ancestors[ct] = [r["ancestor"] for r in res if r["ancestor"] is not None]
            except Exception as exc:
                query_errors[ct] = str(exc)
    m = {"n_cell_types_queried": len(cell_types),
         "n_cell_types_with_ancestors": int(sum(1 for v in ancestors.values() if v)),
         "query_errors": query_errors}
    common.exec_record(log_path, "step3_kg", "query_hierarchy", params, m)
    return ancestors, m


def _rank_candidates(per_cluster_genes, gene_to_cts, target):
    """Aggregate gene->cell_type hits into ranked candidates per cluster.

    ``target`` is the dataset's target organ, passed through from ``--organ``.
    Used by ``_organ_status`` to classify each candidate's organs; the result
    drives candidate ranking via ``_priority`` (organ-matching candidates first,
    same-category ties broken by marker_count then confidence). Organ-agnostic
    ranking — no organ name appears in the sort key.

    Within an organ_status category the ranking preserves the original order
    (marker_count descending, mean_confidence descending, cell_type ascending).
    """
    per_cluster = {}
    for c, genes in per_cluster_genes.items():
        agg = {}
        for g in genes:
            for hit in gene_to_cts.get(g, []):
                key = hit["cell_type"]
                entry = agg.setdefault(key, {
                    "cell_type": key, "supporting_markers": [], "marker_count": 0,
                    "confidences": [], "sources": set(), "organs": set(),
                })
                entry["supporting_markers"].append(g)
                entry["confidences"].append(hit["confidence"])
                entry["sources"].add(hit["source"] or "?")
                if hit.get("organ"):
                    entry["organs"].add(str(hit["organ"]))
        candidates = []
        for key, e in agg.items():
            uniq_markers = list(dict.fromkeys(e["supporting_markers"]))
            organs = sorted(e["organs"])
            candidates.append({
                "cell_type": key,
                "supporting_markers": uniq_markers,
                "marker_count": len(uniq_markers),
                "mean_confidence": float(np.mean(e["confidences"])) if e["confidences"] else None,
                "min_confidence": float(np.min(e["confidences"])) if e["confidences"] else None,
                "sources": sorted(e["sources"]),
                "organ": organs,
                "organ_status": _organ_status(organs, target),
            })
        # Rank: organ_status priority first (organ-matching > non-matching);
        # same-category ties keep original marker_count → confidence → cell_type.
        candidates.sort(key=lambda x: (_priority(x["organ_status"]),
                                       -x["marker_count"],
                                       -(x["mean_confidence"] or 0.0), x["cell_type"]))
        n_queried = len(genes)
        per_cluster[c] = {
            "n_markers": n_queried,
            "n_markers_hit": int(sum(1 for g in genes if gene_to_cts.get(g))),
            "candidates": candidates,
        }
    return per_cluster


def op_aggregate_candidates(markers_json, gene_to_cts, log_path, params, target) -> dict:
    per_cluster_genes = {c: v["marker_genes"] for c, v in markers_json["per_cluster"].items()}
    per_cluster = _rank_candidates(per_cluster_genes, gene_to_cts, target)
    candidate_stats = {}
    counts = []
    entropies = {}
    tied = {}
    confs = []
    all_types = set()
    for c, info in per_cluster.items():
        cands = info["candidates"]
        counts.append(len(cands))
        mc = [x["marker_count"] for x in cands]
        entropies[c] = common.shannon_entropy(mc) if mc else 0.0
        tied[c] = int(sum(1 for x in cands if x["marker_count"] == mc[0])) if mc else 0
        for x in cands:
            all_types.add(x["cell_type"])
            if x["mean_confidence"] is not None:
                confs.append(x["mean_confidence"])
    candidate_stats = {
        "n_candidates_per_cluster": counts,
        "n_candidates_distribution": common.describe_distribution(counts) if counts else None,
        "candidate_ranking_entropy": entropies,
        "n_tied_at_top": tied,
        "confidence_distribution_across_candidates": common.describe_distribution(confs) if confs else None,
        "n_unique_cell_types_across_clusters": len(all_types),
    }
    m = {"candidate_stats": candidate_stats,
         "n_clusters_with_candidates": int(sum(1 for c in per_cluster if per_cluster[c]["candidates"]))}
    common.exec_record(log_path, "step3_kg", "aggregate_candidates", params, m)
    return per_cluster, candidate_stats, m


def op_write_hits(out_dir, log_path, params, payload) -> dict:
    json_path = os.path.join(out_dir, "kg_hits.json")
    txt_path = os.path.join(out_dir, "kg_source.txt")
    common.write_json(json_path, payload)
    src = payload.get("kg_source", "") + "\n"
    prov = payload.get("kg_provenance", {})
    src += f"version: {payload.get('kg_version', '')}\n"
    src += f"date: {payload.get('kg_date', '')}\n"
    src += f"datasets: {prov.get('datasets', [])}\n"
    src += f"species: {prov.get('species', [])}\n"
    src += f"marker_resources: {prov.get('marker_resources', [])}\n"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(src)
    m = {"kg_hits_json": json_path, "kg_source_txt": txt_path}
    rid = common.exec_record(log_path, "step3_kg", "write_hits", params, m)
    m["run_id"] = rid
    return m


def cmd_query(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    out_dir = common.step_dir(project_dir, "step3_kg")
    log = common.run_log_path(project_dir)
    step2_dir = common.step_dir(project_dir, "step2_markers")
    markers = common.read_json(os.path.join(step2_dir, "markers.json"))
    if not markers:
        return common.fail("缺少 step2_markers/markers.json,请先运行 step2_markers run")
    # target organ 必填(spec 设计:代码中不硬编码任何 organ 名称;缺 --organ 则 fail-fast)
    if not args.organ or not str(args.organ).strip():
        return common.fail("--organ 必填:目标 organ 名称(如 root / brain / leaf);默认不提供任何 organ")

    # B 类参数:CLI 未传时从 SUPPRESS 落到代码默认(env 不接管,运维仅靠 CLI 临时改)
    min_confidence = common.env_or_default(args, "min_confidence", (), 0.0, cast=float)
    gene_key = common.env_or_default(args, "gene_key", (),
                                     "name_map4Arabidopsis_thaliana_symbol.json")
    max_ancestor_hops = common.env_or_default(args, "max_ancestor_hops", (), 3, cast=int)
    species_type = common.env_or_default(args, "species_type", (), "Plant")

    cfg = common.neo4j_config(args)
    p = {"organ": args.organ, "species": args.species, "species_type": species_type,
         "min_confidence": min_confidence, "strict_organ": args.strict_organ,
         "gene_key": gene_key, "max_ancestor_hops": max_ancestor_hops}

    driver, conn = op_connect(cfg, log, p)
    try:
        genes = sorted({g for v in markers["per_cluster"].values() for g in v["marker_genes"]})
        config = {"organ": args.organ, "species": args.species,
                  "species_type": species_type, "min_confidence": min_confidence,
                  "strict_organ": args.strict_organ, "gene_key": gene_key}
        gene_to_cts, qstats = op_query_genes(driver, genes, config, log, p)
        cell_types = sorted({h["cell_type"] for hits in gene_to_cts.values() for h in hits})
        ancestors, hstats = op_query_hierarchy(driver, cell_types, max_ancestor_hops, log, p)
        per_cluster, candidate_stats, astats = op_aggregate_candidates(markers, gene_to_cts, log, p, args.organ)
        payload = {
            "kg_source": "neo4j",
            "kg_version": conn.get("kg_version"),
            "kg_date": conn.get("kg_date"),
            "kg_provenance": conn.get("kg_provenance", {}),
            "query_config": config,
            "gene_to_cts": gene_to_cts,
            "ancestors": ancestors,
            "query_stats": qstats,
            "candidate_stats": candidate_stats,
            "per_cluster": per_cluster,
        }
        m = op_write_hits(out_dir, log, p, payload)
    finally:
        try:
            driver.close()
        except Exception:
            pass
    return common.ok({"kg_hits_json": m["kg_hits_json"], "kg_source_txt": m["kg_source_txt"],
                      "n_genes_queried": qstats["n_unique_genes_queried"],
                      "overall_hit_rate": qstats["overall_hit_rate"],
                      "last_exec_run_id": m["run_id"]})


def cmd_test_connection(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    log = common.run_log_path(project_dir)
    cfg = common.neo4j_config(args)
    p = {}
    try:
        driver, conn = op_connect(cfg, log, p)
        driver.close()
        return common.ok({"connected": True, "kg_provenance": conn.get("kg_provenance", {})})
    except Exception as exc:
        return common.fail(f"无法连接 Neo4j: {exc}")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(parser)
    if not args.subcommand:
        parser.print_help()
        return 2
    try:
        if args.subcommand == "query":
            result = cmd_query(args)
        elif args.subcommand == "test-connection":
            result = cmd_test_connection(args)
        else:
            result = common.fail(f"未知子命令:{args.subcommand}")
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"{args.subcommand} failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
