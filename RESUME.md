# RESUME CHECKPOINT — paused 2026-06-24 ~14:00, resume ~18:30

## Where we are
**Phase 1 (DONE & running):** the CCI Threshold Checker app — RBI 6-month-avg
scraper, thresholds table in any currency, upload→flag check, number
harmonization. Live at http://127.0.0.1:5057 (`./run.sh`). 124 days of real
rate data in `data/rates.db`.

**Front-end reworked (DONE, 2026-06-25):** `/` is now `home.html` — a thresholds
table with independent **currency** (INR/USD/EUR/Other) and **numbering**
(lakh·crore / million·billion) toggles, rendered client-side from `/api/bootstrap`,
beside a **chat assistant** (`/api/chat` → `core/assistant.py`). Old dashboard
moved to `/rates`; form moved to "Advanced Form" (`/check`). Number formatting is
mirrored in `static/home.js`. When building the two-party calculator, integrate it
into this home/chat experience (or a dedicated tab) rather than the old form.

**Phase 2 (PAUSED by user):** CCI legal corpus (code written, not run) + two-party
calculator + PDF export. Resume per the TODO below when the user says go.

### Written but NOT yet run
- `core/corpus.py` — corpus DB + scraper + resumable downloader (stdlib only).
- `scripts/build_corpus.py` — CLI: `--index --reference --download --limit --all --status`.

### Verified CCI endpoints (recon complete)
- Combination listings (DataTables JSON, GET same URL + `X-Requested-With`, params `draw,start,length`):
  - `orders-section31` = **1440**, `orders-section43a_44` = 68,
    `cases-approved-with-modification` = 33, `notice-under-review` = 18.
  - PDFs in each record's `order_file_content` / `summary_file_content` (HTML-entity JSON);
    file path → `https://www.cci.gov.in/<file_name>` (e.g. images/caseorders/en/orderNNN.pdf).
- Reference: Act page = 2 direct PDFs; `legal-framwork/regulations` (2) & `…/notifications` (23)
  via DataTables JSON `file_content`; FAQ booklet = `https://www.cci.gov.in/pdfs/FAQ_Book_English.pdf`.

## TODO on resume (in order)
1. `python scripts/build_corpus.py --index` (fast, JSON only) → populates `combinations` + `order_files`.
2. `python scripts/build_corpus.py --reference` → fetch Act / Regs / Notifications / FAQ.
3. Test `python scripts/build_corpus.py --download --limit 15`; confirm PDFs land in `data/corpus/`.
4. Launch full `--download` in BACKGROUND (run_in_background Bash) — ~1559 cases, up to ~2.9k PDFs, ~1–3 GB; resumable.
5. **Two-party calculator** — new `core/parties.py` + `/calculator` route + template + nav.
   Structure-aware aggregation:
     - Transaction type: Acquisition / Merger / Amalgamation.
     - Parties (jurisdictional) test = **aggregate** acquirer + target at ENTERPRISE level.
     - Group test = the **acquirer's group** (merged group for mergers) — entered separately.
     - De minimis (small target) & SBOI = **target only**, never aggregated.
     - Deal value threshold = deal value + target SBOI.
   Reuse `core/thresholds.evaluate()` by composing the right inputs from acquirer/target/group.
6. **PDF export** of a screening result (decide: `fpdf2` dependency vs print-CSS route). Add `/check` result → PDF.
7. **/library UI** — browse reference docs + searchable combinations table; serve local PDFs.
   `core/corpus.py` already exposes: stats, list_reference, search_combinations,
   files_for_combination, get_order_file, get_reference, distinct_statuses.
8. Add nav links (Calculator, Library); update `README.md` + memory.

## Notes
- Scheduled CLOUD agents can't do this (work is on the local Mac FS). Resume by messaging in this session.
- Be polite to cci.gov.in (sleeps already built into downloader). Downloads are idempotent/resumable.
