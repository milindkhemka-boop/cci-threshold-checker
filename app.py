"""
CCI Threshold Checker — local Flask app.

Run:  python3 app.py   (or ./run.sh)
Then open http://127.0.0.1:5057

Privacy: uploaded financials are parsed in memory and never written to disk.
Only public RBI reference-rate history is stored (data/rates.db).
"""

import json
import os
import traceback
from datetime import date

from flask import (Flask, render_template, request, jsonify, redirect, url_for, flash)

from core import db, rates as rates_mod, thresholds as th, fx, extract, assistant
from core import numbers as N
from core import llm, assessor, retrieval


def render_markdown(text):
    import markdown
    return markdown.markdown(text or "", extensions=["extra", "sane_lists", "nl2br"])

app = Flask(__name__)
app.secret_key = "cci-threshold-checker-local"  # local single-user; not security-sensitive
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload cap


def _avg(as_of=None):
    cfg = th.load_config()
    window = int(cfg["meta"].get("fx_window_days", 183))
    return rates_mod.compute_average(window, as_of=as_of), cfg


@app.context_processor
def inject_globals():
    cfg = th.load_config()
    return {
        "fmt_dual": N.format_dual,
        "fmt_indian": N.format_indian,
        "fmt_western": N.format_western,
        "meta": cfg["meta"],
        "last_scrape_at": db.get_meta("last_scrape_at"),
    }


CATEGORY_LABELS = [
    ("jurisdictional_parties", "Jurisdictional — Parties to the combination"),
    ("jurisdictional_group", "Jurisdictional — Group"),
    ("de_minimis", "Small-Target / De Minimis exemption"),
    ("deal_value", "Deal Value Threshold"),
    ("sbo", "Substantial Business Operations in India (SBOI)"),
]


@app.route("/")
def index():
    return redirect(url_for("rates_page"))


@app.route("/rates")
def rates_page():
    avg, cfg = _avg()
    display = request.args.get("ccy", "USD")
    table = th.build_table(cfg, avg["averages"], display)
    currencies = fx.supported(avg["averages"]) if avg["has_data"] else ["INR", "USD", "EUR", "GBP", "JPY", "AED"]
    grouped = [(label, [r for r in table if r["category"] == key]) for key, label in CATEGORY_LABELS]
    return render_template("index.html", avg=avg, grouped=grouped,
                           display=display, currencies=currencies)


@app.route("/api/bootstrap")
def api_bootstrap():
    """Everything the home page needs to render the table client-side.

    Optional ?as_of=YYYY-MM-DD computes the 6-month average ending on that date.
    ?fetch=1 first scrapes/caches the historical window if coverage is thin.
    """
    as_of = request.args.get("as_of") or None
    ensure_info = None
    if as_of and request.args.get("fetch") in ("1", "true", "yes"):
        try:
            ensure_info = rates_mod.ensure_window(as_of)
        except Exception as exc:
            ensure_info = {"error": str(exc)}
    avg, cfg = _avg(as_of)
    rates = avg["averages"]
    items = []
    for t in cfg["thresholds"]:
        item = {
            "id": t["id"], "category": t["category"], "label": t["label"],
            "citation": t.get("citation", ""), "effective": t.get("effective", ""),
            "note": t.get("note", ""), "india_leg": None,
        }
        if t.get("type") == "ratio":
            item["kind"] = "ratio"
            item["ratio_pct"] = t["ratio_pct"]
            item["statutory"] = f"≥ {t['ratio_pct']}% of global  &  > ₹{N.indian_group(t['abs_value'])} cr"
            item["value_inr"] = None
            item["abs_value_inr"] = th.threshold_inr(t["abs_value"], t["abs_unit"], rates)
        elif t.get("unit") == "PERCENT":
            item["kind"] = "percent"
            item["statutory"] = f"≥ {t['value']}%"
            item["value_inr"] = None
        else:
            item["kind"] = "monetary"
            unit = t["unit"]
            item["value_inr"] = th.threshold_inr(t["value"], unit, rates)
            if unit == "USD_BILLION":
                item["statutory"] = f"USD {t['value']} billion"
            else:
                item["statutory"] = f"₹{N.indian_group(t['value'])} crore"
            if t.get("india_leg"):
                leg = t["india_leg"]
                item["india_leg"] = {
                    "statutory": f"₹{N.indian_group(leg['value'])} crore",
                    "value_inr": th.threshold_inr(leg["value"], leg["unit"], rates),
                }
        items.append(item)
    currencies = fx.supported(rates) if avg["has_data"] else ["INR", "USD", "EUR", "GBP", "JPY", "AED"]
    rate_history = [
        {"date": r["date"], "usd": r.get("usd"), "gbp": r.get("gbp"), "eur": r.get("eur"),
         "jpy": r.get("jpy"), "aed": r.get("aed"), "idr": r.get("idr")}
        for r in avg.get("rows", [])
    ]
    return jsonify({
        "has_data": avg["has_data"], "rates": rates, "latest": avg.get("latest", {}),
        "as_of": avg["as_of"], "is_today": avg.get("is_today", True),
        "today": date.today().isoformat(),
        "from": avg.get("from"), "to": avg.get("to"),
        "n_days": avg["n_days"], "window_days": avg["window_days"],
        "ensure": ensure_info,
        "thresholds": items, "categories": CATEGORY_LABELS, "currencies": currencies,
        "disclaimer": cfg["meta"]["disclaimer"],
        "engine_ready": llm.has_key(),
        "corpus_ready": retrieval.is_ready(),
        "rate_history": rate_history,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    as_of = request.form.get("as_of") or None
    avg, _ = _avg(as_of)
    message = (request.form.get("message") or "").strip()
    try:
        history = json.loads(request.form.get("history") or "[]")
    except (ValueError, TypeError):
        history = []

    # parse any uploaded documents in memory (never stored)
    file_texts, notes = [], []
    for f in request.files.getlist("files"):
        data = f.read()
        text, note = extract.extract_text(f.filename, data)
        if note:
            notes.append(note)
        if text:
            file_texts.append(f"--- Document: {f.filename} ---\n{text[:60000]}")

    if not llm.has_key():
        # FREE mode (no API key): deterministic quick-screen — extracts figures and
        # flags which thresholds are crossed. The AI reasoning engine is optional.
        currency = request.form.get("currency", "INR")
        unit = request.form.get("system", "inr")
        reply = assistant.analyze(message, file_texts, avg["averages"], currency, unit)
        html = reply.get("html", "")
        if notes:
            html += "<p class='chat-note'>" + " ".join(notes) + "</p>"
        html += ("<div class='searches'>Free quick-screen (no AI). This extracts figures and "
                 "flags threshold breaches; it does not reason over case law. Add an Anthropic API "
                 "key (see README) to enable the full notification-assessment engine.</div>")
        if llm.key_status() == "placeholder":
            html += render_markdown(
                "\n\n> **Note:** `config/api_key.txt` still contains the placeholder text "
                "(`sk-ant-YOUR-KEY`), not a real key. Replace it with your actual key from "
                "console.anthropic.com to switch on the AI engine.")
        return jsonify({"html": html, "free_mode": True})

    user_text = message
    if file_texts:
        user_text = (user_text + "\n\n" if user_text else "") + "\n\n".join(file_texts)
    if not user_text.strip():
        user_text = "(The user attached documents without a message. Review them and assess notifiability.)"

    as_of_label = "today (most recent trailing 6-month average)" if avg.get("is_today") else f"{avg['as_of']} (trailing 6-month average ending that date)"
    try:
        result = assessor.assess(history, user_text, avg["averages"], as_of_label)
    except Exception as exc:
        es = str(exc)
        if any(t in es for t in ("authentication_error", "invalid x-api-key", "401")):
            msg = ("**Your API key was rejected (401).** Check that `config/api_key.txt` (or the "
                   "`ANTHROPIC_API_KEY` variable) holds your real key from console.anthropic.com — "
                   "not a placeholder — and that the account has credit.")
        elif any(t in es for t in ("credit", "billing", "quota", "insufficient")):
            msg = ("**The API account is out of credit or over its limit.** Add credit at "
                   "console.anthropic.com (Billing), then try again.")
        else:
            msg = f"**Engine error:** {es}"
        return jsonify({"html": render_markdown(msg), "error": True})

    html = render_markdown(result["markdown"])
    if result.get("searches"):
        uniq = list(dict.fromkeys(s for s in result["searches"] if s))
        if uniq:
            from html import escape as _esc
            html += ("<div class='searches'>Searched the CCI corpus: "
                     + "; ".join(f"<em>{_esc(s)}</em>" for s in uniq) + "</div>")
    if notes:
        html += "<p class='chat-note'>" + " ".join(notes) + "</p>"
    return jsonify({"html": html, "markdown": result["markdown"], "searches": result.get("searches", [])})


@app.route("/refresh-rates", methods=["POST"])
def refresh_rates():
    try:
        summary = rates_mod.scrape_six_months()
        return jsonify({"ok": True, **summary})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "trace": traceback.format_exc()[-1200:]}), 500


@app.route("/api/rates")
def api_rates():
    avg, _ = _avg()
    return jsonify(avg)


@app.route("/thresholds")
def thresholds_view():
    avg, cfg = _avg()
    return render_template("thresholds.html", cfg=cfg,
                           raw=json.dumps(cfg, indent=2, ensure_ascii=False))


@app.route("/thresholds/save", methods=["POST"])
def thresholds_save():
    raw = request.form.get("raw", "")
    try:
        cfg = json.loads(raw)
        th.save_config(cfg)
        flash("Thresholds saved.", "ok")
    except Exception as exc:
        flash(f"Not saved — invalid JSON: {exc}", "error")
    return redirect(url_for("thresholds_view"))


@app.route("/about")
def about():
    avg, cfg = _avg()
    return render_template("about.html", avg=avg, cfg=cfg)


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5057))
    print(f"\n  CCI Threshold Checker running at  http://0.0.0.0:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
