#!/usr/bin/env python3
"""
Terminal test of the notification-assessment engine.

    python3 scripts/assess_cli.py "Acquirer A is buying 100% of Target B. A has
        India turnover ₹8,000 cr and worldwide turnover USD 6 bn; B has India
        turnover ₹300 cr, India assets ₹200 cr; deal value ₹2,400 cr; B is a
        fintech with ~12% of its global users in India."

Requires an Anthropic API key (ANTHROPIC_API_KEY env var, or config/api_key.txt).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import assessor, llm, rates as R, thresholds as TH  # noqa: E402


def main():
    if not llm.has_key():
        print("No API key found. Set ANTHROPIC_API_KEY or create config/api_key.txt.")
        sys.exit(1)
    prompt = " ".join(sys.argv[1:]).strip() or input("Describe the transaction: ").strip()
    if not prompt:
        sys.exit(0)
    window = int(TH.load_config()["meta"].get("fx_window_days", 183))
    avg = R.compute_average(window)
    print("\nAssessing (Opus 4.8, reasoning over the Act, Regulations, FAQs & cases)…\n")
    res = assessor.assess([], prompt, avg["averages"], "today")
    print(res["markdown"])
    if res["searches"]:
        print("\n— searched the corpus for: " + "; ".join(s for s in res["searches"] if s))


if __name__ == "__main__":
    main()
