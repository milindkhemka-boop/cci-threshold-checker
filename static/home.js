// ---------------------------------------------------------------------------
// State + number formatting (mirrors core/numbers.py)
// ---------------------------------------------------------------------------
const SYM = { INR: "₹", USD: "$", EUR: "€", GBP: "£", JPY: "¥", AED: "AED ", IDR: "IDR " };
let STATE = { ccy: "INR", sys: "inr", as_of: null, boot: null };

function indianGroup(n) {
  let s = Math.round(Math.abs(n)).toString();
  const neg = n < 0 ? "-" : "";
  if (s.length <= 3) return neg + s;
  const last3 = s.slice(-3);
  let rest = s.slice(0, -3), parts = [];
  while (rest.length > 2) { parts.unshift(rest.slice(-2)); rest = rest.slice(0, -2); }
  if (rest) parts.unshift(rest);
  return neg + parts.join(",") + "," + last3;
}
const trim = (x) => x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function formatIndian(v) {
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e12) return s + trim(a / 1e12) + " lakh crore";
  if (a >= 1e7)  { const c = a / 1e7; return s + (c === Math.round(c) ? indianGroup(c) : trim(c)) + " crore"; }
  if (a >= 1e5)  return s + trim(a / 1e5) + " lakh";
  return s + indianGroup(a);
}
function formatWestern(v) {
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e12) return s + trim(a / 1e12) + " trillion";
  if (a >= 1e9)  return s + trim(a / 1e9) + " billion";
  if (a >= 1e6)  return s + trim(a / 1e6) + " million";
  if (a >= 1e3)  return s + trim(a / 1e3) + " thousand";
  return s + Math.round(a).toLocaleString("en-US");
}
function fmtValue(valueInr) {
  if (valueInr == null) return "—";
  const { ccy, sys, boot } = STATE;
  let amt = valueInr;
  if (ccy !== "INR") {
    const r = boot.rates[ccy];
    if (!r) return "—";
    amt = valueInr / r;
  }
  const body = sys === "inr" ? formatIndian(amt) : formatWestern(amt);
  return (SYM[ccy] || ccy + " ") + body;
}

// ---------------------------------------------------------------------------
// Bootstrap + rendering
// ---------------------------------------------------------------------------
async function boot(opts = {}) {
  const params = new URLSearchParams();
  if (STATE.as_of) {
    params.set("as_of", STATE.as_of);
    if (opts.fetch) params.set("fetch", "1");
  }
  const url = "/api/bootstrap" + (params.toString() ? "?" + params.toString() : "");
  const res = await fetch(url);
  STATE.boot = await res.json();
  renderRateStrip();
  renderRatePanel();
  populateOtherCcy();
  renderTable();
  renderRateHistory();
  document.getElementById("tableDisclaimer").textContent = STATE.boot.disclaimer || "";
}

function renderRatePanel() {
  const b = STATE.boot;
  const summary = document.getElementById("rateSummary");
  const grid = document.getElementById("rateGrid");
  const empty = document.getElementById("rateEmptyState");
  if (!summary || !grid) return;
  if (!b.has_data) {
    summary.innerHTML = "";
    grid.innerHTML = "";
    if (empty) empty.style.display = "";
    return;
  }
  if (empty) empty.style.display = "none";
  const tag = b.is_today ? "" : `<span class="asof-tag">as of ${b.as_of}</span> `;
  summary.innerHTML = `${tag}Averaged over <strong>${b.n_days}</strong> RBI publication days, ` +
    `${b.from} → ${b.to} (trailing ${b.window_days} days).`;
  let html = "";
  const ccyOrder = ["USD", "GBP", "EUR", "JPY", "AED", "IDR"].filter(c => c in b.rates);
  for (const ccy of ccyOrder) {
    const val = b.rates[ccy];
    const latest = b.latest && b.latest[ccy];
    html += `<div class="rate-chip">
        <div class="rate-ccy">${ccy}</div>
        <div class="rate-val">₹${val.toFixed(4)}</div>
        <div class="rate-sub muted">per 1 ${ccy}${latest != null ? ` · latest ₹${latest.toFixed(2)}` : ""}</div>
      </div>`;
  }
  grid.innerHTML = html;
}

const RATE_HISTORY_CCY = ["usd", "gbp", "eur", "jpy", "aed", "idr"];

function renderRateHistory() {
  const b = STATE.boot, host = document.getElementById("rateHistoryTable");
  const rows = b.rate_history || [];
  const sub = document.getElementById("rateHistorySub");
  if (!rows.length) {
    host.innerHTML = `<p class="muted">No daily rate history available yet.</p>`;
    if (sub) sub.textContent = "Daily RBI reference rates behind the six-month average";
    return;
  }
  const cols = RATE_HISTORY_CCY.filter(c => rows.some(r => r[c] != null));
  if (sub) {
    sub.textContent = `${rows.length} RBI publication days, ${b.from} → ${b.to} — each rate is INR per 1 unit`;
  }
  let html = `<table class="rate-history"><thead><tr><th>Date</th>` +
    cols.map(c => `<th>${c.toUpperCase()}</th>`).join("") + `</tr></thead><tbody>`;
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    html += `<tr><td>${r.date}</td>` +
      cols.map(c => r[c] != null ? `<td>${r[c].toFixed(4)}</td>` : `<td class="na">—</td>`).join("") +
      `</tr>`;
  }
  html += `</tbody></table>`;
  host.innerHTML = html;
}

function renderRateStrip() {
  const b = STATE.boot, el = document.getElementById("rateStrip");
  if (!el) return;
  if (!b.has_data) {
    el.innerHTML = `<span class="warn">No RBI rate data for the 6 months to ${b.as_of}.</span> ` +
      (b.is_today ? `<button class="btn small-btn" onclick="refreshRates()">Fetch RBI rates now</button>` : "");
    return;
  }
  const r = b.rates;
  const bits = ["USD", "EUR", "GBP"].filter(c => r[c]).map(c => `${SYM[c]||c} ₹${r[c].toFixed(2)}`);
  const tag = b.is_today ? "" : `<span class="asof-tag">as of ${b.as_of}</span> `;
  el.innerHTML = `${tag}<strong>CCI conversion rate</strong> — 6-month RBI average to ${b.as_of} ` +
    `(${b.from} → ${b.to}, ${b.n_days} days): &nbsp; ${bits.join(" &nbsp;·&nbsp; ")}` +
    (b.n_days < 20 ? ` <span class="warn">— sparse data for this period</span>` : "");
}

function populateOtherCcy() {
  const sel = document.getElementById("otherCcy");
  if (sel.dataset.filled) return;
  const primary = ["INR", "USD", "EUR"];
  STATE.boot.currencies.filter(c => !primary.includes(c)).forEach(c => {
    const o = document.createElement("option"); o.value = c; o.textContent = c; sel.appendChild(o);
  });
  sel.dataset.filled = "1";
}

// ---- as-of date ----
function initDate() {
  const inp = document.getElementById("asOfDate");
  const today = (STATE.boot && STATE.boot.today) || new Date().toISOString().slice(0, 10);
  inp.max = today;
  if (!inp.value) inp.value = today;
  inp.addEventListener("change", async () => {
    STATE.as_of = (inp.value && inp.value !== inp.max) ? inp.value : null;
    await reloadForDate();
  });
  document.getElementById("todayBtn").addEventListener("click", async () => {
    STATE.as_of = null; inp.value = inp.max; await reloadForDate();
  });
}

async function reloadForDate() {
  const inp = document.getElementById("asOfDate");
  const el = document.getElementById("rateStrip");
  if (el) {
    el.innerHTML = `<span class="muted">Computing the 6-month average to ${STATE.as_of || "today"}… ` +
      `fetching historical RBI rates if needed.</span>`;
  }
  inp.disabled = true;
  try { await boot({ fetch: true }); }
  catch (e) { if (el) el.innerHTML = `<span class="warn">Could not load rates for that date: ${e}</span>`; }
  finally { inp.disabled = false; }
}

const CATEGORY_KICKER = {
  jurisdictional_parties: "Competition Act 2002 · S.5(a)(i)",
  jurisdictional_group: "Competition Act 2002 · S.5(a)(ii)",
  de_minimis: "MCA S.O. 1132(E), 07-Mar-2024",
  deal_value: "Competition Act 2002 · S.5(d)",
  sbo: "Combination Regulations 2024",
};

function renderTable() {
  const b = STATE.boot, host = document.getElementById("thresholdTable");
  let html = `<div class="table-scroll"><table class="threshold-table">
    <thead><tr><th>Threshold</th><th>As enacted</th><th>Value · ${STATE.ccy}</th></tr></thead><tbody>`;
  for (const [key, label] of b.categories) {
    const rows = b.thresholds.filter(t => t.category === key);
    if (!rows.length) continue;
    const kicker = CATEGORY_KICKER[key];
    html += `<tr class="cat-row"><td colspan="3">
        ${kicker ? `<span class="cat-kicker">${kicker}</span>` : ""}
        <div class="sch-head"><span class="sch-dot" aria-hidden="true"></span>${label}</div></td></tr>`;
    for (const t of rows) {
      const isMoney = t.kind === "monetary";
      const isRatio = t.kind === "ratio" && t.abs_value_inr != null;
      let conv, enacted;
      if (isMoney) {
        conv = fmtValue(t.value_inr);
        enacted = t.statutory;
      } else if (isRatio) {
        conv = `≥ ${t.ratio_pct}% of global &amp; &gt; ${fmtValue(t.abs_value_inr)}`;
        enacted = "—";
      } else {
        conv = `<span class="muted">${t.statutory}</span>`;
        enacted = "—";
      }
      if (t.india_leg) {
        enacted += `<span class="legv">India leg · ${t.india_leg.statutory}</span>`;
        if (t.india_leg.value_inr != null) conv += `<span class="legv">India leg · ${fmtValue(t.india_leg.value_inr)}</span>`;
      }
      html += `<tr class="data-row">
        <td><div class="th-label">${t.label}</div>
            ${t.note ? `<div class="th-note">${t.note}</div>` : ""}
            <div class="cite">${t.citation}${t.effective ? " · eff. " + t.effective : ""}</div></td>
        <td class="lg-enacted">${enacted}</td>
        <td class="lg-value${isMoney || isRatio ? "" : " muted"}">${conv}</td>
      </tr>`;
    }
  }
  html += `</tbody></table></div>`;
  host.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Toggles
// ---------------------------------------------------------------------------
function initToggles() {
  document.querySelectorAll("#ccyToggle .seg button").forEach(btn => {
    btn.addEventListener("click", () => {
      setActive("#ccyToggle .seg button", btn);
      document.getElementById("otherCcy").value = "";
      STATE.ccy = btn.dataset.ccy; renderTable();
    });
  });
  document.getElementById("otherCcy").addEventListener("change", (e) => {
    if (!e.target.value) return;
    document.querySelectorAll("#ccyToggle .seg button").forEach(b => b.classList.remove("active"));
    STATE.ccy = e.target.value; renderTable();
  });
  document.querySelectorAll("#numToggle .seg button").forEach(btn => {
    btn.addEventListener("click", () => {
      setActive("#numToggle .seg button", btn);
      STATE.sys = btn.dataset.sys; renderTable();
    });
  });
}
function setActive(sel, btn) {
  document.querySelectorAll(sel).forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
}

// ---------------------------------------------------------------------------
// Rate refresh
// ---------------------------------------------------------------------------
async function refreshRates() {
  const el = document.getElementById("refreshStatus") || document.getElementById("rateStrip");
  if (el) el.innerHTML = `<span class="muted">Fetching ~6 months of RBI rates…</span>`;
  try {
    const r = await fetch("/refresh-rates", { method: "POST" });
    const d = await r.json();
    if (d.ok) { if (el) el.innerHTML = ""; await boot(); }
    else if (el) el.innerHTML = `<span class="warn">Fetch failed: ${d.error || ""}</span>`;
  } catch (e) { if (el) el.innerHTML = `<span class="warn">Fetch failed: ${e}</span>`; }
}

// ---------------------------------------------------------------------------
window.refreshRates = refreshRates;
document.addEventListener("DOMContentLoaded", async () => {
  initToggles();
  await boot();
  initDate();
});
