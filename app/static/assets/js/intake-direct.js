(function () {
  'use strict';
  const root = document.querySelector('[data-intake-direct]');
  if (!root) return;
  const accept = root.querySelector('[data-intake-accept]');
  const button = accept.querySelector('button');
  const message = accept.querySelector('[data-intake-message]');
  let sequence = 0, timer, controller, lastInputs;
  function settings() {
    if (root.dataset.stage === 'metadata') {
      const editor = root.querySelector('[data-prepared-metadata-editor]');
      const data = new FormData();
      data.set('csrf_token', accept.elements.csrf_token.value);
      data.set('path', accept.elements.path.value);
      data.set('document', JSON.stringify(editor.intakeDocument()));
      return data;
    }
    return new FormData(root.querySelector('[data-intake-settings]'));
  }
  async function refresh(expected) {
    controller?.abort(); controller = new AbortController();
    try {
      const response = await fetch(root.dataset.previewUrl, {method: 'POST', body: settings(),
        signal: controller.signal, headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) throw new Error('Preview unavailable. Your files have not changed.');
      const value = await response.json();
      if (expected !== sequence) return;
      root.querySelector('[data-intake-preview]').innerHTML = value.html;
      accept.elements.digest.value = value.digest;
      accept.elements.options.value = JSON.stringify(value.options);
      button.disabled = !value.ready;
      message.textContent = value.ready ? 'Ready for acceptance' : 'Resolve the displayed issues';
    } catch (error) {
      if (error.name === 'AbortError' || expected !== sequence) return;
      button.disabled = true;
      message.textContent = error.message;
    }
  }
  function changed(event) {
    if (event?.target.closest('[data-intake-accept], [data-intake-preview]')) return;
    let current;
    try { current = JSON.stringify([...settings().entries()]); }
    catch (error) { button.disabled = true; message.textContent = error.message; return; }
    // Blur's change event must not invalidate the identical preview just before
    // the merchant clicks Accept. Only actual setting changes create new work.
    if (current === lastInputs) return;
    lastInputs = current;
    button.disabled = true; message.textContent = 'Updating preview…';
    const expected = ++sequence; clearTimeout(timer);
    timer = setTimeout(() => refresh(expected), 300);
  }
  root.addEventListener('input', changed);
  root.addEventListener('change', changed);
  root.addEventListener('click', event => {
    if (event.target.closest('[data-add-list], [data-add-attribute], [data-add-modifier], [data-row-action], [data-prepared-mode]')) changed(event);
  });
  root.addEventListener('submit', event => {
    if (event.target === accept) {
      if (button.disabled) event.preventDefault();
      else {
        root.querySelector('[data-prepared-metadata-editor]')?.intakeAccepted?.();
        button.disabled = true; message.textContent = 'Accepting reviewed changes…';
      }
    } else { event.preventDefault(); changed(event); }
  });
  // Also handles packaging-editor updates that don't originate in a text input.
  root.addEventListener('intake:changed', changed);
  try { lastInputs = JSON.stringify([...settings().entries()]); } catch (_) {}
})();
