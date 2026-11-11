/* Searchable accessory picker for the asset assignment form.
   The text input keeps free-text accessories; assets chosen from the filtered
   list are submitted as hidden accessory_assets values. */
(() => {
  const input = document.getElementById('id_accessories');
  if (!input || input.dataset.accessoryPicker !== '1') return;
  const options = JSON.parse(input.dataset.accessoryOptions || '[]');
  const byId = id => options.find(o => o.id === String(id));
  const mainSelect = document.getElementById('id_asset');
  const mainValue = () => (mainSelect ? mainSelect.value : '');

  const hidden = Array.from(document.querySelectorAll('input[name="accessory_assets"]'));
  const selected = new Set(hidden.map(el => el.value).filter(Boolean));
  hidden.forEach(el => el.remove());

  const wrap = document.createElement('div');
  wrap.className = 'asset-accessory-picker';
  input.parentNode.insertBefore(wrap, input);
  const chips = document.createElement('div');
  chips.className = 'asset-accessory-chips';
  const control = document.createElement('div');
  control.className = 'asset-accessory-control';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'asset-accessory-toggle';
  toggle.setAttribute('aria-label', 'Show available accessories');
  toggle.textContent = '\u25be';
  const list = document.createElement('ul');
  list.className = 'asset-accessory-list';
  list.id = 'asset-accessory-list';
  list.setAttribute('role', 'listbox');
  list.hidden = true;
  const hiddenWrap = document.createElement('div');
  hiddenWrap.hidden = true;
  wrap.append(chips, control, hiddenWrap);
  control.append(input, toggle, list);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-controls', list.id);

  let active = -1;

  const addHidden = id => {
    const el = document.createElement('input');
    el.type = 'hidden';
    el.name = 'accessory_assets';
    el.value = id;
    hiddenWrap.append(el);
  };
  Array.from(selected).forEach(addHidden);

  const query = () => {
    const raw = input.value;
    const at = raw.lastIndexOf(',');
    return raw.slice(at + 1).trim();
  };

  const open = () => {
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };
  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  };

  const visibleOptions = () => {
    const term = query().toLowerCase();
    const main = mainValue();
    return options.filter(o => (
      !selected.has(o.id) && o.id !== main
      && (!term || `${o.code} ${o.name}`.toLowerCase().includes(term))
    ));
  };

  const render = () => {
    list.innerHTML = '';
    const hits = visibleOptions();
    hits.slice(0, 80).forEach((o, i) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.id = `asset-accessory-option-${i}`;
      li.dataset.id = o.id;
      const label = document.createElement('span');
      label.textContent = `${o.code} \u2014 ${o.name}`;
      li.append(label);
      if (o.available > 1) {
        const count = document.createElement('small');
        count.className = 'muted';
        count.textContent = `${o.available} available`;
        li.append(count);
      }
      li.addEventListener('mousedown', e => e.preventDefault());
      li.addEventListener('click', () => choose(o));
      list.append(li);
    });
    if (!hits.length) {
      const li = document.createElement('li');
      li.className = 'none';
      li.textContent = options.length
        ? (query() ? 'No asset matches; your text stays as a manual accessory.' : 'No other assets are available.')
        : 'No register assets are available.';
      list.append(li);
    }
    active = -1;
  };

  const show = () => {
    render();
    open();
  };

  const renderChips = () => {
    chips.innerHTML = '';
    chips.hidden = selected.size === 0;
    selected.forEach(id => {
      const o = byId(id);
      const chip = document.createElement('span');
      chip.className = 'asset-accessory-chip';
      const label = document.createElement('span');
      label.textContent = o ? `${o.code} \u2014 ${o.name}` : `Asset #${id}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '\u00d7';
      remove.setAttribute('aria-label', `Remove ${o ? o.code : `asset ${id}`}`);
      remove.addEventListener('click', () => removeAsset(id));
      chip.append(label, remove);
      chips.append(chip);
    });
  };

  const removeAsset = id => {
    if (!selected.delete(id)) return;
    hiddenWrap.querySelectorAll('input[name="accessory_assets"]').forEach(el => {
      if (el.value === id) el.remove();
    });
    renderChips();
    if (!list.hidden) render();
  };

  function choose(option) {
    if (!option || selected.has(option.id)) return;
    selected.add(option.id);
    addHidden(option.id);
    renderChips();
    const raw = input.value;
    const at = raw.lastIndexOf(',');
    input.value = at >= 0 ? raw.slice(0, at).trim() : '';
    close();
    input.focus();
  }

  const move = step => {
    const items = [...list.querySelectorAll('li[role="option"]')];
    if (!items.length) return;
    active = (active + step + items.length) % items.length;
    items.forEach((li, i) => li.classList.toggle('active', i === active));
    items[active].scrollIntoView({ block: 'nearest' });
    input.setAttribute('aria-activedescendant', items[active].id);
  };

  input.addEventListener('focus', () => {
    if (!input.disabled) show();
  });
  input.addEventListener('input', () => show());
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (list.hidden) show();
      else move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (list.hidden) show();
      else move(-1);
    } else if (e.key === 'Enter') {
      if (!list.hidden) {
        e.preventDefault();
        const items = [...list.querySelectorAll('li[role="option"]')];
        const pick = items[active] || (items.length === 1 ? items[0] : null);
        if (pick) choose(byId(pick.dataset.id));
        else close();
      }
    } else if (e.key === 'Escape') {
      close();
    }
  });
  input.addEventListener('blur', () => setTimeout(close, 140));

  toggle.addEventListener('mousedown', e => e.preventDefault());
  toggle.addEventListener('click', () => {
    if (input.disabled) return;
    const wasOpen = !list.hidden;
    input.focus();
    if (wasOpen) close();
    else show();
  });

  if (mainSelect) {
    mainSelect.addEventListener('change', () => {
      const main = mainValue();
      if (main && selected.has(main)) removeAsset(main);
      else if (!list.hidden) render();
    });
    if (mainValue() && selected.has(mainValue())) removeAsset(mainValue());
  }

  renderChips();
})();
