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

// Selection is limited to the currently rendered/filtered page.
(() => {
  const all=document.querySelector('[data-order-select-all]');
  if(!all&&!document.querySelector('[data-order-bulk]'))return;
  const rows=[...document.querySelectorAll('input[name=order_id][form=bulk-orders]')];
  const update=()=>{const count=rows.filter(c=>c.checked).length;const label=document.querySelector('[data-selected-count]');if(label)label.textContent=count+' selected';if(all){all.checked=!!rows.length&&count===rows.length;all.indeterminate=count>0&&count<rows.length;}};
  all?.addEventListener('change',()=>{for(const row of rows)row.checked=all.checked;update();});
  for(const row of rows)row.addEventListener('change',update);
  const print=async()=>{await Promise.all([...document.querySelectorAll('.order-pick-sheet img')].map(img=>img.decode().catch(()=>{})));window.print();};
  document.querySelector('[data-order-print]')?.addEventListener('click',print);
  if(document.querySelector('[data-order-bulk]')?.dataset.autoPrint==='yes')void print();
})();
