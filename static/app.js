// ---- Dashboard: fetch RBI rates ----
async function refreshRates() {
  const btn = document.getElementById('refreshBtn');
  const status = document.getElementById('refreshStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Fetching from RBI…'; }
  if (status) status.innerHTML = '<span class="muted">Scraping ~6 months of daily reference rates — this takes a few seconds…</span>';
  try {
    const res = await fetch('/refresh-rates', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      status.innerHTML = `<span class="pill pill-green">Done</span> Stored ${data.fetched} days (DB now holds ${data.total_in_db}). Latest: ${data.latest || '—'}.` +
        (data.errors && data.errors.length ? ` <span class="muted">(${data.errors.length} window(s) had issues)</span>` : '') +
        ` <a href="/">Reload to see averages →</a>`;
      setTimeout(() => location.reload(), 900);
    } else {
      status.innerHTML = `<span class="pill pill-red">Failed</span> ${data.error || 'Unknown error'}`;
    }
  } catch (e) {
    status.innerHTML = `<span class="pill pill-red">Failed</span> ${e}`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Fetch RBI rates now'; }
  }
}

// ---- Check page ----
let _lastField = null;       // last-focused input element
let _selectedCand = null;    // currently selected candidate value (number)

function initCheckPage() {
  const dz = document.getElementById('dropzone');
  const input = document.getElementById('documents');
  if (!dz || !input) return;

  // track which field the user wants to fill
  document.querySelectorAll('input[data-field]').forEach(el => {
    el.addEventListener('focus', () => { _lastField = el; });
    el.addEventListener('click', () => {
      _lastField = el;
      if (_selectedCand !== null) {
        el.value = _selectedCand;
        el.classList.add('lit');
        // clear selection highlight
        document.querySelectorAll('.cand.sel').forEach(c => c.classList.remove('sel'));
        _selectedCand = null;
      }
    });
  });

  ['dragenter', 'dragover'].forEach(ev =>
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(ev =>
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', e => {
    input.files = e.dataTransfer.files;
    uploadForExtract();
  });
  input.addEventListener('change', uploadForExtract);
}

async function uploadForExtract() {
  const input = document.getElementById('documents');
  const notes = document.getElementById('extractNotes');
  const box = document.getElementById('candidates');
  if (!input.files.length) return;
  notes.textContent = 'Reading document(s)…';
  box.innerHTML = '';
  const fd = new FormData();
  for (const f of input.files) fd.append('documents', f);
  try {
    const res = await fetch('/extract', { method: 'POST', body: fd });
    const data = await res.json();
    const bits = [];
    bits.push(`Detected currency: <strong>${data.currency}</strong>, reporting scale: <strong>${data.scale}</strong>.`);
    if (data.notes && data.notes.length) bits.push(data.notes.join(' '));
    notes.innerHTML = bits.join(' ');

    if (!data.candidates.length) { return; }
    box.innerHTML = '<div class="muted small" style="width:100%">Detected figures — click one, then click the field to fill:</div>';
    data.candidates.forEach(c => {
      const el = document.createElement('div');
      el.className = 'cand';
      el.innerHTML = `<span class="cl">${c.label}</span>` +
        `<span class="cv">${c.display}</span>` +
        (c.scope_hint ? `<span class="ch">${c.scope_hint}</span>` : '') +
        `<span class="ch">${c.line}</span>`;
      el.addEventListener('click', () => {
        document.querySelectorAll('.cand.sel').forEach(x => x.classList.remove('sel'));
        el.classList.add('sel');
        _selectedCand = c.value_native;
        // if a field is already focused, fill immediately
        if (_lastField) {
          _lastField.value = c.value_native;
          _lastField.classList.add('lit');
          el.classList.remove('sel');
          _selectedCand = null;
        }
      });
      box.appendChild(el);
    });
    // set form currency to detected currency if available
    const ccySel = document.getElementById('currency');
    if (ccySel) {
      for (const opt of ccySel.options) if (opt.value === data.currency) ccySel.value = data.currency;
    }
  } catch (e) {
    notes.innerHTML = `<span class="pill pill-red">Error</span> ${e}`;
  }
}
