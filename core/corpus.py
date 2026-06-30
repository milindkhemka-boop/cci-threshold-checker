"""
CCI legal-document corpus: builds and stores a local library of

  * reference statutes / regulations / notifications / FAQ booklet, and
  * every combination case listed on cci.gov.in, with its order & summary PDFs.

Data model (SQLite, data/corpus.db):
    reference_docs   one row per statute/regulation/notification/FAQ PDF
    combinations     one row per combination case (C-YYYY/MM/NNNN)
    order_files      one row per PDF attached to a combination (order/summary/media)

Files are saved under data/corpus/{orders,summaries,media,reference}/.
Everything is idempotent and resumable: indexing upserts, downloading skips
files already fetched. Stdlib only.
"""

import json
import os
import re
import ssl
import time
import hashlib
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CORPUS_DB = os.path.join(DATA_DIR, "corpus.db")
FILES_DIR = os.path.join(DATA_DIR, "corpus")

BASE = "https://www.cci.gov.in/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 (KHTML, like Gecko)"

# Combination listing endpoints (DataTables server-side JSON on the same URL).
COMBINATION_CATEGORIES = [
    "orders-section31",                 # main: orders & notices (~1440)
    "orders-section43a_44",             # penalty orders
    "cases-approved-with-modification",
    "notice-under-review",
]

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get(url, binary=False, ajax=False, retries=3, timeout=60):
    headers = {"User-Agent": USER_AGENT, "Referer": BASE}
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "ignore")
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(CORPUS_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    for sub in ("orders", "summaries", "media", "reference"):
        os.makedirs(os.path.join(FILES_DIR, sub), exist_ok=True)
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS reference_docs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, title TEXT, url TEXT UNIQUE, filename TEXT,
            local_path TEXT, file_size INTEGER, sha256 TEXT,
            status TEXT DEFAULT 'pending', http_code INTEGER,
            error TEXT, fetched_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS combinations(
            id INTEGER PRIMARY KEY,
            combination_no TEXT, order_type TEXT, form_type TEXT, section_id TEXT,
            party_name TEXT, notification_date TEXT, decision_date TEXT,
            date_of_order TEXT, order_status TEXT, order_status_id TEXT,
            detail_url TEXT, source_category TEXT, indexed_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS order_files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            combination_id INTEGER, combination_no TEXT,
            kind TEXT, title TEXT, url TEXT UNIQUE, filename TEXT,
            local_path TEXT, file_size INTEGER, sha256 TEXT,
            status TEXT DEFAULT 'pending', http_code INTEGER,
            error TEXT, fetched_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_of_status ON order_files(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_of_comb ON order_files(combination_id)")


def set_meta(key, value):  # reuse a tiny kv via reference_docs? keep simple file
    pass


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _parse_file_content(raw):
    """
    The CCI '*_file_content' fields are HTML-entity-encoded JSON arrays like
    [{"title": "...", "file_name": "images/.../order123.pdf", "file_size": "..."}].
    Returns list of (title, rel_path, size_kb). Falls back to regex if needed.
    """
    out = []
    if not raw or str(raw).strip() in ("", "None", "null"):
        return out
    txt = unescape(str(raw))
    items = None
    try:
        items = json.loads(txt)
    except Exception:
        items = None
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            fn = (it.get("file_name") or "").strip()
            if fn.lower().endswith(".pdf"):
                out.append((it.get("title") or "", fn, it.get("file_size") or ""))
    else:
        for fn in re.findall(r'images/[^"\\\s]+\.pdf', txt):
            out.append(("", fn, ""))
    return out


def _abs_url(rel):
    rel = rel.lstrip("/")
    return urllib.parse.urljoin(BASE, rel)


def _strip_tags(s):
    return re.sub(r"<[^>]+>", " ", unescape(str(s or ""))).strip()


# --------------------------------------------------------------------------- #
# Indexing combinations
# --------------------------------------------------------------------------- #
def index_category(category, page_size=100, log=print):
    init_db()
    url0 = f"{BASE}combination/{category}"
    first = json.loads(http_get(f"{url0}?draw=1&start=0&length=1", ajax=True))
    total = int(first.get("recordsTotal", 0))
    log(f"  [{category}] total records: {total}")
    seen_combos, new_files = 0, 0
    start = 0
    while start < total:
        url = f"{url0}?draw=1&start={start}&length={page_size}"
        data = json.loads(http_get(url, ajax=True))
        rows = data.get("data", [])
        if not rows:
            break
        with _conn() as c:
            for rec in rows:
                cid = rec.get("id")
                if cid is None:
                    continue
                c.execute("""INSERT INTO combinations
                    (id, combination_no, order_type, form_type, section_id, party_name,
                     notification_date, decision_date, date_of_order, order_status,
                     order_status_id, detail_url, source_category, indexed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                     order_status=excluded.order_status, decision_date=excluded.decision_date,
                     order_type=excluded.order_type""",
                    (cid, rec.get("combination_no"), rec.get("order_type"),
                     rec.get("form_type"), str(rec.get("section_id")),
                     rec.get("party_name"), rec.get("notification_date"),
                     rec.get("decision_date"), rec.get("date_of_order"),
                     rec.get("order_status"), str(rec.get("order_status_id")),
                     _detail_url(rec, category), category,
                     datetime.utcnow().isoformat(timespec="seconds")))
                seen_combos += 1
                for field, kind in (("order_file_content", "order"),
                                    ("summary_file_content", "summary"),
                                    ("media_file_content", "media")):
                    for title, rel, size in _parse_file_content(rec.get(field)):
                        url_abs = _abs_url(rel)
                        try:
                            size_i = int(float(size)) if size else None
                        except ValueError:
                            size_i = None
                        cur = c.execute("""INSERT OR IGNORE INTO order_files
                            (combination_id, combination_no, kind, title, url, filename,
                             file_size, status)
                            VALUES (?,?,?,?,?,?,?, 'pending')""",
                            (cid, rec.get("combination_no"), kind, title, url_abs,
                             os.path.basename(rel), size_i))
                        if cur.rowcount:
                            new_files += 1
        start += page_size
        log(f"    indexed {min(start, total)}/{total}  (+{new_files} files so far)")
        time.sleep(0.3)
    return seen_combos, new_files


def _detail_url(rec, category):
    # build the public 'View Documents' detail link if discoverable
    m = re.search(r'href="([^"]+)"', str(rec.get("summary_files") or rec.get("order_files") or ""))
    if m:
        return m.group(1)
    cid = rec.get("id")
    return f"{BASE}combination/order/details/summary/{cid}/0/{category}"


def index_all(log=print):
    init_db()
    totals = {}
    for cat in COMBINATION_CATEGORIES:
        try:
            totals[cat] = index_category(cat, log=log)
        except Exception as exc:
            log(f"  [{cat}] ERROR: {exc}")
            totals[cat] = ("err", str(exc))
    return totals


# --------------------------------------------------------------------------- #
# Reference documents
# --------------------------------------------------------------------------- #
def _add_reference(category, title, url, log=print):
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO reference_docs (category, title, url, filename, status) "
            "VALUES (?,?,?,?, 'pending')",
            (category, title, url, os.path.basename(urllib.parse.urlparse(url).path)))
        return cur.rowcount


def index_reference(log=print):
    init_db()
    added = 0

    # 1) Act page — direct PDF links
    try:
        html = http_get(f"{BASE}combination/legal-framwork/act")
        for rel in sorted(set(re.findall(r'images/[^"\']+\.pdf', html))):
            added += _add_reference("Act", os.path.basename(rel), _abs_url(rel), log)
    except Exception as exc:
        log(f"  act page error: {exc}")

    # 2) Regulations & Notifications — DataTables JSON with file_content
    for path, cat in (("legal-framwork/regulations", "Regulations"),
                      ("legal-framwork/notifications", "Notifications")):
        try:
            url0 = f"{BASE}combination/{path}"
            meta = json.loads(http_get(f"{url0}?draw=1&start=0&length=1", ajax=True))
            total = int(meta.get("recordsTotal", 0))
            data = json.loads(http_get(f"{url0}?draw=1&start=0&length={max(total,1)}", ajax=True))
            for rec in data.get("data", []):
                title = rec.get("title") or ""
                files = _parse_file_content(rec.get("file_content"))
                if not files:  # some rows keep the link in a 'files' html blob
                    for rel in re.findall(r'images/[^"\']+\.pdf', str(rec.get("files") or "")):
                        files.append((title, rel, ""))
                for ftitle, rel, _ in files:
                    added += _add_reference(cat, ftitle or title, _abs_url(rel), log)
        except Exception as exc:
            log(f"  {cat} error: {exc}")

    # 3) FAQ booklet (and other well-known booklets)
    for url, title in (
        (f"{BASE}pdfs/FAQ_Book_English.pdf", "CCI FAQ Booklet (English)"),
        (f"{BASE}pdfs/FAQ_Book_Hindi.pdf", "CCI FAQ Booklet (Hindi)"),
    ):
        added += _add_reference("FAQ", title, url, log)

    log(f"  reference docs queued: +{added}")
    return added


# Comprehensive CCI legal framework (site-wide, not just the combination subset):
# DataTables endpoints return HTML-entity-encoded JSON with file_content links.
_LF_JSON = [
    ("legal-framwork/fetch-regulationslist", "Regulation"),
    ("legal-framwork/fetch-ruleslist", "Rule"),
    ("legal-framwork/notifications", "Notification"),
]


def index_legal_framework(log=print):
    """Queue EVERY CCI rule, regulation, notification and the Act (site-wide)."""
    init_db()
    added = 0

    # Act — direct PDF links on the page
    try:
        html = http_get(f"{BASE}legal-framwork/act")
        for rel in sorted(set(re.findall(r'images/[^"\']+\.pdf', html))):
            added += _add_reference("Act", os.path.basename(rel), _abs_url(rel), log)
    except Exception as exc:
        log(f"  legal-framwork/act error: {exc}")

    for path, cat in _LF_JSON:
        try:
            url0 = f"{BASE}{path}"
            meta = json.loads(http_get(f"{url0}?draw=1&start=0&length=1", ajax=True))
            total = int(meta.get("recordsTotal", 0))
            data = json.loads(http_get(f"{url0}?draw=1&start=0&length={max(total, 1)}", ajax=True))
            n = 0
            for rec in data.get("data", []):
                title = _strip_tags(rec.get("description") or rec.get("title") or "")
                files = _parse_file_content(rec.get("file_content"))
                if not files:
                    for rel in re.findall(r'images/[^"\']+\.pdf', str(rec.get("files") or "")):
                        files.append((title, rel, ""))
                for ftitle, rel, _ in files:
                    n += _add_reference(cat, _strip_tags(ftitle) or title, _abs_url(rel), log)
            added += n
            log(f"  [{cat}] {total} records → +{n} files queued")
        except Exception as exc:
            log(f"  {cat} ({path}) error: {exc}")

    log(f"  legal framework queued: +{added}")
    return added


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #
def _save(url, dest_dir, filename):
    os.makedirs(dest_dir, exist_ok=True)
    data = http_get(url, binary=True)
    path = os.path.join(dest_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    sha = hashlib.sha256(data).hexdigest()
    return os.path.relpath(path, ROOT), len(data), sha


def download_reference(limit=None, log=print):
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM reference_docs WHERE status!='done' "
                         + ("LIMIT ?" if limit else ""),
                         ((limit,) if limit else ())).fetchall()
    done = 0
    for r in rows:
        try:
            fn = r["filename"] or f"ref_{r['id']}.pdf"
            rel, size, sha = _save(r["url"], os.path.join(FILES_DIR, "reference"), fn)
            with _conn() as c:
                c.execute("UPDATE reference_docs SET status='done', local_path=?, "
                          "file_size=?, sha256=?, http_code=200, error=NULL, fetched_at=? WHERE id=?",
                          (rel, size, sha, datetime.utcnow().isoformat(timespec="seconds"), r["id"]))
            done += 1
            log(f"  [ref] {fn}  ({size//1024} KB)")
        except Exception as exc:
            with _conn() as c:
                c.execute("UPDATE reference_docs SET status='error', error=? WHERE id=?",
                          (str(exc)[:300], r["id"]))
            log(f"  [ref] FAIL {r['url']}: {exc}")
        time.sleep(0.2)
    return done


def download_orders(limit=None, log=print, sleep=0.25):
    init_db()
    sub = {"order": "orders", "summary": "summaries", "media": "media"}
    with _conn() as c:
        rows = c.execute("SELECT * FROM order_files WHERE status='pending' "
                         + ("LIMIT ?" if limit else ""),
                         ((limit,) if limit else ())).fetchall()
    done, fail = 0, 0
    for r in rows:
        try:
            rel, size, sha = _save(r["url"], os.path.join(FILES_DIR, sub.get(r["kind"], "orders")),
                                   r["filename"] or f"file_{r['id']}.pdf")
            with _conn() as c:
                c.execute("UPDATE order_files SET status='done', local_path=?, file_size=?, "
                          "sha256=?, http_code=200, error=NULL, fetched_at=? WHERE id=?",
                          (rel, size, sha, datetime.utcnow().isoformat(timespec="seconds"), r["id"]))
            done += 1
            if done % 25 == 0:
                log(f"  downloaded {done} (latest {r['filename']}, {size//1024} KB)")
        except Exception as exc:
            with _conn() as c:
                c.execute("UPDATE order_files SET status='error', error=?, fetched_at=? WHERE id=?",
                          (str(exc)[:300], datetime.utcnow().isoformat(timespec="seconds"), r["id"]))
            fail += 1
            log(f"  FAIL {r['url']}: {str(exc)[:120]}")
        time.sleep(sleep)
    return done, fail


# --------------------------------------------------------------------------- #
# Stats / queries (used by the web UI)
# --------------------------------------------------------------------------- #
def stats():
    init_db()
    with _conn() as c:
        def one(q, *a):
            return c.execute(q, a).fetchone()[0]
        return {
            "combinations": one("SELECT COUNT(*) FROM combinations"),
            "order_files_total": one("SELECT COUNT(*) FROM order_files"),
            "order_files_done": one("SELECT COUNT(*) FROM order_files WHERE status='done'"),
            "order_files_pending": one("SELECT COUNT(*) FROM order_files WHERE status='pending'"),
            "order_files_error": one("SELECT COUNT(*) FROM order_files WHERE status='error'"),
            "reference_total": one("SELECT COUNT(*) FROM reference_docs"),
            "reference_done": one("SELECT COUNT(*) FROM reference_docs WHERE status='done'"),
            "bytes_done": one("SELECT COALESCE(SUM(file_size),0) FROM order_files WHERE status='done'"),
        }


def list_reference():
    init_db()
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM reference_docs ORDER BY category, title").fetchall()]


def search_combinations(q="", status="", limit=200, offset=0):
    init_db()
    where, args = [], []
    if q:
        where.append("(combination_no LIKE ? OR party_name LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if status:
        where.append("order_status = ?")
        args.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM combinations {clause}", args).fetchone()[0]
        rows = c.execute(
            f"""SELECT c.*,
                  (SELECT COUNT(*) FROM order_files o WHERE o.combination_id=c.id) AS n_files,
                  (SELECT COUNT(*) FROM order_files o WHERE o.combination_id=c.id AND o.status='done') AS n_done
                FROM combinations c {clause}
                ORDER BY c.id DESC LIMIT ? OFFSET ?""",
            args + [limit, offset]).fetchall()
        return total, [dict(r) for r in rows]


def files_for_combination(cid):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM order_files WHERE combination_id=? ORDER BY kind", (cid,)).fetchall()]


def get_order_file(fid):
    with _conn() as c:
        r = c.execute("SELECT * FROM order_files WHERE id=?", (fid,)).fetchone()
        return dict(r) if r else None


def get_reference(rid):
    with _conn() as c:
        r = c.execute("SELECT * FROM reference_docs WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def distinct_statuses():
    with _conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT order_status FROM combinations WHERE order_status!='' "
            "ORDER BY order_status").fetchall()]
