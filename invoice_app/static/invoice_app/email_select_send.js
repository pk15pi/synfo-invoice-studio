/* Shared row selection + sequential send for Email salary slips / Email invoices. */
(() => {
  const root = document.getElementById('qb-email-select');
  if (!root) return;

  const progress = document.getElementById('qb-send-progress');
  const btnGap = document.getElementById('qb-send-selected');
  const btnBulk = document.getElementById('qb-bulk-send-selected');
  const btnCancel = document.getElementById('qb-send-cancel');
  const selectAll = document.getElementById('qb-select-all');
  const csrf = document.querySelector('input[name=csrfmiddlewaretoken]')?.value
    || document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith('csrftoken='))?.split('=')[1];

  const allIds = (root.dataset.allIds || '').split(',').map(s => s.trim()).filter(Boolean);
  const gapSec = Math.max(0, parseInt(root.dataset.gap || '30', 10) || 0);
  const urlTemplate = root.dataset.urlTemplate || '';
  const params = new URLSearchParams(location.search);
  params.delete('page');
  params.delete('page_size');
  const storageKey = 'qb-email-sel:' + location.pathname + '?' + params.toString();

  let cancelled = false;
  let running = false;
  let selected = new Set();

  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) {
      const allowed = new Set(allIds);
      saved.forEach((id) => { if (allowed.has(String(id))) selected.add(String(id)); });
    }
  } catch (_) { /* ignore bad storage */ }

  function persist() {
    try { sessionStorage.setItem(storageKey, JSON.stringify([...selected])); } catch (_) { /* ignore */ }
  }

  function orderedSelected() {
    return allIds.filter((id) => selected.has(id));
  }

  function syncRowChecks() {
    document.querySelectorAll('input.qb-email-row[type=checkbox]').forEach((cb) => {
      cb.checked = selected.has(String(cb.value));
    });
  }

  function syncHeader() {
    if (!selectAll) return;
    const n = selected.size;
    const total = allIds.length;
    selectAll.indeterminate = n > 0 && n < total;
    selectAll.checked = total > 0 && n === total;
  }

  function syncButtons() {
    const n = orderedSelected().length;
    // Keep primary/secondary colors visible; only disable when SMTP is unset or a run is in progress.
    const canClick = !running && root.dataset.smtpReady === '1';
    if (btnGap) btnGap.disabled = !canClick;
    if (btnBulk) btnBulk.disabled = !canClick;
    if (!running && progress) {
      progress.textContent = n === 0
        ? 'Select one or more rows to send.'
        : `${n} selected of ${allIds.length}.`;
    }
  }

  function refresh() {
    syncRowChecks();
    syncHeader();
    syncButtons();
    persist();
  }

  document.querySelectorAll('input.qb-email-row[type=checkbox]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const id = String(cb.value);
      if (cb.checked) selected.add(id);
      else selected.delete(id);
      refresh();
    });
  });

  selectAll?.addEventListener('change', () => {
    if (selectAll.checked) allIds.forEach((id) => selected.add(id));
    else selected.clear();
    refresh();
  });

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function sendOne(id) {
    const url = urlTemplate.replace('__ID__', String(id));
    // Selected sends deliberately resent already-sent rows (skip_if_sent=0).
    const body = new URLSearchParams({ skip_if_sent: '0' });
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrf || '',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body,
      credentials: 'same-origin',
    });
    return resp.json();
  }

  async function run(gapMs, label) {
    const ids = orderedSelected();
    if (!ids.length) {
      if (progress) progress.textContent = 'Select one or more rows to send.';
      return;
    }
    cancelled = false;
    running = true;
    if (btnGap) btnGap.disabled = true;
    if (btnBulk) btnBulk.disabled = true;
    if (btnCancel) btnCancel.hidden = false;
    let sent = 0, failed = 0, skipped = 0;
    for (let i = 0; i < ids.length; i++) {
      if (cancelled) break;
      if (progress) progress.textContent = `${label}: sending ${i + 1} of ${ids.length}…`;
      try {
        const result = await sendOne(ids[i]);
        if (result.status === 'sent') sent += 1;
        else if (result.status === 'skipped') skipped += 1;
        else failed += 1;
      } catch (_) {
        failed += 1;
      }
      if (cancelled) break;
      if (gapMs > 0 && i < ids.length - 1) {
        if (progress) {
          progress.textContent = `${label}: waiting ${gapMs / 1000}s before next (${i + 1}/${ids.length} done)…`;
        }
        await sleep(gapMs);
      }
    }
    const stopped = cancelled ? ' (cancelled)' : '';
    if (progress) {
      progress.textContent = `${label} finished${stopped}: ${sent} sent, ${failed} failed, ${skipped} skipped (e.g. missing email). Reloading…`;
    }
    if (btnCancel) btnCancel.hidden = true;
    try { sessionStorage.removeItem(storageKey); } catch (_) { /* ignore */ }
    setTimeout(() => location.reload(), 800);
  }

  btnGap?.addEventListener('click', () => {
    const ids = orderedSelected();
    if (!ids.length) return;
    if (!confirm(`Send ${ids.length} selected email(s), waiting ${gapSec} second(s) between each? Already-sent selected rows will be resent.`)) return;
    run(gapSec * 1000, 'Send selected');
  });
  btnBulk?.addEventListener('click', () => {
    const ids = orderedSelected();
    if (!ids.length) return;
    if (!confirm(`Bulk send ${ids.length} selected email(s) with NO delay? Sending immediately may increase the chance emails land in junk. Already-sent selected rows will be resent.`)) return;
    run(0, 'Bulk send selected');
  });
  btnCancel?.addEventListener('click', () => {
    cancelled = true;
    if (progress) progress.textContent = 'Cancelling after the current email…';
  });

  refresh();
})();
