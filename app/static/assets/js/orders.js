/* Poll local task receipts only while an explicitly queued/background import is active. */
(() => {
  'use strict';
  const panel=document.querySelector('[data-order-sync]');
  if (!panel || panel.dataset.active!=='true') return;
  let attempts=0;
  async function refresh() {
    if (document.hidden || ++attempts>75) return;
    try {
      const response=await fetch(panel.dataset.statusUrl,{headers:{Accept:'application/json'},cache:'no-store',signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('Local Orders status is unavailable.');
      const state=await response.json();
      panel.querySelector('[data-order-sync-label]').textContent=state.label;
      panel.querySelector('[data-order-sync-message]').textContent=state.message;
      panel.querySelector('[data-order-sync-time]').textContent=state.last_successful;
      if (!state.active) {
        panel.querySelector('[data-order-sync-feedback]').textContent='Import finished. Refresh the local list to see newly saved orders.';
        return;
      }
      setTimeout(refresh,8000);
    } catch (_) {
      panel.querySelector('[data-order-sync-feedback]').textContent='Status check unavailable. The server-side import is unaffected; refresh this page for saved progress.';
    }
  }
  setTimeout(refresh,3000);
})();
