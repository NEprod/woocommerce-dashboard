(function (root) {
  "use strict";

  function liveUrl(operationId, endpoint, query) {
    const suffix = query ? "?" + query.toString() : "";
    return "/api/operations/" + encodeURIComponent(operationId) + "/" + endpoint + suffix;
  }

  function nextCursor(current, payload) {
    const candidate = Number(payload && payload.next_cursor);
    return Number.isSafeInteger(candidate) && candidate >= current ? candidate : current;
  }

  function shouldPause(failures) { return failures >= 3; }

  root.OperationsWorkspaceClient = {liveUrl, nextCursor, shouldPause};
  const documentRef = root.document;
  if (!documentRef || typeof documentRef.querySelector !== "function") return;

  const hero = documentRef.querySelector("[data-operation-id]");
  if (!hero) return;
  const operationId = hero.dataset.operationId;
  const logs = documentRef.querySelector("[data-operation-logs]");
  const logForm = documentRef.querySelector("[data-log-form]");
  const logPagination = documentRef.querySelector("[data-log-pagination]");
  const logNote = documentRef.querySelector("[data-log-note]");
  const retry = documentRef.querySelector("[data-live-retry]");
  const connectivity = documentRef.querySelector("[data-live-connectivity]");
  let cursor = 0;
  let terminal = hero.dataset.terminal === "true";
  let paused = false;
  let failures = 0;
  let statusBusy = false;
  let logBusy = false;
  let filtered = false;
  let pollTimer = null;
  const renderedSequences = new Set();

  function text(selector, value) {
    const element = documentRef.querySelector(selector);
    if (element && value !== undefined && value !== null) element.textContent = String(value);
  }

  function formatActivity(value) {
    const timestamp = Date.parse(value || "");
    if (!Number.isFinite(timestamp)) return "Awaiting scanner heartbeat";
    const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 5) return "just now";
    if (seconds < 60) return seconds + " seconds ago";
    return Math.floor(seconds / 60) + " minutes ago";
  }

  function markConnected() {
    failures = 0;
    if (connectivity) connectivity.textContent = terminal ? "Final state received" : "Live status connected";
    if (retry) retry.hidden = true;
  }

  function markFailure() {
    failures += 1;
    if (connectivity) connectivity.textContent = "Live status temporarily unavailable. The scan is continuing in the background.";
    if (shouldPause(failures)) {
      paused = true;
      if (retry) retry.hidden = false;
    }
  }

  function renderStatus(payload) {
    const operation = payload.operation || {};
    const live = payload.live || {};
    const progress = live.progress || {};
    const counts = live.counts || {};
    const summary = payload.summary || live.summary || {};
    text("[data-operation-heading]", operation.status_label);
    const status = documentRef.querySelector("[data-operation-status]");
    if (status) {
      status.textContent = operation.status_label || operation.status;
      status.className = "status-badge status-" + (operation.status || "running");
    }
    ["attempted", "succeeded", "failed", "warning_count", "error_count"].forEach(function (key) {
      text('[data-operation-count="' + key + '"]', operation[key]);
    });
    text("[data-operation-duration]", operation.duration);
    text("[data-live-stage]", String(live.stage || operation.status || "running").replaceAll("_", " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }));
    text("[data-current-item]", live.current_item || (payload.terminal ? "Operation finished" : "Waiting for the current stage to complete"));
    text("[data-live-message]", live.latest_message || (payload.terminal ? "Final operation state received." : "Running — waiting for the current stage to complete."));
    text("[data-live-activity]", formatActivity(payload.last_activity));
    const bar = documentRef.querySelector("[data-live-progressbar]");
    if (bar) {
      const total = Number(progress.total || 0);
      const completed = Number(progress.completed || 0);
      const percent = total > 0 ? Math.min(100, Math.max(0, completed / total * 100)) : 0;
      bar.setAttribute("aria-valuemax", String(total || 1));
      bar.setAttribute("aria-valuenow", String(completed));
      const fill = bar.querySelector("span");
      if (fill) fill.style.width = percent + "%";
    }
    const countParts = [];
    if (progress.total) countParts.push((progress.completed || 0) + " of " + progress.total + " collections");
    else if (progress.completed) countParts.push(progress.completed + " collections processed; total calculating");
    [["products", counts.products], ["variations", summary.variations_processed], ["parent images", summary.parent_images], ["variation images", summary.variation_images], ["output images copied", summary.output_images_copied]].forEach(function (item) {
      if (item[1] !== undefined && item[1] !== null) countParts.push(item[1] + " " + item[0]);
    });
    text("[data-live-counts]", countParts.length ? countParts.join(" · ") : "Structured counts will appear as work completes.");
    terminal = payload.terminal === true;
    hero.dataset.terminal = terminal ? "true" : "false";
    markConnected();
  }

  function appendLogEntries(payload) {
    if (payload.gap && logNote) logNote.textContent = "Earlier retained lines rolled over. Display resumed from the oldest available sequence.";
    (payload.entries || []).forEach(function (entry) {
      const sequence = Number(entry.sequence);
      if (renderedSequences.has(sequence)) return;
      renderedSequences.add(sequence);
      const row = documentRef.createElement("div");
      row.className = "operation-log-line is-" + (entry.severity || "info");
      row.dataset.sequence = String(sequence);
      row.textContent = entry.line;
      logs.appendChild(row);
    });
    cursor = nextCursor(cursor, payload);
    if (!logs.children.length) {
      const empty = documentRef.createElement("p");
      empty.textContent = "No new lines yet. Live polling remains active.";
      logs.appendChild(empty);
    } else {
      const placeholder = logs.querySelector("p");
      if (placeholder && renderedSequences.size) placeholder.remove();
    }
  }

  async function pollLogs() {
    if (logBusy || filtered) return;
    logBusy = true;
    const query = new URLSearchParams({after: String(cursor), per_page: "100"});
    try {
      const response = await root.fetch(liveUrl(operationId, "logs", query), {headers: {"Accept": "application/json", "Cache-Control": "no-cache"}, credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("log request failed");
      appendLogEntries(await response.json());
      markConnected();
    } catch (_error) { markFailure(); }
    finally { logBusy = false; }
  }

  async function loadFilteredLogs() {
    const form = new FormData(logForm);
    const query = new URLSearchParams({page: "1", per_page: "100", q: form.get("q") || "", severity: form.get("severity") || ""});
    filtered = Boolean(query.get("q") || query.get("severity"));
    if (!filtered) {
      cursor = 0; renderedSequences.clear(); logs.replaceChildren(); await pollLogs(); return;
    }
    try {
      const response = await root.fetch(liveUrl(operationId, "logs", query), {headers: {"Accept": "application/json", "Cache-Control": "no-cache"}, credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("log request failed");
      const payload = await response.json();
      logs.replaceChildren();
      (payload.items || []).forEach(function (line) { const row = documentRef.createElement("div"); row.className = "operation-log-line"; row.textContent = line; logs.appendChild(row); });
      logPagination.textContent = payload.total + " matching retained lines";
    } catch (_error) { logs.textContent = "Logs could not be loaded. Use Apply to try again."; }
  }

  async function pollStatus() {
    if (statusBusy) return;
    statusBusy = true;
    try {
      const response = await root.fetch(liveUrl(operationId, "status"), {headers: {"Accept": "application/json", "Cache-Control": "no-cache"}, credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("status request failed");
      renderStatus(await response.json());
      if (terminal) await pollLogs();
    } catch (_error) { markFailure(); }
    finally { statusBusy = false; }
  }

  function schedule() {
    if (pollTimer !== null || paused || terminal || documentRef.hidden) return;
    pollTimer = root.setTimeout(async function () {
      pollTimer = null;
      await pollStatus();
      await pollLogs();
      schedule();
    }, 3000);
  }

  logForm.addEventListener("submit", function (event) { event.preventDefault(); loadFilteredLogs(); });
  if (retry) retry.addEventListener("click", async function () { paused = false; failures = 0; await pollStatus(); await pollLogs(); schedule(); });
  documentRef.addEventListener("visibilitychange", function () {
    if (!documentRef.hidden && !terminal) { paused = false; failures = 0; pollStatus().then(pollLogs).then(schedule); }
  });
  logs.replaceChildren();
  pollLogs();
  if (!terminal) pollStatus().then(schedule);
})(typeof window !== "undefined" ? window : globalThis);
