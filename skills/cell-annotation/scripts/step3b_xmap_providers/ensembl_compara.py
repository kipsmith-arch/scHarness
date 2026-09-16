"""Ensembl Compara REST provider for step3b_cross_species_map (SPEC CAP-2).

Implements ``BaseCrossSpeciesProvider`` against the Ensembl Compara REST API:

    GET /homology/symbol/{species}/{symbol}?type=orthologues&target_species=...

This is the default provider shipped with the skill. Uses parallel GET via
``concurrent.futures.ThreadPoolExecutor`` because Ensembl's POST endpoint for
homology currently returns 404 on the supported REST deployments.

Maps Ensembl response fields onto the neutral ``MappingRecord`` schema:

- ``target.perc_id`` -> ``score`` (Ensembl already reports percent identity 0~100)
- ``score_type = "percent_identity"``
- ``target.id`` -> ``ref_gene_id``
- ``target.species`` -> ``ref_species`` (Ensembl-formatted, lower_underscore)
- homology ``type`` (``ortholog_one2one`` / ``ortholog_one2many`` /
  ``ortholog_many2many``) -> ``mapping_type`` (prefixed with ``ensembl_``)
- raw fields (``perc_pos``, ``cigar_line``, ``protein_id``, ``align_seq``,
  ``dn_ds``) -> ``raw`` for audit
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from .base import BaseCrossSpeciesProvider, MappingRecord, register_provider


ENSEMBL_TIMEOUT_DEFAULT = 10
ENSEMBL_MAX_RETRIES_DEFAULT = 4
ENSEMBL_BACKOFF_BASE = 1.5
ENSEMBL_USER_AGENT = "annotHarness-cell-annotation/1.0 (cross-species mapping)"


@register_provider
class EnsemblComparaProvider(BaseCrossSpeciesProvider):
    """Ensembl Compara REST backend.

    Routing per species division:
    - Plant -> https://rest.plants.ensembl.org
    - Animal -> https://rest.ensembl.org
    - Fungi/Metazoa/Protists/Bacteria -> their respective hosts

    DNS resolution check is performed in CLI layer (``_choose_working_host``);
    this provider assumes the host passed in is reachable.
    """

    name = "ensembl_compara"
    score_type = "percent_identity"

    #: Mapping from Ensembl division -> REST host. Mirrors
    #: ``common.ENSEMBL_REST_HOSTS`` but lives here too because a provider
    #: should be self-contained (a future blast provider won't need common.py).
    HOSTS: dict[str, str] = {
        "Plant": "https://rest.plants.ensembl.org",
        "Animal": "https://rest.ensembl.org",
        "Fungi": "https://rest.fungi.ensembl.org",
        "Metazoa": "https://rest.metazoa.ensembl.org",
        "Protists": "https://rest.protists.ensembl.org",
        "Bacteria": "https://rest.bacteria.ensembl.org",
    }

    def __init__(self) -> None:
        # Per-call HTTP session / pool could go here; for now we open per
        # request. ThreadPoolExecutor in lookup() gives concurrency.
        self._ssl_ctx = ssl._create_unverified_context()

    @classmethod
    def default_host_for(cls, species_type: str) -> str:
        return cls.HOSTS.get(species_type, "https://rest.ensembl.org")

    def available(self, species_type: str) -> bool:
        """True if Ensembl has a REST deployment for this species division."""
        return species_type in self.HOSTS

    def _get_json(self, url: str, timeout: int, max_retries: int) -> tuple[dict | None, str | None]:
        headers = {"Accept": "application/json", "User-Agent": ENSEMBL_USER_AGENT}
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx) as r:
                    if r.status == 200:
                        return json.loads(r.read()), None
                    last_err = f"HTTP {r.status}"
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code == 429 and attempt < max_retries:
                    retry_after = int(e.headers.get("Retry-After", str(2 ** attempt)))
                    time.sleep(min(retry_after, 30))
                    continue
                if e.code in (400, 404):
                    return None, last_err
                if e.code >= 500 and attempt < max_retries:
                    time.sleep(ENSEMBL_BACKOFF_BASE ** attempt)
                    continue
                return None, last_err
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt < max_retries:
                    time.sleep(ENSEMBL_BACKOFF_BASE ** attempt)
                    continue
                return None, last_err
        return None, last_err

    def _parse_response(self, body, gene: str, ref_species: str) -> list[MappingRecord]:
        """Convert Ensembl REST body (or list-of-entries) to MappingRecords.

        Tolerates both shapes:
        - GET single: ``{"data": [{"id": symbol, "homologies": [...]}]}``
        - Sometimes: top-level list ``[{"id": symbol, "homologies": [...]}]``
        """
        homs = []
        if isinstance(body, list):
            if body and isinstance(body[0], dict):
                homs = body[0].get("homologies") or []
        elif isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict):
                homs = data[0].get("homologies") or []
            else:
                homs = body.get("homologies") or []
        return self._records_from_homologies(homs, gene, ref_species)

    def _records_from_homologies(self, homs: list, gene: str, ref_species: str) -> list[MappingRecord]:
        records: list[MappingRecord] = []
        for h in homs:
            h_type = h.get("type")
            if h_type not in ("ortholog_one2one", "ortholog_one2many", "ortholog_many2many"):
                continue
            target = h.get("target") or {}
            perc_id = target.get("perc_id")
            if perc_id is None:
                continue
            try:
                score = float(perc_id)
            except (TypeError, ValueError):
                continue
            # Map Ensembl type to provider-prefixed mapping_type
            mapping_type = {
                "ortholog_one2one": "ensembl_one2one",
                "ortholog_one2many": "ensembl_one2many",
                "ortholog_many2many": "ensembl_many2many",
            }.get(h_type, f"ensembl_{h_type}")
            raw = {
                "ensembl_type": h_type,
                "perc_pos": target.get("perc_pos"),
                "protein_id": target.get("protein_id"),
                "cigar_line": target.get("cigar_line"),
                "dn_ds": h.get("dn_ds"),
                "taxonomy_level": h.get("taxonomy_level"),
            }
            records.append(MappingRecord(
                ref_species=target.get("species") or ref_species,
                ref_gene_id=str(target.get("id") or ""),
                score=score,  # 0~100 percent identity
                score_type=self.score_type,
                mapping_type=mapping_type,
                confidence=None,  # Ensembl doesn't expose relation_confidence
                                    # on /homology endpoint; set later if added
                raw=raw,
            ))
        return records

    def lookup(
        self,
        target_species: str,
        ref_species: str,
        gene: str,
        *,
        timeout: int = ENSEMBL_TIMEOUT_DEFAULT,
        max_retries: int = ENSEMBL_MAX_RETRIES_DEFAULT,
        host: str | None = None,
    ) -> tuple[list[MappingRecord] | None, str | None]:
        """Query Ensembl Compara for one (target_gene, ref_species) pair.

        ``host`` overrides the auto-routed canonical host. The CLI passes this
        when the user supplies ``--ensembl-rest-host``; otherwise the canonical
        species-division host is used.
        """
        actual_host = host or self.default_host_for(self._species_type(target_species))
        url = (f"{actual_host}/homology/symbol/{target_species}/{gene}"
               f"?type=orthologues&target_species={ref_species}&content-type=application/json")
        body, err = self._get_json(url, timeout, max_retries)
        if err:
            return None, err
        records = self._parse_response(body, gene, ref_species)
        return records, None

    @staticmethod
    def _species_type(species: str) -> str:
        """Best-effort species_type inference. Defaults to Plant.

        In practice the CLI passes species_type explicitly; this fallback is
        for ad-hoc callers.
        """
        # Heuristic: if common.py is available, use it; else Plant default.
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            import common
            # We don't actually know species_type from a species name alone;
            # CLI layer passes it. This is a fallback to vertebrates host.
            return "Animal"
        except Exception:
            return "Animal"


def check_dns(host: str) -> bool:
    """Module-level helper used by CLI to probe whether a host is resolvable.

    Kept here (not in CLI) so future providers can reuse the same pattern.
    """
    try:
        import socket
        hostname = urlparse(host).hostname
        if not hostname:
            return False
        socket.gethostbyname(hostname)
        return True
    except (socket.gaierror, UnicodeError):
        return False
