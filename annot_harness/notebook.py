"""Loop built-in notebook: cross-session LLM memory (rag_design.md).

Two tools are exposed through the dispatcher as type="builtin":
    - write_note:      append one note to notes.jsonl
    - retrieve_notes:  semantic search over notes with BM25 fallback

Design constraints:
    - The loop stores and retrieves plain text but never parses note content.
    - Storage path priority: RAG_NOTES_DIR > <project-dir>/notes.jsonl.
    - Vector index: **Chroma** persistent vector DB, stored in a notes_chroma/
      directory next to notes.jsonl. notes.jsonl stays the source of truth;
      Chroma is an append-only projection (double-write on write_note,
      reconcile-on-read). Embeddings come from the pluggable Embedder below.
    - Embedding provider is pluggable via RAG_EMBEDDING:
          "" (default)      Chroma + local sentence-transformers model
          "local:<model>"   Chroma + the given local model
          "off"/"none"/"bm25"  disable Chroma entirely (keyword scoring only)
      On any Chroma / embedding failure retrieval degrades to BM25 over
      notes.jsonl automatically — the notebook tools never fail.
    - Notes are append-only NDJSON; retrieval reloads the file so notes
      written by other processes/sessions are visible.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# tokenization / BM25 (dependency-free keyword fallback)
# ---------------------------------------------------------------------------

def tokenize(text: str) -> List[str]:
    """Tokenize mixed latin + CJK text: latin words plus individual CJK chars."""
    tokens = [m.group().lower() for m in _WORD_RE.finditer(text or "")]
    tokens += [ch for ch in (text or "") if _CJK_RE.match(ch)]
    return tokens


def _bm25_scores(query_tokens: List[str], doc_tokens: List[List[str]]) -> List[float]:
    """Classic BM25 (k1=1.5, b=0.75) with +1 idf smoothing."""
    n_docs = len(doc_tokens)
    if n_docs == 0:
        return []
    doc_freq = Counter()
    for toks in doc_tokens:
        for t in set(toks):
            doc_freq[t] += 1
    avgdl = sum(len(t) for t in doc_tokens) / n_docs
    k1, b = 1.5, 0.75
    scores = []
    for toks in doc_tokens:
        tf = Counter(toks)
        dl = len(toks)
        score = 0.0
        for t in set(query_tokens):
            if t in tf:
                idf = math.log((n_docs - doc_freq[t] + 0.5) / (doc_freq[t] + 0.5) + 1.0)
                score += idf * (tf[t] * (k1 + 1.0)) / (tf[t] + k1 * (1.0 - b + b * dl / max(avgdl, 1.0)))
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# pluggable embedding provider (sentence-transformers local by default)
# ---------------------------------------------------------------------------

class Embedder:
    """Lazy-loaded, failure-tolerant embedding provider.

    Loading is local-first (local_files_only=True): in offline environments a
    cached model loads instantly and a missing model fails fast, so retrieval
    degrades to BM25 without long network retries. A plain online attempt is
    made only when the user explicitly requested a non-default RAG_EMBEDDING.
    """

    def __init__(self) -> None:
        self.model = None
        self.model_name: Optional[str] = None
        self._explicit = False
        self._configured = False
        self._configured_spec = ""

    def configure(self, spec: str) -> None:
        """Configure from RAG_EMBEDDING value.

        '' / unset  -> local model (default) + Chroma
        'off'/'none'/'bm25' -> embeddings disabled (BM25 only)
        'local[:<model>]'   -> the given local model + Chroma
        anything else       -> treat as a bare model name
        Online download is attempted only for an explicitly requested model
        that is not cached locally; the default stays offline-safe.
        """
        spec = (spec or "").strip().lower()
        if self._configured and spec == self._configured_spec:
            return
        self._configured_spec = spec
        self._configured = True
        self.model = None
        self.model_name = None
        self._explicit = False
        if spec in ("off", "none", "bm25"):
            return
        if spec in ("", "local"):
            model = DEFAULT_EMBEDDING_MODEL
        elif spec.startswith("local:"):
            model = spec.split(":", 1)[1].strip() or DEFAULT_EMBEDDING_MODEL
        else:
            model = spec
            self._explicit = True
        if not self.load(model):
            self.model = None
            self.model_name = None

    def load(self, model_name: str) -> bool:
        import logging

        logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            return False
        try:
            # local-first: fast and offline-safe
            self.model = SentenceTransformer(model_name, local_files_only=True)
            self.model_name = model_name
            return True
        except Exception:
            pass
        if self._explicit and ":" not in model_name and "local:" not in model_name:
            try:
                self.model = SentenceTransformer(model_name)
                self.model_name = model_name
                return True
            except Exception:
                pass
        return False

    def available(self) -> bool:
        return self.model is not None

    def encode(self, texts: List[str]):
        """Return normalized embeddings or None on any failure."""
        if self.model is None:
            return None
        try:
            return self.model.encode(list(texts), normalize_embeddings=True)
        except Exception:
            return None


_embedder = Embedder()


def get_embedder() -> Embedder:
    """Return the global embedder, auto-initialized on first use."""
    if not _embedder._configured:
        _embedder.configure(os.environ.get("RAG_EMBEDDING", ""))
    return _embedder


# ---------------------------------------------------------------------------
# note storage
# ---------------------------------------------------------------------------

@dataclass
class Note:
    note_id: str
    ts: str
    session_id: str
    project_id: str
    tags: List[str] = field(default_factory=list)
    content: str = ""


class Notebook:
    """Append-only note store backed by notes.jsonl (NDJSON).

    notes.jsonl is the source of truth; a Chroma persistent vector index
    (VectorIndex, one directory next to notes.jsonl) is kept in sync as an
    append-only projection for semantic retrieval. Retrieval prefers Chroma
    and falls back to BM25 over notes.jsonl whenever Chroma / embeddings are
    unavailable.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.notes: List[Note] = []
        self.index = VectorIndex(path, get_embedder())

    # -- storage -----------------------------------------------------------

    def _load(self) -> None:
        self.notes = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.notes.append(
                    Note(
                        note_id=rec.get("note_id", ""),
                        ts=rec.get("ts", ""),
                        session_id=rec.get("session_id", ""),
                        project_id=rec.get("project_id", ""),
                        tags=list(rec.get("tags") or []),
                        content=rec.get("content", ""),
                    )
                )

    def _next_note_id(self) -> str:
        max_seq = 0
        for note in self.notes:
            m = re.match(r"note-(\d+)", note.note_id or "")
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"note-{max_seq + 1:04d}"

    def _append(self, note: Note) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "note_id": note.note_id,
            "ts": note.ts,
            "session_id": note.session_id,
            "project_id": note.project_id,
            "tags": note.tags,
            "content": note.content,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.notes.append(note)

    # -- builtin tool implementations --------------------------------------

    def write(self, args: dict, state: dict) -> dict:
        """write_note tool: append one note, return its note_id."""
        self._load()
        content = str(args.get("content", "")).strip()
        if not content:
            return {"status": "error", "error": "note content is empty"}
        note = Note(
            note_id=self._next_note_id(),
            ts=utcnow_iso(),
            session_id=str(state.get("session_id", "")),
            project_id=Path(str(state.get("project_dir", ""))).name,
            tags=[str(t) for t in (args.get("tags") or [])],
            content=content,
        )
        self._append(note)
        self.index.add(note)  # Chroma upsert; no-op when index unavailable
        return {"status": "ok", "data": {"note_id": note.note_id}}

    def retrieve(self, args: dict, state: dict) -> dict:
        """retrieve_notes tool: top-k notes by semantic (Chroma) relevance.

        Falls back to BM25 over notes.jsonl when the vector index or the
        embedding provider is unavailable, or when the query returns nothing.
        """
        self._load()
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 5) or 5)
        tags = [str(t) for t in (args.get("tags") or [])]

        candidates = [n for n in self.notes if not tags or set(tags) <= set(n.tags)]
        if not candidates:
            return {"status": "ok", "data": {"notes": []}}

        chroma_hits = self.index.query(query, top_k, tags)
        if chroma_hits is not None:
            return {"status": "ok", "data": {"notes": chroma_hits}}

        # BM25 fallback over notes.jsonl
        scores = _bm25_scores(tokenize(query), [tokenize(n.content) for n in candidates])
        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        notes_out = [
            {
                "note_id": note.note_id,
                "content": note.content,
                "tags": note.tags,
                "ts": note.ts,
                "session_id": note.session_id,
                "score": round(float(score), 4),
            }
            for note, score in ranked[:top_k]
        ]
        return {"status": "ok", "data": {"notes": notes_out}}


# ---------------------------------------------------------------------------
# Chroma persistent vector index (projection of notes.jsonl)
# ---------------------------------------------------------------------------

_COLLECTION_NAME = "notes"
_INDEX_VERSION = 2  # bump when metadata schema changes (triggers rebuild)


def _chroma_dir_for(notes_path: Path) -> Path:
    """Chroma persist dir sits next to notes.jsonl (shared via RAG_NOTES_DIR)."""
    return notes_path.parent / "notes_chroma"


class VectorIndex:
    """Chroma-backed append-only vector index over notes.

    Write path: upsert the new note (id = note_id, metadata carries ts /
    session_id / project_id / tags). Read path: cosine similarity query with
    optional tag filter. Any failure (chromadb missing, embedding provider
    down, query error) returns None so the caller falls back to BM25 — the
    index must never make the notebook tools fail.
    """

    def __init__(self, notes_path: Path, embedder: Embedder) -> None:
        self.notes_path = notes_path
        self.embedder = embedder
        self._client = None
        self._collection = None

    # -- lifecycle ---------------------------------------------------------

    def _ensure(self):
        """Lazily open the Chroma client + collection; None on any failure."""
        if self._collection is not None:
            return self._collection
        if not self.embedder.available():
            return None
        try:
            import chromadb
        except Exception:
            return None
        try:
            self._client = chromadb.PersistentClient(path=str(_chroma_dir_for(self.notes_path)))
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine", "index_version": _INDEX_VERSION},
            )
            return self._collection
        except Exception:
            self._client = None
            self._collection = None
            return None

    def _reset(self) -> None:
        try:
            if self._client is not None and self._collection is not None:
                self._client.delete_collection(_COLLECTION_NAME)
            self._collection = (
                self._client.create_collection(
                    name=_COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine", "index_version": _INDEX_VERSION},
                )
                if self._client is not None
                else None
            )
        except Exception:
            self._collection = None

    def _meta_of(self, note: Note) -> dict:
        meta = {
            "ts": note.ts,
            "session_id": note.session_id,
            "project_id": note.project_id,
        }
        # list value so Chroma $contains matches any single tag; Chroma rejects
        # empty lists, so omit the key entirely when there are no tags
        if note.tags:
            meta["tags"] = list(note.tags)
        return meta

    # -- write -------------------------------------------------------------

    def add(self, note: Note) -> None:
        """Upsert one note into the index; silently no-op on failure."""
        coll = self._ensure()
        if coll is None:
            return
        try:
            emb = self.embedder.encode([note.content])
            if emb is None:
                return
            coll.upsert(
                ids=[note.note_id],
                embeddings=emb.tolist(),
                documents=[note.content],
                metadatas=[self._meta_of(note)],
            )
        except Exception:
            return

    def sync(self, n_notes: int) -> None:
        """Rebuild the index when it is out of sync with notes.jsonl.

        Cheap at notebook scale (tens to hundreds of notes); keeps the index
        usable when notes.jsonl was edited or the collection was deleted.
        """
        coll = self._ensure()
        if coll is None or n_notes == 0:
            return
        try:
            meta = coll.metadata or {}
            if coll.count() == n_notes and meta.get("index_version") == _INDEX_VERSION:
                return
        except Exception:
            return
        self._reset()
        if self._collection is None:
            return
        try:
            notes = [n for n in self._load_notes()]
            embs = self.embedder.encode([n.content for n in notes])
            if embs is None:
                return
            self._collection.upsert(
                ids=[n.note_id for n in notes],
                embeddings=embs.tolist(),
                documents=[n.content for n in notes],
                metadatas=[self._meta_of(n) for n in notes],
            )
        except Exception:
            return

    def _load_notes(self) -> List[Note]:
        notes = []
        if self.notes_path.exists():
            for line in self.notes_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                notes.append(
                    Note(
                        note_id=rec.get("note_id", ""),
                        ts=rec.get("ts", ""),
                        session_id=rec.get("session_id", ""),
                        project_id=rec.get("project_id", ""),
                        tags=list(rec.get("tags") or []),
                        content=rec.get("content", ""),
                    )
                )
        return notes

    # -- read --------------------------------------------------------------

    def query(self, query: str, top_k: int, tags: List[str]):
        """Return top-k hits as list of dicts, or None to signal fallback."""
        coll = self._ensure()
        if coll is None:
            return None
        try:
            self.sync(len(self._load_notes()))
        except Exception:
            pass
        coll = self._ensure()  # re-fetch: sync() may have rebuilt the collection
        if coll is None:
            return None
        try:
            q = self.embedder.encode([query])
            if q is None:
                return None
            where = None
            if tags:
                clauses = [{"tags": {"$contains": t}} for t in tags]
                where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
            res = coll.query(
                query_embeddings=q.tolist(),
                n_results=top_k,
                where=where,
                include=["metadatas", "distances"],
            )
        except Exception:
            return None

        ids = res.get("ids") or [[]]
        ids = ids[0] if ids and ids[0] else []
        if not ids:
            return []
        distances = (res.get("distances") or [[]])[0]
        metas = (res.get("metadatas") or [None])[0]

        by_id = {n.note_id: n for n in self._load_notes()}
        hits = []
        for i, note_id in enumerate(ids):
            meta = metas[i] if metas and i < len(metas) else {}
            meta = meta or {}
            note = by_id.get(note_id)
            hits.append(
                {
                    "note_id": note_id,
                    "content": note.content if note else "",
                    "tags": note.tags if note else [],
                    "ts": meta.get("ts") or (note.ts if note else ""),
                    "session_id": meta.get("session_id") or (note.session_id if note else ""),
                    "score": round(float(1.0 - distances[i]), 4),
                }
            )
        return hits


# ---------------------------------------------------------------------------
# module-level registry: one Notebook per resolved path (shared across sessions)
# ---------------------------------------------------------------------------

_notebooks: Dict[str, Notebook] = {}


def get_notebook(path) -> Notebook:
    key = str(Path(path).resolve())
    if key not in _notebooks:
        _notebooks[key] = Notebook(Path(path))
    return _notebooks[key]


def write_note(args: dict, state: dict) -> dict:
    return get_notebook(state["notes_path"]).write(args, state)


def retrieve_notes(args: dict, state: dict) -> dict:
    return get_notebook(state["notes_path"]).retrieve(args, state)


# dispatch target for the loop dispatcher (type="builtin")
NOTEBOOK_FUNCS = {"write_note": write_note, "retrieve_notes": retrieve_notes}


def init_embedder() -> None:
    """Configure the global embedder from RAG_EMBEDDING (called at session start)."""
    get_embedder().configure(os.environ.get("RAG_EMBEDDING", ""))
