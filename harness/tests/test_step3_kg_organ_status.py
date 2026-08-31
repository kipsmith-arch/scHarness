"""Tests for step3_kg._organ_status boundary matching (deferred-work D-2 fix).

Before the fix: ``_organ_status`` used ``target_norm in o`` substring
matching on each organ field as a whole, which produced false positives:
- target="Root" matched organ field "Rootstock" (substring)
- target="Leaf" matched organ field "Leaflet" (substring)

After the fix: ``_iter_organ_tokens`` splits a KG organ field on the
pipe separator, drops the "Unknown" sentinel, and yields a list of
trimmed tokens. ``_field_matches_target`` then checks whether any token
equals the target (whole-word, case-insensitive) — substring matches
no longer pass. ``_organ_status`` classifies per-field (not per-token)
because a field like "Stem|Root|Leaf" is a single multi-organ cell type
that applies to all three organs, not three independent cell types.

These tests pin the boundary contract so the regression cannot return.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import step3_kg  # noqa: E402


# ---------------------------------------------------------------------------
# _iter_organ_tokens (the pipe-split normalizer)
# ---------------------------------------------------------------------------

class TestIterOrganTokens:
    """Pin the pipe-split normalizer. Each input is a single KG organ field
    (string); the output is a list of trimmed non-empty tokens (excluding
    the "Unknown" sentinel).
    """

    def test_single_token(self):
        assert step3_kg._iter_organ_tokens("Root") == ["Root"]

    def test_pipe_separated(self):
        assert step3_kg._iter_organ_tokens("Stem|Root|Leaf") == ["Stem", "Root", "Leaf"]

    def test_pipe_with_spaces(self):
        # Whitespace around tokens is trimmed
        assert step3_kg._iter_organ_tokens("Stem | Root | Leaf") == ["Stem", "Root", "Leaf"]

    def test_pipe_with_extra_whitespace_segments_ignored(self):
        # Empty / whitespace-only segments between pipes are dropped
        assert step3_kg._iter_organ_tokens("|  |Root|  |") == ["Root"]

    def test_unknown_sentinel_excluded_at_field_level(self):
        # A field that is exactly "Unknown" yields []
        assert step3_kg._iter_organ_tokens("Unknown") == []

    def test_unknown_sentinel_excluded_within_field(self):
        # The "Unknown" token is also dropped when mixed with real tokens
        assert step3_kg._iter_organ_tokens("Root|Unknown") == ["Root"]
        assert step3_kg._iter_organ_tokens("Unknown|Root|Unknown") == ["Root"]

    def test_unknown_sentinel_only_title_case(self):
        # The current implementation matches "Unknown" exactly. Lowercase
        # variants ("unknown", "UNKNOWN") are NOT excluded by design --
        # they are treated as real organ labels (which they shouldn't be
        # in well-curated KG, but we don't silently absorb that risk).
        # If lowercase variants appear in the KG, the upstream data
        # curator should normalize them, not this function.
        assert step3_kg._iter_organ_tokens("Root|unknown") == ["Root", "unknown"]
        assert step3_kg._iter_organ_tokens("Root|UNKNOWN") == ["Root", "UNKNOWN"]

    def test_none_input(self):
        assert step3_kg._iter_organ_tokens(None) == []

    def test_empty_string(self):
        assert step3_kg._iter_organ_tokens("") == []

    def test_whitespace_only(self):
        assert step3_kg._iter_organ_tokens("   ") == []

    def test_pipe_only(self):
        assert step3_kg._iter_organ_tokens("||") == []
        assert step3_kg._iter_organ_tokens("   |   |   ") == []


# ---------------------------------------------------------------------------
# _field_matches_target (per-field whole-word match)
# ---------------------------------------------------------------------------

class TestFieldMatchesTarget:
    """Pin the boundary-aware field-level match. The pre-fix substring
    logic would return True for field "Rootstock" with target "Root";
    the post-fix boundary logic returns False.
    """

    def test_exact_single_token(self):
        assert step3_kg._field_matches_target("Root", "Root") is True

    def test_single_token_case_insensitive(self):
        assert step3_kg._field_matches_target("root", "Root") is True
        assert step3_kg._field_matches_target("ROOT", "Root") is True
        assert step3_kg._field_matches_target("Root", "root") is True

    def test_pipe_field_contains_target(self):
        assert step3_kg._field_matches_target("Stem|Root|Leaf", "Root") is True
        assert step3_kg._field_matches_target("Stem|Root|Leaf", "Stem") is True
        assert step3_kg._field_matches_target("Stem|Root|Leaf", "Leaf") is True

    def test_target_substring_of_token_does_not_match(self):
        # D-2 regression: "Root" in "Rootstock" must NOT match
        assert step3_kg._field_matches_target("Rootstock", "Root") is False
        # "Leaf" in "Leaflet"
        assert step3_kg._field_matches_target("Leaflet", "Leaf") is False
        # "Brain" in "Brainstem"
        assert step3_kg._field_matches_target("Brainstem", "Brain") is False

    def test_token_substring_of_target_does_not_match(self):
        # Target "Root" must NOT match a longer token that contains "Root"
        # as a substring but is its own organ label
        assert step3_kg._field_matches_target("Root tip", "Root") is False
        assert step3_kg._field_matches_target("Root_Tip", "Root") is False

    def test_pipe_field_contains_substring_only_token(self):
        # A field "Stem|Rootstock|Leaf" doesn't match target "Root":
        # the only "Root" candidate is "Rootstock" which is boundary-mismatched
        assert step3_kg._field_matches_target("Stem|Rootstock|Leaf", "Root") is False

    def test_empty_field(self):
        assert step3_kg._field_matches_target("", "Root") is False
        assert step3_kg._field_matches_target("|", "Root") is False
        assert step3_kg._field_matches_target("|  |", "Root") is False

    def test_empty_target(self):
        assert step3_kg._field_matches_target("Root", "") is False
        assert step3_kg._field_matches_target("Root", "   ") is False
        assert step3_kg._field_matches_target("Root", None) is False

    def test_unknown_field_does_not_match(self):
        # "Unknown" field contributes nothing (filtered out before match)
        assert step3_kg._field_matches_target("Unknown", "Root") is False
        # Field that has Unknown + Root: only Root tokens are considered;
        # "Root" does match.
        assert step3_kg._field_matches_target("Root|Unknown", "Root") is True
        # Field that has only Unknown: empty after filter, no match.
        assert step3_kg._field_matches_target("Unknown|Unknown", "Root") is False

    def test_unrelated_field(self):
        assert step3_kg._field_matches_target("Liver", "Root") is False
        assert step3_kg._field_matches_target("Heart|Lung", "Root") is False


# ---------------------------------------------------------------------------
# _organ_status (full integration: returns the category the LLM consumes)
# ---------------------------------------------------------------------------

class TestOrganStatus:
    """End-to-end _organ_status: the actual category returned to LLM.

    Semantic model (post-D-2-fix):
    - The function receives a list of KG organ fields (each field is a
      string, possibly pipe-separated like "Stem|Root|Leaf").
    - Each *field* is evaluated as a whole: it "matches target" iff any
      of its non-Unknown tokens equals the target as a whole word.
    - Classification is then per-field:
        * "root"      : at least one field matches target AND all fields match
        * "partial"   : at least one field matches target AND at least one doesn't
        * "mismatch"  : all fields are non-target-matching
        * "unknown"   : no fields contain real (non-Unknown) tokens
    """

    # --- direct match (single field or all-fields) ---

    def test_root_single_field(self):
        assert step3_kg._organ_status(["Root"], "root") == "root"

    def test_root_pipe_field_with_target(self):
        # "Stem|Root|Leaf" — field matches target "Root" as a whole
        assert step3_kg._organ_status(["Stem|Root|Leaf"], "root") == "root"

    def test_root_case_insensitive(self):
        assert step3_kg._organ_status(["root"], "Root") == "root"
        assert step3_kg._organ_status(["ROOT"], "root") == "root"

    def test_root_multiple_fields_one_match_one_similar_label_mismatch(self):
        # Two fields: "Root" (matches target) and "Root Tip" (does NOT
        # match because "Root Tip" is its own KG organ label, distinct
        # from "Root"). Per-field semantic: one matches, one doesn't ->
        # partial. Note: organ-label normalization (e.g. "Root Tip" -> "Root")
        # is the deferred D-1 work, not addressed by the D-2 fix.
        assert step3_kg._organ_status(["Root", "Root Tip"], "root") == "partial"
        assert step3_kg._organ_status(
            ["Stem|Root", "Root|Leaf"], "root"
        ) == "root"

    # --- D-2 regression: substring must NOT match ---

    def test_rootstock_does_not_classify_as_root(self):
        # The pre-fix substring logic would have returned "root" here.
        # Post-fix: "Rootstock" is its own organ label, not target-root.
        assert step3_kg._organ_status(["Rootstock"], "root") == "mismatch"

    def test_leaflet_does_not_classify_as_leaf(self):
        assert step3_kg._organ_status(["Leaflet"], "leaf") == "mismatch"

    def test_brainstem_does_not_classify_as_brain(self):
        assert step3_kg._organ_status(["Brainstem"], "brain") == "mismatch"

    def test_pipe_field_where_only_substring_match_exists(self):
        # "Stem|Rootstock|Leaf" — no token equals target "Root", so the
        # field is non-target. All non-target -> mismatch (not partial).
        assert step3_kg._organ_status(
            ["Stem|Rootstock|Leaf"], "root"
        ) == "mismatch"

    def test_mixed_fields_with_one_substring_field(self):
        # Field "Root" matches; field "Rootstock" doesn't (substring fails).
        # One matches, one doesn't -> partial.
        assert step3_kg._organ_status(
            ["Root", "Rootstock"], "root"
        ) == "partial"

    # --- partial (mixed target + non-target) ---

    def test_partial_pipe_field_with_extra_organ(self):
        # "Stem|Root" — Root matches, Stem is non-target within the same
        # field. Per the semantic model this is still "root" because
        # the field as a whole matches target (any token equals target).
        # This test pins the per-field semantic explicitly so future
        # "per-token" regressions would fail.
        assert step3_kg._organ_status(
            ["Stem|Root"], "root"
        ) == "root"

    def test_partial_two_fields_one_match_one_not(self):
        # Field "Root" matches; field "Stem" doesn't -> partial
        assert step3_kg._organ_status(
            ["Root", "Stem"], "root"
        ) == "partial"

    def test_partial_with_substring_and_exact(self):
        # "Root" matches (exact); "Stem|Rootstock" doesn't (substring fails
        # on Rootstock). One matches, one doesn't -> partial.
        assert step3_kg._organ_status(
            ["Root", "Stem|Rootstock"], "root"
        ) == "partial"

    # --- mismatch / unknown ---

    def test_mismatch_all_non_target(self):
        assert step3_kg._organ_status(["Liver|Heart"], "root") == "mismatch"

    def test_mismatch_multiple_non_target_fields(self):
        assert step3_kg._organ_status(
            ["Liver", "Heart|Lung"], "root"
        ) == "mismatch"

    def test_unknown_empty_list(self):
        assert step3_kg._organ_status([], "root") == "unknown"

    def test_unknown_only_unknown_sentinel(self):
        # Field "Unknown" is filtered out; no real tokens -> unknown
        assert step3_kg._organ_status(["Unknown"], "root") == "unknown"

    def test_unknown_only_empty_and_none(self):
        # None / empty / whitespace fields contribute nothing
        assert step3_kg._organ_status([None, "", "   "], "root") == "unknown"

    def test_unknown_mixed_with_real_target_field(self):
        # Field "Unknown" contributes nothing; field "Root" matches -> root
        # (not unknown)
        assert step3_kg._organ_status(["Unknown", "Root"], "root") == "root"

    def test_unknown_with_only_unknown_then_non_target(self):
        # Field "Unknown" contributes nothing; field "Liver" is non-target.
        # No target, all non-target -> mismatch.
        assert step3_kg._organ_status(["Unknown", "Liver"], "root") == "mismatch"

    def test_empty_target_raises(self):
        # Defensive: empty/None target is a programming error, fail loud
        with pytest.raises(ValueError, match="non-empty string"):
            step3_kg._organ_status(["Root"], "")
        with pytest.raises(ValueError, match="non-empty string"):
            step3_kg._organ_status(["Root"], None)
        with pytest.raises(ValueError, match="non-empty string"):
            step3_kg._organ_status(["Root"], "   ")

    # --- B1 regression: real root dataset still works ---

    def test_b1_root_dataset_smoke(self):
        # Reproduce the actual usage from B1 evaluation: target="root",
        # KG organ fields are pipe-separated. The post-fix logic matches
        # the pre-fix logic for the single-word labels the root dataset
        # actually uses ("Root", "Liver", etc.).
        #
        # NB: "Root Tip" is a separate organ in the KG (per real queries)
        # and is NOT normalized to "Root" -- that would be the deferred
        # D-1 work. The B1 evaluation used target="root" and the
        # pre-fix substring logic which would have classified "Root Tip"
        # as root via the "Root" substring. The post-fix logic
        # correctly classifies it as non-target, which is a stricter but
        # more accurate behavior; the B1 numbers may shift slightly and
        # need re-verification (this test asserts the new strict behavior,
        # not the old substring behavior).
        cases = [
            (["Root"], "root", "root"),
            (["Root Tip"], "root", "mismatch"),  # strict boundary match
            (["Stem|Root|Leaf"], "root", "root"),
            (["Root|Root Tip"], "root", "root"),  # one field contains target
            (["Liver"], "root", "mismatch"),
            ([], "root", "unknown"),
        ]
        for orgs, target, want in cases:
            got = step3_kg._organ_status(orgs, target)
            assert got == want, f"_organ_status({orgs=}, {target=!r}) = {got!r}, want {want!r}"
