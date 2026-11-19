/* Presentation only: invoke the existing read-only preview form, never Confirm. */
(function (root) {
  'use strict';
  const start = () => {
    const form = document.querySelector('[data-preview-form]');
    const result = document.querySelector('[data-preview-result]');
    const status = form?.querySelector('[data-preview-status]');
    const button = form?.querySelector('[data-preview-build]');
    if (!form || !result || !button || !status) return;
    let busy = false, controller;
    const build = async event => {
      event?.preventDefault();
      if (busy || !form.checkValidity()) return;
      const body = new URLSearchParams(new FormData(form));
      // Keep the two existing advanced scopes and CSRF; never inherit approval.
      if (body.get('action') !== 'preview' || body.has('review') || body.has('acknowledge')) return;
      const url = new URL(form.getAttribute('action') || root.location.href, root.location.href);
      if (url.origin !== root.location.origin) return;
      busy = true; button.disabled = true; result.setAttribute('aria-busy', 'true');
      result.querySelectorAll('[data-shopify-publish] button, [data-shopify-publish] input').forEach(node => { node.disabled = true; });
      status.textContent = 'Loading preview — reading local sources and Shopify…';
      controller = new AbortController();
      try {
        const response = await fetch(url.href, {method:'POST', body, credentials:'same-origin',
          headers:{Accept:'text/html'}, signal:controller.signal});
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        const content = page.querySelector('[data-preview-result]');
        if (!content) throw new Error('Session or preview response unavailable. Sign in if needed, then build again.');
        result.replaceChildren(...Array.from(content.childNodes).map(node => document.importNode(node, true)));
        status.textContent = response.ok ? 'Preview ready — review the changes below' : 'Preview failed or blocked — inspect the issue below';
        document.dispatchEvent(new CustomEvent('tlc:review-ready'));
      } catch (error) {
        // An old signed form remains blocked after an interrupted rebuild.
        status.textContent = 'Preview stale / rebuild required. ' + (error.name === 'AbortError' ? 'Reading was interrupted.' : error.message) + ' No publishing or automatic retry was started.';
      } finally {
        busy = false; button.disabled = false; result.removeAttribute('aria-busy');
      }
    };
    form.addEventListener('submit', build);
    root.addEventListener('pagehide', () => controller?.abort());
    document.addEventListener('click', event => {
      const toggle = event.target.closest('[data-review-expand]');
      if (toggle) {
        const expanded = toggle.dataset.reviewExpand === 'yes';
        result.querySelectorAll('[data-review-group]').forEach(node => {
          if (node.dataset.reviewGroup.startsWith('Variant ')) node.open = expanded;
        });
        const matching = result.querySelector('[data-review-matching]');
        if (matching) matching.open = expanded;
      }
    });
    let operationPending = false;
    try { operationPending = !!root.sessionStorage.getItem('tlc-shopify-publish:' + root.location.pathname); } catch (_) {}
    if (form.dataset.autoPreview === 'yes' && !operationPending) build();
    else if (operationPending) status.textContent = 'Existing publishing receipt is open. Preview is not rebuilt automatically; inspect its progress first.';
  };
  if (typeof document !== 'undefined') start();
})(typeof window === 'undefined' ? globalThis : window);
