"""
Currency conversion using the CCI 6-month-average reference rates.

`rates` is the "averages" map from rates.compute_average(): currency code ->
INR per 1 unit of that currency. INR itself is always 1.0.
"""


def supported(rates):
    codes = ["INR"] + [c for c in rates.keys()]
    # preserve a sensible display order
    order = ["INR", "USD", "EUR", "GBP", "JPY", "AED", "IDR"]
    return [c for c in order if c in codes] + [c for c in codes if c not in order]


def inr_per_unit(rates, currency):
    if currency == "INR":
        return 1.0
    return rates.get(currency)


def to_inr(amount, currency, rates):
    """Convert an amount in `currency` to INR (base rupees)."""
    if amount is None:
        return None
    r = inr_per_unit(rates, currency)
    return None if r is None else amount * r


def from_inr(inr_amount, currency, rates):
    """Convert a base-rupee amount into `currency`."""
    if inr_amount is None:
        return None
    r = inr_per_unit(rates, currency)
    if not r:
        return None
    return inr_amount / r


def convert(amount, from_ccy, to_ccy, rates):
    inr = to_inr(amount, from_ccy, rates)
    return from_inr(inr, to_ccy, rates)
