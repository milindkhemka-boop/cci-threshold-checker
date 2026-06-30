#!/usr/bin/env python3
"""
Build / update the CCI legal-document corpus.

    python3 scripts/build_corpus.py --index        # catalogue every combination case
    python3 scripts/build_corpus.py --reference     # queue + download statutes/regs/FAQ
    python3 scripts/build_corpus.py --download       # download all pending order PDFs (resumable)
    python3 scripts/build_corpus.py --download --limit 20
    python3 scripts/build_corpus.py --all            # index + reference + download everything
    python3 scripts/build_corpus.py --status         # show progress

Safe to re-run and to interrupt: indexing upserts, downloads skip finished files.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import corpus  # noqa: E402


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def show_status():
    s = corpus.stats()
    print("── CCI corpus status ─────────────────────────")
    print(f"  Combinations indexed : {s['combinations']}")
    print(f"  Order/summary PDFs    : {s['order_files_done']} done / "
          f"{s['order_files_pending']} pending / {s['order_files_error']} error "
          f"(of {s['order_files_total']})")
    print(f"  Reference documents   : {s['reference_done']} / {s['reference_total']}")
    print(f"  Downloaded size       : {s['bytes_done']/1_048_576:.1f} MB")
    print("──────────────────────────────────────────────")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--reference", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.status and not any([args.index, args.reference, args.download, args.all]):
        show_status(); return

    if args.index or args.all:
        log("Indexing combination cases…")
        corpus.index_all(log=log)
        show_status()

    if args.reference or args.all:
        log("Queuing + downloading reference documents…")
        corpus.index_reference(log=log)
        log("Queuing the full CCI legal framework (all rules & regulations)…")
        corpus.index_legal_framework(log=log)
        n = corpus.download_reference(log=log)
        log(f"Reference downloads complete (+{n}).")

    if args.download or args.all:
        log("Downloading combination order/summary PDFs (resumable)…")
        done, fail = corpus.download_orders(limit=args.limit, log=log)
        log(f"Download pass complete: {done} fetched, {fail} failed.")
        show_status()

    if not any([args.index, args.reference, args.download, args.all, args.status]):
        ap.print_help()


if __name__ == "__main__":
    main()
