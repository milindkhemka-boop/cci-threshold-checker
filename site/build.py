#!/usr/bin/env python3
"""
Self-contained builder for the public CCI thresholds page.

- Scrapes RBI reference rates (stdlib only) and maintains docs/rates.json
  (a growing daily history). First run seeds from START_DATE; later runs top up.
- Renders docs/index.html: a single static page that bakes in the full history,
  so the in-page DATE TOGGLE can compute the trailing six-month average ending on
  ANY chosen date — entirely client-side. Plus currency + numbering toggles,
  India-leg conversions, and a methodology list of the daily rates for the
  selected window.

Run daily via GitHub Actions (see .github/workflows/daily.yml); commit docs/.
"""

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.parse
from datetime import date, timedelta, datetime
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
RATES_JSON = os.path.join(DOCS, "rates.json")
INDEX_HTML = os.path.join(DOCS, "index.html")

START_DATE = date(2023, 1, 1)          # earliest history to seed
WINDOW_DAYS = 183                       # six months
ARCHIVE = "https://www.rbi.org.in/scripts/referenceratearchive.aspx"
UA = "Mozilla/5.0 (compatible; CCIThresholdsBot/1.0)"
COLUMN_SCALE = {"USD": 1, "GBP": 1, "EUR": 1, "JPY": 100, "AED": 1, "IDR": 10000}

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# Thresholds in their NATIVE units; the page converts using the selected window.
THRESHOLDS = [
    {"category": "jurisdictional_parties", "label": "Parties — Assets in India", "statutory": "₹2,500 crore", "def": {"type": "inr", "crore": 2500}},
    {"category": "jurisdictional_parties", "label": "Parties — Turnover in India", "statutory": "₹7,500 crore", "def": {"type": "inr", "crore": 7500}},
    {"category": "jurisdictional_parties", "label": "Parties — Worldwide Assets", "statutory": "USD 1.25 billion", "def": {"type": "usd", "bn": 1.25}, "leg_crore": 1250, "leg_statutory": "₹1,250 crore"},
    {"category": "jurisdictional_parties", "label": "Parties — Worldwide Turnover", "statutory": "USD 3.75 billion", "def": {"type": "usd", "bn": 3.75}, "leg_crore": 3750, "leg_statutory": "₹3,750 crore"},
    {"category": "jurisdictional_group", "label": "Group — Assets in India", "statutory": "₹10,000 crore", "def": {"type": "inr", "crore": 10000}},
    {"category": "jurisdictional_group", "label": "Group — Turnover in India", "statutory": "₹30,000 crore", "def": {"type": "inr", "crore": 30000}},
    {"category": "jurisdictional_group", "label": "Group — Worldwide Assets", "statutory": "USD 5 billion", "def": {"type": "usd", "bn": 5}, "leg_crore": 1250, "leg_statutory": "₹1,250 crore"},
    {"category": "jurisdictional_group", "label": "Group — Worldwide Turnover", "statutory": "USD 15 billion", "def": {"type": "usd", "bn": 15}, "leg_crore": 3750, "leg_statutory": "₹3,750 crore"},
    {"category": "de_minimis", "label": "Small-target exemption — Target assets in India", "statutory": "₹450 crore", "def": {"type": "inr", "crore": 450}},
    {"category": "de_minimis", "label": "Small-target exemption — Target turnover in India", "statutory": "₹1,250 crore", "def": {"type": "inr", "crore": 1250}},
    {"category": "deal_value", "label": "Deal value threshold", "statutory": "₹2,000 crore", "def": {"type": "inr", "crore": 2000}},
    {"category": "sbo", "label": "SBOI — India turnover ≥ 10% of global & > ₹500 cr", "statutory": "≥ 10% of global & > ₹500 cr", "def": {"type": "ratio", "ratio_pct": 10, "abs_crore": 500}},
    {"category": "sbo", "label": "SBOI — India GMV ≥ 10% of global GMV & > ₹500 cr", "statutory": "≥ 10% of global & > ₹500 cr", "def": {"type": "ratio", "ratio_pct": 10, "abs_crore": 500}},
    {"category": "sbo", "label": "SBOI — India users ≥ 10% of global users", "statutory": "≥ 10%", "def": {"type": "percent"}},
]
CATEGORIES = [
    ["jurisdictional_parties", "Jurisdictional — Parties to the combination"],
    ["jurisdictional_group", "Jurisdictional — Group"],
    ["de_minimis", "Small-target / de minimis exemption"],
    ["deal_value", "Deal value threshold"],
    ["sbo", "Substantial Business Operations in India (SBOI)"],
]


# ----------------------------- scraping -----------------------------
def _http(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Referer": ARCHIVE})
    with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
        return r.read().decode("utf-8", "ignore")


def _hidden(html, name):
    m = re.search(r'id="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else ""


class _T(HTMLParser):
    def __init__(self):
        super().__init__(); self.cell = False; self.row = []; self.rows = []; self.buf = ""
    def handle_starttag(self, t, a):
        if t in ("td", "th"): self.cell = True; self.buf = ""
        elif t == "tr": self.row = []
    def handle_endtag(self, t):
        if t in ("td", "th"): self.cell = False; self.row.append(re.sub(r"\s+", " ", self.buf).strip())
        elif t == "tr" and self.row: self.rows.append(self.row)
    def handle_data(self, d):
        if self.cell: self.buf += d


def scrape_range(frm, to):
    page = _http(ARCHIVE)
    fields = {"__EVENTTARGET": "", "__EVENTARGUMENT": "",
              "__VIEWSTATE": _hidden(page, "__VIEWSTATE"),
              "__VIEWSTATEGENERATOR": _hidden(page, "__VIEWSTATEGENERATOR"),
              "__EVENTVALIDATION": _hidden(page, "__EVENTVALIDATION"),
              "chkAll": "on", "txtFromDate": frm.strftime("%d/%m/%Y"),
              "txtToDate": to.strftime("%d/%m/%Y"), "btnSubmit": " GO "}
    html = _http(ARCHIVE, urllib.parse.urlencode(fields).encode())
    p = _T(); p.feed(html)
    header = next((r for r in p.rows if "DATE" in " ".join(r).upper() and "USD" in " ".join(r).upper()), None)
    if not header:
        return []
    colccy = {i: ccy for i, cell in enumerate(header) for ccy in COLUMN_SCALE if cell.upper().startswith(ccy)}
    out = []
    for r in p.rows:
        if not r or not re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", r[0].strip()):
            continue
        dd, mm, yy = r[0].split("/")
        rec = {"d": f"{yy}-{int(mm):02d}-{int(dd):02d}"}
        ok = False
        for i, ccy in colccy.items():
            if i < len(r):
                try:
                    v = float(r[i].replace(",", ""))
                    if v > 0:
                        rec[ccy] = round(v / COLUMN_SCALE[ccy], 4); ok = True
                except ValueError:
                    pass
        if ok:
            out.append(rec)
    return out


def scrape_into(history, frm, to, chunk=55):
    by_date = {r["d"]: r for r in history}
    cur = frm
    while cur <= to:
        end = min(cur + timedelta(days=chunk), to)
        try:
            for rec in scrape_range(cur, end):
                by_date[rec["d"]] = {**by_date.get(rec["d"], {}), **rec}
        except Exception as e:
            print("  scrape error", cur, end, e)
        cur = end + timedelta(days=1)
        time.sleep(0.6)
    return sorted(by_date.values(), key=lambda r: r["d"])


# ----------------------------- history -----------------------------
def load_history():
    try:
        with open(RATES_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def update_history():
    hist = load_history()
    today = date.today()
    if not hist:
        print("Seeding history from", START_DATE)
        hist = scrape_into(hist, START_DATE, today)
    else:
        last = datetime.strptime(hist[-1]["d"], "%Y-%m-%d").date()
        frm = last - timedelta(days=45)   # backfill recent + revisions
        print("Topping up from", frm)
        hist = scrape_into(hist, frm, today)
    os.makedirs(DOCS, exist_ok=True)
    with open(RATES_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, separators=(",", ":"))
    return hist


# ----------------------------- render -----------------------------
def render(history):
    thr = []
    for t in THRESHOLDS:
        item = {"category": t["category"], "label": t["label"], "statutory": t["statutory"], "def": t["def"]}
        if t.get("leg_crore"):
            item["leg_crore"] = t["leg_crore"]; item["leg_statutory"] = t["leg_statutory"]
        thr.append(item)
    data = {"history": history, "thresholds": thr, "categories": CATEGORIES,
            "window_days": WINDOW_DAYS, "built": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    os.makedirs(DOCS, exist_ok=True)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", INDEX_HTML, "| history", len(history), "days,",
          history[0]["d"], "→", history[-1]["d"])


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CCI merger-control thresholds</title>
<meta name="description" content="CCI merger-control (Section 5) thresholds converted to any currency on the six-month average of RBI reference rates, for any date.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,600&family=IBM+Plex+Mono:wght@500;600&family=IBM+Plex+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--navy:#0d2b52;--navy2:#143a68;--copper:#bf7c54;--copper2:#a5673f;--copperS:#d6a982;--sage:#a9bd8f;--sageBg:#e7eedb;--sageLine:#cdd9b8;--sageInk:#34452c;--cream:#f7f5ef;--page:#ffffff;--page2:#f1ede1;--line:#e3ddcc;--line2:#d3cbb2;--ink:#0d2b52;--ink2:#2a3340;--muted:#787566;
--serif:"Cormorant Garamond",Georgia,serif;--sans:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
*{box-sizing:border-box}body{margin:0;font-family:var(--sans);color:var(--ink2);background:var(--cream);font-size:15px;line-height:1.55}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}a{color:var(--copper2)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}.num{text-align:right;white-space:nowrap}.muted{color:var(--muted)}
header.mh{background:var(--navy);color:#e7ecf2;border-bottom:2px solid var(--copper)}
.mh-in{display:flex;align-items:center;justify-content:space-between;min-height:64px;gap:16px;flex-wrap:wrap}
.brand{font-family:var(--serif);font-weight:600;font-size:23px;color:#fff;display:flex;align-items:center;gap:11px}
.seal{width:26px;height:26px;border:1px solid var(--copperS);border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--copperS);font-family:var(--serif);font-size:14px}
.mh nav a{color:#c2cbd7;font-size:11px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;margin-left:18px;text-decoration:none}
.mh nav a:hover{color:var(--copperS)}
.hero{background:var(--navy);color:#e7ecf2;padding:34px 0 40px;position:relative;
 background-image:repeating-linear-gradient(45deg,rgba(214,169,130,.05) 0 1px,transparent 1px 15px)}
.eyebrow{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--copperS);font-weight:600}
h1{font-family:var(--serif);font-weight:600;font-size:46px;line-height:1.04;margin:12px 0 0;color:#fff}
h1 .c{color:var(--copper)}
.sub{margin:12px 0 0;max-width:62ch;color:#aeb9c8;font-size:16px}
.controls{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;margin-top:22px}
.dt label{display:block;font-size:10px;letter-spacing:1.6px;text-transform:uppercase;color:var(--copperS);font-weight:600;margin-bottom:6px}
.dt input{font:inherit;font-size:14px;padding:8px 11px;border:1px solid #29467a;border-radius:7px;background:#143a68;color:#fff}
.dt .btn{font:inherit;font-size:12px;font-weight:600;color:var(--copperS);background:transparent;border:1px solid #29467a;border-radius:7px;padding:9px 13px;cursor:pointer}
.dt .btn:hover{border-color:var(--copper);color:#fff}
.rates{margin-top:22px;border:1px solid #1d3a63;border-radius:8px;overflow:hidden}
.rates .rh{padding:10px 16px;background:var(--navy2);border-bottom:1px solid #1d3a63;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.rates .rh .eyebrow{color:#9fb0c6}.rates .rh .win{font-size:12px;color:#8fa0b8}
.rgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.rcell{padding:13px 16px;border-right:1px solid #14305a;border-top:1px solid #14305a}
.rcell .cc{font-family:var(--mono);font-size:11px;color:var(--copperS);font-weight:600}
.rcell .rv{font-family:var(--mono);font-size:15px;color:#fff;margin-top:3px}
.rcell .rs{font-size:10.5px;color:#8fa0b8;margin-top:2px}
.divider{height:14px;background-image:radial-gradient(circle,var(--copper) 1.4px,transparent 1.6px);background-size:20px 14px;opacity:.5}
section{padding:38px 0}#thresholds{background:var(--cream)}
#methodology{background:var(--sageBg)}
#methodology h2,#methodology .mcap strong{color:var(--sageInk)}
h2{font-family:var(--serif);font-weight:600;font-size:31px;color:var(--ink);margin:0 0 4px}
.dotrule{display:flex;align-items:center;gap:10px;margin:8px 0 22px}.dotrule .ln{height:1px;width:46px;background:var(--copper);opacity:.5}.dotrule .dt2{width:7px;height:7px;border-radius:50%;background:var(--copper)}
#methodology .dotrule .ln{background:var(--sage)}#methodology .dotrule .dt2{background:var(--copper2)}
.toolbar{display:flex;gap:24px;flex-wrap:wrap;margin:0 0 18px}
.tg .lbl{font-size:10px;letter-spacing:1.3px;text-transform:uppercase;color:var(--muted);font-weight:600;display:block;margin-bottom:6px}
.seg{display:inline-flex;background:var(--page2);border:1px solid var(--line2);border-radius:7px;padding:3px}
.seg button{font:inherit;font-size:12px;font-weight:600;cursor:pointer;border:0;background:transparent;color:var(--ink2);padding:6px 12px;border-radius:5px}
.seg button.on{background:var(--navy);color:#fff}
table.t{width:100%;border-collapse:collapse;font-size:14px}
table.t thead th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;padding:6px 12px 10px;border-bottom:1px solid var(--navy)}
table.t thead th:last-child,table.t thead th:nth-child(2){text-align:right}
tr.cat td{padding:18px 12px 6px}
.sch{display:flex;align-items:center;gap:11px;font-family:var(--serif);font-weight:600;font-size:20px;color:var(--ink)}
.sch .d{width:8px;height:8px;border-radius:50%;background:var(--copper);box-shadow:0 0 0 3px var(--cream)}
.sch .ln{flex:1;height:1px;background:var(--copper);opacity:.32}
tr.row td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr.row td:first-child{box-shadow:inset 2px 0 var(--sage)}
.lab{font-size:14.5px;font-weight:600;color:var(--ink)}
.en{font-family:var(--mono);font-size:12.5px;color:var(--ink2)}
.en .legv,.val .legv{display:block;color:var(--muted);font-size:11.5px;margin-top:4px;font-weight:500}
.val{font-family:var(--mono);font-weight:600;font-size:14px;color:var(--ink)}
.foot-note{margin-top:14px;font-size:12px;color:var(--muted);font-style:italic;font-family:var(--serif)}
.mcap{font-size:13px;color:var(--ink2);max-width:80ch}
.mtable-wrap{max-height:480px;overflow:auto;border:1px solid var(--sageLine);border-radius:8px;margin-top:12px;background:var(--page)}
table.m{width:100%;border-collapse:collapse;font-size:13px}
table.m thead th{position:sticky;top:0;background:var(--page2);text-align:right;font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);font-weight:600;padding:9px 12px;border-bottom:1px solid var(--line2)}
table.m thead th:first-child{text-align:left}
table.m td{padding:7px 12px;border-bottom:1px solid var(--line)}
table.m tr.avg td{font-weight:600;color:var(--sageInk);background:var(--sageBg);border-top:2px solid var(--copper);position:sticky;bottom:0}
table.m tr.avg td:first-child{font-family:var(--sans)}
footer{background:var(--navy);color:#aeb9c8;border-top:1px solid var(--copper);padding:22px 0;font-size:12.5px}
footer .dis{font-family:var(--serif);font-style:italic;color:#c8d0db;font-size:14px}
@media(max-width:620px){h1{font-size:32px}h2{font-size:24px}.mh nav a{margin-left:12px}}
</style></head>
<body>
<header class="mh"><div class="wrap mh-in">
  <div class="brand"><span class="seal">C</span>CCI Threshold Checker</div>
  <nav><a href="#thresholds">Thresholds</a><a href="#methodology">Methodology</a></nav>
</div></header>

<div class="hero"><div class="wrap">
  <div class="eyebrow">India · merger control</div>
  <h1>CCI merger-control <span class="c">thresholds</span></h1>
  <p class="sub">Converted to any currency on the average of RBI reference rates over the six months ending on a date you choose.</p>
  <div class="controls">
    <div class="dt"><label for="asof">As-of date</label><input type="date" id="asof"></div>
    <div class="dt"><label>&nbsp;</label><button class="btn" id="latest">Latest</button></div>
  </div>
  <div class="rates">
    <div class="rh"><span class="eyebrow" id="rhead">Six-month average</span><span class="win" id="rwin"></span></div>
    <div class="rgrid" id="rgrid"></div>
  </div>
</div></div>
<div class="divider"></div>

<section id="thresholds"><div class="wrap">
  <h2>Thresholds</h2><div class="dotrule"><span class="dt2"></span><span class="ln"></span></div>
  <div class="toolbar">
    <div class="tg"><span class="lbl">Currency</span><div class="seg" id="ccy"></div></div>
    <div class="tg"><span class="lbl">Numbering</span><div class="seg" id="num"><button data-s="inr" class="on">Lakh · Crore</button><button data-s="intl">Million · Billion</button></div></div>
  </div>
  <table class="t"><thead><tr><th>Threshold</th><th>As enacted</th><th id="valhead">Value · INR</th></tr></thead><tbody id="tbody"></tbody></table>
  <p class="foot-note" id="tnote"></p>
</div></section>

<section id="methodology"><div class="wrap">
  <h2>Methodology</h2><div class="dotrule"><span class="dt2"></span><span class="ln"></span></div>
  <p class="mcap">The figures are the simple average of the daily RBI reference rates over the six months ending on the selected date. Rates are INR per one unit, except JPY shown per 100. JPY and IDR are normalised from RBI's per-100 / per-10,000 quotes. Source: <a href="https://www.rbi.org.in/scripts/referenceratearchive.aspx">RBI Reference Rate Archive</a>.</p>
  <p class="mcap" style="margin-top:8px"><strong>AED:</strong> RBI began publishing the INR–AED reference rate on 22 January 2026; there is no AED rate before that date, so for any window covering earlier dates the AED average is computed over fewer days.</p>
  <div class="mtable-wrap"><table class="m"><thead><tr><th>Date</th><th>USD</th><th>GBP</th><th>EUR</th><th>JPY / 100</th><th>AED</th></tr></thead>
  <tbody id="mbody"></tbody><tfoot id="mfoot"></tfoot></table></div>
</div></section>

<footer><div class="wrap"><div class="dis">CCI Threshold Checker — a reference for Indian merger-control thresholds.</div>
<div style="margin-top:6px" id="built">Rate data from the RBI Reference Rate Archive. Decision support, not legal advice.</div></div></footer>

<script>
const DATA=__DATA__;
const CUR=["INR","USD","EUR","GBP","AED"], SYM={INR:"₹",USD:"$",EUR:"€",GBP:"£",AED:"AED "}, LAB={INR:"INR ₹",USD:"USD $",EUR:"EUR €",GBP:"GBP £",AED:"AED"};
let CCY="INR", SYS="inr", ASOF=DATA.history[DATA.history.length-1].d;
function ind(n){let s=Math.round(Math.abs(n)).toString();const g=n<0?"-":"";if(s.length<=3)return g+s;const l=s.slice(-3);let r=s.slice(0,-3),p=[];while(r.length>2){p.unshift(r.slice(-2));r=r.slice(0,-2);}if(r)p.unshift(r);return g+p.join(",")+","+l;}
const tr=x=>x.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});
function fInd(v){const a=Math.abs(v),s=v<0?"-":"";if(a>=1e12)return s+tr(a/1e12)+" lakh crore";if(a>=1e7){const c=a/1e7;return s+(c===Math.round(c)?ind(c):tr(c))+" crore";}if(a>=1e5)return s+tr(a/1e5)+" lakh";return s+ind(a);}
function fWest(v){const a=Math.abs(v),s=v<0?"-":"";if(a>=1e12)return s+tr(a/1e12)+" trillion";if(a>=1e9)return s+tr(a/1e9)+" billion";if(a>=1e6)return s+tr(a/1e6)+" million";if(a>=1e3)return s+tr(a/1e3)+" thousand";return s+Math.round(a).toLocaleString("en-US");}
function fmtAmt(a){return (SYM[CCY]||CCY+" ")+(SYS==="inr"?fInd(a):fWest(a));}
function addDays(d,n){const t=new Date(d+"T00:00:00Z");t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10);}
function windowFor(asof){const start=addDays(asof,-DATA.window_days);const rows=DATA.history.filter(r=>r.d>start&&r.d<=asof);
 const avg={INR:1},cnt={};for(const c of ["USD","GBP","EUR","JPY","AED"]){const xs=rows.map(r=>r[c]).filter(v=>v!=null);if(xs.length){avg[c]=xs.reduce((a,b)=>a+b,0)/xs.length;cnt[c]=xs.length;}}
 return {rows,avg,cnt,n:rows.length,from:rows.length?rows[0].d:null,to:rows.length?rows[rows.length-1].d:null};}
function inrAmount(t,avg){const d=t.def;if(d.type==="inr")return d.crore*1e7;if(d.type==="usd")return avg.USD?d.bn*1e9*avg.USD:null;return null;}
function render(){const w=windowFor(ASOF);
 document.getElementById("rhead").textContent="Six-month average to "+ASOF;
 document.getElementById("rwin").textContent=w.n+" RBI publication days"+(w.from?" · "+w.from+" – "+w.to:"");
 let rg="";for(const c of ["USD","EUR","GBP","AED"]){const v=w.avg[c];rg+=`<div class="rcell"><div class="cc">${c}</div><div class="rv">${v?"₹"+v.toFixed(4):"—"}</div><div class="rs">per 1 ${c}${c==="AED"&&w.cnt.AED?" · "+w.cnt.AED+" days":""}</div></div>`;}
 if(w.avg.JPY)rg+=`<div class="rcell"><div class="cc">JPY</div><div class="rv">₹${(w.avg.JPY*100).toFixed(4)}</div><div class="rs">per 100 ¥</div></div>`;
 document.getElementById("rgrid").innerHTML=rg;
 document.getElementById("valhead").textContent="Value · "+CCY;
 let h="";for(const[k,label]of DATA.categories){const rows=DATA.thresholds.filter(t=>t.category===k);if(!rows.length)continue;
  h+=`<tr class="cat"><td colspan="3"><div class="sch"><span class="d"></span>${label}<span class="ln"></span></div></td></tr>`;
  for(const t of rows){const money=t.def.type==="inr"||t.def.type==="usd";let en=t.statutory,val;
   if(money){const ia=inrAmount(t,w.avg);val=ia==null?"—":fmtAmt(CCY==="INR"?ia:ia/w.avg[CCY]);}
   else if(t.def.type==="ratio"&&t.def.abs_crore){const ai=t.def.abs_crore*1e7;val=`≥ ${t.def.ratio_pct}% of global &amp; &gt; ${fmtAmt(CCY==="INR"?ai:ai/w.avg[CCY])}`;}
   else{val=`<span class="muted">${t.statutory}</span>`;}
   if(t.leg_crore){const li=t.leg_crore*1e7;en+=`<span class="legv">India leg · ${t.leg_statutory}</span>`;val+=`<span class="legv">India leg · ${fmtAmt(CCY==="INR"?li:li/w.avg[CCY])}</span>`;}
   h+=`<tr class="row"><td><div class="lab">${t.label}</div></td><td class="en">${en}</td><td class="val">${val}</td></tr>`;}}
 document.getElementById("tbody").innerHTML=h;
 document.getElementById("tnote").textContent="Indicative reference for the six months to "+ASOF+". Not legal advice.";
 const f=(v,s)=>v==null?"—":(v*(s||1)).toFixed(4);
 let mb="";for(const r of w.rows.slice().reverse())mb+=`<tr><td class="mono">${r.d}</td><td class="mono num">${f(r.USD)}</td><td class="mono num">${f(r.GBP)}</td><td class="mono num">${f(r.EUR)}</td><td class="mono num">${f(r.JPY,100)}</td><td class="mono num">${f(r.AED)}</td></tr>`;
 document.getElementById("mbody").innerHTML=mb;
 document.getElementById("mfoot").innerHTML=`<tr class="avg"><td>Six-month average</td><td class="mono num">${f(w.avg.USD)}</td><td class="mono num">${f(w.avg.GBP)}</td><td class="mono num">${f(w.avg.EUR)}</td><td class="mono num">${f(w.avg.JPY,100)}</td><td class="mono num">${f(w.avg.AED)}</td></tr>`;}
const ccyBox=document.getElementById("ccy");CUR.forEach(c=>{const b=document.createElement("button");b.dataset.c=c;b.textContent=LAB[c];if(c==="INR")b.className="on";b.onclick=()=>{ccyBox.querySelectorAll("button").forEach(x=>x.classList.remove("on"));b.classList.add("on");CCY=c;render();};ccyBox.appendChild(b);});
document.querySelectorAll("#num button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#num button").forEach(x=>x.classList.remove("on"));b.classList.add("on");SYS=b.dataset.s;render();});
const inp=document.getElementById("asof");const minD=addDays(DATA.history[0].d,DATA.window_days);const maxD=DATA.history[DATA.history.length-1].d;
inp.min=minD;inp.max=maxD;inp.value=maxD;
inp.onchange=()=>{let v=inp.value;if(!v)return;if(v>maxD)v=maxD;if(v<minD)v=minD;inp.value=v;ASOF=v;render();};
document.getElementById("latest").onclick=()=>{inp.value=maxD;ASOF=maxD;render();};
document.getElementById("built").innerHTML="Auto-updated daily from the RBI Reference Rate Archive · last build "+DATA.built+" · decision support, not legal advice.";
render();
</script>
</body></html>
"""


def main():
    hist = update_history()
    if not hist:
        raise SystemExit("No rate history scraped.")
    render(hist)


if __name__ == "__main__":
    main()
