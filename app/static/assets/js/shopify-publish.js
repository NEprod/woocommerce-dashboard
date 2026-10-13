/* One confirmed POST; progress/reconnection reads durable server receipts only. */
(function (root) {
  'use strict';
  const terminal = state => ['completed','processing','attention','failed','recovery_required'].includes(state);
  const destination = (path, origin) => {
    const url = new URL(path, origin);
    if (url.origin !== new URL(origin).origin || url.pathname !== '/products') throw new Error('Invalid Products destination');
    return url.href;
  };
  root.ShopifyPublishClient = {terminal, destination};
  if (!root.document) return;
  const dialog = document.querySelector('[data-shopify-progress]');
  if (!dialog) return;
  const stage = dialog.querySelector('[data-publish-stage]');
  const message = dialog.querySelector('[data-publish-message]');
  const history = dialog.querySelector('[data-publish-history]');
  const back = dialog.querySelector('[data-publish-products]');
  const key = 'tlc-shopify-publish:' + location.pathname;
  let pending = null, polling = false, submitted = false, stopped = false, started = Date.now();
  const store = value => { try { if (value) sessionStorage.setItem(key,JSON.stringify(value)); else sessionStorage.removeItem(key); } catch (_) { /* reload recovery remains available in History */ } };
  const show = value => {
    stage.textContent = value.stage || value.state || 'Inspect operation receipt';
    message.textContent = value.message || '';
    if (value.history_url) { history.href = value.history_url; history.hidden = false; }
    if (terminal(value.state)) {
      stopped = true; store(null); back.hidden = false;
      if (['completed','processing'].includes(value.state)) {
        stage.textContent = value.state === 'completed' ? 'Verified — returning to Products' : 'Processing — check status from Products';
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
    if (!pending || stopped || polling) return;
    if (Date.now()-started > 600000) { message.textContent = 'Progress checking paused. Open Products / History; no retry was issued.'; back.hidden = false; return; }
    polling = true;
    try {
      const body = new URLSearchParams({review:pending.review,csrf_token:pending.csrf});
      const value = await readJson(await fetch(pending.url,{method:'POST',body,credentials:'same-origin',headers:{Accept:'application/json'}}));
      show(value);
    } catch (error) { message.textContent = error.message; back.hidden = false; }
    finally { polling = false; if (!stopped) root.setTimeout(poll,2000); }
  };
  const open = () => { if (!dialog.open) dialog.showModal(); };
  dialog.querySelector('[data-publish-close]').addEventListener('click',() => dialog.close());
  document.querySelectorAll('[data-shopify-publish]').forEach(form => form.addEventListener('submit',async event => {
    if (!form.checkValidity()) return;
    event.preventDefault();
    if (submitted) return;
    submitted = true;
    const body = new URLSearchParams(new FormData(form));
    pending = {review:body.get('review'),csrf:body.get('csrf_token'),url:form.dataset.progressUrl,returnTo:body.get('return_to')};
    store(pending); open();
    form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    root.setTimeout(poll,500);
    try { show(await readJson(await fetch(form.action || location.href,{method:'POST',body,credentials:'same-origin',headers:{Accept:'application/json'}}))); }
    catch (error) { message.textContent = error.message + ' Reconnecting to the recorded operation; no write is resubmitted.'; back.hidden = false; }
  }));
  try { pending = JSON.parse(sessionStorage.getItem(key)); } catch (_) { pending = null; }
  if (pending) { submitted = true; open(); poll(); }
})(typeof window === 'undefined' ? globalThis : window);
