(function () {
  "use strict";
  const panel = document.querySelector("[data-taxonomy-batch]");
  const start = panel?.querySelector("[data-taxonomy-continue]");
  if (!start) return;
  start.addEventListener("click", async function () {
    start.disabled = true;
    const progress = panel.querySelector("[data-taxonomy-progress]");
    try {
      for (let i = 0; i < 50; i += 1) {
        progress.textContent = "Applying one approved stage; successful earlier stages are retained…";
        const response = await fetch(panel.dataset.stepUrl, {method: "POST", headers: {
          "Content-Type": "application/json", "X-CSRFToken": panel.dataset.csrf},
          body: JSON.stringify({execution: panel.dataset.execution})});
        let result;
        try { result = await response.json(); }
        catch (_) { throw new Error("The stage response was unavailable. Open retained batch history and inspect/reconcile before continuing; no automatic retry was made."); }
        if (!response.ok) throw new Error(result.error || "Stage unavailable. Inspect retained batch; do not blindly retry.");
        progress.textContent = `${result.completed} / ${result.total} stages verified · ${result.excluded} products excluded.`;
        if (result.status !== "approved") { window.location.assign(result.history); return; }
      }
      progress.textContent = "Stage budget reached. Inspect the retained batch before continuing.";
    } catch (error) { progress.textContent = error.message; }
  });
  if (document.querySelector("[data-taxonomy-auto-execute]")) start.click();
})();
