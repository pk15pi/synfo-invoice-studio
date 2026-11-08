/* Shared filter-bar helpers: remember the last filters per page (offered as a link, never auto-applied),
   forget them on Clear, and show option names in the "Filtered:" summary. */
(() => {
  const actions = document.querySelectorAll('[data-filter-actions]');
  if (!actions.length) return;
  const KEY = 'qb-filters:' + location.pathname;
  const IGNORE = new Set(['sort', 'direction', 'page', 'export', 'download', 'csrfmiddlewaretoken']);
  const meaningful = qs => { for (const [k, v] of new URLSearchParams(qs)) { if (!IGNORE.has(k) && v) return true; } return false; };
  let store = null;
  try { store = window.localStorage; } catch (e) { store = null; }
  if (store) {
    try {
      if (location.search && meaningful(location.search)) {
        store.setItem(KEY, location.search.slice(1));
      } else if (!location.search) {
        const saved = store.getItem(KEY);
        if (saved) actions.forEach(box => {
          const link = document.createElement('a');
          link.className = 'filter-restore'; link.dataset.filterRestore = '1';
          link.href = location.pathname + '?' + saved; link.textContent = 'Use my last filters';
          box.appendChild(link);
        });
      }
    } catch (e) { /* storage full or blocked: nothing to remember */ }
  }
  document.querySelectorAll('[data-filter-clear]').forEach(a => a.addEventListener('click', () => {
    try { if (store) store.removeItem(KEY); } catch (e) { /* ignore */ }
  }));
  document.querySelectorAll('[data-filter-summary] [data-filter-key]').forEach(span => {
    const key = span.dataset.filterKey;
    if (key === 'period') return;
    const scope = span.closest('form') || document;
    const el = scope.querySelector('select[name="' + key + '"]') || document.querySelector('select[name="' + key + '"]');
    if (el && el.selectedIndex >= 0 && el.value) {
      const label = span.textContent.split(':')[0];
      span.textContent = label + ': ' + el.options[el.selectedIndex].text.trim();
    }
  });
})();