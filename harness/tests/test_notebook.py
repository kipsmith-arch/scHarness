"""Notebook tests: BM25 fallback, Chroma index, tag filters, degradation.

Chroma tests use the deterministic FakeEmbedder from conftest — no real
sentence-transformers model is loaded, so the suite runs offline and fast.
The real-embedder degradation path (embedding unavailable -> BM25) is covered
via FakeEmbedder(available=False).
"""

from __future__ import annotations

import json
import shutil

import pytest

from harness import notebook
from harness.notebook import Note, tokenize

from .conftest import FakeEmbedder, make_session_state


def write(project, content, tags=None, session="sess-a"):
    state = make_session_state(project)
    state["session_id"] = session
    return notebook.write_note(
        {"content": content, "tags": tags or []},
        state,
    )


def retrieve(project, query, top_k=5, tags=None):
    return notebook.retrieve_notes(
        {"query": query, "top_k": top_k, **({"tags": tags} if tags else {})},
        make_session_state(project),
    )


# ---------------------------------------------------------------------------
# tokenization / BM25
# ---------------------------------------------------------------------------

def test_tokenize_mixed_latin_cjk():
    toks = tokenize("silhouette 0.15 聚类质量差")
    assert "silhouette" in toks
    assert "0" in toks and "15" in toks
    assert "聚" in toks and "类" in toks and "差" in toks


def test_bm25_ranks_shared_tokens_first(tmp_path):
    notebook._embedder = FakeEmbedder(available=False)  # force BM25
    p = tmp_path / "p"
    write(p, "leiden 分辨率 1.0 出现小簇,降到 0.6 后合理", tags=["clustering"])
    write(p, "叶肉细胞 marker 用 CAB2/RBCS;QC 看叶绿体基因占比", tags=["markers"])
    r = retrieve(p, "分辨率 小簇 怎么办", top_k=2)
    notes = r["data"]["notes"]
    assert notes[0]["note_id"] == "note-0001"  # shares 分辨率/簇 tokens
    assert notes[0]["score"] > 0


def test_bm25_tag_filter(tmp_path):
    notebook._embedder = FakeEmbedder(available=False)
    p = tmp_path / "p"
    write(p, "笔记A 聚类内容", tags=["clustering"])
    write(p, "笔记B 聚类内容", tags=["markers"])
    r = retrieve(p, "聚类", tags=["markers"])
    assert [n["note_id"] for n in r["data"]["notes"]] == ["note-0002"]


# ---------------------------------------------------------------------------
# Chroma index (FakeEmbedder)
# ---------------------------------------------------------------------------

def test_write_creates_jsonl_and_chroma(tmp_path):
    p = tmp_path / "p"
    write(p, "经验:silhouette 低先查发育谱", tags=["qc"])
    assert (p / "notes.jsonl").exists()
    assert (p / "notes_chroma").exists()
    nb = notebook.get_notebook(str(p / "notes.jsonl"))
    assert nb.index._collection.count() == 1


def test_chroma_retrieve_returns_cosine_scores(tmp_path):
    p = tmp_path / "p"
    write(p, "leiden 分辨率 1.0 出现小簇,降到 0.6 后合理", tags=["clustering"])
    write(p, "叶肉细胞 marker 用 CAB2;QC 看叶绿体占比", tags=["markers"])
    r = retrieve(p, "叶绿体 QC 质控", top_k=2)
    notes = r["data"]["notes"]
    assert len(notes) == 2
    for n in notes:
        assert 0.0 <= n["score"] <= 1.0  # cosine similarity
    assert notes[0]["note_id"] == "note-0002"  # semantically closer


def test_chroma_tag_filter_single_and_multi(tmp_path):
    p = tmp_path / "p"
    write(p, "内容A", tags=["clustering", "SRP171040"])
    write(p, "内容B", tags=["markers"])
    single = retrieve(p, "内容", tags=["markers"])
    assert [n["note_id"] for n in single["data"]["notes"]] == ["note-0002"]
    multi = retrieve(p, "内容", tags=["clustering", "SRP171040"])
    assert [n["note_id"] for n in multi["data"]["notes"]] == ["note-0001"]


def test_chroma_cross_session_persistence(tmp_path):
    """Notes written in one session are visible to a later one (same dir)."""
    p = tmp_path / "p"
    write(p, "旧会话经验:高分辨率易出小簇", session="sess-1")
    r = retrieve(p, "小簇 分辨率 经验")
    assert r["data"]["notes"][0]["session_id"] == "sess-1"


def test_chroma_rebuild_after_index_loss(tmp_path):
    p = tmp_path / "p"
    write(p, "经验A", tags=["a"])
    write(p, "经验B", tags=["b"])
    # simulate a lost index by dropping the collection (Windows keeps the
    # on-disk files locked by the open client, so delete via the client)
    nb = notebook.get_notebook(str(p / "notes.jsonl"))
    nb.index._client.delete_collection("notes")
    nb.index._collection = None
    r = retrieve(p, "经验A", top_k=2)
    assert [n["note_id"] for n in r["data"]["notes"]] == ["note-0001", "note-0002"]
    assert nb.index._collection.count() == 2


def test_chroma_sync_rebuilds_on_mismatch(tmp_path):
    """Hand-edited jsonl (external write) is picked up by the index."""
    p = tmp_path / "p"
    write(p, "经验A", tags=["a"])
    # simulate an external append to the authoritative jsonl
    with open(p / "notes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"note_id": "note-0099", "ts": "2026-01-01T00:00:00Z",
                            "session_id": "ext", "project_id": "ext",
                            "tags": ["ext"], "content": "外部写入的经验"}, ensure_ascii=False) + "\n")
    r = retrieve(p, "外部写入 经验", top_k=3)
    assert any(n["note_id"] == "note-0099" for n in r["data"]["notes"])


def test_chroma_version_mismatch_triggers_rebuild(tmp_path):
    """A collection created by an older index schema must be rebuilt."""
    p = tmp_path / "p"
    write(p, "经验A", tags=["a"])
    nb = notebook.get_notebook(str(p / "notes.jsonl"))
    client = nb.index._client
    client.delete_collection("notes")
    client.create_collection(name="notes", metadata={"hnsw:space": "cosine"})  # no index_version
    nb.index._collection = None
    r = retrieve(p, "经验A")
    assert r["data"]["notes"][0]["note_id"] == "note-0001"
    assert nb.index._collection.metadata.get("index_version") == notebook._INDEX_VERSION


# ---------------------------------------------------------------------------
# degradation / edge cases
# ---------------------------------------------------------------------------

def test_embedding_unavailable_falls_back_to_bm25(tmp_path):
    """Simulates offline / missing model: tools must still work."""
    notebook._embedder = FakeEmbedder(available=False)
    p = tmp_path / "p"
    write(p, "silhouette 0.15 时先查发育连续谱,别急着调分辨率", tags=["clustering"])
    r = retrieve(p, "分辨率 调整", top_k=2)
    assert r["status"] == "ok"
    assert r["data"]["notes"][0]["note_id"] == "note-0001"


def test_no_notes_returns_empty(tmp_path):
    p = tmp_path / "p"
    r = retrieve(p, "任何查询")
    assert r == {"status": "ok", "data": {"notes": []}}


def test_write_empty_content_rejected(tmp_path):
    p = tmp_path / "p"
    r = write(p, "   ")
    assert r["status"] == "error"
    assert "empty" in r["error"]


def test_note_ids_monotonic(tmp_path):
    p = tmp_path / "p"
    ids = [write(p, f"内容{i}")["data"]["note_id"] for i in range(3)]
    assert ids == ["note-0001", "note-0002", "note-0003"]


def test_write_without_tags_indexed(tmp_path):
    """Regression: Chroma rejects empty-list metadata; tagless notes must still index."""
    p = tmp_path / "p"
    write(p, "无标签的经验", tags=[])
    nb = notebook.get_notebook(str(p / "notes.jsonl"))
    assert nb.index._collection.count() == 1
    r = retrieve(p, "无标签的经验")
    assert r["data"]["notes"][0]["note_id"] == "note-0001"


def test_note_fields_round_trip(tmp_path):
    p = tmp_path / "p"
    write(p, "经验内容", tags=["a", "b"])
    nb = notebook.get_notebook(str(p / "notes.jsonl"))
    note: Note = nb.notes[0]
    assert note.tags == ["a", "b"]
    assert note.session_id == "sess-a"
    assert note.project_id == p.name
    assert note.ts  # non-empty timestamp
