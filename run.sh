#!/usr/bin/env bash
# One-command launcher: sets up a virtual environment, installs dependencies,
# and starts the CCI Threshold Checker. Re-run any time.
set -e
cd "$(dirname "$0")"

PY=python3
VENV=.venv

if [ ! -d "$VENV" ]; then
  echo "→ Creating virtual environment…"
  $PY -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "→ Installing dependencies (first run only)…"
python -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
python -m pip install --quiet -r requirements.txt

# If no rates yet, do an initial scrape so the app is useful immediately.
if [ ! -f data/rates.db ]; then
  echo "→ No rate history found — fetching ~6 months from the RBI archive…"
  python scripts/scrape_rates.py || echo "  (scrape will be available from the app's 'Fetch RBI rates now' button)"
fi

echo "→ Starting server at http://127.0.0.1:5057"
( sleep 1.5; (command -v open >/dev/null && open http://127.0.0.1:5057) || true ) &
exec python app.py
