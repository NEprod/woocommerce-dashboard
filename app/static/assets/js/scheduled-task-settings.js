(function () {
  "use strict";
  const forms = [...document.querySelectorAll('[data-schedule-form]')];
  if (!forms.length) return;
  const state = form => form.querySelector('[name="enabled"]').checked + ':' + form.querySelector('[name="interval"]').value;
  const saved = new Map(forms.map(form => [form, form.dataset.initialEnabled + ':' + form.dataset.initialInterval]));
  const status = form => document.querySelector('[data-schedule-state="' + form.id.slice('schedule-'.length) + '"]');
  const button = form => document.querySelector('[form="' + form.id + '"]');
  const dirty = () => forms.some(form => state(form) !== saved.get(form));
  forms.forEach(form => {
    form.addEventListener('change', () => { status(form).textContent = state(form) === saved.get(form) ? 'Saved' : 'Unsaved changes'; });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const feedback = status(form), submit = button(form);
      if (state(form) === saved.get(form)) { feedback.textContent = 'Already saved'; return; }
      submit.disabled = true; feedback.textContent = 'Saving…';
      try {
        const response = await fetch(form.action, {method: 'POST', headers: {
          'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''},
          body: JSON.stringify({enabled: form.querySelector('[name="enabled"]').checked,
            interval: Number(form.querySelector('[name="interval"]').value)})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Save was not accepted.');
        // Preserve a newer edit made while the request was in flight.
        saved.set(form, result.enabled + ':' + result.interval);
        feedback.textContent = state(form) === saved.get(form) ? 'Saved' : 'Saved earlier selection; newer changes remain unsaved';
      } catch (error) { feedback.textContent = 'Not saved: ' + error.message; }
      finally { submit.disabled = false; }
    });
  });
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
}());
