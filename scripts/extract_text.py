#!/usr/bin/env python3
"""
Extract text from the corpus PDFs into the FTS index used by the assessment
engine, and dump the key primary sources to data/primary/ for always-in-context
grounding.

    python3 scripts/extract_text.py --primary    # Act / Regs / FAQ (fast)
    python3 scripts/extract_text.py --corpus      # all order & summary PDFs (long, resumable)
    python3 scripts/extract_text.py --all
    python3 scripts/extract_text.py --status

Resumable: already-indexed files are skipped. Scanned/image-only PDFs that yield
no text are recorded as errors and skipped.
"""

import argparse
import io
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import retrieval  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DB = os.path.join(ROOT, "data", "corpus.db")
PRIMARY_DIR = os.path.join(ROOT, "data", "primary")


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def pdf_text(path):
    from pypdf import PdfReader
    with open(path, "rb") as fh:
        reader = PdfReader(io.BytesIO(fh.read()))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)


def chunk_text(text, words_per_chunk=320, overlap=40):
    text = re.sub(r"[ \t]+", " ", text)
    words = text.split()
    if not words:
        return []
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + words_per_chunk]))
        i += words_per_chunk - overlap
    return chunks


def _corpus_conn():
    c = sqlite3.connect(CORPUS_DB)
    c.row_factory = sqlite3.Row
    return c


def _index_file(abs_path, ref, source_type, kind, log_each=False):
    if retrieval.is_processed(abs_path):
        return 0
    try:
        text = pdf_text(abs_path)
        chunks = chunk_text(text)
        if not chunks:
            retrieval.mark_error(abs_path, ref, source_type, kind, "no extractable text")
            return 0
        retrieval.add_document(ref, source_type, kind, abs_path, chunks)
        if log_each:
            log(f"  indexed {os.path.basename(abs_path)} ({len(chunks)} chunks)")
        return len(chunks)
    except Exception as exc:
        retrieval.mark_error(abs_path, ref, source_type, kind, exc)
        return 0


def do_primary():
    retrieval.init_db()
    os.makedirs(PRIMARY_DIR, exist_ok=True)
    with _corpus_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT category, title, local_path FROM reference_docs WHERE local_path IS NOT NULL")]
    log(f"Primary/reference documents to index: {len(rows)}")
    n = 0
    for r in rows:
        abs_path = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(abs_path):
            continue
        stype = "faq" if r["category"] == "FAQ" else "primary"
        kind = r["category"]
        added = _index_file(abs_path, r["title"] or os.path.basename(abs_path), stype, kind, log_each=True)
        n += added
        # also dump Act + Combination Regulations 2024 to plain text for in-context grounding
        if r["category"] in ("Act", "Regulations") and added:
            try:
                txt = pdf_text(abs_path)
                safe = re.sub(r"[^a-z0-9]+", "_", (r["title"] or "doc").lower())[:60]
                with open(os.path.join(PRIMARY_DIR, f"{safe}.txt"), "w", encoding="utf-8") as f:
                    f.write(txt)
            except Exception:
                pass
    log(f"Primary indexing done (+{n} chunks).")


def do_corpus(limit=None):
    retrieval.init_db()
    with _corpus_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT combination_no, kind, local_path FROM order_files "
            "WHERE status='done' AND local_path IS NOT NULL ORDER BY id")]
    todo = [r for r in rows if not retrieval.is_processed(os.path.join(ROOT, r["local_path"]))]
    if limit:
        todo = todo[:limit]
    log(f"Order/summary PDFs to index: {len(todo)} (of {len(rows)} total)")
    done = 0
    for r in todo:
        abs_path = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(abs_path):
            continue
        _index_file(abs_path, r["combination_no"], r["kind"], r["kind"])
        done += 1
        if done % 50 == 0:
            log(f"  …{done}/{len(todo)} files indexed")
    log(f"Corpus indexing pass complete (+{done} files).")


def show_status():
    s = retrieval.status()
    print("── FTS index status ─────────────────────────")
    print(f"  Total chunks   : {s['total_chunks']}")
    print(f"  By source type : {s['by_source_type']}")
    print(f"  Files done     : {s['files_done']}  (errors: {s['files_error']})")
    print("─────────────────────────────────────────────")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", action="store_true")
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.primary or a.all:
        do_primary()
    if a.corpus or a.all:
        do_corpus(limit=a.limit)
    if a.status or not any([a.primary, a.corpus, a.all]):
        show_status()


if __name__ == "__main__":
    main()
