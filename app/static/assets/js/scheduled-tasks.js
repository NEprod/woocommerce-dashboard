(function () {
  "use strict";
  const panel = document.querySelector("[data-scheduled-task]");
  if (!panel) return;
  const feedback = panel.querySelector("[data-task-feedback]");
  const button = panel.querySelector("[data-task-run]");
  let polling = false;
  function show(task) {
    const badge = panel.querySelector("[data-task-state]");
    badge.textContent = task.state.replaceAll("_", " ");
    badge.className = "status-badge " + ({queued: "status-running", running: "status-running", completed: "status-succeeded", paused: "status-partial", partially_completed: "status-partial", failed: "status-failed"}[task.state] || "");
    panel.querySelectorAll("[data-task-time]").forEach(node => {
      node.textContent = task[node.dataset.taskTime] ? new Date(task[node.dataset.taskTime]).toLocaleString('en-GB', {timeZone:'Europe/London',timeZoneName:'short',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : "Not yet";
    });
    panel.querySelector("[data-task-outcome]").textContent = task.outcome?.replaceAll("_", " ") || "Not yet run";
    panel.querySelector("[data-task-duration]").textContent = task.duration == null ? "Not yet" : `${task.duration.toFixed(1)} seconds`;
    panel.querySelector("[data-task-progress]").textContent = Object.entries(task.progress || {}).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(" · ");
    feedback.textContent = task.error || task.reason || `Task ${task.state.replaceAll("_", " ")}. ${task.key === "shopify_publisher" ? "Only separately authorised scanned work may publish; original receipts retain readback and recovery evidence." : task.key === "shopify_sales" ? "Only authorised due sale/release transitions may execute." : "No Shopify writes."}`;
    return task;
  }
  async function fetchTask(url, data) {
    const response = await fetch(url, data ? {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": panel.dataset.csrf}, body: JSON.stringify(data)} : {headers: {"Accept": "application/json"}});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Task request unavailable. Saved history is retained.");
    return show(result);
  }
  async function refresh() { return fetchTask(panel.dataset.statusUrl); }
  button.addEventListener("click", async () => {
    if (polling) return;
    polling = true; button.disabled = true;
    try {
      const task = await fetchTask(button.dataset.runUrl, {});
      if (!task.history_url) throw new Error("Run accepted. Open task history to inspect its saved progress; do not submit again.");
      // Follow the accepted run, never a later run of the same task. The shared
      // run page observes persisted progress without another start request.
      window.location.assign(task.history_url);
      return;
    } catch (error) { feedback.textContent = error.message; }
    polling = false; button.disabled = false;
  });
  panel.querySelector("[data-task-settings]")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    try { await fetchTask(form.getAttribute("action"), {enabled: form.elements.enabled.checked, interval: Number(form.elements.interval.value)}); }
    catch (error) { feedback.textContent = error.message; }
  });
  panel.querySelector("[data-task-refresh]").addEventListener("click", () => refresh().catch(error => { feedback.textContent = error.message; }));
}());
