/* Local presentation only: one bounded revalidation at a known transition, never Shopify. */
(function (global) {
  'use strict';
  const amount = (low, high) => low == null ? 'Not set' : `£${low}${high != null && high !== low ? `–£${high}` : ''}`;
  const format = price => amount(price?.minimum, price?.maximum);
  function delay(items) {
    const waits = items.filter(p => p.price?.next_transition).map(p =>
      Date.parse(p.price.next_transition) - Date.parse(p.price.as_of)).filter(Number.isFinite);
    return waits.length ? Math.max(0, Math.min(...waits)) + 300 : null;
  }
  function watch(items, changed, env=global) {
    let current=items, timer=null, due=null, busy=false, stopped=false;
    const endpoint=env.document.querySelector('[data-sale-prices-url]')?.dataset.salePricesUrl;
    function arm() {
      env.clearTimeout(timer);
      const wait=delay(current);
      due=wait == null ? null : Date.now()+wait;
      if (wait != null) timer=env.setTimeout(refresh,Math.min(wait,2147483647));
    }
    async function refresh() {
      if (stopped || busy || !current.length || !endpoint) return;
      // Long timers may be clamped by browsers. Never poll before the known boundary.
      if (due != null && Date.now()+100 < due) { timer=env.setTimeout(refresh,Math.min(due-Date.now(),2147483647));return; }
      busy=true;
      const controller=new AbortController(), deadline=env.setTimeout(()=>controller.abort(),15000);
      try {
        const response=await env.fetch(`${endpoint}?ids=${current.map(p=>p.id).join(',')}`,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
        if (!response.ok) throw new Error('Local price revalidation unavailable');
        const payload=await response.json();
        if (stopped) return;
        current=payload.items;changed(current);arm();
      } catch (_error) {
        env.document.querySelectorAll('[data-sale-price-notice], [data-local-price]').forEach(node=>{node.textContent='Local price refresh unavailable; reload or return to this tab to retry. Shopify has not been checked.';});
        // No retry storm or new sale scheduler; a later focus/visibility event can retry.
      } finally { env.clearTimeout(deadline);busy=false; }
    }
    const resume=()=>{if (!env.document.hidden && due != null && Date.now() >= due) refresh();};
    env.addEventListener('focus',resume);env.addEventListener('pageshow',resume);env.document.addEventListener('visibilitychange',resume);
    arm();
    return {refresh:()=>{due=0;return refresh();},stop:()=>{stopped=true;env.clearTimeout(timer);env.removeEventListener('focus',resume);env.removeEventListener('pageshow',resume);env.document.removeEventListener('visibilitychange',resume);}};
  }
  global.TLCSalePrices={format,amount,delay,watch};
  if (!global.document) return;
  function update(items) {
    items.forEach(item=>{
      global.document.querySelectorAll(`[data-local-shopify="${item.id}"]`).forEach(panel=>{
        panel.querySelector('[data-local-shopify-label]').textContent=item.shopify.label;
        panel.querySelector('[data-local-shopify-message]').textContent=item.shopify.message;
        const tone={no_change:'success',update:'warning',attention:'warning',recovery_required:'danger',processing:'info',local_changes:'info'}[item.shopify.state] || 'neutral';
        panel.querySelector('[data-local-shopify-badge]').className=`catalogue-pill is-shopify-${tone}`;
      });
      global.document.querySelectorAll(`[data-local-product="${item.id}"]`).forEach(root=>{
      const price=item.price;
      const values={'effective':format(price),'state':`Sale: ${price.label}`,
        'regular':amount(price.regular_minimum,price.regular_maximum),
        'configured':amount(price.configured_sale_minimum,price.configured_sale_maximum)};
      Object.entries(values).forEach(([key,value])=>root.querySelectorAll(`[data-sale-${key}]`).forEach(n=>{n.textContent=value;}));
      root.querySelectorAll('[data-sale-price-notice]').forEach(n=>{n.textContent=price.error || 'Saved local schedule, not proof of Shopify execution.';});
      price.variants.forEach(v=>global.document.querySelectorAll(`[data-sale-variant="${v.id}"]`).forEach(n=>{n.textContent=`£${v.price}`;}));
      });
    });
  }
  const seeds=Array.from(global.document.querySelectorAll('[data-local-pricing]')).map(n=>({id:Number(n.dataset.localProduct),price:JSON.parse(n.dataset.localPricing)}));
  if (seeds.length) {
    const controller=watch(seeds,update);
    global.addEventListener('tlc:local-metadata-saved',()=>controller.refresh());
  }
})(globalThis);
