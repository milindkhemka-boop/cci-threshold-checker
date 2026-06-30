"""
Notification-assessment engine (Claude Opus 4.8).

Answers the core question — does this transaction require notification to the
CCI? — by reasoning over the primary law first (Competition Act, Combination
Regulations 2024, FAQs, held in context + searchable) and falling back to the
combination-order corpus for issues the primary sources don't squarely resolve.

Design principle (the most important one): surface uncertainty rather than
guess. Where the law is unsettled, fact-dependent, or silent, the engine names
the uncertainty and its source and leaves the conclusion to the user.
"""

import json
import os

from . import llm, retrieval, thresholds as TH, numbers as N

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGS_TXT = os.path.join(ROOT, "data", "primary", "combination_regulations.txt")
MAX_REGS_CHARS = 260_000  # the Combination Regulations 2024 full text

SEARCH_TOOL = {
    "name": "search_cci_corpus",
    "description": (
        "Full-text search the CCI legal corpus. Use this whenever the materials "
        "already in your context do not SQUARELY resolve an issue — to pull the "
        "exact wording of a Competition Act provision, an FAQ answer, or a "
        "precedent from past combination orders (e.g. what counts as 'control', "
        "whether steps are 'interconnected' and form one combination, 'ordinary "
        "course of business', application of SBOI, or a Schedule I exemption). "
        "Returns ranked excerpts with their source (provision or combination "
        "registration number). Search primary sources first; consult cases only "
        "for points the primary sources leave open."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Issue keywords, e.g. 'acquisition of sole control minority shareholding' or 'interconnected transactions single combination'.",
            },
            "scope": {
                "type": "string",
                "enum": ["primary", "cases", "all"],
                "description": "'primary' = Act/Regulations/FAQ/notifications; 'cases' = past combination orders & summaries; 'all' = both.",
            },
        },
        "required": ["query"],
    },
}

_SCOPE_MAP = {
    "primary": ["primary", "faq"],
    "cases": ["order", "summary"],
    "all": None,
}


def _load_regs():
    try:
        with open(REGS_TXT, encoding="utf-8") as fh:
            return fh.read()[:MAX_REGS_CHARS]
    except FileNotFoundError:
        return ""


def _threshold_rules_text():
    cfg = TH.load_config()
    lines = ["ENCODED THRESHOLDS (verify against the primary text below; values as configured):"]
    for t in cfg["thresholds"]:
        if t.get("type") == "ratio":
            v = f"India ≥ {t['ratio_pct']}% of global AND > ₹{t['abs_value']} cr"
        elif t.get("unit") == "PERCENT":
            v = f"≥ {t['value']}%"
        elif t.get("unit") == "USD_BILLION":
            v = f"USD {t['value']} billion"
        else:
            v = f"₹{t['value']} crore"
        leg = ""
        if t.get("india_leg"):
            leg = f" [India leg ≥ ₹{t['india_leg']['value']} cr]"
        lines.append(f"  • [{t['category']}] {t['label']}: {v}{leg}  — {t.get('citation','')}")
    return "\n".join(lines)


SYSTEM_STABLE = """You are a specialist assistant for Indian merger-control (combination) assessment under the Competition Act, 2002. Your single job is to help a competition lawyer determine whether a given transaction REQUIRES NOTIFICATION to the Competition Commission of India (CCI) — or is exempt, or falls outside the regime.

HOW YOU MUST WORK

1. Source hierarchy — use sources in this order and CITE them:
   (a) the Competition Act, 2002 (esp. s.5, s.6, s.2 definitions, s.20, s.29-31, s.43A);
   (b) the CCI (Combinations) Regulations, 2024 (full text is provided below in your context);
   (c) the Combination FAQs and the MCA exemption notifications;
   (d) ONLY for issues (a)-(c) do not squarely resolve: the body of past combination orders/cases.
   Search the corpus (search_cci_corpus tool) for exact provision wording, FAQ answers, and case precedents rather than relying on memory. Cite the provision or the combination registration number (e.g. C-YYYY/MM/NNNN) for anything material.

2. Reason through the actual structure of the transaction, not just the numbers:
   - What is it? Acquisition (of control / shares / voting rights / assets) under s.5(a), or a merger/amalgamation under s.5(c)? What exactly is being acquired, and how much?
   - Jurisdictional thresholds (s.5): aggregate the PARTIES (acquirer enterprise + target enterprise) for the parties test; use the GROUP figures for the group test; apply the India and worldwide limbs (worldwide limbs need the India-leg minimum).
   - Small-target / de minimis exemption: assessed on the TARGET alone (not aggregated); unavailable where the deal value exceeds the deal-value-threshold floor.
   - Deal Value Threshold (s.5(d)): deal value above the floor AND target has Substantial Business Operations in India (SBOI) — check the SBOI tests in the Regulations.
   - Exemptions: Schedule I categories (e.g. acquisitions solely as an investment / in the ordinary course of business below the control line, intra-group reorganisations, etc.) — check the current Schedule and notifications.
   - Interconnected / composite steps may be a single combination — check the Regulations and case law.

3. THE UNCERTAINTY RULE (most important):
   Do not give a confident yes/no where the law is unsettled, the facts are incomplete, or the sources are silent or conflicting. Instead, structure every assessment as:
   - WHAT IS CLEAR — each conclusion with its provision/case citation.
   - WHAT IS UNCERTAIN — name each open point AND its source of uncertainty: a gap or ambiguity in the statutory text, a fact you don't have, a question on which the FAQs are silent, or cases that conflict or don't squarely apply. Say what additional fact or authority would resolve it. Do NOT paper over doubt with a best guess. Leave the judgement call to the user.
   - WHAT I NEED — if a key fact is missing (deal structure, % acquired, control, India vs global figures, deal value, sector), ask for it rather than assuming. State any assumption you do make, explicitly.

4. Output: a clear notification assessment — likely notifiable / likely not / cannot determine without X — followed by the CLEAR / UNCERTAIN / NEEDED structure above. Be precise and lawyerly; quote the operative words of provisions where they matter. This is decision support, NOT legal advice; say so once at the end.

You convert foreign-currency figures using the CCI basis (average of RBI reference rates over the trailing six months); the current values are given in the volatile context block.

=== ENCODED THRESHOLD RULES ===
{THRESHOLD_RULES}

=== FULL TEXT: CCI (COMBINATIONS) REGULATIONS, 2024 (primary source — quote/cite from here) ===
{REGS_TEXT}
=== END REGULATIONS TEXT ===
"""


def build_system(rates, as_of_label):
    stable = SYSTEM_STABLE.replace("{THRESHOLD_RULES}", _threshold_rules_text())
    stable = stable.replace("{REGS_TEXT}", _load_regs() or "(Regulations text not extracted; rely on search_cci_corpus and the encoded rules.)")
    # volatile block — the FX rate and any as-of date change per request
    usd = rates.get("USD"); eur = rates.get("EUR")
    vbits = [f"As-of / valuation date basis: {as_of_label}."]
    if usd:
        vbits.append(f"6-month average RBI reference rate: USD = ₹{usd:.4f}, "
                     + (f"EUR = ₹{eur:.4f}. " if eur else ". ")
                     + f"So USD 1.25 bn ≈ ₹{usd*1.25e9/1e7:,.0f} cr, USD 3.75 bn ≈ ₹{usd*3.75e9/1e7:,.0f} cr, "
                       f"USD 5 bn ≈ ₹{usd*5e9/1e7:,.0f} cr, USD 15 bn ≈ ₹{usd*15e9/1e7:,.0f} cr.")
    else:
        vbits.append("No exchange-rate data is loaded; ask the user to fetch RBI rates if a worldwide USD limb is in play.")
    volatile = "VOLATILE CONTEXT (changes per request):\n" + "\n".join(vbits)
    return [
        {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": volatile},
    ]


def _run_search(tool_input):
    query = (tool_input or {}).get("query", "")
    scope = (tool_input or {}).get("scope", "all")
    rows = retrieval.search(query, limit=8, source_types=_SCOPE_MAP.get(scope))
    if not rows:
        return f"No matches for: {query!r} (scope={scope}). The corpus may still be indexing, or try different keywords."
    out = [f"Top results for {query!r} (scope={scope}):"]
    for r in rows:
        tag = r["ref"] or r["kind"]
        out.append(f"\n[{r['source_type']}:{r['kind']}] {tag}\n  …{r['snippet']}…")
    return "\n".join(out)


def assess(history, user_text, rates, as_of_label="today", max_iterations=6):
    """
    history: list of {role: 'user'|'assistant', content: str} from prior turns.
    user_text: the new user message (may already include extracted document text).
    Returns {markdown: str, searches: [query, ...]}.
    """
    client = llm.get_client()
    system = build_system(rates, as_of_label)
    messages = [{"role": m["role"], "content": m["content"]} for m in (history or [])]
    messages.append({"role": "user", "content": user_text})

    searches = []
    final_text = ""
    for _ in range(max_iterations):
        with client.messages.stream(
            model=llm.MODEL,
            max_tokens=12000,
            system=system,
            tools=[SEARCH_TOOL],
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "search_cci_corpus":
                    searches.append(block.input.get("query", ""))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _run_search(block.input),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # end_turn / refusal / max_tokens
        final_text = "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason == "refusal":
            final_text = (final_text or
                          "The request was declined by the model's safety system. "
                          "Please rephrase or remove any sensitive content.")
        break

    return {"markdown": final_text or "I couldn't complete the assessment — please try again.",
            "searches": searches}
