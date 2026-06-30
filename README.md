# CCI Threshold Checker

A private, local web app for screening transactions against **Competition Commission of India (CCI) merger-control thresholds**.

It:

1. **Assesses notifiability** — a chat assistant (Claude Opus 4.8) that determines whether a transaction *requires notification to the CCI*, reasoning from the **Competition Act → Combination Regulations 2024 → FAQs → past combination orders** (in that order), and **flagging genuine uncertainty** instead of guessing. Needs an API key (below).
2. **Scrapes RBI reference rates** daily and computes the **trailing 6-month average** — the CCI's conversion basis — with an **as-of date** toggle for historical rates.
3. Shows **every threshold** (parties & group, deal value, small-target/de minimis, SBOI) in a table with **currency** and **lakh-crore / million-billion** toggles, recomputed from the average.
4. Holds a local **database of the CCI corpus** — every combination order + the statutes, regulations and FAQs (`data/corpus/`, `data/corpus.db`), full-text indexed (`data/corpus_fts.db`) so the assistant can cite specific provisions and `C-YYYY/MM/NNNN` orders.

> **Privacy:** uploaded financials are parsed **in memory and never written to disk**. Only public RBI rates + the public CCI corpus are stored. The assessment engine sends the deal facts you provide to the Anthropic API (your key, your account).

---

## Connect the assessment engine (API key)

The notifiability assistant uses the Claude API. Provide a key one of two ways:

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # before launching, OR
echo "sk-ant-..." > config/api_key.txt      # git-ignored; read only locally
```

Then `./run.sh`. Test it from the terminal: `python scripts/assess_cli.py "Acquirer A buys 100% of Target B; A India turnover ₹8,000 cr…"`. The rate table, threshold conversions, and corpus all work **without** a key — only the chat assessment needs it.

---

## Quick start

```bash
cd "cci-threshold-checker"
./run.sh
```

`run.sh` creates a virtual environment, installs dependencies, fetches ~6 months of rates on first run, and opens <http://127.0.0.1:5057>.

### Manual start (if you prefer)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/scrape_rates.py     # fetch rates once
python app.py                      # http://127.0.0.1:5057
```

The app runs even without the optional document parsers (`pypdf`, `openpyxl`, `python-docx`) — you can still paste text, upload `.txt`/`.csv`/`.eml`, or type figures in.

---

## Daily auto-update of rates

**Option A — macOS launchd (recommended):**

1. Edit `scripts/com.ccichecker.scrape.plist` and replace both `__PROJECT_DIR__` placeholders with this folder's absolute path.
2. ```bash
   cp scripts/com.ccichecker.scrape.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.ccichecker.scrape.plist
   ```
   It runs daily at 18:30 (and on next wake if the Mac was asleep). Logs to `data/scrape.log`.

**Option B — cron:**

```cron
30 18 * * *  cd "/absolute/path/to/cci-threshold-checker" && .venv/bin/python scripts/scrape_rates.py >> data/scrape.log 2>&1
```

**Option C — manual:** click **“Fetch RBI rates now”** on the dashboard, or run `python scripts/scrape_rates.py`.

A 6-month average is robust to the odd missed day.

---

## Using it

- **Rates & Thresholds** — the 6-month average and the full threshold table; switch the display currency.
- **Check Financials** — upload a document (figures are auto-detected and you click to fill them), pick the currency and default magnitude, then run the check. The result gives a per-threshold status plus an overall notifiability indicator.
- **Configure** — edit threshold values, citations and effective dates (no code changes) when the law is revised.
- **Methodology** — sources, the exchange-rate basis, and the numbering-system equivalences.

---

## Thresholds in this build

Reflects the **MCA revision of 7 March 2024** and the **Deal Value Threshold / CCI (Combinations) Regulations 2024** (in force 10 September 2024):

| Limb | Value |
|---|---|
| Parties — India | assets > ₹2,500 cr **or** turnover > ₹7,500 cr |
| Parties — worldwide | assets > USD 1.25 bn **or** turnover > USD 3.75 bn (India leg ₹1,250 cr / ₹3,750 cr) |
| Group — India | assets > ₹10,000 cr **or** turnover > ₹30,000 cr |
| Group — worldwide | assets > USD 5 bn **or** turnover > USD 15 bn (same India legs) |
| Small-target / de minimis | target India assets ≤ ₹450 cr **or** turnover ≤ ₹1,250 cr (blocked if deal > ₹2,000 cr) |
| Deal Value Threshold | deal > ₹2,000 cr **and** target has SBOI |
| SBOI | India turnover/GMV ≥ 10% of global **and** > ₹500 cr; or India users ≥ 10% |

> **Disclaimer.** Indicative screening only — **not legal advice**. Verify values against the bare Act, current MCA notifications and the CCI (Combinations) Regulations before any filing decision. Turnover = *revenue from operations* (book values, audited accounts of the preceding financial year).

---

## Layout

```
app.py                  Flask app & routes
core/
  rates.py              RBI scraper + 6-month average (stdlib only)
  numbers.py            lakh/crore ↔ million/billion parsing & formatting
  fx.py                 currency conversion via the CCI average
  thresholds.py         threshold engine + notifiability synthesis
  extract.py            document parsing → candidate figures
  db.py                 SQLite rate store
config/thresholds.json  editable thresholds (values, citations, dates)
templates/  static/     UI
scripts/                daily-scrape CLI + launchd job
data/rates.db           rate history (created at runtime)
```
