"""Step 3 precheck — KG coverage diagnostics for the target species (CAP-1).

Subcommands:
    run  — query KG for target-species cell type coverage, return recommended
           strategy (single_species / mixed / cross_species_only) and a ranked
           list of candidate reference species. 0 h5ad, pure Neo4j + JSON.

Per SPEC cross-species-routing CAP-1:
- Inputs: --target-species, --organ, --species-type, --project-dir,
          --high-threshold (default 500), --low-threshold (default 50)
- Output: step3_kg_precheck/coverage_report.json
- Coverage tiers: high (>= high-threshold genes with CT), medium, low
- Recommended strategy: single_species / mixed / cross_species_only
- Recommended reference species: ranked by ct_coverage * 0.5 +
  phylogenetic_distance * 0.4 + ensembl_divisible * 0.1

This tool is purely advisory. It does NOT decide; the LLM decides at the
``cross_species_routing`` decision point. The tool just makes the
recommendation visible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  -- also triggers load_skill_dotenv()


# Coverage tier thresholds (genes with at least one marker_of edge in KG).
# These are start values; SPEC says they may be tuned per organ via evals.
DEFAULT_HIGH_THRESHOLD = 500
DEFAULT_LOW_THRESHOLD = 50

# Maximum recommended reference species returned. Keeps the LLM decision
# view short; ranking carries the rest of the signal.
MAX_RECOMMENDED_REFS = 8

# Phylogenetic distance heuristic — coarse but defensible for cross-species
# annotation. Per SPEC cross-species-routing §metrics-extension, LLM scoring
# weights this at 0.4. Real NCBI taxonomy distance would be better; this is
# a sufficient v1.
# Same genus = 1.0, same family (heuristic prefix match) = 0.7,
# same order (shared middle token) = 0.4, else 0.2.
PHYLO_TOKENS = {
    # family-level clusters used in the heuristic
    "brassicaceae": {"arabidopsis", "brassica"},
    "poaceae": {"oryza", "zea", "sorghum", "triticum", "setaria"},
    "solanaceae": {"solanum", "nicotiana"},
    "fabaceae": {"glycine", "medicago", "catharanthus"},  # catharanthus is apocynaceae but rarely miscounted
    "rosaceae": {"fragaria", "malus", "prunus"},
    "malvaceae": {"gossypium", "bombax"},
    "eudicots_general": {"arabidopsis", "brassica", "glycine", "medicago",
                         "solanum", "nicotiana", "fragaria", "gossypium",
                         "populus", "manihot", "catharanthus", "bombax"},
}


def _phylo_score(target_species: str, ref_species: str) -> float:
    """Return 0.2~1.0 phylogenetic proximity score between two Ensembl-style
    species names (lower_underscore)."""
    target_genus = target_species.split("_")[0] if "_" in target_species else target_species
    ref_genus = ref_species.split("_")[0] if "_" in ref_species else ref_species
    if target_genus == ref_genus:
        return 1.0
    target_family = None
    ref_family = None
    for fam, genera in PHYLO_TOKENS.items():
        if target_genus in genera:
            target_family = fam
        if ref_genus in genera:
            ref_family = fam
    if target_family and target_family == ref_family:
        return 0.7
    if (target_family and target_family.endswith("_general")
            and ref_genus in PHYLO_TOKENS[target_family]):
        return 0.7
    return 0.2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="step3_kg_precheck.py",
        description="Step 3 precheck — KG coverage for target species (0 h5ad)",
    )
    p.add_argument("--dump-schema", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="subcommand")

    p_r = sub.add_parser("run", help="query KG coverage for the target species")
    # A-class (LLM-visible): biological decisions + I/O paths
    p_r.add_argument("--target-species", required=True,
                     help="target species in Ensembl/KG format (lower_underscore, e.g. arabidopsis_thaliana)")
    p_r.add_argument("--organ", required=True,
                     help="target organ (passed through for downstream step3_kg)")
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
    common.exec_record(log_path, "step3_kg_precheck", "target_coverage", params, m)
    return m


def op_candidate_refs(driver, target_species: str, species_type: str, log_path: str, params: dict) -> dict:
    """Find candidate reference species in the same species_type with high CT
    coverage. Score = ct_coverage_norm * 0.5 + phylo_norm * 0.4 + ensembl * 0.1."""
    cypher = (
        "MATCH (g:Gene)-[r:marker_of]->(o:Ontology) "
        "WHERE g.Species_type = $st AND g.Species <> $target "
        "RETURN g.Species AS species, "
        " count(DISTINCT g.Name) AS n_genes, "
        " count(DISTINCT o.Name) AS n_unique_ct, "
        " count(r) AS n_edges "
        "ORDER BY n_genes DESC LIMIT 50"
    )
    rows = []
    with driver.session() as s:
        res = s.run(cypher, st=species_type, target=target_species)
        rows = [dict(r) for r in res]

    if not rows:
        return {"recommended_reference_species": [],
                "scoring_basis": {"ct_weight": 0.5, "phylo_weight": 0.4, "ensembl_weight": 0.1}}

    # Normalize ct coverage by max in this candidate set (so best=1.0).
    max_ct = max(r["n_genes"] for r in rows) or 1
    max_unique_ct = max(r["n_unique_ct"] for r in rows) or 1

    candidates = []
    for r in rows:
        ct_norm = (r["n_genes"] + r["n_unique_ct"] * 0.3) / (max_ct + max_unique_ct * 0.3)
        phylo = _phylo_score(target_species, r["species"])
        # Ensembl divisibility: assume yes for known divisions; this is
        # a v1 heuristic. step2_ortholog will validate at runtime.
        ensembl = 1.0 if species_type in common.ENSEMBL_REST_HOSTS else 0.5
        score = ct_norm * 0.5 + phylo * 0.4 + ensembl * 0.1
        candidates.append({
            "species": r["species"],
            "species_type": species_type,
            "kg_genes": int(r["n_genes"]),
            "kg_unique_ct": int(r["n_unique_ct"]),
            "n_marker_edges": int(r["n_edges"]),
            "score": round(float(score), 4),
            "score_breakdown": {
                "ct_coverage_norm": round(float(ct_norm), 4),
                "phylogenetic_distance_norm": float(phylo),
                "ensembl_divisible": float(ensembl),
            },
        })

    candidates.sort(key=lambda x: -x["score"])
    candidates = candidates[:MAX_RECOMMENDED_REFS]

    m = {
        "recommended_reference_species": candidates,
        "scoring_basis": {
            "ct_weight": 0.5,
            "phylo_weight": 0.4,
            "ensembl_weight": 0.1,
            "max_candidates_considered": 50,
            "max_returned": MAX_RECOMMENDED_REFS,
        },
    }
    common.exec_record(log_path, "step3_kg_precheck", "candidate_refs", params, m)
    return m


def _coverage_tier(genes_with_ct: int, high: int, low: int) -> str:
    if genes_with_ct >= high:
        return "high"
    if genes_with_ct >= low:
        return "medium"
    return "low"


def _recommended_strategy(tier: str) -> str:
    return {"high": "single_species", "medium": "mixed", "low": "cross_species_only"}.get(tier, "cross_species_only")


def _strategy_rationale(target_species: str, tier: str, genes_with_ct: int,
                        n_refs: int, refs: list, strategy: str) -> str:
    if tier == "high":
        return (f"目标物种 {target_species} 在 KG 中 {genes_with_ct} genes 有 cell type 标注(高覆盖),"
                f"直接路径足以支持注释。推荐 {n_refs} 个近缘参考物种作为辅助。")
    if tier == "medium":
        return (f"目标物种 {target_species} 在 KG 中仅 {genes_with_ct} genes 有 cell type 标注(中等覆盖),"
                f"建议混合路径 — 直接路径优先,跨物种路径补漏。"
                f"推荐参考物种: {', '.join(r['species'] for r in refs[:3]) or '(无同 species_type 物种)'}。")
    return (f"目标物种 {target_species} 在 KG 中仅 {genes_with_ct} genes 有 cell type 标注(严重不足),"
            f"必须跨物种路径。推荐参考物种: {', '.join(r['species'] for r in refs[:3]) or '(无同 species_type 物种)'}。")


def cmd_run(args) -> dict:
    project_dir = common.env_or_default(args, "project_dir", (), "output")
    out_dir = common.step_dir(project_dir, "step3_kg_precheck")
    log = common.run_log_path(project_dir)
    cfg = common.neo4j_config(args)
    target = args.target_species
    species_type = args.species_type or "Plant"

    params = {"target_species": target, "organ": args.organ, "species_type": species_type,
              "high_threshold": args.high_threshold, "low_threshold": args.low_threshold}

    driver = _driver(cfg)
    try:
        cov = op_target_coverage(driver, target, species_type, log, params)
        refs_block = op_candidate_refs(driver, target, species_type, log, params)
    finally:
        try:
            driver.close()
        except Exception:
            pass

    genes_with_ct = cov["n_target_genes_with_ct"]
    tier = _coverage_tier(genes_with_ct, args.high_threshold, args.low_threshold)
    strategy = _recommended_strategy(tier)
    refs = refs_block["recommended_reference_species"]

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
        "recommended_reference_species": refs,
        "strategy_rationale": _strategy_rationale(target, tier, genes_with_ct, len(refs), refs, strategy),
        "warnings": [],
    }

    out_path = os.path.join(out_dir, "coverage_report.json")
    common.write_json(out_path, payload)
    common.exec_record(log, "step3_kg_precheck", "write_report", params,
                       {"coverage_report_json": out_path})
    return common.ok({
        "coverage_report_json": out_path,
        "coverage_tier": tier,
        "recommended_strategy": strategy,
        "n_target_genes_with_ct": genes_with_ct,
        "n_recommended_refs": len(refs),
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
