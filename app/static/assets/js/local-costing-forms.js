// Local planning only: warn before leaving edited forms, never auto-save.
for (const form of document.querySelectorAll('[data-local-costing-form]')) {
  let dirty = false;
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', () => { dirty = false; });
  window.addEventListener('beforeunload', event => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
}
