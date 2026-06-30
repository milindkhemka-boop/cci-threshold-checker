"""
Screening assistant — the brain behind the chat panel.

For this first cut it works without an LLM: it reads any uploaded financials and
the typed message, pulls out the figures it recognises (revenue from operations,
total assets, deal value, India/global splits), and screens them against the
thresholds. It also answers a handful of common questions about the thresholds
and the exchange-rate basis.

Deeper, citation-backed answers will come once the CCI document corpus is wired
in; until then the replies are clear about what they are (an indicative screen).
"""

import html
import re

from . import numbers as N, fx, thresholds as TH, extract


# --------------------------------------------------------------------------- #
# formatting that honours the user's currency + numbering toggles
# --------------------------------------------------------------------------- #
def fmt(value_inr, currency, system, rates):
    if value_inr is None:
        return "—"
    amt = value_inr if currency == "INR" else fx.from_inr(value_inr, currency, rates)
    if amt is None:
        return "—"
    sym = N.CURRENCY_SYMBOL.get(currency, currency + " ")
    body = N.format_indian(amt) if system == "inr" else N.format_western(amt)
    return f"{sym}{body}"


# --------------------------------------------------------------------------- #
# figure extraction from message + files
# --------------------------------------------------------------------------- #
def _collect_candidates(text_blobs):
    cands = []
    for t in text_blobs:
        if not t:
            continue
        c, _, _ = extract.find_candidates(t)
        cands += c
    return cands


def _to_inr(value_native, currency, rates):
    if currency == "INR":
        return value_native
    return fx.to_inr(value_native, currency, rates)


def _build_entity(cands, rates):
    """Fold candidate figures into a single entity profile (best-effort)."""
    entity = {"turnover": {}, "assets": {}, "deal_value": None, "users_pct": None}
    for c in cands:
        v_inr = _to_inr(c["value_native"], c["currency"], rates)
        if v_inr is None:
            continue
        scope = c["scope_hint"] or "india"  # default unscoped figures to India
        metric = c["metric"]
        if metric in ("turnover", "total_revenue"):
            cur = entity["turnover"].get(scope)
            entity["turnover"][scope] = max(cur, v_inr) if cur else v_inr
        elif metric == "total_assets":
            cur = entity["assets"].get(scope)
            entity["assets"][scope] = max(cur, v_inr) if cur else v_inr
        elif metric == "deal_value":
            entity["deal_value"] = v_inr if entity["deal_value"] is None else max(entity["deal_value"], v_inr)
    return entity


def _entity_inputs(entity):
    ti = entity["turnover"].get("india")
    tg = entity["turnover"].get("global")
    ai = entity["assets"].get("india")
    ag = entity["assets"].get("global")
    dv = entity["deal_value"]
    inp = {}
    if ti is not None:
        inp["parties_turnover_india"] = ti
        inp["target_turnover_india"] = ti
    if tg is not None:
        inp["parties_turnover_worldwide"] = tg
    if ai is not None:
        inp["parties_assets_india"] = ai
        inp["target_assets_india"] = ai
    if ag is not None:
        inp["parties_assets_worldwide"] = ag
    if dv is not None:
        inp["deal_value"] = dv
    return inp


# --------------------------------------------------------------------------- #
# intents for figure-free questions
# --------------------------------------------------------------------------- #
def _intent_answer(msg, rates, currency, system):
    m = msg.lower()
    usd = fmt(1e9 * 1.25, currency, system, rates)  # placeholder, replaced per-intent below

    def cr(v):  # ₹ crore value in chosen toggles
        return fmt(v * 1e7, currency, system, rates)

    if any(k in m for k in ("de minimis", "de-minimis", "small target", "small-target")):
        return ("The small-target (de minimis) exemption applies where the <b>target</b> "
                f"has assets in India of {cr(450)} or less, <i>or</i> turnover in India of "
                f"{cr(1250)} or less. It is not available where the deal value exceeds "
                f"{cr(2000)}. Upload the target's accounts and I'll check it.")
    if "deal value" in m or "dvt" in m:
        return ("The Deal Value Threshold catches a transaction where the global deal value "
                f"exceeds {cr(2000)} <b>and</b> the target has Substantial Business Operations "
                "in India (broadly, ≥10% of its global turnover, GMV or users, with a ₹500 crore "
                "floor for turnover/GMV).")
    if any(k in m for k in ("sbo", "substantial business")):
        return ("‘Substantial Business Operations in India’ is met if the target's India turnover "
                f"or GMV is at least 10% of its global figure and above {cr(500)}, or if 10% or "
                "more of its users are in India. It only matters for the Deal Value Threshold.")
    if any(k in m for k in ("exchange", "rbi", "rate", "currency", "conversion")):
        u = rates.get("USD"); e = rates.get("EUR")
        return ("Foreign-currency figures are converted at the average of RBI reference rates over "
                f"the last six months — the basis the CCI uses. Right now that is about "
                f"₹{u:.2f}/USD and ₹{e:.2f}/EUR." if u and e else
                "Foreign-currency figures use the trailing 6-month average of RBI reference rates. "
                "Fetch rates on the dashboard if none are loaded yet.")
    if any(k in m for k in ("group", "parties", "jurisdiction")):
        return ("There are two jurisdictional limbs: the <b>parties</b> (acquirer + target combined) "
                f"and the <b>group</b>. Parties: India assets &gt; {cr(2500)} or turnover &gt; "
                f"{cr(7500)}; or worldwide assets &gt; USD 1.25 bn / turnover &gt; USD 3.75 bn "
                "(each with an India leg). The group limbs are four times higher. The two-party "
                "calculator (coming) handles the aggregation properly.")
    if any(k in m for k in ("help", "what can you", "how do", "hello", "hi ", "hey", "start")):
        return _intro()
    return None


def _intro():
    return ("I can screen a transaction against the CCI merger-control thresholds. "
            "Upload a financial statement, an email or a deal note — or just type the figures, "
            "e.g. <i>“India turnover ₹9,000 crore, total assets ₹3,200 crore, deal value ₹2,500 crore”</i>. "
            "I'll pull out the numbers and tell you which thresholds are crossed. "
            "You can also ask things like <i>“what's the de minimis threshold?”</i> or "
            "<i>“what exchange rate do you use?”</i>")


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #
def analyze(message, file_texts, rates, currency="INR", system="inr"):
    """
    message:    typed text (may be empty)
    file_texts: list of extracted document texts (may be empty)
    returns:    {"html": "<assistant reply>"}
    """
    message = (message or "").strip()
    has_rates = bool(rates)
    cands = _collect_candidates(file_texts + ([message] if message else []))
    entity = _build_entity(cands, rates) if has_rates else {"turnover": {}, "assets": {}, "deal_value": None}
    inputs = _entity_inputs(entity)

    # No figures found → try to answer a question, else intro.
    if not inputs:
        ans = _intent_answer(message, rates, currency, system) if message else _intro()
        if not ans:
            ans = ("I couldn't spot any figures in that. " + _intro())
        return {"html": ans}

    if not has_rates:
        return {"html": "I found figures, but no exchange-rate data is loaded yet — fetch the RBI "
                        "rates on the dashboard first so foreign-currency conversion works."}

    ev = TH.evaluate(inputs, rates)
    res = ev["results"]

    # ----- build the reply -----
    parts = []

    # detected figures
    det = []
    if "parties_turnover_india" in inputs:
        det.append(f"India turnover (revenue from operations): <b>{fmt(inputs['parties_turnover_india'],currency,system,rates)}</b>")
    if "parties_turnover_worldwide" in inputs:
        det.append(f"Worldwide turnover: <b>{fmt(inputs['parties_turnover_worldwide'],currency,system,rates)}</b>")
    if "parties_assets_india" in inputs:
        det.append(f"India assets: <b>{fmt(inputs['parties_assets_india'],currency,system,rates)}</b>")
    if "parties_assets_worldwide" in inputs:
        det.append(f"Worldwide assets: <b>{fmt(inputs['parties_assets_worldwide'],currency,system,rates)}</b>")
    if "deal_value" in inputs:
        det.append(f"Deal value: <b>{fmt(inputs['deal_value'],currency,system,rates)}</b>")
    parts.append("Here's what I read:<ul><li>" + "</li><li>".join(det) + "</li></ul>")

    # jurisdictional limbs this entity alone already crosses
    crossed = [t["label"] for t in TH.load_config()["thresholds"]
               if t["category"] in ("jurisdictional_parties", "jurisdictional_group")
               and res.get(t["id"], {}).get("status") == "BREACH"]
    if crossed:
        parts.append("On its own this entity already meets: <ul><li>"
                     + "</li><li>".join(html.escape(c) for c in crossed)
                     + "</li></ul>A combination involving it would likely cross the jurisdictional "
                       "thresholds — so it would be notifiable unless an exemption applies.")
    else:
        parts.append("On its own, this entity's figures do not cross any single jurisdictional limb "
                     "(but a combination aggregates both parties, so the combined test can still be met).")

    # de minimis view
    dm = []
    if res.get("de_minimis_assets", {}).get("status") == "EXEMPT":
        dm.append("assets in India within the ₹450 cr ceiling")
    if res.get("de_minimis_turnover", {}).get("status") == "EXEMPT":
        dm.append("turnover in India within the ₹1,250 cr ceiling")
    if dm:
        note = (" However, the deal value exceeds ₹2,000 cr, which switches the small-target "
                "exemption off."
                if inputs.get("deal_value", 0) > 2000 * 1e7 else
                " So as a <i>target</i> it may attract the small-target exemption (deal value permitting).")
        parts.append("It is small-target sized (" + "; ".join(dm) + ")." + note)

    # deal value prong
    if inputs.get("deal_value", 0) > 2000 * 1e7:
        parts.append("The deal value is above ₹2,000 cr, so the Deal Value Threshold can bite if the "
                     "target has Substantial Business Operations in India.")

    parts.append("<span class='chat-note'>Indicative screen of a single entity. A full notifiability "
                 "view needs both sides of the deal — the two-party calculator is coming next. "
                 "Not legal advice.</span>")

    return {"html": "".join(f"<p>{p}</p>" for p in parts)}
