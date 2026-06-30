# CCI merger-control thresholds — public reference page

A single static page showing the CCI (Section 5) merger-control thresholds,
converted to any currency on the **average of RBI reference rates over the six
months ending on a date you choose**. It updates itself every day.

- `build.py` — scrapes the RBI Reference Rate Archive, keeps a growing daily
  history in `docs/rates.json`, and renders `docs/index.html`. Standard library
  only — no dependencies.
- `docs/index.html` — the page that GitHub Pages serves. Bakes in the full rate
  history, so the **date toggle**, **currency** and **lakh-crore / million-billion**
  toggles all run in the browser.
- `.github/workflows/daily.yml` — runs `build.py` once a day, then commits the
  refreshed `docs/`.

## Put it online (free, auto-updating)

1. Create a **public** GitHub repository.
2. Upload **all of these files, keeping the folder structure** (`build.py`,
   `docs/`, `.github/workflows/daily.yml`, `README.md`). GitHub's web uploader
   preserves folders when you drag them in.
3. Repo **Settings → Pages** → Source: **Deploy from a branch**, Branch:
   **main**, Folder: **/docs** → Save. Your page goes live at
   `https://<username>.github.io/<repo>/`.
4. Repo **Settings → Actions → General** → allow workflows (and "Read and write
   permissions" under *Workflow permissions*). The job then runs daily at
   ~15:00 IST, re-scrapes the latest RBI rate, and republishes. You can also run
   it on demand from the **Actions** tab → *Update CCI rates daily* → *Run
   workflow*.

## Run / refresh locally

```bash
python3 build.py     # scrapes (first run seeds the full history) and writes docs/
```

Open `docs/index.html` in a browser to preview.

> Indicative reference only — not legal advice. Rate data from the RBI Reference
> Rate Archive; thresholds reflect the MCA revision of 7 March 2024 and the CCI
> (Combinations) Regulations 2024.
