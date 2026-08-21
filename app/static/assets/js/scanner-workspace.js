(function (root) {
  "use strict";

  function controlledError(message) {
    return new Error(message);
  }

  async function readStartResponse(response) {
    if (response.redirected || response.status === 401 || response.status === 403) {
      throw controlledError("Your session has expired. Sign in again before starting another scan.");
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw controlledError("The scan could not be started because the server returned an unexpected response.");
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw controlledError("The scan could not be started because the server returned an unexpected response.");
    }
    if (!response.ok) {
      throw controlledError(payload.message || "The scan could not be started safely.");
    }
    if (!payload || payload.ok !== true || typeof payload.operation_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(payload.operation_id)) {
      throw controlledError("The scan started without a valid operation identity. Refresh Scanner to open the active operation.");
    }
    return payload;
  }

  function operationDestination(payload, baseUrl) {
    const fallback = "Scan started successfully, but automatic navigation was unavailable. Open the operation directly.";
    try {
      const operationId = String(payload.operation_id || "");
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(operationId) || typeof payload.destination !== "string") {
        throw controlledError(fallback);
      }
      const base = new URL(baseUrl);
      const destination = new URL(payload.destination, base);
      const expectedPath = `/operations/${encodeURIComponent(operationId)}`;
      if (
        destination.origin !== base.origin || destination.pathname !== expectedPath ||
        destination.search || destination.hash || payload.destination.endsWith("/")
      ) {
        throw controlledError(fallback);
      }
      return destination.href;
    } catch (_error) {
      throw controlledError(fallback);
    }
  }

  root.ScannerWorkspaceClient = {readStartResponse, operationDestination};

  const documentRef = root.document;
  if (!documentRef || typeof documentRef.querySelector !== "function") return;
  const dialog = documentRef.querySelector("[data-scan-dialog]");
  if (!dialog) return;
  const modeLabel = dialog.querySelector("[data-confirm-mode-label]");
  const impact = dialog.querySelector("[data-confirm-impact]");
  const ordinaryCheck = dialog.querySelector("[data-confirm-operation]");
  const fullLabel = dialog.querySelector("[data-full-confirm]");
  const fullCheck = fullLabel.querySelector("input");
  const start = dialog.querySelector("[data-start-scan]");
  const error = dialog.querySelector("[data-scan-error]");
  const success = dialog.querySelector("[data-scan-success]");
  const openOperation = dialog.querySelector("[data-open-created-operation]");
  let mode = null;
  let submitting = false;
  let operationStarted = false;

  const copy = {
    append: "Append selects catalogue items not already completed by the existing marker workflow.",
    update: "Update selects products through the established update-marker workflow.",
    full: "Full is catalogue-wide, reconciles complete scope, and may take substantially longer."
  };

  documentRef.querySelectorAll("[data-open-scan-confirm]").forEach(function (button) {
    button.addEventListener("click", function () {
      mode = button.dataset.openScanConfirm;
      modeLabel.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
      impact.textContent = copy[mode];
      fullLabel.hidden = mode !== "full";
      ordinaryCheck.checked = false;
      fullCheck.checked = false;
      error.hidden = true;
      success.hidden = true;
      operationStarted = false;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    });
  });
  dialog.querySelector("[data-cancel-scan]").addEventListener("click", function () { dialog.close(); });

  start.addEventListener("click", async function () {
    if (submitting || operationStarted) return;
    if (!ordinaryCheck.checked || (mode === "full" && !fullCheck.checked)) {
      error.textContent = "Complete the required confirmation before starting.";
      error.hidden = false;
      return;
    }
    submitting = true;
    start.disabled = true;
    start.textContent = "Starting…";
    try {
      const params = new URLSearchParams(root.location.search);
      const response = await root.fetch("/scanner/start", {
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
      const payload = await readStartResponse(response);
      operationStarted = true;
      error.hidden = true;
      success.hidden = false;
      start.textContent = "Scan started";
      try {
        const destination = operationDestination(payload, root.location.href);
        openOperation.href = destination;
        root.location.assign(destination);
      } catch (navigationError) {
        openOperation.href = `/operations/${encodeURIComponent(payload.operation_id)}`;
        error.textContent = navigationError.message;
        error.hidden = false;
      }
    } catch (requestError) {
      if (operationStarted) {
        success.hidden = false;
        error.textContent = "Scan started successfully, but automatic navigation was unavailable. Open the operation directly.";
      } else {
        error.textContent = requestError.message || "The operation could not be started safely.";
        submitting = false;
        start.disabled = false;
        start.textContent = "Start operation";
      }
      error.hidden = false;
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
