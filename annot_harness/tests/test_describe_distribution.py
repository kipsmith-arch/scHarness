"""describe_distribution compact histogram (counts next to percentiles)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "cell-annotation" / "scripts"))

import common  # noqa: E402


def test_histogram_is_count_list_beside_percentiles():
    rng = np.random.default_rng(0)
    values = rng.normal(10.0, 2.0, size=500)
    d = common.describe_distribution(values, n_bins=20)
    assert d is not None
    assert isinstance(d["histogram"], list)
    assert len(d["histogram"]) == 20
    assert all(isinstance(c, int) for c in d["histogram"])
    assert sum(d["histogram"]) == 500
    assert "bin_edges" not in d
    assert set(d["percentiles"]) == {f"p{k}" for k in (1, 5, 10, 25, 50, 75, 90, 95, 99)}
    edges = common.histogram_bin_edges(d)
    assert edges is not None
    assert len(edges) == 21
    np.testing.assert_allclose(edges[0], d["min"])
    np.testing.assert_allclose(edges[-1], d["max"])


def test_ulp_scale_range_keeps_histogram_length():
    values = np.array([1.0, 1.0 + 2e-16] * 10)
    d = common.describe_distribution(values, n_bins=20)
    assert d is not None
    assert len(d["histogram"]) == 20
    assert sum(d["histogram"]) == 20


def test_compact_floats_keep_json_short():
    values = np.array([0.1234567890123, 1.0, 2.0])
    d = common.describe_distribution(values)
    blob = json.dumps(d)
    assert "0.1234567890123" not in blob
    assert "bin_edges" not in blob


def test_resolve_batch_key_case_insensitive():
    cols = ["orig.ident", "nCount_RNA"]
    assert common.resolve_batch_key(cols, "Orig.ident") == "orig.ident"
    assert common.resolve_batch_key(cols, None) == "orig.ident"
