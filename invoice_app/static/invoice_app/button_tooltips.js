(() => {
  const descriptions = {
    save: 'Save the current changes.',
    'save settings': 'Save the current business and payroll settings.',
    'save as draft': 'Save this record as a draft.',
    'save and submit': 'Save this record and submit it for review.',
    submit: 'Submit this form for processing.',
    filter: 'Apply the selected filters to the list.',
    search: 'Search using the entered text and selected filters.',
    apply: 'Apply the selected options and refresh the results.',
    show: 'Display results for the selected options.',
    go: 'Load the selected month, year, or record.',
    reset: 'Clear the current filters and restore the default view.',
    clear: 'Clear the current selection or entered values.',
    back: 'Return to the previous page without submitting this form.',
    cancel: 'Cancel this action and return without saving.',
    'cancel entry': 'Cancel this entry and preserve it in the record history.',
    'yes, delete': 'Permanently delete the selected record.',
    delete: 'Delete the selected record.',
    remove: 'Remove the selected item from this form.',
    edit: 'Open this record so you can edit it.',
    view: 'Open this record for viewing.',
    open: 'Open the selected record.',
    preview: 'Preview the document before saving or downloading it.',
    'preview pdf': 'Open a PDF preview.',
    'download pdf': 'Download the PDF file.',
    download: 'Download the selected file.',
    export: 'Export the current results to a file.',
    'export excel': 'Download the current results as an Excel workbook.',
    'export csv': 'Download the current results as a CSV file.',
    upload: 'Upload the selected file.',
    replace: 'Replace the current file with the selected file.',
    activate: 'Activate this record so it can be used again.',
    deactivate: 'Deactivate this record so it is not offered for new use.',
    approve: 'Approve this request.',
    reject: 'Reject this request.',
    'return for correction': 'Send this request back for correction.',
    'record repayment': 'Record the repayment details entered above.',
    'record payment': 'Record the payment details entered above.',
    'record leave': 'Create a leave application using the entered details.',
    'add row': 'Add a new row to this form.',
    'show all saved projects': 'Show the saved projects available for selection.'
  };

  const prefixes = [
    [/^download\b/i, 'Download the selected file.'],
    [/^export\b/i, 'Export the current results to a file.'],
    [/^preview\b/i, 'Preview the selected document.'],
    [/^save\b/i, 'Save the changes entered on this page.'],
    [/^submit\b/i, 'Submit this form or request for processing.'],
    [/^filter\b/i, 'Apply the selected filters to the list.'],
    [/^search\b/i, 'Search the available records.'],
    [/^apply\b/i, 'Apply the selected options and refresh the results.'],
    [/^create\b/i, 'Create the selected record or period.'],
    [/^add\b/i, 'Add a new item.'],
    [/^new\b/i, 'Start creating a new record.'],
    [/^edit\b/i, 'Open this record so you can edit it.'],
    [/^view\b/i, 'Open this record for viewing.'],
    [/^open\b/i, 'Open the selected record.'],
    [/^delete\b/i, 'Delete the selected record.'],
    [/^remove\b/i, 'Remove the selected item.'],
    [/^cancel\b/i, 'Cancel the selected operation or record.'],
    [/^approve\b/i, 'Approve this request.'],
    [/^reject\b/i, 'Reject this request.'],
    [/^generate\b/i, 'Generate the selected document or records.'],
    [/^refresh\b/i, 'Refresh the displayed information.'],
    [/^update\b/i, 'Save the updated settings or details.'],
    [/^record\b/i, 'Save this transaction in the records.'],
    [/^mark\b/i, 'Mark the selected record with the chosen status.'],
    [/^pay\b/i, 'Record the selected salary payment.'],
    [/^import\b/i, 'Import the selected file or records.'],
    [/^replace\b/i, 'Replace the current file with the selected file.'],
    [/^write off\b/i, 'Write off the outstanding balance entered above.'],
    [/^reconcile\b/i, 'Reconcile the selected transactions.'],
    [/^run\b/i, 'Run the selected check or report.'],
    [/^continue\b/i, 'Continue to the next step.'],
    [/^go back\b/i, 'Return to the previous page.']
  ];

  function describe(control) {
    const raw = control.getAttribute('aria-label') || control.innerText || control.value || '';
    const label = raw.replace(/\s+/g, ' ').trim().replace(/^\+\s*/, '');
    if (!label) return 'Run this action.';
    const key = label.toLowerCase().replace(/[.!?]+$/, '');
    if (descriptions[key]) return descriptions[key];
    const prefix = prefixes.find(([pattern]) => pattern.test(label));
    return prefix ? `${prefix[1]} (${label}).` : `${label}.`;
  }

  function addTitles(root = document) {
    root.querySelectorAll('button, input[type="submit"], input[type="button"], input[type="reset"], a.button, a.icon-btn, a.nav-cta, [role="button"]').forEach(control => {
      if (!control.hasAttribute('title') || !control.getAttribute('title').trim()) {
        control.setAttribute('title', describe(control));
      }
    });
  }

  addTitles();
  new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.matches('button, input[type="submit"], input[type="button"], input[type="reset"], a.button, a.icon-btn, a.nav-cta, [role="button"]')) {
        if (!node.hasAttribute('title') || !node.getAttribute('title').trim()) node.setAttribute('title', describe(node));
      } else {
        addTitles(node);
      }
    }
  }))).observe(document.body, { childList: true, subtree: true });
})();
