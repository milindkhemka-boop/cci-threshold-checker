"""
Scraper for RBI reference rates + the CCI 6-month-average computation.

Source: RBI Reference Rate Archive
        https://www.rbi.org.in/scripts/referenceratearchive.aspx

The archive is an ASP.NET postback form. We GET the page to harvest the hidden
state fields (__VIEWSTATE etc.), then POST a date range with the "All currencies"
checkbox to receive a table of daily rates for USD, GBP, EUR, JPY, AED, IDR.

JPY is quoted per 100 units and IDR per 10000 units in the RBI table; we
normalize everything to "INR per 1 unit" before returning.

Only the Python standard library is used (urllib + html.parser) so the scraper
has no third-party dependencies.
"""

import re
import ssl
import time
import urllib.request
import urllib.parse
from datetime import date, timedelta, datetime
from html.parser import HTMLParser

from . import db


def _as_date(v):
    """Coerce None / date / 'YYYY-MM-DD' string into a date (or None)."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

ARCHIVE_URL = "https://www.rbi.org.in/scripts/referenceratearchive.aspx"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 (KHTML, like Gecko)"

# RBI table column -> (currency code, units quoted per row)
COLUMN_SCALE = {"USD": 1, "GBP": 1, "EUR": 1, "JPY": 100, "AED": 1, "IDR": 10000}

_SSL_CTX = ssl.create_default_context()
# RBI's chain occasionally trips strict verification on older macOS Python builds;
# fall back to unverified so the daily job is resilient. (Public reference data.)
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _http(url, data=None):
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, "Referer": ARCHIVE_URL}
    )
    with urllib.request.urlopen(req, timeout=40, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def _hidden(html, name):
    m = re.search(r'id="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
    if not m:
        m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else ""


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_cell = False
        self._row = []
        self.rows = []
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in_cell = True
            self._buf = ""
        elif tag == "tr":
            self._row = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._row.append(re.sub(r"\s+", " ", self._buf).strip())
        elif tag == "tr" and self._row:
            self.rows.append(self._row)

    def handle_data(self, data):
        if self._in_cell:
            self._buf += data


_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _parse_rate_rows(html):
    """Return list of normalized {date, USD, GBP, EUR, JPY, AED, IDR} (INR per 1 unit)."""
    parser = _TableParser()
    parser.feed(html)

    # Find the header row to map columns to currency codes.
    header = None
    for row in parser.rows:
        joined = " ".join(row).upper()
        if "DATE" in joined and "USD" in joined:
            header = row
            break
    if not header:
        return []

    col_ccy = {}
    for i, cell in enumerate(header):
        for ccy in COLUMN_SCALE:
            if cell.upper().startswith(ccy):
                col_ccy[i] = ccy
    date_col = 0
    for i, cell in enumerate(header):
        if cell.strip().upper() == "DATE":
            date_col = i
            break

    out = []
    for row in parser.rows:
        if len(row) <= date_col:
            continue
        m = _DATE_RE.match(row[date_col].strip())
        if not m:
            continue
        dd, mm, yyyy = m.groups()
        iso = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
        rec = {"date": iso}
        ok = False
        for i, ccy in col_ccy.items():
            if i >= len(row):
                continue
            raw = row[i].replace(",", "").strip()
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue
            if val <= 0:
                continue
            rec[ccy] = val / COLUMN_SCALE[ccy]  # normalize to INR per 1 unit
            ok = True
        if ok:
            out.append(rec)
    return out


def scrape_range(from_date, to_date):
    """Scrape one date window (date objects). Returns list of normalized rate dicts."""
    page = _http(ARCHIVE_URL)
    fields = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _hidden(page, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(page, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(page, "__EVENTVALIDATION"),
        "chkAll": "on",
        "txtFromDate": from_date.strftime("%d/%m/%Y"),
        "txtToDate": to_date.strftime("%d/%m/%Y"),
        "btnSubmit": " GO ",
    }
    html = _http(ARCHIVE_URL, urllib.parse.urlencode(fields).encode())
    return _parse_rate_rows(html)


def scrape_range_into_db(from_date, to_date, chunk_days=55, sleep=0.6):
    """Scrape an arbitrary [from_date, to_date] window in chunks and upsert it."""
    db.init_db()
    all_rows = {}
    errors = []
    cursor = from_date
    while cursor <= to_date:
        chunk_end = min(cursor + timedelta(days=chunk_days), to_date)
        try:
            for rec in scrape_range(cursor, chunk_end):
                all_rows[rec["date"]] = rec
        except Exception as exc:  # keep going; partial data is still useful
            errors.append(f"{cursor}..{chunk_end}: {exc}")
        cursor = chunk_end + timedelta(days=1)
        time.sleep(sleep)  # be polite to RBI
    rows = list(all_rows.values())
    written = db.upsert_rates(rows, source="RBI") if rows else 0
    return {"written": written, "fetched": len(rows), "errors": errors}


def scrape_six_months(window_days=190, chunk_days=55):
    """Scrape ~6 months of history up to today and upsert it."""
    today = date.today()
    s = scrape_range_into_db(today - timedelta(days=window_days), today, chunk_days)
    s["latest"] = db.get_latest_date()
    s["total_in_db"] = db.count_rows()
    return s


def ensure_window(as_of, window_days=183, min_fraction=0.8):
    """
    Make sure the DB has enough RBI rates to average the 6 months ending on
    `as_of`. If coverage is thin (e.g. a historical date we've never fetched),
    scrape that window from the RBI archive and cache it. Idempotent.
    """
    end = _as_date(as_of) or date.today()
    if end > date.today():
        end = date.today()
    start = end - timedelta(days=window_days)
    have = db.count_between(start.isoformat(), end.isoformat())
    expected = max(int(window_days * 0.66), 1)  # ~ RBI publication days in the window
    scraped = None
    if have < int(expected * min_fraction):
        scraped = scrape_range_into_db(start - timedelta(days=10), end)
        have = db.count_between(start.isoformat(), end.isoformat())
    return {"as_of": end.isoformat(), "from": start.isoformat(),
            "have_days": have, "expected": expected, "scraped": scraped}


def compute_average(window_days=183, as_of=None):
    """
    The CCI conversion rate: the average of RBI reference rates over the 6 months
    ending on `as_of` (default today). Computed purely from stored data — call
    ensure_window() first if you need historical coverage fetched.
    """
    end = _as_date(as_of) or date.today()
    if end > date.today():
        end = date.today()
    start = end - timedelta(days=window_days)
    rows = db.get_rates_between(start.isoformat(), end.isoformat())
    result = {
        "window_days": window_days,
        "cutoff": start.isoformat(),
        "as_of": end.isoformat(),
        "is_today": end == date.today(),
        "n_days": len(rows),
        "from": rows[0]["date"] if rows else None,
        "to": rows[-1]["date"] if rows else None,
        "averages": {},   # ccy -> INR per 1 unit (the CCI rate)
        "latest": {},     # ccy -> last daily rate in the window (for reference)
        "samples": {},    # ccy -> count of non-null observations
        "min": {},
        "max": {},
        "has_data": bool(rows),
        "rows": rows,     # the daily RBI rows the average was computed from
    }
    for ccy in db.CURRENCIES:
        key = ccy.lower()
        vals = [r[key] for r in rows if r.get(key) is not None]
        if vals:
            result["averages"][ccy] = sum(vals) / len(vals)
            result["latest"][ccy] = rows[-1].get(key)
            result["samples"][ccy] = len(vals)
            result["min"][ccy] = min(vals)
            result["max"][ccy] = max(vals)
    return result
