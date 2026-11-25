(() => {
  const itemClass = document.getElementById('id_item_class');
  if (!itemClass) return;
  const serialField = document.getElementById('id_serial_number');
  const qtyField = document.getElementById('id_quantity');
  const serialRow = serialField ? serialField.closest('.form-field') : null;
  const qtyRow = qtyField ? qtyField.closest('.form-field') : null;

  function sync() {
    const cls = itemClass.value;
    const isFixed = cls === 'fixed';
    const isBulk = cls === 'bulk';

    if (serialRow) {
      serialRow.style.display = isFixed ? '' : 'none';
      if (serialField) serialField.required = isFixed;
    }
    if (qtyRow) {
      qtyRow.style.display = isBulk ? '' : 'none';
      if (qtyField) qtyField.required = isBulk;
    }
  }

  itemClass.addEventListener('change', sync);
  sync();
})();
