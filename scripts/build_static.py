#!/usr/bin/env python3
"""
Generate a single self-contained static page (public/index.html) — the CCI
thresholds reference: rates + table (currency & numbering toggles, with India-leg
conversions) + methodology (full RBI daily-rate list). No backend, no external
deps beyond Google Fonts. Host it anywhere.

    python3 scripts/build_static.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import rates as R, thresholds as TH, db, numbers as N  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "index.html")
CRORE = 1e7


def build_data():
    cfg = TH.load_config()
    window = int(cfg["meta"]["fx_window_days"])
    avg = R.compute_average(window)
    rates = {k: round(v, 4) for k, v in avg["averages"].items()}
    samples = avg.get("samples", {})

    def clean(label):
        return re.sub(r"\s*\((with India leg|with SBOI|digital)\)", "", label).strip()

    items = []
    for t in cfg["thresholds"]:
        if t.get("type") == "ratio":
            stat, kind, vinr = f"≥ {t['ratio_pct']}% of global & > ₹{t['abs_value']} cr", "ratio", None
        elif t.get("unit") == "PERCENT":
            stat, kind, vinr = f"≥ {t['value']}%", "percent", None
        elif t["unit"] == "USD_BILLION":
            u = avg["averages"].get("USD")
            stat, kind, vinr = f"USD {t['value']} billion", "monetary", (t["value"] * 1e9 * u if u else None)
        else:
            stat, kind, vinr = f"₹{N.indian_group(t['value'])} crore", "monetary", t["value"] * CRORE
        leg_inr = leg_stat = None
        if t.get("india_leg"):
            leg_inr = t["india_leg"]["value"] * CRORE
            leg_stat = f"₹{N.indian_group(t['india_leg']['value'])} crore"
        items.append({"category": t["category"], "label": clean(t["label"]), "statutory": stat,
                      "value_inr": round(vinr, 2) if vinr else None, "kind": kind,
                      "india_leg_inr": round(leg_inr, 2) if leg_inr else None,
                      "india_leg_statutory": leg_stat})

    cats = [["jurisdictional_parties", "Jurisdictional — Parties to the combination"],
            ["jurisdictional_group", "Jurisdictional — Group"],
            ["de_minimis", "Small-target / de minimis exemption"],
            ["deal_value", "Deal value threshold"],
            ["sbo", "Substantial Business Operations in India (SBOI)"]]

    rows = db.get_rates_between(avg["cutoff"], avg["as_of"])
    daily = [{"d": r["date"], "USD": r["usd"], "GBP": r["gbp"], "EUR": r["eur"],
              "JPY": r["jpy"], "AED": r["aed"]} for r in rows]
    first_aed = next((r["d"] for r in daily if r["AED"] is not None), None)

    return {"as_of": avg["as_of"], "window_from": avg["from"], "window_to": avg["to"],
            "n_days": avg["n_days"], "rates": rates, "samples": samples,
            "currencies": ["INR", "USD", "EUR", "GBP", "AED"],
            "thresholds": items, "categories": cats, "daily": daily, "first_aed": first_aed}


def daily_rows_html(daily):
    out = []
    for r in daily:
        def c(v, scale=1):
            return f"{v*scale:.4f}" if v is not None else "—"
        out.append(
            f"<tr><td class='mono'>{r['d']}</td><td class='mono num'>{c(r['USD'])}</td>"
            f"<td class='mono num'>{c(r['GBP'])}</td><td class='mono num'>{c(r['EUR'])}</td>"
            f"<td class='mono num'>{c(r['JPY'],100)}</td><td class='mono num'>{c(r['AED'])}</td></tr>")
    return "\n".join(out)


def avg_row_html(rates):
    def c(k, scale=1):
        v = rates.get(k)
        return f"{v*scale:.4f}" if v is not None else "—"
    return (f"<tr class='avg'><td>Six-month average</td><td class='mono num'>{c('USD')}</td>"
            f"<td class='mono num'>{c('GBP')}</td><td class='mono num'>{c('EUR')}</td>"
            f"<td class='mono num'>{c('JPY',100)}</td><td class='mono num'>{c('AED')}</td></tr>")


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CCI merger-control thresholds</title>
<meta name="description" content="CCI merger-control (Section 5) thresholds converted to any currency on the six-month average of RBI reference rates.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,600&family=IBM+Plex+Mono:wght@500;600&family=IBM+Plex+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--navy:#0b2645;--copper:#bf7c54;--copper2:#a5673f;--copperS:#d6a982;--sageBg:#e7eedb;--sageLine:#cdd9b8;--cream:#f5f1e6;--page:#fffdf8;--page2:#f2ecdd;--line:#e3ddcc;--line2:#d3cbb2;--ink:#0b2645;--ink2:#2a3340;--muted:#787566;
--serif:"Cormorant Garamond",Georgia,serif;--sans:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
*{box-sizing:border-box}body{margin:0;font-family:var(--sans);color:var(--ink2);background:var(--cream);font-size:15px;line-height:1.55}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}a{color:var(--copper2)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}.num{text-align:right;white-space:nowrap}.muted{color:var(--muted)}
header.mh{background:var(--navy);color:#e7ecf2;border-bottom:2px solid var(--copper)}
.mh-in{display:flex;align-items:center;justify-content:space-between;min-height:64px;gap:16px;flex-wrap:wrap}
.brand{font-family:var(--serif);font-weight:600;font-size:22px;color:#fff;display:flex;align-items:center;gap:11px}
.seal{width:26px;height:26px;border:1px solid var(--copperS);border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--copperS);font-family:var(--serif);font-size:14px}
.mh nav a{color:#c2cbd7;font-size:11px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;margin-left:18px;text-decoration:none}
.mh nav a:hover{color:var(--copperS)}
.hero{background:var(--navy);color:#e7ecf2;padding:38px 0 44px}
.eyebrow{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--copperS);font-weight:600}
h1{font-family:var(--serif);font-weight:600;font-size:46px;line-height:1.05;margin:14px 0 0;color:#fff}
h1 .c{color:var(--copper)}
.sub{margin:14px 0 0;max-width:62ch;color:#aeb9c8;font-size:16px}
.rates{margin-top:26px;border:1px solid #1d3a63;border-radius:8px;overflow:hidden}
.rates .rh{padding:10px 16px;background:#0e2a4d;border-bottom:1px solid #1d3a63;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.rates .rh .eyebrow{color:#9fb0c6}.rates .rh span.win{font-size:12px;color:#8fa0b8}
.rgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.rcell{padding:14px 16px;border-right:1px solid #14305a}.rcell:last-child{border-right:0}
.rcell .cc{font-family:var(--mono);font-size:11px;color:var(--copperS);font-weight:600}
.rcell .rv{font-family:var(--mono);font-size:15px;color:#fff;margin-top:3px}
.rcell .rs{font-size:10.5px;color:#8fa0b8;margin-top:2px}
section{padding:40px 0}#thresholds{background:var(--cream)}#methodology{background:var(--page)}
h2{font-family:var(--serif);font-weight:600;font-size:30px;color:var(--ink);margin:0 0 4px}
.dotrule{display:flex;align-items:center;gap:10px;margin:8px 0 22px}.dotrule .ln{height:1px;width:46px;background:var(--copper);opacity:.5}.dotrule .dt{width:7px;height:7px;border-radius:50%;background:var(--copper)}
.toolbar{display:flex;gap:24px;flex-wrap:wrap;margin:0 0 18px}
.tg .lbl{font-size:10px;letter-spacing:1.3px;text-transform:uppercase;color:var(--muted);font-weight:600;display:block;margin-bottom:6px}
.seg{display:inline-flex;background:var(--page2);border:1px solid var(--line2);border-radius:7px;padding:3px}
.seg button{font:inherit;font-size:12px;font-weight:600;cursor:pointer;border:0;background:transparent;color:var(--ink2);padding:6px 12px;border-radius:5px}
.seg button.on{background:var(--navy);color:#fff}
table.t{width:100%;border-collapse:collapse;font-size:14px}
table.t thead th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;padding:6px 12px 10px;border-bottom:1px solid var(--navy)}
table.t thead th:last-child,table.t thead th:nth-child(2){text-align:right}
tr.cat td{padding:18px 12px 6px}
.sch{display:flex;align-items:center;gap:11px;font-family:var(--serif);font-weight:600;font-size:19px;color:var(--ink)}
.sch .d{width:8px;height:8px;border-radius:50%;background:var(--copper);box-shadow:0 0 0 3px var(--page)}
.sch .ln{flex:1;height:1px;background:var(--copper);opacity:.32}
tr.row td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr.row td:first-child{box-shadow:inset 2px 0 var(--copperS)}
.lab{font-size:14.5px;font-weight:600;color:var(--ink)}
.leg{font-size:12px;color:var(--muted);margin-top:4px}
.en{font-family:var(--mono);font-size:12.5px;color:var(--ink2)}
.en .legv{display:block;color:var(--muted);font-size:11.5px;margin-top:4px}
.val{font-family:var(--mono);font-weight:600;font-size:14px;color:var(--ink)}
.val .legv{display:block;color:var(--muted);font-weight:500;font-size:12px;margin-top:4px}
.foot-note{margin-top:14px;font-size:12px;color:var(--muted);font-style:italic;font-family:var(--serif)}
.mtable-wrap{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-top:8px}
table.m{width:100%;border-collapse:collapse;font-size:13px}
table.m thead th{position:sticky;top:0;background:var(--page2);text-align:right;font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);font-weight:600;padding:9px 12px;border-bottom:1px solid var(--line2)}
table.m thead th:first-child{text-align:left}
table.m td{padding:7px 12px;border-bottom:1px solid var(--line)}
table.m tr.avg td{font-weight:600;color:var(--ink);background:var(--sageBg);border-top:2px solid var(--copper);position:sticky;bottom:0}
table.m tr.avg td:first-child{font-family:var(--sans)}
.mcap{font-size:13px;color:var(--ink2);max-width:80ch}
footer{background:var(--navy);color:#aeb9c8;border-top:1px solid var(--copper);padding:22px 0;font-size:12.5px}
footer .dis{font-family:var(--serif);font-style:italic;color:#c8d0db;font-size:14px}
@media(max-width:620px){h1{font-size:33px}h2{font-size:24px}.mh nav a{margin-left:12px}}
</style></head>
<body>
<header class="mh"><div class="wrap mh-in">
  <div class="brand"><span class="seal">C</span>CCI Threshold Checker</div>
  <nav><a href="#thresholds">Thresholds</a><a href="#methodology">Methodology</a></nav>
</div></header>

<div class="hero"><div class="wrap">
  <div class="eyebrow">India · merger control</div>
  <h1>CCI merger-control <span class="c">thresholds</span></h1>
  <p class="sub">Converted to any currency on the average of RBI reference rates over the trailing six months.</p>
  <div class="rates">
    <div class="rh"><span class="eyebrow">Six-month average to __ASOF__</span><span class="win">__N__ RBI publication days · __WFROM__ – __WTO__</span></div>
    <div class="rgrid">__RATECELLS__</div>
  </div>
</div></div>

<section id="thresholds"><div class="wrap">
  <h2>Thresholds</h2><div class="dotrule"><span class="dt"></span><span class="ln"></span></div>
  <div class="toolbar">
    <div class="tg"><span class="lbl">Currency</span><div class="seg" id="ccy">__CCYBTN__</div></div>
    <div class="tg"><span class="lbl">Numbering</span><div class="seg" id="num"><button data-s="inr" class="on">Lakh · Crore</button><button data-s="intl">Million · Billion</button></div></div>
  </div>
  <table class="t"><thead><tr><th>Threshold</th><th>As enacted</th><th id="valhead">Value · INR</th></tr></thead><tbody id="tbody"></tbody></table>
  <p class="foot-note">Indicative reference, current to __ASOF__. Not legal advice.</p>
</div></section>

<section id="methodology"><div class="wrap">
  <h2>Methodology</h2><div class="dotrule"><span class="dt"></span><span class="ln"></span></div>
  <p class="mcap">The figures are the simple average of the daily RBI reference rates over the six months to __ASOF__ (__N__ publication days, __WFROM__ – __WTO__). Rates are INR per one unit, except JPY which is shown per 100. JPY and IDR are normalised from RBI's per-100 / per-10,000 quotes. Source: <a href="https://www.rbi.org.in/scripts/referenceratearchive.aspx">RBI Reference Rate Archive</a>.</p>
  <p class="mcap" style="margin-top:10px"><strong>AED:</strong> RBI began publishing the INR–AED reference rate on __FIRSTAED__; there is no AED rate before that date, so the AED average is computed over __AEDN__ of the __N__ days.</p>
  <div class="mtable-wrap"><table class="m"><thead><tr><th>Date</th><th>USD</th><th>GBP</th><th>EUR</th><th>JPY / 100</th><th>AED</th></tr></thead>
  <tbody>__DAILY__</tbody><tfoot>__AVGROW__</tfoot></table></div>
</div></section>

<footer><div class="wrap"><div class="dis">CCI Threshold Checker — a reference for Indian merger-control thresholds.</div>
<div style="margin-top:6px">Rate data from the RBI Reference Rate Archive. Decision support, not legal advice.</div></div></footer>

<script>
const DATA = __DATA__;
const SYM={INR:"₹",USD:"$",EUR:"€",GBP:"£",AED:"AED "};
let CCY="INR", SYS="inr";
function ind(n){let s=Math.round(Math.abs(n)).toString();const neg=n<0?"-":"";if(s.length<=3)return neg+s;
 const l3=s.slice(-3);let r=s.slice(0,-3),p=[];while(r.length>2){p.unshift(r.slice(-2));r=r.slice(0,-2);}if(r)p.unshift(r);return neg+p.join(",")+","+l3;}
const tr=x=>x.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});
function fInd(v){const a=Math.abs(v),s=v<0?"-":"";if(a>=1e12)return s+tr(a/1e12)+" lakh crore";if(a>=1e7){const c=a/1e7;return s+(c===Math.round(c)?ind(c):tr(c))+" crore";}if(a>=1e5)return s+tr(a/1e5)+" lakh";return s+ind(a);}
function fWest(v){const a=Math.abs(v),s=v<0?"-":"";if(a>=1e12)return s+tr(a/1e12)+" trillion";if(a>=1e9)return s+tr(a/1e9)+" billion";if(a>=1e6)return s+tr(a/1e6)+" million";if(a>=1e3)return s+tr(a/1e3)+" thousand";return s+Math.round(a).toLocaleString("en-US");}
function fv(vinr){if(vinr==null)return "—";let a=vinr;if(CCY!=="INR"){const r=DATA.rates[CCY];if(!r)return "—";a=vinr/r;}return (SYM[CCY]||CCY+" ")+(SYS==="inr"?fInd(a):fWest(a));}
function render(){document.getElementById("valhead").textContent="Value · "+CCY;
 let h="";for(const[k,label]of DATA.categories){const rows=DATA.thresholds.filter(t=>t.category===k);if(!rows.length)continue;
  h+=`<tr class="cat"><td colspan="3"><div class="sch"><span class="d"></span>${label}<span class="ln"></span></div></td></tr>`;
  for(const t of rows){const money=t.kind==="monetary";
   let en=t.statutory, val=money?fv(t.value_inr):`<span class="muted">${t.statutory}</span>`;
   if(t.india_leg_inr!=null){en+=`<span class="legv">India leg · ${t.india_leg_statutory}</span>`;val+=`<span class="legv">India leg · ${fv(t.india_leg_inr)}</span>`;}
   h+=`<tr class="row"><td><div class="lab">${t.label}</div></td><td class="en">${en}</td><td class="val">${val}</td></tr>`;}}
 document.getElementById("tbody").innerHTML=h;}
document.querySelectorAll("#ccy button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#ccy button").forEach(x=>x.classList.remove("on"));b.classList.add("on");CCY=b.dataset.c;render();});
document.querySelectorAll("#num button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#num button").forEach(x=>x.classList.remove("on"));b.classList.add("on");SYS=b.dataset.s;render();});
render();
</script>
</body></html>
"""


def main():
    d = build_data()
    rate_cells = ""
    for cc in ["USD", "EUR", "GBP", "AED"]:
        v = d["rates"].get(cc)
        sub = ("per 1 " + cc) if cc != "AED" else f"per 1 AED · {d['samples'].get('AED','?')} days"
        rate_cells += f"<div class='rcell'><div class='cc'>{cc}</div><div class='rv'>₹{v:.4f}</div><div class='rs'>{sub}</div></div>"
    jpy = d["rates"].get("JPY")
    if jpy:
        rate_cells += f"<div class='rcell'><div class='cc'>JPY</div><div class='rv'>₹{jpy*100:.4f}</div><div class='rs'>per 100 ¥</div></div>"
    ccy_btn = "".join(
        f"<button data-c='{c}' class='{'on' if c=='INR' else ''}'>{c}{(' '+SYM) if False else ''}</button>".replace("SYM", "")
        for c in d["currencies"])
    # cleaner currency buttons with symbols
    sym = {"INR": "INR ₹", "USD": "USD $", "EUR": "EUR €", "GBP": "GBP £", "AED": "AED"}
    ccy_btn = "".join(f"<button data-c='{c}' class='{'on' if c=='INR' else ''}'>{sym[c]}</button>" for c in d["currencies"])

    html = (HTML
            .replace("__ASOF__", d["as_of"]).replace("__N__", str(d["n_days"]))
            .replace("__WFROM__", d["window_from"]).replace("__WTO__", d["window_to"])
            .replace("__RATECELLS__", rate_cells).replace("__CCYBTN__", ccy_btn)
            .replace("__DAILY__", daily_rows_html(d["daily"])).replace("__AVGROW__", avg_row_html(d["rates"]))
            .replace("__FIRSTAED__", d["first_aed"] or "—").replace("__AEDN__", str(d["samples"].get("AED", "?")))
            .replace("__DATA__", json.dumps({"rates": d["rates"], "thresholds": d["thresholds"],
                                             "categories": d["categories"]}, ensure_ascii=False)))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", OUT, "(", os.path.getsize(OUT), "bytes )")
    print("as_of", d["as_of"], "| daily rows", len(d["daily"]), "| AED days", d["samples"].get("AED"))


if __name__ == "__main__":
    main()
