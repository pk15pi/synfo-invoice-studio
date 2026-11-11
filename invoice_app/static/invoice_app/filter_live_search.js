/* Submit a server-side filter form while the user types in its search box, so results
   update without pressing Apply. The field keeps focus and caret after the reload. */
(() => {
  const forms = document.querySelectorAll('form[data-filter-form][data-live-search]');
  if (!forms.length) return;
  const KEY = 'qb-live-search-focus';
  const DELAY = 400;
  let store = null;
  try { store = window.sessionStorage; } catch (e) { store = null; }

  const isSearchInput = input => !input.disabled && !input.readOnly && !input.matches(
    '.flatpickr-input, [type="hidden"], [type="date"], [type="number"], [type="email"], [type="password"], [type="file"]'
  );

  const submitLater = (input, state) => {
    window.clearTimeout(state.timer);
    state.timer = window.setTimeout(() => {
      if (input.value === state.value) return;
      state.value = input.value;
      try { if (store) store.setItem(KEY, location.pathname + '\n' + input.name); } catch (e) { /* blocked */ }
      if (!input.form) return;
      if (typeof input.form.requestSubmit === 'function') input.form.requestSubmit();
      else input.form.submit();
    }, DELAY);
  };

  document.querySelectorAll('form[data-filter-form][data-live-search] input[name="q"], form[data-filter-form][data-live-search] input[name$="_search"]').forEach(input => {
    if (!isSearchInput(input)) return;
    const state = { value: input.value, timer: 0 };
    input.addEventListener('input', () => {
      if (input.isComposing) return;
      submitLater(input, state);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') window.clearTimeout(state.timer);
    });
  });

  if (!store) return;
  let saved = null;
  try { saved = store.getItem(KEY); } catch (e) { return; }
  if (!saved) return;
  try { store.removeItem(KEY); } catch (e) { /* ignore */ }
  const separator = saved.indexOf('\n');
  if (separator < 0 || saved.slice(0, separator) !== location.pathname) return;
  let input = null;
  try {
    input = document.querySelector(
      'form[data-filter-form][data-live-search] input[name="' + CSS.escape(saved.slice(separator + 1)) + '"]'
    );
  } catch (e) { return; }
  if (!input || !isSearchInput(input)) return;
  input.focus({ preventScroll: true });
  if (typeof input.setSelectionRange === 'function') {
    const end = input.value.length;
    input.setSelectionRange(end, end);
  }
})();
