/* One confirmed POST; progress/reconnection reads durable server receipts only. */
(function (root) {
  'use strict';
  const terminal = (state,value) => state==='processing' ? value?.background_active!==true : ['completed','attention','failed','stalled','recovery_required','receipt_unavailable'].includes(state);
  const confirmationUrl = (form, href) => {
    // HTMLFormElement.action is shadowed by our <input name="action">.
    // Read the content attribute, never the form's named-control property.
    const url=new URL(form.getAttribute('action') || href,href);
    if (url.origin!==new URL(href).origin) throw new Error('Invalid confirmation destination');
    return url.href;
  };
  const destination = (path, origin) => {
    const url = new URL(path, origin);
    if (url.origin !== new URL(origin).origin || url.pathname !== '/products') throw new Error('Invalid Products destination');
    return url.href;
  };
  root.ShopifyPublishClient = {terminal, destination, confirmationUrl};
  if (!root.document) return;
  const dialog = document.querySelector('[data-shopify-progress]');
  if (!dialog) return;
  const stage = dialog.querySelector('[data-publish-stage]');
  const message = dialog.querySelector('[data-publish-message]');
  const history = dialog.querySelector('[data-publish-history]');
  const back = dialog.querySelector('[data-publish-products]');
  const identity = dialog.querySelector('[data-publish-product]');
  const details = dialog.querySelector('[data-publish-details]');
  const completed = dialog.querySelector('[data-publish-completed]');
  const resume = document.querySelector('[data-publish-resume]');
  const key = 'tlc-shopify-publish:' + location.pathname;
  let pending = null, polling = false, submitted = false, stopped = false, paused = false, started = Date.now();
  let timer=null, controller=null, failures=0, lastResponse=null;
  const store = value => { try { if (value) sessionStorage.setItem(key,JSON.stringify(value)); else sessionStorage.removeItem(key); } catch (_) { /* reload recovery remains available in History */ } };
  const show = value => {
    lastResponse=value;
    stage.textContent = value.stage || value.state || 'Inspect operation receipt';
    message.textContent = value.message || '';
    if (identity) identity.textContent=[value.title || pending?.title,value.sku || pending?.sku,value.operation_type].filter(Boolean).join(' · ');
    if (details) details.textContent=`Status: ${value.state} · ${value.elapsed_seconds ?? Math.floor((Date.now()-started)/1000)} s elapsed · Last server response: ${value.server_checked_at || new Date().toISOString()}${value.operation_id ? ' · Operation '+value.operation_id : ''}`;
    if (completed) completed.textContent=(value.completed_stages || []).length ? 'Recorded completed stages: '+value.completed_stages.join(' · ') : 'No completed stage recorded yet.';
    if (value.state==='receipt_unavailable') { pause(); store(pending); back.hidden=false; return; }
    if (value.history_url) { history.href = value.history_url; history.hidden = false; }
    if (terminal(value.state,value)) {
      stopped = true; store(null); back.hidden = false;
      if (value.state==='completed' && !paused) {
        stage.textContent = (value.completion_label || 'Verified') + ' — returning to Products';
        root.setTimeout(() => location.assign(destination(value.return_url || pending.returnTo,location.origin)),1200);
      }
    }
  };
  const readJson = async response => {
    if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Session or response unavailable. Inspect History; do not resubmit.');
    const value = await response.json();
    if (!response.ok && !value.state) throw new Error(value.error || value.message || 'Operation receipt unavailable; do not resubmit.');
    return value;
  };
  const poll = async () => {
    if (!pending || stopped || polling || paused) return;
    if (Date.now()-started > 600000) { pause(); message.textContent = 'Progress checking paused after ten minutes. Inspect Products / History; completion is not assumed and no retry was issued.'; back.hidden = false; return; }
    polling = true;
    controller=new AbortController();
    const deadline=root.setTimeout(()=>controller?.abort(),10000);
    try {
      const body = new URLSearchParams({review:pending.review,csrf_token:pending.csrf});
      const value = await readJson(await fetch(pending.url,{method:'POST',body,signal:controller.signal,credentials:'same-origin',headers:{Accept:'application/json'}}));
      failures=0;
      if (!paused) show(value);
      if (value.state==='waiting' && Date.now()-started>30000) { pause(); message.textContent='No accepted operation receipt found. Inspect History or server logs; Shopify failure is not assumed. Do not resubmit an uncertain operation.'; back.hidden=false; }
    } catch (error) {
      if (!paused) { message.textContent='Receipt unavailable: '+error.message+' No mutation is retried.'; back.hidden=false; failures++; }
      if (failures>=5) pause();
    }
    finally { root.clearTimeout(deadline); controller=null; polling=false; if (!stopped && !paused) timer=root.setTimeout(poll,Math.min(10000,2000*2**failures)); }
  };
  const pause=()=>{ paused=true; root.clearTimeout(timer); controller?.abort(); if (resume) resume.hidden=false; };
  const open = () => { paused=false; started=Date.now(); if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open',''); } if (resume) resume.hidden=true; };
  dialog.querySelector('[data-publish-close]').addEventListener('click',() => { pause(); dialog.close(); });
  dialog.addEventListener('close',pause);
  dialog.addEventListener('cancel',pause);
  resume?.addEventListener('click',()=>{ open(); stopped=false; if (lastResponse && lastResponse.state!=='receipt_unavailable') show(lastResponse); poll(); });
  document.addEventListener('visibilitychange',()=>{ if (document.hidden) pause(); else if (dialog.open && pending && !stopped) { paused=false; poll(); } });
  root.addEventListener('pagehide',pause);
  root.addEventListener('pageshow',()=>{ if (dialog.open && pending && !stopped && !document.hidden) { paused=false; poll(); } });
  document.querySelectorAll('[data-shopify-publish]').forEach(form => form.addEventListener('submit',async event => {
    if (!form.checkValidity()) return;
    event.preventDefault();
    if (submitted) return;
    submitted = true;
    const body = new URLSearchParams(new FormData(form));
    pending = {review:body.get('review'),csrf:body.get('csrf_token'),url:form.dataset.progressUrl,returnTo:body.get('return_to'),title:form.dataset.title,sku:form.dataset.sku};
    store(pending); open();
    form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    timer=root.setTimeout(poll,500);
    try { show(await readJson(await fetch(confirmationUrl(form,location.href),{method:'POST',body,credentials:'same-origin',headers:{Accept:'application/json'}}))); }
    catch (error) { message.textContent = 'Confirmation response unavailable: '+error.message+' Inspect the recorded operation; no write is resubmitted.'; back.hidden=false; }
  }));
  try { pending = JSON.parse(sessionStorage.getItem(key)); } catch (_) { pending = null; }
  if (pending) { submitted = true; open(); poll(); }
})(typeof window === 'undefined' ? globalThis : window);
