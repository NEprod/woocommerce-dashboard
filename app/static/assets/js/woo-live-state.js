/* Shared, finite, sequential read-only reconciliation. Never starts a Preview. */
(function (root) {
  "use strict";
  function actionLabel(state) {
    return state === "local_only" ? "Review Create" :
      ["update", "remote_drift"].includes(state) ? "Review Update" :
      state === "link_candidate" ? "Review & Link" : "Review Preview";
  }
  function checkedLabel(timestamp) {
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) return "";
    const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
    return (minutes < 1 ? "just now" : minutes + " min ago") + " (" + date.toLocaleString() + ")";
  }
  const labels = {unknown: "Unable to refresh", local_only: "Local only", no_change: "No Change",
    update: "Update available", remote_drift: "Remote drift", blocked: "Blocked",
    link_candidate: "Link required", recovery_required: "Recovery required"};
  function terminal(state) {
    if (!state || typeof state !== "object" || !Object.hasOwn(labels, state.state)) throw Error("Invalid Woo state");
    if (state.busy) return {error: (state.message || "Woo is busy.") + " Use Refresh Woo to retry later."};
    return {...state, label: state.state === "unknown" ? labels.unknown : state.label || labels[state.state]};
  }
  async function requestState(fetcher, url, options, timeout = 45000) {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const response = await fetcher(url, {...options, signal: controller.signal});
          if (!response.ok) throw Error("Woo check unavailable");
          return await response.json();
        })(),
        new Promise((_, reject) => { timer = setTimeout(() => {
          controller.abort(); reject(Error("Woo check deadline exceeded"));
        }, timeout); })
      ]);
    } finally { clearTimeout(timer); }
  }
  async function drain(ids, read, render, force, {activity = () => {}, pause = () => new Promise(resolve => setTimeout(resolve, 1000))} = {}) {
    for (const id of [...new Set(ids)]) {
      activity(id, true);
      try {
        let state = await read(id, force);
        for (let attempt = 0; state && state.busy && attempt < 2; attempt++) {
          await pause();
          state = await read(id, false);
        }
        render(id, terminal(state));
      }
      catch (_) { render(id, { error: "Unable to refresh Woo — showing last known state. Use Refresh Woo to retry." }); }
      finally { activity(id, false); }
    }
  }
  function failureView(previous, message) {
    const known = previous && previous.state !== "unknown";
    return {state: known ? previous.state : "unknown", label: known ? previous.label : "Unable to refresh",
      message: [known ? "Last known state: " + previous.note : "No verified state is available.", message].filter(Boolean).join(" · ")};
  }
  if (typeof module !== "undefined") module.exports = { actionLabel, drain, requestState, terminal, failureView };
  if (!root.document) return;
  const doc = root.document, seen = new WeakSet(), known = new Map(), lastKnown = new Map(), queued = new Set(), active = new Set();
  let running = false;
  function nodes(id) { return [...doc.querySelectorAll('[data-woo-product="' + id + '"]')]; }
  function activity(id, checking) {
    if (checking) active.add(id); else active.delete(id);
    doc.querySelectorAll('[data-woo-active-count]').forEach(el => { el.textContent = String(active.size); });
    nodes(id).forEach(el => {
      el.setAttribute("aria-busy", checking ? "true" : "false");
      const label = el.querySelector('[data-woo-label]');
      if (checking && label) label.textContent = "Checking Woo…";
    });
  }
  function render(id, state) {
    const failed = state.error || state.busy;
    if (failed) state = failureView(lastKnown.get(id), state.error || state.message);
    const previous = known.get(id);
    if (state.checked_at) doc.querySelectorAll('[data-woo-last-checked]').forEach(el => {
      if (!el.dataset.wooLastChecked || Date.parse(state.checked_at) > Date.parse(el.dataset.wooLastChecked)) {
        el.dataset.wooLastChecked = state.checked_at;
        el.textContent = "Most recent product check: " + checkedLabel(state.checked_at);
      }
    });
    if (!state.error && !state.busy) {
      if (previous && previous !== state.state) {
        for (const [key, delta] of [[previous, -1], [state.state, 1]]) {
          doc.querySelectorAll('[data-woo-count="' + key + '"]').forEach(el => {
            el.textContent = String(Math.max(0, Number(el.textContent) + delta));
          });
        }
      }
      known.set(id, state.state);
    }
    nodes(id).forEach(el => {
      let note = el.querySelector("[data-woo-note]");
      if (!note) { note = doc.createElement("small"); note.dataset.wooNote = ""; el.appendChild(note); }
      note.textContent = state.error || [state.message, state.checked_at ? "Checked " + checkedLabel(state.checked_at) :
        state.verified_at ? "Last verified " + checkedLabel(state.verified_at) : ""].filter(Boolean).join(" · ");
      if (state.error || state.busy) return;
      const label = el.querySelector("[data-woo-label]");
      if (label) label.textContent = state.label;
      el.dataset.wooState = state.state;
      el.dataset.wooNeedsRefresh = "0";
      const row = el.closest("tr");
      const button = row && row.querySelector('form[action$="/preview/estimate"] button');
      if (button) button.textContent = actionLabel(state.state);
      if (!failed) lastKnown.set(id, {state: state.state, label: state.label, note: note.textContent});
    });
  }
  async function read(id, force) {
    const body = new URLSearchParams({ csrf_token: doc.querySelector('meta[name="csrf-token"]').content,
      force: force ? "1" : "0" });
    return requestState(fetch, "/woo-sync/state/" + id, { method: "POST", credentials: "same-origin", body });
  }
  async function run(force) {
    if (running) return;
    running = true;
    doc.querySelectorAll("[data-woo-refresh]").forEach(b => { b.disabled = true; });
    try {
      while (queued.size) {
        const ids = [...queued]; queued.clear();
        await drain(ids, read, render, force, {activity});
      }
    } finally {
      for (const id of [...active]) {
        render(id, {error: "Woo check ended without a result. Use Refresh Woo to retry."});
        activity(id, false);
      }
      running = false;
      doc.querySelectorAll("[data-woo-refresh]").forEach(b => { b.disabled = false; });
    }
  }
  function discover() {
    doc.querySelectorAll("[data-woo-product]").forEach(el => {
      if (seen.has(el)) return;
      seen.add(el);
      const id = el.dataset.wooProduct;
      if (!/^\d+$/.test(id)) return;
      if (!known.has(id)) known.set(id, el.dataset.wooState || "unknown");
      if (!lastKnown.has(id)) lastKnown.set(id, {state: known.get(id),
        label: el.querySelector('[data-woo-label]')?.textContent || "Unable to refresh",
        note: el.querySelector('[data-woo-note]')?.textContent || ""});
      if (el.dataset.wooNeedsRefresh !== "0") queued.add(id);
    });
    if (queued.size) run(false);
  }
  doc.addEventListener("click", event => {
    if (!event.target.closest("[data-woo-refresh]")) return;
    doc.querySelectorAll("[data-woo-product]").forEach(el => queued.add(el.dataset.wooProduct));
    run(true);
  });
  new MutationObserver(discover).observe(doc.body, { childList: true, subtree: true });
  discover();
})(typeof window === "undefined" ? globalThis : window);
