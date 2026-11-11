/* Date entry helpers. Flatpickr provides the calendar on pointer devices;
   touch devices keep the browser's native picker. The helpers also add a
   Today shortcut to single-date filters and a "quick range" chooser beside
   paired filter dates. Those controls are unnamed and never submit, so query
   names, values and the existing filter workflow do not change. */
(() => {
  const pad = n => String(n).padStart(2, '0');
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const atNoon = d => { const x = new Date(d); x.setHours(12, 0, 0, 0); return x; };
  const addDays = (d, n) => { const x = atNoon(d); x.setDate(x.getDate() + n); return x; };
  const monthStart = d => new Date(d.getFullYear(), d.getMonth(), 1);
  const monthEnd = d => new Date(d.getFullYear(), d.getMonth() + 1, 0);

  const ranges = () => {
    const today = atNoon(new Date());
    const fyStartYear = today.getMonth() >= 3 ? today.getFullYear() : today.getFullYear() - 1;
    const monthsSinceApril = (today.getMonth() - 3 + 12) % 12;
    let quarterMonth = 3 + Math.floor(monthsSinceApril / 3) * 3;
    if (quarterMonth > 11) quarterMonth -= 12;
    const quarterStart = new Date(today.getFullYear(), quarterMonth, 1);
    return [
      ['Today', today, today],
      ['Last 7 days', addDays(today, -6), today],
      ['This month', monthStart(today), monthEnd(today)],
      ['Last month', new Date(today.getFullYear(), today.getMonth() - 1, 1), new Date(today.getFullYear(), today.getMonth(), 0)],
      ['This quarter', quarterStart, new Date(quarterStart.getFullYear(), quarterStart.getMonth() + 3, 0)],
      ['This financial year', new Date(fyStartYear, 3, 1), new Date(fyStartYear + 1, 2, 31)],
    ];
  };

  const TOKEN_PAIR = { from: 'to', to: 'from', start: 'end', end: 'start', min: 'max', max: 'min', after: 'before', before: 'after' };
  const START_TOKENS = new Set(['from', 'start', 'min', 'after']);
  const SUFFIX_PAIR = /^(.*)([_-])(from|to|start|end|min|max|after|before)((?:[_-]filter)?)$/i;
  const PREFIX_PAIR = /^(from|to|start|end|min|max|after|before)([_-])(.*)$/i;

  const keyOf = el => (el.getAttribute('name') || el.id || '').trim();
  const pairFor = el => {
    const key = keyOf(el);
    if (!key) return null;
    let match = key.match(SUFFIX_PAIR);
    if (match) {
      const token = match[3].toLowerCase();
      return {
        other: `${match[1]}${match[2]}${TOKEN_PAIR[token]}${match[4]}`,
        starts: START_TOKENS.has(token),
      };
    }
    match = key.match(PREFIX_PAIR);
    if (match) {
      const token = match[1].toLowerCase();
      return {
        other: `${TOKEN_PAIR[token]}${match[2]}${match[3]}`,
        starts: START_TOKENS.has(token),
      };
    }
    return null;
  };

  const searchRoot = el => el.closest('.compact-date-range')
    || el.closest('form, .more-filters-body, .bk-review-filters, .filters, .list-filters')
    || document;

  const findDate = (root, key) => Array.from(root.querySelectorAll('input'))
    .find(candidate => keyOf(candidate) === key
      && (candidate.type === 'date' || candidate.classList.contains('flatpickr-input'))) || null;

  const isFilterContext = el => {
    if (el.closest('.standard-period-filter, td, th')) return false;
    const form = el.closest('form');
    const formIsFilter = form && (
      (form.method || '').toLowerCase() === 'get'
      || form.matches('[data-filter-form], .qb-filter-form, .filters, .filter-form, .compact-filters, .list-filters')
    );
    return Boolean(formIsFilter)
      || Boolean(el.closest('.more-filters-body, .bk-review-filters, .list-filters, .compact-filters, [data-filter-actions]'));
  };

  const fire = el => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const pickers = new WeakMap();
  const useCustomPicker = () => typeof window.flatpickr === 'function'
    && !(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);

  const attachPicker = input => {
    if (!useCustomPicker() || pickers.has(input)) return;
    const picker = window.flatpickr(input, {
      dateFormat: 'Y-m-d',
      allowInput: true,
      disableMobile: true,
    });
    pickers.set(input, picker);
    input.addEventListener('change', () => {
      const selected = picker.selectedDates[0];
      const shown = selected ? iso(selected) : '';
      if ((input.value || '') === shown) return;
      if (!input.value) picker.clear(false);
      else picker.setDate(input.value, false);
    });
  };

  const setValue = (input, value) => {
    input.value = value;
    const picker = pickers.get(input);
    if (picker) {
      if (!value) picker.clear(false);
      else picker.setDate(value, false);
    }
    fire(input);
  };

  const applyRange = (start, end, first, second) => {
    setValue(start, iso(first));
    setValue(end, iso(second));
  };

  let applying = false;

  const buildRangePicker = (start, end) => {
    const box = document.createElement('span');
    box.className = 'qf-date-presets';
    const label = document.createElement('span');
    label.className = 'qf-date-presets-label';
    label.textContent = 'Quick range';
    const select = document.createElement('select');
    select.className = 'field qf-date-preset-select';
    select.setAttribute('aria-label', 'Quick date range');
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Choose a range';
    select.append(placeholder);
    ranges().forEach(([name], index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = name;
      select.append(option);
    });
    select.addEventListener('change', () => {
      if (select.value === '') return;
      const [, first, second] = ranges()[Number(select.value)];
      applying = true;
      applyRange(start, end, first, second);
      applying = false;
    });
    const reset = () => { if (!applying) select.value = ''; };
    start.addEventListener('change', reset);
    end.addEventListener('change', reset);
    box.append(label, select);
    return box;
  };

  const buildTodayButton = input => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'qf-date-today';
    button.textContent = 'Today';
    button.setAttribute('aria-label', 'Set this date to today');
    button.addEventListener('click', () => {
      setValue(input, iso(atNoon(new Date())));
    });
    return button;
  };

  const enhanceInput = input => {
    if (input.dataset.qfDateReady || input.type !== 'date') return;
    input.dataset.qfDateReady = '1';
    attachPicker(input);
    if (input.disabled || !isFilterContext(input)) return;

    const pair = pairFor(input);
    const other = pair && findDate(searchRoot(input), pair.other);
    if (pair && other) {
      if (input.dataset.qfDatePaired || other.dataset.qfDatePaired) return;
      const start = pair.starts ? input : other;
      const end = pair.starts ? other : input;
      if (!start.name && !start.id) return;
      start.dataset.qfDatePaired = '1';
      end.dataset.qfDatePaired = '1';
      const sharedField = start.closest('.compact-date-range');
      const anchor = sharedField || end.closest('label') || end;
      anchor.insertAdjacentElement('afterend', buildRangePicker(start, end));
      return;
    }

    const anchor = input.closest('label') || input;
    anchor.insertAdjacentElement('afterend', buildTodayButton(input));
  };

  const enhancePeriodRange = root => {
    root.querySelectorAll('[data-period-quick-range]:not([data-qfDateReady])').forEach(select => {
      select.dataset.qfDateReady = '1';
      const scope = select.closest('[data-standard-period-filter]');
      const start = scope && scope.querySelector('input[name="date_from"]');
      const end = scope && scope.querySelector('input[name="date_to"]');
      if (!start || !end) return;
      select.addEventListener('change', () => {
        if (select.value === '') return;
        const [, first, second] = ranges()[Number(select.value)];
        applying = true;
        applyRange(start, end, first, second);
        applying = false;
      });
      const reset = () => { if (!applying) select.value = ''; };
      start.addEventListener('change', reset);
      end.addEventListener('change', reset);
    });
  };

  const enhance = root => {
    enhancePeriodRange(root);
    if (root.matches && root.matches('input[type="date"]')) enhanceInput(root);
    root.querySelectorAll('input[type="date"]').forEach(enhanceInput);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => enhance(document));
  } else {
    enhance(document);
  }

  new MutationObserver(mutations => {
    mutations.forEach(mutation => mutation.addedNodes.forEach(node => {
      if (node.nodeType === 1) enhance(node);
    }));
  }).observe(document.body, { childList: true, subtree: true });
})();
