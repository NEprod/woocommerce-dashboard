(function () {
  "use strict";

  const stageLabels = {
    queued: "Queued",
    preparing: "Preparing catalogue",
    scanning: "Resolving catalogue",
    ingesting: "Updating catalogue index",
    finalizing: "Finalizing identities",
    completed: "Operation complete",
    partial: "Completed with issues",
    failed: "Operation failed",
    working: "Working"
  };

  function create(root) {
    if (!root) return null;

    const status = root.querySelector("[data-progress-status]");
    const statusLabel = root.querySelector("[data-progress-status-label]");
    const stage = root.querySelector("[data-progress-stage]");
    const current = root.querySelector("[data-progress-current]");
    const elapsed = root.querySelector("[data-progress-elapsed]");
    const bar = root.querySelector("[data-progress-bar]");
    const label = root.querySelector("[data-progress-label]");
    const log = root.querySelector("[data-progress-log]");

    function setState(value) {
      const normalized = value === "done" ? "completed" : (value || "working");
      root.dataset.progressState = normalized;
      root.setAttribute("aria-busy", ["queued", "preparing", "scanning", "ingesting", "finalizing", "working", "running"].includes(normalized) ? "true" : "false");
      if (statusLabel) statusLabel.textContent = stageLabels[normalized] || normalized.replaceAll("_", " ");
      if (status) status.className = "status-pill operation-status scan-progress is-" + normalized;
    }

    function update(payload) {
      payload = payload || {};
      const operation = payload.operation || {};
      const progress = payload.progress || {};
      const counts = payload.counts || {};
      const timing = payload.timing || {};
      const operationStage = operation.stage || payload.status || "working";
      const percent = Number.isFinite(Number(progress.percent)) ? Math.max(0, Math.min(100, Number(progress.percent))) : 0;
      const completed = Number(progress.completed || 0);
      const total = Number(progress.total || 0);

      setState(operationStage);
      if (stage) stage.textContent = stageLabels[operationStage] || operationStage.replaceAll("_", " ");
      if (current) current.textContent = operation.current_item ? "Currently processing " + operation.current_item : (operationStage === "completed" ? "All selected catalogue work has finished." : "Operation status is being updated.");
      if (elapsed) {
        const elapsedSeconds = Math.max(0, Number(timing.elapsed_seconds || 0));
        const minutes = Math.floor(elapsedSeconds / 60);
        const seconds = Math.floor(elapsedSeconds % 60);
        elapsed.textContent = "Elapsed " + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
      }
      if (bar) {
        bar.style.width = percent + "%";
        bar.setAttribute("aria-valuenow", String(percent));
        bar.setAttribute("aria-valuetext", completed + " of " + total + " collections processed");
      }
      if (label) label.textContent = completed + " of " + total + " collections processed";
      Object.keys(counts).forEach(function (name) {
        const target = root.querySelector('[data-count="' + name + '"]');
        if (target) target.textContent = String(counts[name] || 0);
      });
      return payload;
    }

    function begin(type) {
      update({
        operation: {stage: "preparing", current_item: null, type: type},
        progress: {completed: 0, total: 0, percent: 0},
        timing: {elapsed_seconds: 0},
        counts: {collections: 0, products: 0, variations: 0, warnings: 0, failures: 0}
      });
      if (log) log.textContent = "";
    }

    function appendLog(line) {
      if (!log) return;
      log.textContent += line + "\n";
      log.scrollTop = log.scrollHeight;
    }

    function fail(message) {
      setState("failed");
      if (stage) stage.textContent = stageLabels.failed;
      if (current) current.textContent = message || "The operation could not be completed.";
    }

    return {root: root, update: update, begin: begin, appendLog: appendLog, fail: fail};
  }

  window.OperationProgress = {
    create: create,
    createAll: function () {
      return Array.from(document.querySelectorAll("[data-operation-progress]")).map(create);
    }
  };
})();
