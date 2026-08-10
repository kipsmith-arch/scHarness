"""Shared fixtures for harness tests.

- Makes the project root importable regardless of the invocation cwd.
- Installs a deterministic FakeEmbedder so tests never load the real
  sentence-transformers model (slow, network/offline dependent).
- Resets the notebook module-level state (embedder singleton + per-path
  Notebook registry) between tests.
"""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness import notebook  # noqa: E402
from harness.notebook import Embedder, tokenize  # noqa: E402


class FakeEmbedder:
    """Deterministic bag-of-tokens embedding; no model is loaded.

    `available=False` mimics an offline / unconfigured provider so retrieval
    falls back to BM25. Vectors are hashed per token (stable via sha1), so
    similar-token texts get similar vectors and cosine ranking is predictable.
    """

    _configured = True

    def __init__(self, available: bool = True, dim: int = 64) -> None:
        self.available_flag = available
        self.dim = dim

    def available(self) -> bool:
        return self.available_flag

    def _vec(self, text: str):
        v = np.zeros(self.dim, dtype=float)
        for tok in set(tokenize(text)):
            h = int(hashlib.sha1(tok.encode("utf-8")).hexdigest(), 16) % self.dim
            v[h] += 1.0
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    def encode(self, texts):
        if not self.available_flag:
            return None
        arr = np.array([self._vec(t) for t in texts])
        # normalize rows
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return np.divide(arr, norms, out=arr, where=norms > 0)


def make_session_state(project_dir) -> dict:
    """A minimal loop AgentState slice used by the notebook tools."""
    return {
        "project_dir": str(project_dir),
        "notes_path": str(Path(project_dir) / "notes.jsonl"),
        "session_id": "sess-test",
    }


@pytest.fixture(autouse=True)
def _clean_notebook_state(monkeypatch):
    """Isolate notebook module state and default to the fake embedder."""
    monkeypatch.delenv("RAG_EMBEDDING", raising=False)
    notebook._embedder = FakeEmbedder()
    notebook._notebooks.clear()
    yield
    notebook._notebooks.clear()
    notebook._embedder = Embedder()  # unconfigured; next use re-configures
