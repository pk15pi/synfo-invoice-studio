(() => {
  document.querySelectorAll('[data-open-dialog]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.querySelector(button.dataset.openDialog);
      if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
    });
  });

  document.querySelectorAll('.bk-confirm-dialog').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  const table = document.getElementById('bk-draft-table');
  if (table) {
    const controls = ['bk-row-search', 'bk-class-filter', 'bk-status-filter', 'bk-from-filter', 'bk-to-filter']
      .map((id) => document.getElementById(id)).filter(Boolean);
    const filterRows = () => {
      const search = (document.getElementById('bk-row-search')?.value || '').toLowerCase();
      const classification = document.getElementById('bk-class-filter')?.value || '';
      const status = document.getElementById('bk-status-filter')?.value || '';
      const from = document.getElementById('bk-from-filter')?.value || '';
      const to = document.getElementById('bk-to-filter')?.value || '';
      table.querySelectorAll('tbody tr[data-classification]').forEach((row) => {
        const date = row.dataset.date || '';
        row.hidden = !(row.dataset.search.includes(search) &&
          (!classification || row.dataset.classification === classification) &&
          (!status || row.dataset.status === status) && (!from || date >= from) && (!to || date <= to));
      });
    };
    controls.forEach((control) => control.addEventListener('input', filterRows));
    controls.forEach((control) => control.addEventListener('change', filterRows));
  }

  document.querySelectorAll('[data-bk-draft-fields]').forEach((fields) => {
    const select = fields.closest('tr')?.querySelector('[data-bk-classification]');
    if (!select) return;
    const syncFields = () => {
      fields.querySelectorAll('[data-bk-draft-kind]').forEach((label) => {
        label.hidden = label.dataset.bkDraftKind !== select.value;
      });
    };
    select.addEventListener('change', syncFields);
    syncFields();
  });

  document.querySelectorAll('form[data-submit-lock]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (form.dataset.submitting === 'true') {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = 'true';
      form.setAttribute('aria-busy', 'true');
      const submitter = event.submitter;
      const submittedValue = submitter && submitter.name && submitter.value;
      if (submittedValue) {
        let hidden = form.querySelector(`input[type="hidden"][name="${CSS.escape(submitter.name)}"]`);
        if (!hidden) {
          hidden = document.createElement('input');
          hidden.type = 'hidden';
          hidden.name = submitter.name;
          form.append(hidden);
        }
        hidden.value = submittedValue;
      }
      form.querySelectorAll('button[type="submit"],button:not([type])').forEach((button) => {
        button.disabled = true;
        if (button === submitter && button.dataset.loadingText) button.textContent = button.dataset.loadingText;
      });
    });
  });
})();
