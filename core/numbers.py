"""
Number-system harmonization for the CCI Threshold Checker.

Bridges the Indian (lakh / crore) and international (million / billion / trillion)
numbering systems, parses messy money strings out of financial documents, and
formats a plain rupee/where-relevant amount back into either convention.

Internally, every monetary amount is a float in *base units of its currency*
(i.e. rupees for INR, dollars for USD, ...). Scale words only ever affect parsing
and display — never storage.

    1 thousand      = 1e3
    1 lakh          = 1e5      (Indian)
    1 million       = 1e6
    1 crore         = 1e7      (Indian)   = 10 million
    1 billion       = 1e9                 = 100 crore
    1 arab          = 1e9      (Indian)
    1 trillion      = 1e12               = 1 lakh crore
"""

import re

# --- scale multipliers -------------------------------------------------------
THOUSAND = 1e3
LAKH = 1e5
MILLION = 1e6
CRORE = 1e7
BILLION = 1e9
ARAB = 1e9
TRILLION = 1e12
LAKH_CRORE = 1e12

# Word/abbreviation -> multiplier. Most-specific first; first match wins.
# Patterns tolerate suffixes attached to digits ("237mn", "1.25bn", "₹450cr")
# as well as spaced words ("1.25 billion", "2,500 crore"). `(?<![a-z])` stops
# matches inside larger words (e.g. "cr" in "score"); single letters require a
# preceding digit so "plan b" is not read as "billion".
SCALE_WORDS = [
    (r"(?<![a-z])lakh\s*crores?", LAKH_CRORE),
    (r"(?<![a-z])lac\s*crores?", LAKH_CRORE),
    (r"(?<![a-z])trillions?", TRILLION),
    (r"(?<![a-z])tn(?![a-z])", TRILLION),
    (r"(?<![a-z])billions?", BILLION),
    (r"(?<![a-z])bn(?![a-z])", BILLION),
    (r"(?<=\d)\s?b(?![a-z])", BILLION),
    (r"(?<![a-z])arabs?", ARAB),
    (r"(?<![a-z])crores?", CRORE),
    (r"(?<![a-z])cr(?![a-z])", CRORE),
    (r"(?<![a-z])millions?", MILLION),
    (r"(?<![a-z])ml?n(?![a-z])", MILLION),   # mn, mln
    (r"(?<=\d)\s?m(?![a-z])", MILLION),
    (r"(?<![a-z])lakhs?", LAKH),
    (r"(?<![a-z])lacs?", LAKH),
    (r"(?<=\d)\s?l(?![a-z])", LAKH),
    (r"(?<![a-z])thousands?", THOUSAND),
    (r"(?<=\d)\s?k(?![a-z])", THOUSAND),
]

# Currency detection from symbols / codes / words.
CURRENCY_PATTERNS = [
    ("INR", r"₹|\brs\.?\b|\binr\b|\brupees?\b|\brup\b"),
    ("USD", r"\$|\busd\b|\bus\s*\$|\bdollars?\b|\bus\s*dollars?\b"),
    ("EUR", r"€|\beur\b|\beuros?\b"),
    ("GBP", r"£|\bgbp\b|\bpounds?\b|\bsterling\b"),
    ("JPY", r"¥|\bjpy\b|\byen\b"),
    ("AED", r"\baed\b|\bdhs?\b|\bdirhams?\b"),
]

CURRENCY_SYMBOL = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AED": "AED ",
}


def detect_currency(text, default="INR"):
    """Best-effort ISO currency code from a free-text money string."""
    if not text:
        return default
    low = text.lower()
    for code, pat in CURRENCY_PATTERNS:
        if re.search(pat, low):
            return code
    return default


def _detect_scale(text):
    """Return (multiplier, matched_word) for the first scale word found, else (1, None)."""
    low = " " + text.lower() + " "
    for pat, mult in SCALE_WORDS:
        if re.search(pat, low):
            return mult, pat
    return 1.0, None


def parse_amount(text, assume_scale=1.0):
    """
    Parse a money string into a base-unit float.

    Handles Western grouping ("20,000,000"), Indian grouping ("2,00,00,000"),
    decimals, currency symbols/words, negatives/parentheses, and scale words
    (lakh, crore, mn, bn, ...).

    `assume_scale` applies a multiplier when the string itself carries no scale
    word — e.g. a statement whose header says "(₹ in crore)" passes assume_scale=CRORE.

    Returns float (base units) or None if no number is found.

    Examples:
        parse_amount("₹2,500 crore")      -> 2.5e10
        parse_amount("USD 1.25 billion")  -> 1.25e9
        parse_amount("2,00,00,000")       -> 2.0e7   (= 2 crore, Indian grouping)
        parse_amount("450", CRORE)        -> 4.5e9   (header said "in crore")
        parse_amount("(1,234.5)")         -> -1234.5 (accounting negative)
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    negative = False
    if s.startswith("(") and s.rstrip().endswith(")"):
        negative = True
    if re.search(r"(?<![\w])-\s*\d", s):
        negative = True

    mult, _ = _detect_scale(s)

    # Pull out the numeric token (digits, commas, dots). Indian and Western
    # grouping both collapse to the same number once commas are stripped.
    m = re.search(r"\d[\d,]*\.?\d*", s)
    if not m:
        return None
    num = m.group(0).replace(",", "")
    if num.count(".") > 1:  # guard against malformed tokens
        first = num.find(".")
        num = num[:first + 1] + num[first + 1:].replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None

    if mult == 1.0:
        mult = assume_scale
    value *= mult
    return -value if negative else value


def detect_statement_scale(text):
    """
    Infer the reporting scale from a statement header / note such as
    "(₹ in crore)", "(Rs. in lakhs)", "Amounts in USD million", "(figures in 000s)".

    Returns (multiplier, label) e.g. (1e7, "crore"). Defaults to (1.0, "absolute").
    """
    if not text:
        return 1.0, "absolute"
    low = text.lower()
    # Look only near scale-indicating phrases to avoid false positives.
    hints = re.findall(
        r"(?:in|amounts?\s+in|figures?\s+in|rs\.?\s*in|₹\s*in|inr\s*in)\s+"
        r"([a-z'000\s]{1,20})",
        low,
    )
    candidates = hints + [low]
    for chunk in candidates:
        if re.search(r"lakh\s*crore", chunk):
            return LAKH_CRORE, "lakh crore"
        if re.search(r"crores?|\bcr\b", chunk):
            return CRORE, "crore"
        if re.search(r"lakhs?|lacs?", chunk):
            return LAKH, "lakh"
        if re.search(r"billions?|\bbn\b", chunk):
            return BILLION, "billion"
        if re.search(r"millions?|\bmn\b", chunk):
            return MILLION, "million"
        if re.search(r"thousands?|'000|\b000s\b", chunk):
            return THOUSAND, "thousand"
    return 1.0, "absolute"


# --- formatting --------------------------------------------------------------

def indian_group(n):
    """Group an integer string in the Indian system: 12,34,56,789."""
    s = str(int(round(n)))
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if len(s) <= 3:
        out = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        out = ",".join(parts) + "," + last3
    return ("-" + out) if neg else out


def western_group(n):
    """Group an integer in the international system: 123,456,789."""
    return "{:,}".format(int(round(n)))


def _trim(x, decimals=2):
    s = "{:,.{d}f}".format(x, d=decimals)
    return s


def format_indian(value, decimals=2):
    """
    Express a base-unit amount in the most natural Indian unit.
    e.g. 2.5e10 -> "2,500.00 crore"; 4.5e9 -> "450.00 crore"; 5e5 -> "5.00 lakh".
    """
    if value is None:
        return "—"
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= LAKH_CRORE:
        return f"{sign}{_trim(a / LAKH_CRORE, decimals)} lakh crore"
    if a >= CRORE:
        whole = a / CRORE
        return f"{sign}{indian_group(whole) if whole == int(whole) else _trim(whole, decimals)} crore"
    if a >= LAKH:
        return f"{sign}{_trim(a / LAKH, decimals)} lakh"
    return f"{sign}{indian_group(a)}"


def format_western(value, decimals=2):
    """
    Express a base-unit amount in the most natural international unit.
    e.g. 1.25e9 -> "1.25 billion"; 2.37e8 -> "237.00 million".
    """
    if value is None:
        return "—"
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= TRILLION:
        return f"{sign}{_trim(a / TRILLION, decimals)} trillion"
    if a >= BILLION:
        return f"{sign}{_trim(a / BILLION, decimals)} billion"
    if a >= MILLION:
        return f"{sign}{_trim(a / MILLION, decimals)} million"
    if a >= THOUSAND:
        return f"{sign}{_trim(a / THOUSAND, decimals)} thousand"
    return f"{sign}{western_group(a)}"


def format_dual(value, currency="INR", decimals=2):
    """
    Human display showing both numbering systems, e.g.:
        "₹2,500.00 crore  (= 25.00 billion)"
    For non-INR currencies the international unit leads:
        "$1.25 billion  (= 125.00 crore)"
    """
    if value is None:
        return "—"
    sym = CURRENCY_SYMBOL.get(currency, currency + " ")
    ind = format_indian(value, decimals)
    west = format_western(value, decimals)
    if currency == "INR":
        return f"{sym}{ind}  (= {west})"
    return f"{sym}{west}  (= {ind})"


def to_crore(value):
    return None if value is None else value / CRORE


def to_million(value):
    return None if value is None else value / MILLION


def to_billion(value):
    return None if value is None else value / BILLION
