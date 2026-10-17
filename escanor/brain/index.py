"""
brain/index.py

Search over the brain: Markdown notes are split into chunks at headings,
stored in SQLite with an FTS5 keyword index, and (when an embedder is
available) an embedding per chunk for meaning-based search.

search() runs both and merges them by reciprocal-rank fusion: a chunk
that ranks well on either list rises, one that ranks well on both rises
most. Vector search is a numpy brute-force cosine over all chunks --
milliseconds at personal scale (<50k chunks), and no SQLite extension to
load (IMPLEMENTATION_PLAN.md Phase 3).

sync() is incremental: a file is re-chunked only when its mtime and
content hash change, and chunks of deleted files are dropped. search()
calls it first, so results are never stale.
"""
from __future__ import annotations
import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from .embeddings import Embedder, NoEmbedder

CHUNK_CHARS = 1600
OVERLAP_CHARS = 200
RRF_K = 60
EMBEDDER_RETRY_SECONDS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    source TEXT NOT NULL, path TEXT NOT NULL, mtime REAL NOT NULL, hash TEXT NOT NULL,
    PRIMARY KEY (source, path)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, path TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL,
    vector BLOB, embedder TEXT
);
CREATE INDEX IF NOT EXISTS chunks_by_file ON chunks(source, path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
END;
"""

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass
class Hit:
    source: str     # "brain" or the name of an attached read-only folder
    path: str       # relative to that source
    heading: str
    text: str
    score: float


def chunk_markdown(text: str, chunk_chars: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[tuple[str, str]]:
    """Split a note into (heading path, text) chunks: one per section,
    with long sections cut into overlapping windows. Frontmatter skipped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    sections: list[tuple[str, list[str]]] = [("", [])]
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2)
            stack = [(lvl, t) for lvl, t in stack if lvl < level] + [(level, title)]
            sections.append((" > ".join(t for _, t in stack), []))
        else:
            sections[-1][1].append(line)

    chunks: list[tuple[str, str]] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        start = 0
        while start < len(body):
            piece = body[start:start + chunk_chars]
            chunks.append((heading, piece))
            if start + chunk_chars >= len(body):
                break
            start += chunk_chars - overlap
    return chunks


def _fts_query(query: str) -> str | None:
    words = re.findall(r"\w+", query.lower())
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


class BrainIndex:
    def __init__(self, db_path: Path, sources: dict[str, Path], embedder: Embedder | None = None):
        """`sources` maps a name to a folder: {"brain": vault root, "obsidian": ...}."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self.sources = {name: path.expanduser() for name, path in sources.items()}
        self.embedder: Embedder = embedder or NoEmbedder()
        self._matrix: tuple[np.ndarray, list[int]] | None = None
        # When the embedder fails (Ollama not running), don't retry on every
        # search -- each try would cost a timeout. Keyword search carries on.
        self._embedder_down_until = 0.0

    def _embedder_usable(self) -> bool:
        return not isinstance(self.embedder, NoEmbedder) and time.monotonic() >= self._embedder_down_until

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        vectors = self.embedder.embed(texts)
        if vectors is None:
            self._embedder_down_until = time.monotonic() + EMBEDDER_RETRY_SECONDS
        return vectors

    # ---------- indexing ----------

    def sync(self) -> dict[str, int]:
        """Bring the index up to date. Returns counts of changed/removed files."""
        changed = removed = 0
        for source, root in self.sources.items():
            seen: set[str] = set()
            if root.is_dir():
                for path in sorted(root.rglob("*.md")):
                    if any(part.startswith(".") for part in path.relative_to(root).parts):
                        continue
                    relative = str(path.relative_to(root))
                    seen.add(relative)
                    if self._index_file(source, root, relative):
                        changed += 1
            known = {r[0] for r in self._db.execute("SELECT path FROM files WHERE source = ?", (source,))}
            for relative in known - seen:
                self._drop(source, relative)
                removed += 1
        self._db.commit()
        if changed or removed:
            self._matrix = None
        self._embed_missing()
        return {"changed": changed, "removed": removed}

    def _index_file(self, source: str, root: Path, relative: str) -> bool:
        path = root / relative
        try:
            mtime = path.stat().st_mtime
            row = self._db.execute("SELECT mtime, hash FROM files WHERE source = ? AND path = ?", (source, relative)).fetchone()
            if row and row[0] == mtime:
                return False
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        digest = hashlib.sha256(text.encode()).hexdigest()
        if row and row[1] == digest:
            self._db.execute("UPDATE files SET mtime = ? WHERE source = ? AND path = ?", (mtime, source, relative))
            return False
        self._drop(source, relative)
        title = Path(relative).stem
        for heading, chunk in chunk_markdown(text):
            self._db.execute("INSERT INTO chunks (source, path, heading, text) VALUES (?, ?, ?, ?)",
                             (source, relative, heading or title, chunk))
        self._db.execute("INSERT OR REPLACE INTO files (source, path, mtime, hash) VALUES (?, ?, ?, ?)",
                         (source, relative, mtime, digest))
        return True

    def _drop(self, source: str, relative: str) -> None:
        self._db.execute("DELETE FROM chunks WHERE source = ? AND path = ?", (source, relative))
        self._db.execute("DELETE FROM files WHERE source = ? AND path = ?", (source, relative))

    def _embed_missing(self, batch: int = 32) -> None:
        """Embed chunks that have no vector (or one from another embedder).
        If the embedder is unavailable, stop quietly: keyword search still works."""
        if not self._embedder_usable():
            return
        rows = self._db.execute(
            "SELECT id, heading, text FROM chunks WHERE vector IS NULL OR embedder IS NOT ?", (self.embedder.name,)
        ).fetchall()
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vectors = self._embed([f"{h}\n{t}" for _, h, t in part])
            if vectors is None:
                return
            for (chunk_id, _, _), vector in zip(part, vectors):
                blob = np.asarray(vector, dtype=np.float32).tobytes()
                self._db.execute("UPDATE chunks SET vector = ?, embedder = ? WHERE id = ?", (blob, self.embedder.name, chunk_id))
            self._db.commit()
            self._matrix = None

    # ---------- search ----------

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        self.sync()
        ranked: dict[int, float] = {}
        for rank, chunk_id in enumerate(self._keyword(query, limit * 4)):
            ranked[chunk_id] = ranked.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, chunk_id in enumerate(self._semantic(query, limit * 4)):
            ranked[chunk_id] = ranked.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:limit]
        hits = []
        for chunk_id, score in best:
            row = self._db.execute("SELECT source, path, heading, text FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row:
                hits.append(Hit(source=row[0], path=row[1], heading=row[2], text=row[3], score=score))
        return hits

    def _keyword(self, query: str, limit: int) -> list[int]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self._db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (fts, limit)
        ).fetchall()
        return [r[0] for r in rows]

    def _semantic(self, query: str, limit: int) -> list[int]:
        if not self._embedder_usable():
            return []
        if self._matrix is None:
            rows = self._db.execute("SELECT id, vector FROM chunks WHERE vector IS NOT NULL AND embedder = ?",
                                    (self.embedder.name,)).fetchall()
            if not rows:
                return []
            matrix = np.vstack([np.frombuffer(blob, dtype=np.float32) for _, blob in rows])
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self._matrix = (matrix / np.where(norms == 0, 1, norms), [r[0] for r in rows])
        vectors = self._embed([query])
        if not vectors:
            return []
        q = np.asarray(vectors[0], dtype=np.float32)
        matrix, ids = self._matrix
        if q.shape[0] != matrix.shape[1]:
            return []
        q = q / (np.linalg.norm(q) or 1)
        scores = matrix @ q
        top = np.argsort(-scores)[:limit]
        return [ids[i] for i in top if scores[i] > 0.2]

    def stats(self) -> dict[str, int]:
        files = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        embedded = self._db.execute("SELECT COUNT(*) FROM chunks WHERE vector IS NOT NULL").fetchone()[0]
        return {"files": files, "chunks": chunks, "embedded": embedded}
