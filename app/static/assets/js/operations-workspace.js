(function () {
  "use strict";
  const hero = document.querySelector("[data-operation-id]");
  if (!hero) return;
  const operationId = hero.dataset.operationId;
  const logs = document.querySelector("[data-operation-logs]");
  const logForm = document.querySelector("[data-log-form]");
  const logPagination = document.querySelector("[data-log-pagination]");
  let logPage = 1;
  let polling = hero.dataset.terminal !== "true";
  let pollFailures = 0;

  function renderLogs(payload) {
    logs.replaceChildren();
    if (!payload.items.length) {
      const empty = document.createElement("p");
      empty.textContent = payload.retained ? "No logs match this filter." : "No process-local logs are retained for this operation.";
      logs.appendChild(empty);
    } else {
      payload.items.forEach(function (line) {
        const row = document.createElement("div");
        row.className = "operation-log-line";
        row.textContent = line;
        logs.appendChild(row);
      });
    }
    logPagination.replaceChildren();
    if (payload.page > 1) addLogButton("Previous", payload.page - 1);
    const summary = document.createElement("span");
    summary.textContent = "Page " + payload.page + " of " + payload.pages + " · " + payload.total + " lines";
    logPagination.appendChild(summary);
    if (payload.page < payload.pages) addLogButton("Next", payload.page + 1);
  }

  function addLogButton(label, page) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-ghost btn-sm";
    button.textContent = label;
    button.addEventListener("click", function () { logPage = page; loadLogs(); });
    logPagination.appendChild(button);
  }

  async function loadLogs() {
    const form = new FormData(logForm);
    const query = new URLSearchParams({page: String(logPage), per_page: "50", q: form.get("q") || "", severity: form.get("severity") || ""});
    try {
      const response = await fetch("/api/operations/" + encodeURIComponent(operationId) + "/logs?" + query.toString(), {headers: {"Accept": "application/json"}, credentials: "same-origin"});
      if (!response.ok) throw new Error("log request failed");
      renderLogs(await response.json());
    } catch (_error) {
      logs.textContent = "Logs could not be loaded. Use Apply to try again.";
    }
  }

  async function poll() {
    if (!polling || document.hidden) return;
    try {
      const response = await fetch("/api/operations/" + encodeURIComponent(operationId) + "/status", {headers: {"Accept": "application/json"}, credentials: "same-origin"});
      if (!response.ok) throw new Error("status request failed");
      const payload = await response.json();
      pollFailures = 0;
      const status = document.querySelector("[data-operation-status]");
      if (status) status.textContent = payload.operation.status_label;
      polling = !payload.terminal;
      if (polling) window.setTimeout(poll, 4000);
      else window.location.reload();
    } catch (_error) {
      pollFailures += 1;
      if (pollFailures < 3) window.setTimeout(poll, 8000);
      else polling = false;
    }
  }

  logForm.addEventListener("submit", function (event) { event.preventDefault(); logPage = 1; loadLogs(); });
  loadLogs();
  if (polling) window.setTimeout(poll, 4000);
})();
