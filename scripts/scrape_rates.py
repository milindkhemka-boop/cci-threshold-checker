#!/usr/bin/env python3
"""
Standalone daily scrape entry point — for cron / launchd.

Usage:
    python3 scripts/scrape_rates.py
    python3 scripts/scrape_rates.py --window 200    # custom history window (days)

Scrapes the trailing window of RBI reference rates into data/rates.db and prints
a one-line summary. Safe to run repeatedly (upserts by date).
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import rates as rates_mod  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=190, help="history window in days")
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        s = rates_mod.scrape_six_months(window_days=args.window)
        print(f"[{stamp}] OK  fetched={s['fetched']} written={s['written']} "
              f"latest={s['latest']} total_in_db={s['total_in_db']}"
              + (f"  errors={len(s['errors'])}" if s["errors"] else ""))
        for e in s["errors"]:
            print(f"    ! {e}")
    except Exception as exc:
        print(f"[{stamp}] FAILED  {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
