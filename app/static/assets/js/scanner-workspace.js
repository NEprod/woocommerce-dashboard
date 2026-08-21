(function () {
  "use strict";
  const dialog = document.querySelector("[data-scan-dialog]");
  if (!dialog) return;
  const modeLabel = dialog.querySelector("[data-confirm-mode-label]");
  const impact = dialog.querySelector("[data-confirm-impact]");
  const ordinaryCheck = dialog.querySelector("[data-confirm-operation]");
  const fullLabel = dialog.querySelector("[data-full-confirm]");
  const fullCheck = fullLabel.querySelector("input");
  const start = dialog.querySelector("[data-start-scan]");
  const error = dialog.querySelector("[data-scan-error]");
  let mode = null;
  let submitting = false;

  const copy = {
    append: "Append selects catalogue items not already completed by the existing marker workflow.",
    update: "Update selects products through the established update-marker workflow.",
    full: "Full is catalogue-wide, reconciles complete scope, and may take substantially longer."
  };

  document.querySelectorAll("[data-open-scan-confirm]").forEach(function (button) {
    button.addEventListener("click", function () {
      mode = button.dataset.openScanConfirm;
      modeLabel.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
      impact.textContent = copy[mode];
      fullLabel.hidden = mode !== "full";
      ordinaryCheck.checked = false;
      fullCheck.checked = false;
      error.hidden = true;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    });
  });
  dialog.querySelector("[data-cancel-scan]").addEventListener("click", function () { dialog.close(); });

  start.addEventListener("click", async function () {
    if (submitting) return;
    if (!ordinaryCheck.checked || (mode === "full" && !fullCheck.checked)) {
      error.textContent = "Complete the required confirmation before starting.";
      error.hidden = false;
      return;
    }
    submitting = true;
    start.disabled = true;
    start.textContent = "Starting…";
    try {
      const params = new URLSearchParams(window.location.search);
      const response = await fetch("/scanner/start", {
        method: "POST",
        headers: {"Content-Type": "application/json", "Accept": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({
          mode: mode,
          confirm_operation: true,
          confirm_full_regeneration: mode === "full" && fullCheck.checked,
          retry_of: params.get("retry_of") || ""
        })
      });
      const type = response.headers.get("content-type") || "";
      const payload = type.includes("application/json") ? await response.json() : {};
      if (!response.ok || !payload.detail_url) throw new Error(payload.message || "The operation could not be started safely.");
      window.location.assign(payload.detail_url);
    } catch (requestError) {
      error.textContent = requestError.message || "The operation could not be started safely.";
      error.hidden = false;
      submitting = false;
      start.disabled = false;
      start.textContent = "Start operation";
    }
  });
})();
