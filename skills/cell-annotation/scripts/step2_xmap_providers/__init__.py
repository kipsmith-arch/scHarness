"""step2_xmap_providers: provider abstraction for cross-species gene mapping.

Per SPEC cross-species-routing CAP-2 (revised 2026-08-24): the original
``step2_ortholog.py`` was hard-wired to Ensembl Compara REST. The cross-species
mapping step in the pipeline is generic — it takes target-species markers and
finds equivalent genes in one or more reference species. The actual *source*
of those mappings (Ensembl Compara, local BLAST/DIAMOND, OMA, eggNOG, etc.)
is a pluggable provider.

This subpackage defines:

- ``base.py``: ``BaseCrossSpeciesProvider`` ABC, ``MappingRecord`` dataclass,
  provider registry. Any provider implementation must subclass and register.
- ``ensembl_compara.py``: the Ensembl Compara REST implementation (the default
  and currently only shipped provider).

The CLI entry is ``step2_cross_species_map.py`` (sibling of this directory).
Adding a new provider = adding one file in this directory + one line in
``base.py``'s ``PROVIDERS`` registry. Callers (LLM, step3_kg) do not change.

Why the subpackage is named ``step2_xmap_providers`` (not
``step2_cross_species_map``): Python forbids a module and a subpackage from
sharing the same name. The CLI script keeps the user-facing name
``step2_cross_species_map``; the provider subpackage uses the internal alias.
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
