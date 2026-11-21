for (const button of document.querySelectorAll('[data-costing-dialog]')) {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.costingDialog);
    const form = dialog.querySelector('form'); form.reset();
    const record = JSON.parse(button.dataset.record || '{}');
    for (const [key, value] of Object.entries(record)) {
      const control = form.elements.namedItem(key);
      if (control) { if (control.type === 'checkbox') control.checked = Boolean(value); else control.value = value ?? ''; }
    }
    dialog.querySelector('[role=alert]').textContent = '';
    dialog.showModal();
  });
}
for (const dialog of document.querySelectorAll('[data-costing-modal]')) {
  dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
  dialog.querySelector('form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget;
    if (form.elements.archived?.checked && !confirm('Archive this profile? Saved references will be retained.')) return;
    const button = form.querySelector('[type=submit]'); button.disabled = true;
    try {
      const response = await fetch(form.getAttribute('action') || location.href, {method:'POST',body:new FormData(form),credentials:'same-origin'});
      if (!response.ok) {
        const html = new DOMParser().parseFromString(await response.text(),'text/html');
        throw new Error(html.querySelector('[role=alert]')?.textContent || 'Could not save. Check the fields and retry.');
      }
      sessionStorage.setItem('tlc-costing-saved','Saved successfully.'); location.assign(response.url);
    } catch (error) { dialog.querySelector('[role=alert]').textContent = error.message; }
    finally { button.disabled = false; }
  });
}
const saved = sessionStorage.getItem('tlc-costing-saved');
if (saved) { sessionStorage.removeItem('tlc-costing-saved'); const notice=document.createElement('p'); notice.className='alert alert-success'; notice.setAttribute('role','status'); notice.textContent=saved; document.querySelector('.workshop').prepend(notice); }
