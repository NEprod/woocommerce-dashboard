(function (root) {
  "use strict";

  function controlledMessage(status) {
    if (status === 401 || status === 403) return "Your session has expired. Sign in again before editing relationships.";
    if (status === 404) return "The product or relationship destination is no longer available.";
    if (status === 409) return "Another catalogue operation is active. Wait for it to finish and try again.";
    return "The relationship request could not be completed safely.";
  }

  async function readJson(response) {
    const type = response.headers && response.headers.get ? (response.headers.get("content-type") || "") : "";
    if (response.redirected || response.status === 401 || response.status === 403) throw new Error(controlledMessage(response.status));
    if ((response.status === 404 || response.status === 409) && !type.includes("application/json")) throw new Error(controlledMessage(response.status));
    if (!type.includes("application/json")) throw new Error("The server returned an unexpected response. No relationship was changed.");
    let payload;
    try { payload = await response.json(); } catch (_error) { throw new Error("The server returned malformed data. No relationship was changed."); }
    if (!response.ok || !payload || payload.ok !== true) throw new Error((payload && payload.error) || controlledMessage(response.status));
    return payload;
  }

  function selectedSkus(container) {
    return Array.from(container.querySelectorAll("[data-relationship-result][aria-selected='true']"))
      .map(function (row) { return row.dataset.productSku; })
      .filter(function (value) { return typeof value === "string" && value.length > 0; });
  }

  function orderedSkus(container) {
    return Array.from(container.querySelectorAll(".relationship-card[data-target-sku]"))
      .map(function (row) { return row.dataset.targetSku; })
      .filter(function (value) { return typeof value === "string" && value.length > 0; });
  }

  function previewCopy(preview) {
    if (preview.exact_relationship_count !== undefined) {
      return `${preview.selected_count} products selected · ${preview.exact_relationship_count} directed relationships · ${preview.new_count} new · ${preview.already_linked_count} already linked · ${preview.invalid_count} invalid`;
    }
    return `${preview.selected_count} products selected · ${preview.new_count} new relationships · ${preview.already_linked_count} already linked · ${preview.invalid_count} invalid`;
  }

  root.ProductRelationshipsClient = {readJson: readJson, selectedSkus: selectedSkus, orderedSkus: orderedSkus, previewCopy: previewCopy};

  const documentRef = root.document;
  if (!documentRef || typeof documentRef.querySelector !== "function") return;
  const workspace = documentRef.querySelector("[data-relationship-workspace]");
  if (!workspace) return;

  const picker = workspace.querySelector("[data-relationship-picker]");
  const search = workspace.querySelector("[data-relationship-search]");
  const results = workspace.querySelector("[data-relationship-results]");
  const summary = workspace.querySelector("[data-selection-summary]");
  const previewPanel = workspace.querySelector("[data-relationship-preview]");
  const previewSummary = workspace.querySelector("[data-preview-summary]");
  const previewWarnings = workspace.querySelector("[data-preview-warnings]");
  const confirmButton = workspace.querySelector("[data-confirm-relationship-update]");
  const errorBox = workspace.querySelector("[data-relationship-error]");
  let timer = null;
  let pending = null;
  let submitting = false;

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }
  function clearError() { errorBox.hidden = true; errorBox.textContent = ""; }
  function updateSelection() {
    const count = selectedSkus(results).length;
    summary.textContent = `${count} product${count === 1 ? "" : "s"} selected`;
    workspace.querySelectorAll("[data-preview-add]").forEach(function (button) { button.disabled = count === 0; });
    workspace.querySelector("[data-preview-mutual]").disabled = count < 2;
  }
  function resultNode(item) {
    const row = documentRef.createElement("button");
    row.type = "button";
    row.className = "relationship-search-result";
    row.dataset.relationshipResult = "";
    row.dataset.productSku = item.sku;
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", "false");
    const image = item.thumbnail ? `<img src="${item.thumbnail}" alt="">` : `<span aria-hidden="true">□</span>`;
    row.innerHTML = `<span class="relationship-thumbnail">${image}</span><span><strong></strong><code></code><small></small></span><span class="catalogue-pill"></span>`;
    row.querySelector("strong").textContent = item.title;
    row.querySelector("code").textContent = item.sku;
    row.querySelector("small").textContent = `${item.collection} · ${item.category} · ${item.product_type}`;
    const state = row.querySelector(".catalogue-pill");
    state.className = `catalogue-pill is-${item.catalogue_status}`;
    state.textContent = `${item.catalogue_status} · ${item.publishing_intent.label}`;
    row.addEventListener("click", function () {
      row.setAttribute("aria-selected", row.getAttribute("aria-selected") === "true" ? "false" : "true");
      updateSelection();
    });
    return row;
  }
  async function runSearch() {
    const query = search.value.trim();
    results.replaceChildren();
    updateSelection();
    if (!query) return;
    try {
      const url = new URL(workspace.dataset.searchUrl, root.location.href);
      url.searchParams.set("q", query);
      const response = await root.fetch(url.toString(), {headers: {"Accept": "application/json"}, credentials: "same-origin"});
      const payload = await readJson(response);
      if (!payload.items.length) {
        const empty = documentRef.createElement("p"); empty.className = "text-muted"; empty.textContent = "No eligible local products matched."; results.appendChild(empty);
      } else payload.items.forEach(function (item) { results.appendChild(resultNode(item)); });
    } catch (error) { showError(error.message); }
  }
  async function post(url, body) {
    const response = await root.fetch(url, {
      method: "POST",
      headers: {"Accept": "application/json", "Content-Type": "application/json", "X-CSRFToken": workspace.dataset.csrfToken},
      credentials: "same-origin",
      body: JSON.stringify(body)
    });
    return readJson(response);
  }
  function showPreview(payload, request) {
    pending = request;
    previewSummary.textContent = previewCopy(payload.preview);
    previewWarnings.replaceChildren();
    (payload.preview.warnings || []).forEach(function (warning) {
      const line = documentRef.createElement("p"); line.className = "relationship-warning"; line.textContent = `${warning.title}: ${warning.messages.join(" ")}`; previewWarnings.appendChild(line);
    });
    (payload.preview.invalid || []).forEach(function (invalid) {
      const line = documentRef.createElement("p"); line.className = "field-error"; line.textContent = invalid.reason; previewWarnings.appendChild(line);
    });
    confirmButton.disabled = !payload.preview.continuation_allowed;
    previewPanel.hidden = false;
  }
  async function previewAdd(type) {
    clearError();
    const request = {relationship_type: type, target_skus: selectedSkus(results), mode: "add", mutual: false};
    try { showPreview(await post(workspace.dataset.previewUrl, request), request); } catch (error) { showError(error.message); }
  }
  async function previewMutual() {
    clearError();
    const request = {product_skus: selectedSkus(results), mutual: true};
    try { showPreview(await post(workspace.dataset.mutualPreviewUrl, request), request); } catch (error) { showError(error.message); }
  }
  async function previewOrder(type) {
    const list = workspace.querySelector(`[data-relationship-list='${type}']`);
    const request = {relationship_type: type, target_skus: orderedSkus(list), mode: "replace", mutual: false};
    if (typeof picker.showModal === "function") picker.showModal(); else picker.setAttribute("open", "");
    try { showPreview(await post(workspace.dataset.previewUrl, request), request); } catch (error) { showError(error.message); }
  }

  workspace.querySelector("[data-open-relationship-picker]").addEventListener("click", function () {
    clearError(); previewPanel.hidden = true; pending = null;
    if (typeof picker.showModal === "function") picker.showModal(); else picker.setAttribute("open", "");
    search.focus();
  });
  search.addEventListener("input", function () { clearTimeout(timer); timer = root.setTimeout(runSearch, 180); });
  results.addEventListener("keydown", function (event) {
    const options = Array.from(results.querySelectorAll("[data-relationship-result]"));
    if (!options.length || !["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) return;
    const current = options.indexOf(documentRef.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      options[(current + direction + options.length) % options.length].focus();
    }
  });
  workspace.querySelector("[data-select-visible]").addEventListener("click", function () { results.querySelectorAll("[data-relationship-result]").forEach(function (row) { row.setAttribute("aria-selected", "true"); }); updateSelection(); });
  workspace.querySelector("[data-clear-selection]").addEventListener("click", function () { results.querySelectorAll("[data-relationship-result]").forEach(function (row) { row.setAttribute("aria-selected", "false"); }); updateSelection(); });
  workspace.querySelectorAll("[data-preview-add]").forEach(function (button) { button.addEventListener("click", function () { previewAdd(button.dataset.previewAdd); }); });
  workspace.querySelector("[data-preview-mutual]").addEventListener("click", previewMutual);
  workspace.querySelector("[data-cancel-relationship-preview]").addEventListener("click", function () { previewPanel.hidden = true; pending = null; });
  confirmButton.addEventListener("click", async function () {
    if (!pending || submitting) return;
    submitting = true; confirmButton.disabled = true; confirmButton.textContent = "Saving…";
    try {
      const url = pending.mutual ? workspace.dataset.mutualConfirmUrl : workspace.dataset.confirmUrl;
      await post(url, Object.assign({}, pending, {confirm: true}));
      root.location.reload();
    } catch (error) {
      showError(error.message); submitting = false; confirmButton.disabled = false; confirmButton.textContent = "Confirm local update";
    }
  });
  workspace.querySelectorAll("[data-remove-relationship]").forEach(function (button) {
    button.addEventListener("click", function () {
      const card = button.closest(".relationship-card"); const group = card.closest("[data-relationship-group]");
      card.remove(); group.querySelector("[data-save-relationship-order]").hidden = false;
    });
  });
  workspace.querySelectorAll("[data-move-relationship]").forEach(function (button) {
    button.addEventListener("click", function () {
      const card = button.closest(".relationship-card"); const list = card.parentElement;
      if (button.dataset.moveRelationship === "up" && card.previousElementSibling) list.insertBefore(card, card.previousElementSibling);
      if (button.dataset.moveRelationship === "down" && card.nextElementSibling) list.insertBefore(card.nextElementSibling, card);
      card.closest("[data-relationship-group]").querySelector("[data-save-relationship-order]").hidden = false;
    });
  });
  workspace.querySelectorAll("[data-save-relationship-order]").forEach(function (button) { button.addEventListener("click", function () { previewOrder(button.dataset.saveRelationshipOrder); }); });
  workspace.querySelectorAll("[data-relationship-list]").forEach(function (list) {
    let dragged = null;
    list.addEventListener("dragstart", function (event) { dragged = event.target.closest(".relationship-card"); });
    list.addEventListener("dragover", function (event) { if (!dragged) return; event.preventDefault(); const over = event.target.closest(".relationship-card"); if (over && over !== dragged) list.insertBefore(dragged, over); });
    list.addEventListener("drop", function () { if (dragged) list.closest("[data-relationship-group]").querySelector("[data-save-relationship-order]").hidden = false; dragged = null; });
  });
})(typeof window !== "undefined" ? window : globalThis);
