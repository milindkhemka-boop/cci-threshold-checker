"""
SQLite storage for RBI reference rates.

Only exchange-rate history is persisted (it is public data). Uploaded client
financials are NEVER written here — they are processed in memory and discarded.

All rates are stored normalized to "INR per 1 unit of the foreign currency"
(the scraper de-scales JPY-per-100 and IDR-per-10000 before saving).
"""

import os
import sqlite3
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "rates.db")

CURRENCIES = ["USD", "GBP", "EUR", "JPY", "AED", "IDR"]


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS rates (
                date       TEXT PRIMARY KEY,   -- ISO yyyy-mm-dd
                usd REAL, gbp REAL, eur REAL, jpy REAL, aed REAL, idr REAL,
                source     TEXT,
                fetched_at TEXT
            )
            """
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )


def upsert_rates(rows, source="RBI"):
    """
    rows: list of dicts {date: 'yyyy-mm-dd', USD: float, GBP: ..., ...}
    Returns number of rows written.
    """
    init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    n = 0
    with _conn() as c:
        for r in rows:
            c.execute(
                """
                INSERT INTO rates (date, usd, gbp, eur, jpy, aed, idr, source, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                    usd=excluded.usd, gbp=excluded.gbp, eur=excluded.eur,
                    jpy=excluded.jpy, aed=excluded.aed, idr=excluded.idr,
                    source=excluded.source, fetched_at=excluded.fetched_at
                """,
                (
                    r["date"], r.get("USD"), r.get("GBP"), r.get("EUR"),
                    r.get("JPY"), r.get("AED"), r.get("IDR"), source, now,
                ),
            )
            n += 1
    # meta writes use their own connections — keep them outside the write txn above
    set_meta("last_scrape_at", now)
    set_meta("last_scrape_count", str(n))
    return n


def get_rates_since(iso_date):
    with _conn() as c:
        cur = c.execute(
            "SELECT * FROM rates WHERE date >= ? ORDER BY date ASC", (iso_date,)
        )
        return [dict(r) for r in cur.fetchall()]


def get_rates_between(start_iso, end_iso):
    with _conn() as c:
        cur = c.execute(
            "SELECT * FROM rates WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (start_iso, end_iso),
        )
        return [dict(r) for r in cur.fetchall()]


def count_between(start_iso, end_iso):
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM rates WHERE date >= ? AND date <= ?",
            (start_iso, end_iso),
        ).fetchone()[0]


def get_all_rates():
    with _conn() as c:
        cur = c.execute("SELECT * FROM rates ORDER BY date ASC")
        return [dict(r) for r in cur.fetchall()]


def get_latest_date():
    with _conn() as c:
        cur = c.execute("SELECT MAX(date) AS d FROM rates")
        row = cur.fetchone()
        return row["d"] if row and row["d"] else None


def count_rows():
    with _conn() as c:
        cur = c.execute("SELECT COUNT(*) AS n FROM rates")
        return cur.fetchone()["n"]


def set_meta(key, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key, default=None):
    with _conn() as c:
        cur = c.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default
