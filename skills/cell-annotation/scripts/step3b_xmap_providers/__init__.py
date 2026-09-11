"""step3b_xmap_providers: provider abstraction for KG-side cross-species mapping.

Per SPEC cross-species-routing CAP-2 (revised 2026-08-24, renamed 2026-09-08):
the original ``step2_ortholog.py`` was hard-wired to Ensembl Compara REST.
Mapping lives under SOP-3 because its job is to raise knowledge-graph hit
rate (look up reference-species genes the KG covers), not to discover markers.

The mapping step is generic — it takes target-species markers and finds
equivalent genes in one or more reference species. The actual *source* of
those mappings (Ensembl Compara, local BLAST/DIAMOND, OMA, eggNOG, etc.)
is a pluggable provider.

This subpackage defines:

- ``base.py``: ``BaseCrossSpeciesProvider`` ABC, ``MappingRecord`` dataclass,
  provider registry. Any provider implementation must subclass and register.
- ``ensembl_compara.py``: the Ensembl Compara REST implementation (the default
  provider).
- ``blastp.py``: local BLASTP against a cached subject protein BLASTDB
  (``--provider blastp``; requires user query FASTA).

The CLI entry is ``step3b_cross_species_map.py`` (sibling of this directory).
Adding a new provider = adding one file in this directory; ``@register_provider``
plus the import in ``base._load_builtin_providers``. Callers (LLM, step3c_kg)
do not change. Default ``--provider`` remains ``ensembl_compara``.

Why the subpackage is named ``step3b_xmap_providers`` (not
``step3b_cross_species_map``): Python forbids a module and a subpackage from
sharing the same name. The CLI script keeps the user-facing name
``step3b_cross_species_map``; the provider subpackage uses the internal alias.
"""

from .base import (  # noqa: F401
    BaseCrossSpeciesProvider,
    MappingRecord,
    PROVIDERS,
    get_provider,
)

__all__ = [
    "BaseCrossSpeciesProvider",
    "MappingRecord",
    "PROVIDERS",
    "get_provider",
]
