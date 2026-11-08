/* Searchable dropdowns (country / state / district) for the customer form.
   The real <select> stays in the form and holds the value; a text box + listbox sits on top of it.
   Typing filters the select's own options; only an option from the list can be chosen.
   Country -> State -> District cascade: options are fetched for the chosen parent and
   a child value that no longer belongs is cleared. */
(() => {
  const combos = {};
  let uid = 0;
  function build(select) {
    const id = 'qbcb' + (++uid);
    const wrap = document.createElement('div');
    wrap.className = 'qb-combo';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'field qb-combo-input';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id);
    input.autocomplete = 'off';
    if (select.id) { const lab = document.querySelector(`label[for="${select.id}"]`); if (lab) { lab.htmlFor = select.id + '_search'; } input.id = select.id + '_search'; }
    if (select.required) input.required = true;
    const list = document.createElement('ul');
    list.className = 'qb-combo-list';
    list.id = id;
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    select.parentNode.insertBefore(wrap, select);
    wrap.append(input, list, select);
    select.classList.add('qb-combo-select');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    let active = -1;
    const opts = () => [...select.options].filter(o => o.value !== '');
    const selectedText = () => (select.selectedOptions[0] && select.value !== '') ? select.selectedOptions[0].textContent : '';
    const sync = () => { input.value = selectedText(); input.placeholder = (select.options[0] && select.options[0].value === '') ? select.options[0].textContent : ''; };
    function render(q) {
      const term = (q || '').trim().toLowerCase();
      list.innerHTML = '';
      const hits = opts().filter(o => !term || o.textContent.toLowerCase().includes(term));
      hits.slice(0, 300).forEach((o, i) => {
        const li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.id = `${id}-o${i}`;
        li.dataset.value = o.value;
        li.textContent = o.textContent;
        if (o.value === select.value) li.setAttribute('aria-selected', 'true');
        li.addEventListener('mousedown', e => { e.preventDefault(); choose(o.value); });
        list.append(li);
      });
      if (!hits.length) { const li = document.createElement('li'); li.className = 'qb-combo-empty'; li.textContent = 'No matches'; list.append(li); }
      active = -1;
      open(true);
    }
    function open(on) { list.hidden = !on; input.setAttribute('aria-expanded', on ? 'true' : 'false'); if (!on) input.removeAttribute('aria-activedescendant'); }
    function choose(value) {
      const changed = select.value !== value;
      select.value = value;
      sync(); open(false);
      if (changed) select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function move(d) {
      const items = [...list.querySelectorAll('[role=option]')];
      if (!items.length) return;
      active = (active + d + items.length) % items.length;
      items.forEach((li, i) => li.classList.toggle('on', i === active));
      items[active].scrollIntoView({block: 'nearest'});
      input.setAttribute('aria-activedescendant', items[active].id);
    }
    input.addEventListener('focus', () => { input.select(); render(''); });
    input.addEventListener('input', () => render(input.value));
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); list.hidden ? render(input.value) : move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') {
        const items = [...list.querySelectorAll('[role=option]')];
        if (!list.hidden && items.length) { e.preventDefault(); choose((items[active] || (items.length === 1 ? items[0] : null) || {dataset: {}}).dataset.value ?? select.value); }
      } else if (e.key === 'Escape') { sync(); open(false); }
    });
    input.addEventListener('blur', () => {
      const t = input.value.trim().toLowerCase();
      if (!t) { if (!select.required || select.value === '') choose(''); else sync(); }
      else {
        const exact = opts().find(o => o.textContent.trim().toLowerCase() === t);
        if (exact) choose(exact.value); else sync();  // free text is never accepted
      }
      open(false);
    });
    sync();
    return {select, input, sync, setOptions(rows, keep) {
      const first = select.options[0] && select.options[0].value === '' ? select.options[0].cloneNode(true) : null;
      select.innerHTML = '';
      if (first) select.append(first);
      rows.forEach(([v, t]) => { const o = new Option(t, v); select.append(o); });
      select.value = rows.some(([v]) => String(v) === String(keep)) ? String(keep) : '';
      sync();
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }};
  }
  document.querySelectorAll('select[data-combo]').forEach(s => { combos[s.dataset.combo] = build(s); });
  async function cascade(parent, child, param) {
    if (!combos[parent] || !combos[child]) return;
    combos[parent].select.addEventListener('change', async () => {
      const c = combos[child], v = combos[parent].select.value, keep = c.select.value;
      if (!v) { c.setOptions([], ''); return; }
      try {
        const r = await fetch(`${c.select.dataset.src}?${param}=${encodeURIComponent(v)}`, {headers: {'Accept': 'application/json'}});
        const data = await r.json();
        c.setOptions(data.options || [], keep);
      } catch (_) { c.setOptions([], ''); }
    });
  }
  cascade('country', 'state', 'country');
  cascade('state', 'district', 'state');
  /* State is mandatory only for India; PIN hint follows the country. */
  const country = combos.country, state = combos.state, pin = document.getElementById('id_pin_code');
  function india() {
    const o = country && country.select.selectedOptions[0];
    const isIn = !!o && o.textContent.trim() === 'India';
    if (state) { state.select.required = isIn; state.input.required = isIn; state.input.closest('.ca-field')?.classList.toggle('is-required', isIn); }
    if (pin) { pin.placeholder = isIn ? '6-digit PIN (optional)' : 'Postal code (optional)'; pin.title = isIn ? 'Indian PIN codes have 6 digits. Optional.' : 'Optional.'; }
  }
  if (country) { country.select.addEventListener('change', india); india(); }
})();