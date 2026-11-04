(function () {
  'use strict';
  const button = document.querySelector('[data-royal-mail-export]');
  const feedback = document.querySelector('[data-export-feedback]');
  if (!button || !feedback) return;
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true; feedback.hidden = false;
    feedback.className = 'alert alert-info'; feedback.textContent = 'Preparing Royal Mail CSV from the saved local catalogue…';
    try {
      const response = await fetch(button.dataset.royalMailExport, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || 'Export unavailable. No file was downloaded.');
      }
      const filename = /filename="(tlc-royal-mail-products-\d{4}-\d{2}-\d{2}\.csv)"/.exec(response.headers.get('Content-Disposition') || '')?.[1];
      const count = response.headers.get('X-Export-Row-Count');
      if (!filename || !/^\d+$/.test(count || '') || !(response.headers.get('Content-Type') || '').startsWith('text/csv'))
        throw new Error('Invalid export response. Please sign in again if your session expired.');
      const warnings = JSON.parse(response.headers.get('X-Export-Warnings') || '{}');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = filename;
      document.body.appendChild(link); link.click(); link.remove();
      // Safari needs the blob URL to remain alive after initiating the download.
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      feedback.className = 'alert alert-success';
      feedback.textContent = `Royal Mail CSV exported successfully — ${filename} — ${count} product rows exported.`;
      const summary = Object.entries(warnings).map(([label, value]) => `${label}: ${value}`).join('; ');
      if (summary) feedback.textContent += ` Warnings: ${summary}.`;
    } catch (error) {
      feedback.className = 'alert alert-danger'; feedback.textContent = `Royal Mail CSV export failed: ${error.message}`;
    } finally { button.disabled = false; }
  });
})();
