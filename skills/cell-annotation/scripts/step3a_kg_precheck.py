"""Step 3 precheck — KG coverage diagnostics for the target species (CAP-1).

Subcommands:
    run  — query KG for target-species cell type coverage, return recommended
           strategy (single_species / mixed / cross_species_only). 0 h5ad,
           pure Neo4j + JSON.

Per SPEC-blastp-homology: 3a is coverage precheck only. It does **not** rank
or recommend reference species — the LLM names ≤3 refs from the static catalog
in ``references/reference-species.md``.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  -- also triggers load_skill_dotenv()


# Coverage tier thresholds (genes with at least one marker_of edge in KG).
# These are start values; SPEC says they may be tuned per organ via evals.
DEFAULT_HIGH_THRESHOLD = 500
DEFAULT_LOW_THRESHOLD = 50


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="step3a_kg_precheck.py",
        description="Step 3 precheck — KG coverage for target species (0 h5ad)",
    )
    p.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="subcommand")

    p_r = sub.add_parser("run", help="query KG coverage for the target species")
    # A-class (LLM-visible): biological decisions + I/O paths
    p_r.add_argument("--target-species", required=True,
                     help="target species in Ensembl/KG format (lower_underscore, e.g. arabidopsis_thaliana)")
    p_r.add_argument("--organ", required=True,
                     help="target organ (passed through for downstream step3c_kg)")
    p_r.add_argument("--species-type", default="Plant",
                     help="species type for KG/Species_type filter (Plant / Animal / Fungi ...)")
    p_r.add_argument("--project-dir", default="output",
                     help="project directory (default: output)")
    p_r.add_argument("--high-threshold", type=int, default=DEFAULT_HIGH_THRESHOLD,
                     help=f"coverage_tier=high if genes_with_ct >= this (default {DEFAULT_HIGH_THRESHOLD})")
    p_r.add_argument("--low-threshold", type=int, default=DEFAULT_LOW_THRESHOLD,
                     help=f"coverage_tier=low if genes_with_ct < this (default {DEFAULT_LOW_THRESHOLD})")
    return p


def _driver(cfg: dict):
    from neo4j import GraphDatabase

    if not cfg.get("password"):
        raise ValueError("缺少 NEO4J_PASSWORD(环境变量或 --password),不硬编码密码")
    return GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))


def op_target_coverage(driver, target_species: str, species_type: str, log_path: str, params: dict) -> dict:
    """Cypher: count genes / unique cell types / edges for target species in KG."""
    cypher = (
        "MATCH (g:Gene) "
        "WHERE g.Species = $sp "
        "RETURN "
        " count(g) AS total_genes, "
        " count(DISTINCT g.Name) AS n_unique_genes"
    )
    cypher_ct = (
        "MATCH (g:Gene)-[r:marker_of]->(o:Ontology) "
        "WHERE g.Species = $sp "
        "RETURN "
        " count(DISTINCT g.Name) AS n_genes_with_ct, "
        " count(DISTINCT o.Name) AS n_unique_ct, "
        " count(r) AS n_edges"
    )
    m: dict = {"target_species": target_species}
    with driver.session() as s:
        res = s.run(cypher, sp=target_species).single()
        m["n_target_genes_in_kg"] = int(res["total_genes"])
        m["n_target_unique_genes"] = int(res["n_unique_genes"])
        res = s.run(cypher_ct, sp=target_species).single()
        m["n_target_genes_with_ct"] = int(res["n_genes_with_ct"])
        m["n_target_unique_ct"] = int(res["n_unique_ct"])
        m["n_target_marker_edges"] = int(res["n_edges"])
    common.exec_record(log_path, "step3a_kg_precheck", "target_coverage", params, m)
    return m


def _coverage_tier(genes_with_ct: int, high: int, low: int) -> str:
    if genes_with_ct >= high:
        return "high"
    if genes_with_ct >= low:
        return "medium"
    return "low"


def _recommended_strategy(tier: str) -> str:
    return {"high": "single_species", "medium": "mixed", "low": "cross_species_only"}.get(tier, "cross_species_only")


def _strategy_rationale(target_species: str, tier: str, genes_with_ct: int, strategy: str) -> str:
    catalog = "references/reference-species.md"
    if tier == "high":
        return (f"目标物种 {target_species} 在 KG 中 {genes_with_ct} genes 有 cell type 标注(高覆盖),"
                f"直接路径足以支持注释。跳过同源映射。")
    if tier == "medium":
        return (f"目标物种 {target_species} 在 KG 中仅 {genes_with_ct} genes 有 cell type 标注(中等覆盖),"
                f"建议混合路径 — 直接路径优先,跨物种路径补漏。"
                f"若做同源,从 {catalog} 按亲缘点名最多 3 个参考物种,勿混动植物库。")
    return (f"目标物种 {target_species} 在 KG 中仅 {genes_with_ct} genes 有 cell type 标注(严重不足),"
            f"必须跨物种路径。从 {catalog} 按亲缘点名最多 3 个参考物种,勿混动植物库。")


def cmd_run(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    out_dir = common.step_dir(project_dir, "step3a_kg_precheck")
    log = common.run_log_path(project_dir)
    cfg = common.neo4j_config(args)
    target = args.target_species
    species_type = args.species_type or "Plant"

    params = {"target_species": target, "organ": args.organ, "species_type": species_type,
              "high_threshold": args.high_threshold, "low_threshold": args.low_threshold}

    driver = _driver(cfg)
    try:
        cov = op_target_coverage(driver, target, species_type, log, params)
    finally:
        try:
            driver.close()
        except Exception:
            pass

    genes_with_ct = cov["n_target_genes_with_ct"]
    tier = _coverage_tier(genes_with_ct, args.high_threshold, args.low_threshold)
    strategy = _recommended_strategy(tier)

    payload = {
        "target_species": target,
        "target_species_type": species_type,
        "target_organ": args.organ,
        "computed_at": common.now_iso(),
        "kg_query_stats": {
            "n_target_genes_in_kg": cov["n_target_genes_in_kg"],
            "n_target_genes_with_ct": genes_with_ct,
            "n_target_unique_ct": cov["n_target_unique_ct"],
            "n_target_marker_edges": cov["n_target_marker_edges"],
        },
        "coverage_tier": tier,
        "recommended_strategy": strategy,
        "strategy_rationale": _strategy_rationale(target, tier, genes_with_ct, strategy),
        "warnings": [],
    }

    out_path = os.path.join(out_dir, "coverage_report.json")
    common.write_json(out_path, payload)
    common.exec_record(log, "step3a_kg_precheck", "write_report", params,
                       {"coverage_report_json": out_path})
    return common.ok({
        "coverage_report_json": out_path,
        "coverage_tier": tier,
        "recommended_strategy": strategy,
        "n_target_genes_with_ct": genes_with_ct,
    })


def main(argv=None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    if getattr(args, "dump_schema", False):
        return common.dump_schema(p)
    if not args.subcommand:
        p.print_help()
        return 2
    try:
        if args.subcommand == "run":
            result = cmd_run(args)
        else:
            result = common.fail(f"未知子命令:{args.subcommand}")
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = common.fail(f"{args.subcommand} failed: {exc}")
    return common.emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
