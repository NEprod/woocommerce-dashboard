(function () {
  "use strict";
  const panel = document.querySelector("[data-task-run-view]");
  if (!panel || panel.dataset.terminal === "true") return;
  const report = document.querySelector("[data-task-run-report]");
  const feedback = panel.querySelector("[data-run-feedback]");
  let terminal = false, away = false, halted = false, timer = null;
  let request = null, generation = 0, failures = 0;
  const visible = () => !away && !document.hidden && navigator.onLine !== false;

  function updateReport(html) {
    if (report.innerHTML === html) return;
    const expanded = Array.from(report.querySelectorAll("details"), node => node.open);
    const x = window.scrollX, y = window.scrollY;
    report.innerHTML = html;
    report.querySelectorAll("details").forEach((node, index) => {
      if (index < expanded.length) node.open = expanded[index];
    });
    if (window.scrollX !== x || window.scrollY !== y) window.scrollTo(x, y);
  }

  function cancel() {
    clearTimeout(timer); timer = null; generation += 1;
    if (request) request.abort();
    request = null;
  }

  function schedule(delay) {
    clearTimeout(timer);
    if (!terminal && !halted && visible()) timer = setTimeout(poll, delay);
  }

  async function poll() {
    if (terminal || halted || !visible() || request) return;
    const version = ++generation;
    const controller = new AbortController(); request = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(panel.dataset.statusUrl, {
        headers: {Accept: "application/json"}, credentials: "same-origin",
        cache: "no-store", signal: controller.signal
      });
      if (version !== generation) return;
      if ([401, 403, 404].includes(response.status)) {
        halted = true;
        feedback.textContent = "Progress viewing is unavailable. Sign in or reopen this run; its saved execution state has not been changed.";
        return;
      }
      if (!response.ok) throw new Error("Progress temporarily unavailable");
      const value = await response.json();
      if (version !== generation) return;
      if (value.id !== panel.dataset.runId || typeof value.terminal !== "boolean" || typeof value.report_html !== "string") {
        throw new Error("Unexpected progress response");
      }
      panel.querySelector("[data-run-state]").textContent = value.status_label || value.state;
      panel.querySelector("[data-run-phase]").textContent = value.phase || value.reason || value.state;
      panel.querySelector("[data-run-counters]").textContent = Object.entries(value.counts || {})
        .map(([key, count]) => `${key.replaceAll("_", " ")}: ${count}`).join(" · ");
      updateReport(value.report_html);
      terminal = value.terminal; failures = 0;
      feedback.textContent = terminal ? "Final saved result. Live updates stopped." :
        "Following this run automatically. Progress reflects saved checkpoints; an in-flight request may not yet appear.";
    } catch (error) {
      if (version === generation) {
        failures += 1;
        feedback.textContent = "Progress connection interrupted. The task's saved state is unchanged; updates will retry without restarting work.";
      }
    } finally {
      clearTimeout(timeout);
      if (version === generation) {
        request = null;
        schedule(failures ? Math.min(20000, 4000 * 2 ** failures) : 4000);
      }
    }
  }

  document.addEventListener("visibilitychange", () => {
    cancel(); if (visible()) poll();
  });
  window.addEventListener("pagehide", () => { away = true; cancel(); });
  window.addEventListener("pageshow", () => { away = false; poll(); });
  window.addEventListener("offline", () => {
    cancel(); feedback.textContent = "Offline. Saved progress is retained; updates resume when the connection returns.";
  });
  window.addEventListener("online", () => { cancel(); poll(); });
  poll();
}());
