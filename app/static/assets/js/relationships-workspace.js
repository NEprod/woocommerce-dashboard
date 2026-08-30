(function (root) {
  "use strict";

  const STORAGE_KEY = "relationship-mutual-selection-v1";

  function normaliseSelection(values, limit) {
    const result = [];
    (Array.isArray(values) ? values : []).forEach(function (value) {
      if (typeof value === "string" && value.trim() && !result.includes(value.trim()) && result.length < limit) result.push(value.trim());
    });
    return result;
  }

  function directedCount(count) { return Math.max(0, Number(count) || 0) * Math.max(0, (Number(count) || 0) - 1); }

  function safeJson(response) {
    const type = response.headers && response.headers.get ? response.headers.get("content-type") || "" : "";
    if (response.redirected || response.status === 401 || response.status === 403) return Promise.reject(new Error("Your session has expired. Sign in again before continuing."));
    if (!type.includes("application/json")) return Promise.reject(new Error("The server returned an unexpected response. No relationships were changed."));
    return response.json().catch(function () { throw new Error("The server returned malformed data. No relationships were changed."); }).then(function (payload) {
      if (!response.ok || !payload || payload.ok !== true) throw new Error((payload && payload.error) || "The relationship request could not be completed safely.");
      return payload;
    });
  }

  root.RelationshipsWorkspaceClient = {normaliseSelection: normaliseSelection, directedCount: directedCount, safeJson: safeJson};
  const documentRef = root.document;
  if (!documentRef || !documentRef.querySelector) return;
  const workspace = documentRef.querySelector("[data-mutual-family]");
  if (!workspace) return;

  const limit = Number(workspace.dataset.selectionLimit) || 30;
  let selected = [];
  let proposal = null;
  let submitting = false;
  try { selected = normaliseSelection(JSON.parse(root.sessionStorage.getItem(STORAGE_KEY) || "[]"), limit); } catch (_error) { selected = []; }
  const count = workspace.querySelector("[data-selected-count]");
  const review = workspace.querySelector("[data-review-family]");
  const preview = workspace.querySelector("[data-family-preview]");
  const errorBox = workspace.querySelector("[data-family-error]");
  const acknowledge = workspace.querySelector("[data-family-ack]");
  const confirm = workspace.querySelector("[data-confirm-family]");

  function persist() { root.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(selected)); }
  function update() {
    workspace.querySelectorAll("[data-family-result]").forEach(function (row) { row.querySelector("input").checked = selected.includes(row.dataset.productSku); });
    count.textContent = `${selected.length} product${selected.length === 1 ? "" : "s"} selected`;
    review.disabled = selected.length < 2;
  }
  function showError(message) { errorBox.textContent = message; errorBox.hidden = false; }
  function clearError() { errorBox.hidden = true; errorBox.textContent = ""; }
  async function post(url, body) {
    return safeJson(await root.fetch(url, {method: "POST", credentials: "same-origin", headers: {"Accept": "application/json", "Content-Type": "application/json", "X-CSRFToken": workspace.dataset.csrfToken}, body: JSON.stringify(body)}));
  }
  function metric(label, value) { const node = documentRef.createElement("article"); node.className = "products-stat-card"; const body = documentRef.createElement("div"); const name = documentRef.createElement("p"); name.textContent = label; const strong = documentRef.createElement("strong"); strong.textContent = String(value); body.append(name, strong); node.append(body); return node; }
  function render(value) {
    proposal = value;
    const metrics = workspace.querySelector("[data-preview-metrics]"); metrics.replaceChildren();
    [["Products", value.selected_count], ["Directed edges", value.exact_relationship_count], ["New", value.new_count], ["Already exist", value.already_linked_count], ["Invalid", value.invalid_count], ["JSON documents", value.affected_document_count], ["Broken targets", value.broken_target_count], ["Warnings", value.warning_count]].forEach(function (item) { metrics.append(metric(item[0], item[1])); });
    const products = workspace.querySelector("[data-preview-products]"); products.replaceChildren(); const list = documentRef.createElement("ul"); list.className = "family-preview-list";
    value.selected_products.forEach(function (item) { const row = documentRef.createElement("li"); row.textContent = `${item.title} — ${item.sku} — ${item.collection}`; list.append(row); }); products.append(list);
    const documentHeading = documentRef.createElement("strong"); documentHeading.textContent = "Authored JSON documents"; products.append(documentHeading);
    const documents = documentRef.createElement("ul"); documents.className = "family-preview-list";
    (value.affected_documents || []).forEach(function (path) { const row = documentRef.createElement("li"); row.textContent = path; documents.append(row); }); products.append(documents);
    const warnings = workspace.querySelector("[data-preview-warnings]"); warnings.replaceChildren();
    (value.warnings || []).forEach(function (item) { const row = documentRef.createElement("p"); row.className = "relationship-warning"; row.textContent = `${item.title}: ${item.messages.join(" ")}`; warnings.append(row); });
    (value.invalid || []).forEach(function (item) { const row = documentRef.createElement("p"); row.className = "field-error"; row.textContent = item.reason; warnings.append(row); });
    acknowledge.checked = false; confirm.disabled = true; preview.hidden = false; preview.scrollIntoView({behavior: "smooth", block: "start"});
  }

  workspace.querySelectorAll("[data-family-result] input").forEach(function (input) { input.addEventListener("change", function () { const sku = input.closest("[data-family-result]").dataset.productSku; if (input.checked && !selected.includes(sku)) { if (selected.length >= limit) { input.checked = false; showError(`Select no more than ${limit} products.`); return; } selected.push(sku); } else if (!input.checked) selected = selected.filter(function (value) { return value !== sku; }); persist(); update(); }); });
  workspace.querySelector("[data-select-visible]").addEventListener("click", function () { clearError(); workspace.querySelectorAll("[data-family-result]").forEach(function (row) { if (selected.length < limit && !selected.includes(row.dataset.productSku)) selected.push(row.dataset.productSku); }); persist(); update(); });
  workspace.querySelector("[data-clear-selection]").addEventListener("click", function () { selected = []; proposal = null; persist(); preview.hidden = true; update(); });
  workspace.querySelector("[role='listbox']")?.addEventListener("keydown", function (event) { if (!["ArrowDown", "ArrowUp", " ", "Enter"].includes(event.key)) return; const inputs = Array.from(workspace.querySelectorAll("[data-family-result] input")); const index = inputs.indexOf(documentRef.activeElement); if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); inputs[(index + (event.key === "ArrowDown" ? 1 : -1) + inputs.length) % inputs.length].focus(); } });
  review.addEventListener("click", async function () { clearError(); try { render((await post(workspace.dataset.previewUrl, {product_skus: selected})).preview); } catch (error) { showError(error.message); } });
  acknowledge.addEventListener("change", function () { confirm.disabled = !acknowledge.checked || !proposal || !proposal.continuation_allowed; });
  confirm.addEventListener("click", async function () { if (submitting || !proposal || !acknowledge.checked) return; submitting = true; confirm.disabled = true; confirm.textContent = "Creating…"; try { const result = await post(workspace.dataset.confirmUrl, {product_skus: selected, proposal_digest: proposal.proposal_digest, acknowledged: true}); root.sessionStorage.removeItem(STORAGE_KEY); root.location.assign(`/operations/${encodeURIComponent(result.operation_id)}`); } catch (error) { showError(error.message); submitting = false; confirm.disabled = false; confirm.textContent = "Create Mutual Cross-Sells"; } });
  update();
})(typeof window !== "undefined" ? window : globalThis);
