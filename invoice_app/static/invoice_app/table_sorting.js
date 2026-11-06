(() => {
  'use strict';

  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  const statusOrder = ['draft', 'pending', 'submitted', 'under review', 'under_review', 'returned',
    'approved', 'issued', 'sent', 'acknowledged', 'signed', 'paid', 'reimbursed', 'active', 'inactive',
    'unreconciled', 'partially matched', 'partial', 'matched', 'ignored', 'rejected', 'cancelled', 'canceled', 'expired', 'overdue'];
  const statusRank = new Map(statusOrder.map((value, index) => [value, index]));
  const actionHeader = /\b(actions?|select|checkbox|edit|delete|view|download|open|remove|close|reopen|preview|export|match)\b/i;
  const dateHeader = /date|time|when|uploaded|created|updated|joined|expiry|expires|due/i;
  const numericHeader = /amount|total|balance|debit|credit|withdrawal|deposit|salary|payable|paid|claimed|approved|reimbursed|gross|net|taxable|gst|tds|principal|interest|instalment|installment|days|working|present|absent|rate|quota|employees|projects|invoices|records|files|size|qty|quantity|unit price|percent|margin|\bnum\b/i;

  function parseDate(value) {
    const text = value.trim();
    let match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
    if (match) return Date.UTC(+match[1], +match[2] - 1, +match[3], +(match[4] || 0), +(match[5] || 0), +(match[6] || 0));
    match = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
    if (match) {
      let year = +match[3];
      if (year < 100) year += year < 50 ? 2000 : 1900;
      return Date.UTC(year, +match[2] - 1, +match[1], +(match[4] || 0), +(match[5] || 0), +(match[6] || 0));
    }
    const parsed = Date.parse(text);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function parseNumber(value) {
    let text = value.trim();
    if (!text || /^[-–—]$/.test(text)) return null;
    const negative = /^\(.*\)$/.test(text);
    text = text.replace(/[₹$€£,\s]/g, '').replace(/%$/, '').replace(/[()]/g, '');
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null;
    const number = Number(text);
    return Number.isFinite(number) ? (negative ? -number : number) : null;
  }

  function columnType(th, values) {
    if (th.dataset.sortType) return th.dataset.sortType.toLowerCase();
    const label = th.textContent.trim();
    if (dateHeader.test(label)) return 'date';
    if (/%|percent|margin/i.test(label)) return 'percent';
    if (numericHeader.test(label)) return 'number';
    const sample = values.filter(Boolean).slice(0, 12);
    if (sample.length && sample.every(value => parseDate(value) !== null)) return 'date';
    if (sample.length && sample.every(value => parseNumber(value) !== null)) return 'number';
    if (/status|state/i.test(label)) return 'status';
    return 'text';
  }

  function compareValues(a, b, type, direction) {
    const aBlank = a === '' || a === null || a === undefined;
    const bBlank = b === '' || b === null || b === undefined;
    if (aBlank || bBlank) return aBlank === bBlank ? 0 : (aBlank ? 1 : -1); // blanks stay last in both directions
    let result = 0;
    if (type === 'date') {
      const av = parseDate(a); const bv = parseDate(b);
      result = av === null || bv === null ? collator.compare(a, b) : av - bv;
    } else if (type === 'number' || type === 'percent' || type === 'currency') {
      const av = parseNumber(a); const bv = parseNumber(b);
      result = av === null || bv === null ? collator.compare(a, b) : av - bv;
    } else if (type === 'status') {
      const av = String(a).trim().toLowerCase(); const bv = String(b).trim().toLowerCase();
      const ar = statusRank.has(av) ? statusRank.get(av) : null;
      const br = statusRank.has(bv) ? statusRank.get(bv) : null;
      result = ar !== null && br !== null ? ar - br : collator.compare(a, b);
    } else {
      result = collator.compare(a, b); // numeric-aware, case-insensitive natural text order
    }
    return direction === 'desc' ? -result : result;
  }

  function isNonDataRow(row) {
    return row.hasAttribute('data-sort-ignore') || /(^|\s)(group|subtotal|total|summary)(\s|$)/i.test(row.className) ||
      row.querySelector('th[colspan],td[colspan]') || row.querySelector('th');
  }

  function hasUnsafeControls(row) {
    return Array.from(row.querySelectorAll('input,select,textarea')).some(control => {
      if (control.closest('details')) return false;
      return !['checkbox', 'radio', 'hidden'].includes((control.type || '').toLowerCase());
    });
  }

  function addIndicator(th) {
    let indicator = th.querySelector(':scope > .table-sort-indicator');
    if (!indicator) {
      indicator = document.createElement('span');
      indicator.className = 'table-sort-indicator';
      indicator.setAttribute('aria-hidden', 'true');
      indicator.textContent = '↕';
      th.appendChild(indicator);
    }
    return indicator;
  }

  function decorateHeader(table, th, key, mode) {
    const indicator = addIndicator(th);
    th.classList.add('table-sortable-header');
    th.dataset.sortKey = key;
    th.tabIndex = 0;
    th.setAttribute('aria-sort', 'none');
    th.title = mode === 'server' ? 'Sort all filtered rows by this column' : 'Sort rows by this column';
    th.addEventListener('click', event => {
      if (event.target.closest('a,button,input,select,textarea')) return;
      if (mode === 'server') serverSort(table, key);
      else localSort(table, th, key, indicator);
    });
    th.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); th.click(); }
    });
    return indicator;
  }

  function serverSort(table, key) {
    const params = new URLSearchParams(window.location.search);
    const isSame = params.get('sort') === key &&
      (table.dataset.serverSort !== 'report' || params.get('sort_table') === (table.dataset.sortTable || '0'));
    const direction = isSame && params.get('direction') === 'asc' ? 'desc' : 'asc';
    params.set('sort', key);
    params.set('direction', direction);
    params.delete('page');
    if (table.dataset.serverSort === 'report') params.set('sort_table', table.dataset.sortTable || '0');
    else params.delete('sort_table');
    const query = params.toString();
    window.location.assign(`${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
  }

  function localSort(table, th, key, indicator) {
    const previous = table.dataset.sortKey;
    const direction = previous === key && table.dataset.sortDirection === 'asc' ? 'desc' : 'asc';
    const body = table.tBodies[0];
    const rows = Array.from(body.rows);
    const movable = rows.filter(row => !isNonDataRow(row));
    const cells = movable.map(row => row.cells[th.cellIndex]).filter(Boolean);
    const type = columnType(th, cells.map(cell => cell.dataset.sortValue ?? cell.textContent.trim()));
    const rowIndex = new Map(movable.map((row, index) => [row, index]));
    movable.sort((ra, rb) => {
      const ca = ra.cells[th.cellIndex]; const cb = rb.cells[th.cellIndex];
      const a = ca ? (ca.dataset.sortValue ?? ca.textContent.trim()) : '';
      const b = cb ? (cb.dataset.sortValue ?? cb.textContent.trim()) : '';
      return compareValues(a, b, type, direction) || rowIndex.get(ra) - rowIndex.get(rb);
    });
    let next = 0;
    body.replaceChildren(...rows.map(row => isNonDataRow(row) ? row : movable[next++]));
    table.dataset.sortKey = key;
    table.dataset.sortDirection = direction;
    table.querySelectorAll('thead th.table-sortable-header').forEach(header => {
      const active = header === th;
      header.classList.toggle('table-sort-active', active);
      header.setAttribute('aria-sort', active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none');
      const icon = addIndicator(header);
      icon.textContent = active ? (direction === 'asc' ? '↑' : '↓') : '↕';
    });
  }

  function applyServerIndicators(table) {
    const params = new URLSearchParams(window.location.search);
    const key = params.get('sort');
    const matchesTable = table.dataset.serverSort !== 'report' || params.get('sort_table') === (table.dataset.sortTable || '0');
    if (!key || !matchesTable) return;
    const th = Array.from(table.tHead.rows[0].cells).find(cell => cell.dataset.sortKey === key);
    if (!th) return;
    const direction = params.get('direction') === 'desc' ? 'desc' : 'asc';
    th.classList.add('table-sort-active');
    th.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
    addIndicator(th).textContent = direction === 'asc' ? '↑' : '↓';
  }

  function enhance(table) {
    if (table.dataset.sortable === 'false' || table.dataset.noSort !== undefined || !table.tHead || !table.tBodies.length) return;
    const headRows = table.tHead.rows;
    if (headRows.length !== 1) return;
    const header = headRows[0];
    if (table.tBodies.length !== 1) return;
    const bodyRows = Array.from(table.tBodies[0].rows);
    const specialRows = bodyRows.filter(isNonDataRow);
    if (specialRows.some(row => !row.hasAttribute('data-sort-ignore'))) return; // grouped/subtotal bodies need their custom row order
    const candidateRows = bodyRows.filter(row => !isNonDataRow(row));
    if (candidateRows.length < 2) return;
    const mode = table.dataset.serverSort ? 'server' : 'client';
    // Client-side reordering of editable line-item grids can disrupt row calculations. Server sorting
    // retains complete row markup and is safe for action forms and checkbox selections.
    if (mode === 'client' && candidateRows.some(hasUnsafeControls)) return;
    Array.from(header.cells).forEach((th, index) => {
      const label = th.textContent.trim();
      if (!label || actionHeader.test(label) || th.querySelector('input,select,textarea,button')) return;
      if (mode === 'server' && !th.dataset.sortKey) return;
      if (th.querySelector('a') && mode !== 'server') return;
      const key = th.dataset.sortKey || String(index);
      decorateHeader(table, th, key, mode);
    });
    if (mode === 'server') applyServerIndicators(table);
  }

  document.querySelectorAll('table').forEach(enhance);
  // Carry an active sort through GET filters that otherwise submit only their own fields.
  const current = new URLSearchParams(window.location.search);
  document.querySelectorAll('form[method="get"],form[method="GET"]').forEach(form => {
    form.addEventListener('submit', () => {
      ['sort', 'direction', 'sort_table'].forEach(name => {
        if (!current.has(name) || form.elements.namedItem(name)) return;
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = name; input.value = current.get(name); form.appendChild(input);
      });
    });
  });
})();
