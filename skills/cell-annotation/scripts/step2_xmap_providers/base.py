"""Provider abstraction for step2_cross_species_map (SPEC CAP-2).

Defines:
- ``MappingRecord``: neutral dataclass representing one mapping result. Each
  provider must produce records of this shape so downstream consumers (LLM,
  step3_kg) don't depend on provider-specific field names.
- ``BaseCrossSpeciesProvider``: ABC that every provider must subclass.
- ``PROVIDERS`` registry: maps provider name -> provider class. The CLI's
  ``--provider`` arg dispatches via this registry.

Design rationale
----------------
Cross-species gene mapping has many backends: Ensembl Compara REST, local
BLAST / DIAMOND against a protein DB, OMA, eggNOG, etc. They all answer the
same question: "for this target gene, what is the equivalent gene in the
reference species, with what score and what confidence?". But their native
output formats differ wildly (Ensembl returns ``perc_id`` + ``perc_pos`` +
homology ``type``; BLAST returns ``pident`` + ``evalue`` + ``bitscore``;
OMA returns ``oma_group_id`` etc.).

We don't want each new backend to require downstream changes. So the
provider contract is intentionally minimal:

- ``MappingRecord.score`` — a normalized 0~100 similarity score, suitable
  for "is this mapping good enough?" filtering. Providers map their native
  metrics into this via ``score_normalize``.
- ``MappingRecord.score_type`` — labels what the score means (so the LLM
  can interpret it correctly: ``percent_identity`` / ``bitscore`` / etc.).
  This is provider-specific and never used for filtering.
- ``MappingRecord.raw`` — provider-native fields preserved for audit.
  Downstream consumers should not rely on ``raw``; it's for debugging.

Adding a new provider = writing one file with one class. No changes to CLI,
no changes to LLM-facing schema, no changes to step3_kg.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import ClassVar


@dataclasses.dataclass
class MappingRecord:
    """One mapping result from a cross-species provider.

    Fields
    ------
    ref_species : str
        Reference species in Ensembl/KG format (lower_underscore).
    ref_gene_id : str
        Reference gene ID, format depends on provider (Ensembl ID, NCBI
        accession, OMA group id, etc.).
    score : float
        Normalized similarity score in 0~100. Higher = more similar.
        Convention: ``percent_identity`` for sequence-based providers,
        ``bitscore`` may be mapped differently (see provider docs).
    score_type : str
        Label for what ``score`` represents (``percent_identity`` /
        ``bitscore`` / ``oma_distance_inverse`` / etc.).
        This is provider-specific; downstream consumers do NOT use it for
        filtering — they use ``score`` after provider normalization.
    mapping_type : str
        Provider-specific classification. Examples:
        - Ensembl Compara: ``ortholog_one2one``, ``ortholog_one2many``,
          ``ortholog_many2many``
        - BLAST: ``blast_top_hit``, ``blast_multiple_hits``
        The string should be unique enough that debugging logs are
        unambiguous. Always prefix with provider name.
    confidence : float | None
        Optional confidence 0~1 (Ensembl Compara has ``relation_confidence``;
        BLAST has e-value -> derive). None means provider did not report.
    raw : dict
        Provider-native fields preserved verbatim for audit. Downstream MUST
        NOT depend on this; it's for debugging and provenance.
    """

    ref_species: str
    ref_gene_id: str
    score: float
    score_type: str
    mapping_type: str
    confidence: float | None = None
    raw: dict = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize to JSON-friendly dict (dataclasses.asdict + minor tweaks)."""
        return {
            "ref_species": self.ref_species,
            "ref_gene_id": self.ref_gene_id,
            "score": float(self.score),
            "score_type": self.score_type,
            "mapping_type": self.mapping_type,
            "confidence": self.confidence,
            "raw": dict(self.raw),
        }


class BaseCrossSpeciesProvider(abc.ABC):
    """Abstract base for cross-species gene mapping providers.

    Subclass and register in ``PROVIDERS`` below. Each provider must implement:

    - ``name``: class attribute, the registry key (e.g. ``"ensembl_compara"``).
    - ``score_type``: class attribute, the ``MappingRecord.score_type`` this
      provider emits (e.g. ``"percent_identity"``).
    - ``available(species_type: str) -> bool``: whether this provider can
      service the given species division (e.g. Ensembl Compara returns True
      for Plant / Animal; a local BLAST DB may return True only if a DB is
      pre-installed for that division).
    - ``lookup(target_species: str, ref_species: str, gene: str, **opts) ->
      tuple[list[MappingRecord] | None, str | None]``: query one gene against
      one reference species. Returns ``(records, error)`` — either records
      (possibly empty) on success, or ``(None, error_message)`` on failure.
      A returned empty list means "successfully queried, no mapping found";
      a returned ``None`` + error means "could not query" (network / parse
      / unsupported). Callers should distinguish these.

    Providers are stateless from the caller's perspective: ``lookup`` takes
    everything it needs. Internal caching / connection pooling is the
    provider's responsibility.
    """

    #: Registry key (lowercase_underscore). Used in CLI --provider.
    name: ClassVar[str] = ""

    #: What MappingRecord.score_type this provider emits.
    score_type: ClassVar[str] = ""

    @abc.abstractmethod
    def available(self, species_type: str) -> bool:
        """Return True if this provider can service the given species division."""

    @abc.abstractmethod
    def lookup(
        self,
        target_species: str,
        ref_species: str,
        gene: str,
        *,
        timeout: int = 10,
        max_retries: int = 4,
    ) -> tuple[list[MappingRecord] | None, str | None]:
        """Look up one (target_gene, ref_species) pair.

        Returns ``(records, None)`` on success (records may be empty list,
        meaning no mapping found in this backend); or ``(None, error_string)``
        on transport / parse / unsupported failures.

        Implementations MUST:
        - Translate the provider's native response into MappingRecord.
        - Set ``mapping_type`` to a provider-specific string with the
          provider name as prefix (e.g. ``ensembl_one2one``).
        - Set ``score`` to a 0~100 normalized similarity score.
        - Set ``score_type`` to the class-level attribute.
        - Preserve original fields in ``raw`` for audit.

        Implementations SHOULD:
        - Implement exponential backoff on 429 / 5xx.
        - Return ``(None, error)`` on 4xx (the gene/species does not exist
          here, no point retrying).
        - Not load heavy dependencies eagerly; defer until first ``lookup``.
        """


# Provider registry — add new providers here. Order is the order they appear
# in CLI --provider help text. The default is the first entry.
PROVIDERS: dict[str, type[BaseCrossSpeciesProvider]] = {}


def register_provider(cls: type[BaseCrossSpeciesProvider]) -> type[BaseCrossSpeciesProvider]:
    """Class decorator that adds a provider to the global registry.

    Usage::

        @register_provider
        class MyProvider(BaseCrossSpeciesProvider):
            name = "my_provider"
            ...
    """
    if not cls.name:
        raise ValueError(f"{cls.__name__}.name must be set to a non-empty registry key")
    if cls.name in PROVIDERS:
        raise ValueError(f"provider {cls.name!r} already registered")
    PROVIDERS[cls.name] = cls
    return cls


def get_provider(name: str) -> BaseCrossSpeciesProvider:
    """Instantiate the named provider. Raises ValueError if unknown."""
    if name not in PROVIDERS:
        raise ValueError(
            f"unknown provider {name!r}; available: {sorted(PROVIDERS.keys())}"
        )
    return PROVIDERS[name]()


# Eagerly import built-in providers so registration happens at package load.
def _load_builtin_providers() -> None:
    from . import ensembl_compara  # noqa: F401  -- side-effect: registers

_load_builtin_providers()
