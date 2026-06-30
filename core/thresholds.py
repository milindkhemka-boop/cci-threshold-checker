"""
Threshold engine.

Loads the editable config, expresses every threshold in INR (converting USD
thresholds with the CCI 6-month-average rate), evaluates a set of client
financials, and produces:

  * per-threshold flags
      - jurisdictional / deal value  -> BREACH (over)  vs CLEAR
      - small-target (de minimis)    -> EXEMPT (under)  vs NOT EXEMPT
      - SBOI tests                   -> MET vs NOT MET
  * a synthesised "notifiability indicator" that combines the limbs the way the
    Act does (any jurisdictional limb, subject to the small-target exemption,
    OR the deal-value threshold where the target has SBOI).

Monetary inputs reach `evaluate()` already converted to base INR (rupees).
Percent inputs (SBOI user share) are passed as raw numbers.
"""

import json
import os

from . import numbers as N

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "thresholds.json"
)

CRORE = 1e7


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)


# --- value conversions -------------------------------------------------------

def threshold_inr(value, unit, rates):
    """Express a configured threshold value in base INR (rupees)."""
    if unit == "INR_CRORE":
        return value * CRORE
    if unit == "USD_BILLION":
        usd = rates.get("USD")
        return None if not usd else value * 1e9 * usd
    return None  # PERCENT and other non-monetary units


def _cmp(value, threshold, op):
    if value is None or threshold is None:
        return None
    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    return None


# --- display table -----------------------------------------------------------

def build_table(cfg, rates, display_ccy="USD"):
    """
    Return display rows for every threshold: value in INR (both numbering
    systems) and converted to the chosen display currency.
    """
    rows = []
    usd = rates.get("USD")
    disp_rate = 1.0 if display_ccy == "INR" else rates.get(display_ccy)
    for t in cfg["thresholds"]:
        unit = t.get("unit", "")
        row = {
            "id": t["id"], "category": t["category"], "label": t["label"],
            "citation": t.get("citation", ""), "effective": t.get("effective", ""),
            "note": t.get("note", ""), "raw_unit": unit,
        }
        if t.get("type") == "ratio":
            row["native"] = f"≥ {t['ratio_pct']}% of global  &  > {N.format_indian(t['abs_value'] * CRORE)} (₹{t['abs_value']} cr)"
            row["inr"] = None
            row["display"] = "—"
            row["is_ratio"] = True
        elif unit == "PERCENT":
            row["native"] = f"≥ {t['value']}%"
            row["inr"] = None
            row["display"] = "—"
            row["is_percent"] = True
        else:
            inr = threshold_inr(t["value"], unit, rates)
            row["inr"] = inr
            if unit == "USD_BILLION":
                row["native"] = f"USD {t['value']} billion"
            else:
                row["native"] = f"₹{N.western_group(t['value'])} crore"
            row["inr_disp"] = N.format_dual(inr, "INR") if inr else "—"
            if disp_rate and inr is not None:
                conv = inr / disp_rate
                row["display"] = N.format_dual(conv, display_ccy)
            else:
                row["display"] = "—"
            if t.get("india_leg"):
                leg = t["india_leg"]
                leg_inr = threshold_inr(leg["value"], leg["unit"], rates)
                row["india_leg"] = f"India leg: ≥ ₹{N.western_group(leg['value'])} crore"
        rows.append(row)
    return rows


# --- evaluation --------------------------------------------------------------

def _result(status, severity, message, **extra):
    r = {"status": status, "severity": severity, "message": message}
    r.update(extra)
    return r


def evaluate(inputs_inr, rates):
    """
    inputs_inr: dict field_id -> base INR amount (monetary) OR raw number (percent).
                Missing / None fields are treated as "not provided".
    rates:      averages map (currency -> INR per unit).

    Returns {results: {id: {...}}, by_category: {...}, summary: {...}}.
    """
    cfg = load_config()
    g = lambda k: inputs_inr.get(k)
    results = {}

    for t in cfg["thresholds"]:
        tid = t["id"]
        cat = t["category"]

        # --- SBOI ratio tests ---
        if t.get("type") == "ratio":
            india = g(t["field"])
            glob = g(t["global_field"])
            abs_inr = t["abs_value"] * CRORE
            if india is None:
                results[tid] = _result("INSUFFICIENT", "muted",
                                        "India figure not provided.")
                continue
            abs_ok = _cmp(india, abs_inr, t.get("abs_operator", "gt"))
            if glob is None or glob == 0:
                results[tid] = _result(
                    "PARTIAL", "amber",
                    f"India figure {N.format_dual(india,'INR')} "
                    f"{'exceeds' if abs_ok else 'is below'} ₹{t['abs_value']} cr, "
                    "but global figure missing — cannot test the 10% ratio.",
                    met=None)
                continue
            ratio = india / glob * 100.0
            met = (ratio >= t["ratio_pct"]) and bool(abs_ok)
            results[tid] = _result(
                "MET" if met else "NOT_MET",
                "red" if met else "green",
                f"India share {ratio:.1f}% of global (need ≥ {t['ratio_pct']}%); "
                f"India value {N.format_dual(india,'INR')} "
                f"(need > ₹{t['abs_value']} cr). {'SBOI test satisfied.' if met else 'Not satisfied.'}",
                met=met, ratio=ratio)
            continue

        # --- SBOI percent test ---
        if t.get("unit") == "PERCENT":
            v = g(t["field"])
            if v is None:
                results[tid] = _result("INSUFFICIENT", "muted", "Not provided.")
                continue
            met = _cmp(v, t["value"], t["operator"])
            results[tid] = _result(
                "MET" if met else "NOT_MET",
                "red" if met else "green",
                f"India users {v:.1f}% of global (need ≥ {t['value']}%). "
                f"{'Satisfied.' if met else 'Not satisfied.'}",
                met=met)
            continue

        # --- monetary thresholds ---
        primary = g(t["field"])
        th_inr = threshold_inr(t["value"], t["unit"], rates)
        native = (f"USD {t['value']} billion" if t["unit"] == "USD_BILLION"
                  else f"₹{N.western_group(t['value'])} crore")

        if primary is None:
            results[tid] = _result("INSUFFICIENT", "muted",
                                   "Figure not provided.", threshold_inr=th_inr,
                                   native=native)
            continue

        crossed = _cmp(primary, th_inr, t["operator"])

        if t["flag"] == "exempt":  # de minimis (under = qualifies)
            results[tid] = _result(
                "EXEMPT" if crossed else "NOT_EXEMPT",
                "green" if crossed else "amber",
                f"{N.format_dual(primary,'INR')} is "
                f"{'at or below' if crossed else 'above'} the {native} "
                f"small-target ceiling — "
                f"{'exemption available on this limb.' if crossed else 'this limb does not give the exemption.'}",
                value_inr=primary, threshold_inr=th_inr, native=native)
            continue

        # flag == breach (jurisdictional / deal value)
        leg = t.get("india_leg")
        if leg:
            leg_val = g(leg["field"])
            leg_inr = threshold_inr(leg["value"], leg["unit"], rates)
            leg_ok = _cmp(leg_val, leg_inr, leg["operator"])
            leg_txt = f"; India leg ≥ ₹{N.western_group(leg['value'])} cr"
            if not crossed:
                results[tid] = _result(
                    "CLEAR", "green",
                    f"{N.format_dual(primary,'INR')} does not exceed {native}.",
                    value_inr=primary, threshold_inr=th_inr, native=native)
            elif leg_val is None:
                results[tid] = _result(
                    "REVIEW", "amber",
                    f"Worldwide figure {N.format_dual(primary,'INR')} exceeds {native}, "
                    f"but the India-leg figure is missing{leg_txt} — limb cannot be confirmed.",
                    value_inr=primary, threshold_inr=th_inr, native=native)
            elif leg_ok:
                results[tid] = _result(
                    "BREACH", "red",
                    f"Worldwide {N.format_dual(primary,'INR')} exceeds {native} AND the "
                    f"India-leg requirement is met — limb satisfied.",
                    value_inr=primary, threshold_inr=th_inr, native=native)
            else:
                results[tid] = _result(
                    "CLEAR", "green",
                    f"Worldwide figure exceeds {native}, but the India-leg minimum "
                    f"(≥ ₹{N.western_group(leg['value'])} cr) is not met — limb not triggered.",
                    value_inr=primary, threshold_inr=th_inr, native=native)
        else:
            results[tid] = _result(
                "BREACH" if crossed else "CLEAR",
                "red" if crossed else "green",
                f"{N.format_dual(primary,'INR')} "
                f"{'EXCEEDS' if crossed else 'does not exceed'} the {native} threshold.",
                value_inr=primary, threshold_inr=th_inr, native=native)

    summary = _synthesise(cfg, results, inputs_inr)
    return {"results": results, "summary": summary}


def _synthesise(cfg, results, inputs_inr):
    """Combine the limbs into an overall notifiability indicator."""
    def status(tid):
        return results.get(tid, {}).get("status")

    juris_ids = [t["id"] for t in cfg["thresholds"]
                 if t["category"] in ("jurisdictional_parties", "jurisdictional_group")]
    jurisdictional_met = any(status(i) == "BREACH" for i in juris_ids)
    jurisdictional_review = any(status(i) == "REVIEW" for i in juris_ids)

    sbo_ids = [t["id"] for t in cfg["thresholds"] if t["category"] == "sbo"]
    sboi_met = any(results.get(i, {}).get("met") for i in sbo_ids)

    deal_value = inputs_inr.get("deal_value")
    deal_floor = cfg["meta"].get("deal_value_floor_for_exemption", 2000) * CRORE
    deal_prong = deal_value is not None and deal_value > deal_floor
    dvt_met = deal_prong and sboi_met

    # Small-target exemption: any de-minimis limb qualifies AND deal value (if known) <= floor.
    dm_qualifies = any(status(i) == "EXEMPT" for i in
                       [t["id"] for t in cfg["thresholds"] if t["category"] == "de_minimis"])
    deal_blocks_exemption = deal_value is not None and deal_value > deal_floor
    de_minimis_applies = dm_qualifies and not deal_blocks_exemption

    points = []
    if dvt_met:
        verdict = "LIKELY NOTIFIABLE"
        tone = "red"
        headline = "Deal Value Threshold met (deal value > ₹2,000 cr and target has SBOI)."
        points.append("Deal value exceeds ₹2,000 crore and at least one SBOI test is satisfied.")
    elif jurisdictional_met and not de_minimis_applies:
        verdict = "LIKELY NOTIFIABLE"
        tone = "red"
        headline = "A jurisdictional limb is crossed and the small-target exemption does not rescue it."
        points.append("At least one parties/group asset or turnover limb is satisfied.")
        if dm_qualifies and deal_blocks_exemption:
            points.append("Small-target exemption is blocked because the deal value exceeds ₹2,000 crore.")
        elif not dm_qualifies:
            points.append("Small-target (de minimis) exemption does not apply on the figures provided.")
    elif jurisdictional_met and de_minimis_applies:
        verdict = "POSSIBLY EXEMPT — REVIEW"
        tone = "amber"
        headline = "Jurisdictional limb crossed, but the small-target exemption appears available."
        points.append("A jurisdictional limb is met, however the target is within the ₹450 cr assets / ₹1,250 cr turnover small-target ceiling and the deal is within ₹2,000 crore.")
    elif jurisdictional_review:
        verdict = "INSUFFICIENT DATA"
        tone = "amber"
        headline = "A worldwide limb is crossed but India-leg figures are missing — provide them to confirm."
    else:
        verdict = "NO FILING INDICATED"
        tone = "green"
        headline = "On the figures provided, no jurisdictional limb or the deal-value threshold is crossed."
        if dvt_met is False and deal_prong and not sboi_met:
            points.append("Deal value exceeds ₹2,000 crore, but no SBOI test is satisfied, so the deal-value threshold is not met.")

    if dm_qualifies:
        points.append("Note: target is within the small-target / de minimis ceiling on at least one limb.")

    return {
        "verdict": verdict, "tone": tone, "headline": headline, "points": points,
        "jurisdictional_met": jurisdictional_met,
        "sboi_met": sboi_met, "dvt_met": dvt_met,
        "de_minimis_applies": de_minimis_applies,
        "deal_prong": deal_prong,
    }
