"""B1 scripted driver CLI — wraps annot_harness.scripted_driver + cell-annotation DAG.

Usage:
    python experiments/scripted_driver.py \\
        --arm default --project-dir output/B1/arm1_default --raw dataset/h5ad/SRP171040.h5ad
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dataclasses import replace
from experiments.cell_annotation_dag import CELL_ANNOTATION_DAG  # noqa: E402
from experiments.judges.default_judge import DefaultJudge  # noqa: E402
from experiments.judges.rule_judge import RuleJudge  # noqa: E402
from annot_harness.scripted_driver import ScriptedRunError, run_scripted  # noqa: E402
from annot_harness.skill_loader import load_skill  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="scripted_driver.py",
        description="B1 ①②: DAG walker via dispatcher.dispatch; judgment layer is --arm.",
    )
    ap.add_argument("--arm", choices=("default", "rule"), default="default")
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--raw", default=None, help="Raw h5ad path (passed as --input to step1)")
    ap.add_argument("--organ", default="root")
    ap.add_argument("--species", default=None,
                    help="Passed to step3c_kg__query --species (KG Species string, e.g. Human)")
    ap.add_argument("--species-type", default="Plant",
                    help="KG Species_type filter (Plant / Animal). Default Plant matches the Arabidopsis runs.")
    ap.add_argument("--batch-key", default=None,
                    help="obs column for step1 batch mixing. Omit to use the script fallback.")
    ap.add_argument("--query-fasta", default=None,
                    help="Protein FASTA for step3b (exists → blastp). Also CELL_ANNOTATION_QUERY_FASTA.")
    ap.add_argument("--session-id", default=None)
    args = ap.parse_args()

    os.makedirs(args.project_dir, exist_ok=True)
    skill = load_skill(os.path.join(REPO_ROOT, "skills", "cell-annotation"))
    for spec in skill.tool_runtime.values():
        spec["timeout"] = max(float(spec.get("timeout") or 0), 14400)
    judge = DefaultJudge() if args.arm == "default" else RuleJudge()
    # Only flags every tool accepts. --organ/--input are per-node (step2 has
    # --input but must not receive the raw h5ad; step2/4/6/7 have no --organ).
    base_args = {"project_dir": args.project_dir}
    # Animal KG nodes have no organ split (organ field is Unknown). Do not pass
    # --organ into KG steps. step1 still records the sample tissue.
    kg_organ = None if (args.species_type or "").strip().lower() == "animal" else args.organ
    dag = []
    for node in CELL_ANNOTATION_DAG:
        extra = dict(node.default_args)
        if node.id in ("step1_prepare.metrics", "step1_prepare.run"):
            extra["organ"] = args.organ
            if args.raw:
                extra["input"] = args.raw
            if args.batch_key:
                extra["batch_key"] = args.batch_key
        elif node.id == "step3a_kg_precheck.run":
            if kg_organ:
                extra["organ"] = kg_organ
            extra["target_species"] = args.species or "arabidopsis_thaliana"
            extra["species_type"] = args.species_type
        elif node.id == "step3b_cross_species_map.run":
            extra["target_species"] = args.species or "arabidopsis_thaliana"
            extra["species_type"] = args.species_type
            extra["input"] = os.path.join(args.project_dir, "step2_markers", "markers.json")
            fasta = args.query_fasta or os.environ.get("CELL_ANNOTATION_QUERY_FASTA")
            if fasta:
                extra["query_fasta"] = fasta
        elif node.id == "step3c_kg.query":
            if kg_organ:
                extra["organ"] = kg_organ
            extra["species_type"] = args.species_type
            if args.species:
                extra["species"] = args.species
        dag.append(replace(node, default_args=extra) if extra != node.default_args else node)
    dataset = {"id": os.path.basename(args.project_dir), "h5ad_path": args.raw, "organ": args.organ}
    try:
        result = run_scripted(
            dag, judge, skill.tool_runtime, args.project_dir,
            base_args=base_args, session_id=args.session_id, dataset=dataset,
        )
    except ScriptedRunError as exc:
        print(f"[driver] FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"[driver] ok nodes={result.get('nodes_run')} skipped={result.get('nodes_skipped')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
