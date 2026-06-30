"""
Full-text retrieval over the CCI corpus (primary sources + combination orders).

A standalone SQLite FTS5 index (data/corpus_fts.db) holds chunked text from the
Act / Regulations / FAQ and every combination order & summary PDF. The
assessment engine searches it (via a tool call) for issues the primary sources
don't squarely resolve. Kept separate from corpus.db so long index builds don't
lock the catalogue.
"""

import os
import re
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FTS_DB = os.path.join(ROOT, "data", "corpus_fts.db")

# text is the only indexed column; the rest are metadata (UNINDEXED).
_COL_TEXT_INDEX = 5  # ref, source_type, kind, path, chunk_no, text


def _conn():
    os.makedirs(os.path.dirname(FTS_DB), exist_ok=True)
    c = sqlite3.connect(FTS_DB, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "ref UNINDEXED, source_type UNINDEXED, kind UNINDEXED, "
            "path UNINDEXED, chunk_no UNINDEXED, text, tokenize='porter unicode61')"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS processed("
            "path TEXT PRIMARY KEY, ref TEXT, source_type TEXT, kind TEXT, "
            "n_chunks INTEGER, status TEXT, error TEXT, ts TEXT)"
        )


def is_processed(path):
    with _conn() as c:
        r = c.execute("SELECT status FROM processed WHERE path=?", (path,)).fetchone()
        return bool(r and r["status"] == "done")


def add_document(ref, source_type, kind, path, chunks):
    """chunks: list[str]. Inserts all chunks and marks the path processed."""
    with _conn() as c:
        for i, ch in enumerate(chunks):
            c.execute(
                "INSERT INTO chunks (ref, source_type, kind, path, chunk_no, text) "
                "VALUES (?,?,?,?,?,?)",
                (ref, source_type, kind, path, i, ch),
            )
        c.execute(
            "INSERT INTO processed (path, ref, source_type, kind, n_chunks, status, ts) "
            "VALUES (?,?,?,?,?, 'done', ?) "
            "ON CONFLICT(path) DO UPDATE SET n_chunks=excluded.n_chunks, status='done', "
            "error=NULL, ts=excluded.ts",
            (path, ref, source_type, kind, len(chunks),
             datetime.utcnow().isoformat(timespec="seconds")),
        )


def mark_error(path, ref, source_type, kind, error):
    with _conn() as c:
        c.execute(
            "INSERT INTO processed (path, ref, source_type, kind, n_chunks, status, error, ts) "
            "VALUES (?,?,?,?,0,'error',?,?) "
            "ON CONFLICT(path) DO UPDATE SET status='error', error=excluded.error",
            (path, ref, source_type, kind, str(error)[:300],
             datetime.utcnow().isoformat(timespec="seconds")),
        )


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_STOP = {"the", "and", "for", "are", "was", "with", "that", "this", "from",
         "have", "has", "would", "which", "under", "any", "all", "its", "into"}


def _fts_query(text):
    """Turn a free-text query into a safe FTS5 OR-query of distinct keywords."""
    seen, terms = set(), []
    for m in _WORD.finditer(text or ""):
        w = m.group(0).lower()
        if w in _STOP or w in seen:
            continue
        seen.add(w)
        terms.append('"' + w.replace('"', "") + '"')
        if len(terms) >= 12:
            break
    return " OR ".join(terms)


def search(query, limit=8, source_types=None):
    """
    Return ranked matches: {ref, source_type, kind, chunk_no, snippet}.
    `source_types` optionally restricts to e.g. ['primary'] or ['order','summary'].
    """
    init_db()
    fq = _fts_query(query)
    if not fq:
        return []
    where = ["chunks MATCH ?"]
    args = [fq]
    if source_types:
        where.append("source_type IN (%s)" % ",".join("?" * len(source_types)))
        args += list(source_types)
    sql = (
        "SELECT ref, source_type, kind, chunk_no, "
        "snippet(chunks, %d, '«', '»', ' … ', 24) AS snippet, bm25(chunks) AS score "
        "FROM chunks WHERE %s ORDER BY bm25(chunks) LIMIT ?"
        % (_COL_TEXT_INDEX, " AND ".join(where))
    )
    args.append(limit)
    with _conn() as c:
        try:
            rows = c.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


def status():
    init_db()
    with _conn() as c:
        def n(q, *a):
            return c.execute(q, a).fetchone()[0]
        by_type = {}
        for r in c.execute("SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type"):
            by_type[r[0]] = r[1]
        return {
            "total_chunks": n("SELECT COUNT(*) FROM chunks"),
            "by_source_type": by_type,
            "files_done": n("SELECT COUNT(*) FROM processed WHERE status='done'"),
            "files_error": n("SELECT COUNT(*) FROM processed WHERE status='error'"),
        }


def is_ready(min_chunks=50):
    try:
        return status()["total_chunks"] >= min_chunks
    except Exception:
        return False
