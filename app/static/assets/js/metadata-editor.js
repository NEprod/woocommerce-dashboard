(function () {
  "use strict";

  const root = document.querySelector("[data-metadata-editor]");
  const bootstrapNode = document.getElementById("metadata-editor-bootstrap");
  if (!root || !bootstrapNode) return;

  const boot = JSON.parse(bootstrapNode.textContent || "{}");
  const guided = root.querySelector("[data-guided-editor]");
  const advanced = root.querySelector('[data-editor-mode="advanced"]');
  const jsonEditor = root.querySelector("[data-json-editor]");
  const jsonLines = root.querySelector("[data-json-lines]");
  const jsonHighlight = root.querySelector("[data-json-highlight]");
  const feedback = document.querySelector("[data-metadata-feedback]");
  const saveButtons = Array.from(document.querySelectorAll("[data-save-metadata]"));
  const saveStates = Array.from(document.querySelectorAll("[data-save-state]"));
  const isShared = boot.kind === "shared";
  const knownFields = new Set([
    "collection_type", "title", "sku_prefix", "price", "sale_price",
    "sale_start_date", "sale_end_date", "weight", "dimensions", "categories",
    "tags", "live", "short_description", "description", "attributes",
    "image_attributes", "variation_modifiers", "shipping_class", "grouped_ids",
    "grouped_products", "upsell_ids", "cross_sell_ids", "upsells", "crosssells",
    "meta_title", "meta_description"
  ]);
  let authored = structuredClone(boot.authored || {});
  let dirty = false;
  let busy = false;
  let activeMode = "guided";

  function setDirty(value) {
    dirty = value;
    saveStates.forEach((node) => { node.textContent = value ? "Unsaved changes" : "Saved"; });
  }

  function setBusy(value) {
    busy = value;
    saveButtons.forEach((button) => {
      button.disabled = value;
      button.setAttribute("aria-busy", String(value));
      button.textContent = value ? "Saving…" : "Save Metadata";
    });
  }

  function showFeedback(type, title, message) {
    feedback.hidden = false;
    feedback.className = `metadata-feedback is-${type}`;
    feedback.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = message;
    feedback.append(heading, copy);
    feedback.scrollIntoView({ block: "nearest" });
  }

  function valueFor(field) {
    if (Object.prototype.hasOwnProperty.call(authored, field)) return authored[field];
    if (!isShared && Object.prototype.hasOwnProperty.call(boot.collection || {}, field)) return boot.collection[field];
    return "";
  }

  function overrideEnabled(field) {
    if (isShared) return true;
    const toggle = guided.querySelector(`[data-override-toggle="${CSS.escape(field)}"]`);
    return !!(toggle && toggle.checked);
  }

  function setControlState(field) {
    if (isShared) return;
    const enabled = overrideEnabled(field);
    const wrapper = guided.querySelector(`[data-field-wrapper="${CSS.escape(field)}"]`);
    if (wrapper) {
      wrapper.classList.toggle("is-inherited", !enabled);
      wrapper.querySelectorAll("input:not([data-override-toggle]), textarea, select, button").forEach((control) => {
        control.disabled = !enabled;
      });
    }
    if (field === "attributes") {
      guided.querySelectorAll("[data-attributes-editor] input, [data-attributes-editor] button, [data-add-attribute]").forEach((control) => { control.disabled = !enabled; });
    }
    if (field === "variation_modifiers") {
      guided.querySelectorAll("[data-modifiers-editor] input, [data-modifiers-editor] button, [data-add-modifier]").forEach((control) => { control.disabled = !enabled; });
    }
    if (field === "dimensions") {
      guided.querySelectorAll("[data-dimension]").forEach((control) => { control.disabled = !enabled; });
    }
  }

  function makeButton(label, action, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-ghost";
    button.dataset.rowAction = action;
    button.dataset.rowIndex = String(index);
    button.textContent = label;
    button.setAttribute("aria-label", `${label} item ${index + 1}`);
    return button;
  }

  function renderList(field, values) {
    const target = guided.querySelector(`[data-list-editor="${CSS.escape(field)}"]`);
    if (!target) return;
    target.replaceChildren();
    (Array.isArray(values) ? values : []).forEach((value, index) => {
      const row = document.createElement("div");
      row.className = "repeatable-row";
      row.dataset.listRow = field;
      const input = document.createElement("input");
      input.value = value == null ? "" : String(value);
      input.setAttribute("aria-label", `${field.replaceAll("_", " ")} item ${index + 1}`);
      row.append(input, makeButton("Move up", "up", index), makeButton("Move down", "down", index), makeButton("Remove", "remove", index));
      target.appendChild(row);
    });
    setControlState(field);
  }

  function listValues(field) {
    const target = guided.querySelector(`[data-list-editor="${CSS.escape(field)}"]`);
    return target ? Array.from(target.querySelectorAll("input")).map((input) => input.value.trim()).filter(Boolean) : [];
  }

  function renderAttributes(value) {
    const target = guided.querySelector("[data-attributes-editor]");
    target.replaceChildren();
    Object.entries(value && typeof value === "object" && !Array.isArray(value) ? value : {}).forEach(([name, values], index) => {
      const row = document.createElement("div");
      row.className = "structured-editor-row attribute-editor-row";
      row.dataset.attributeRow = "";
      const nameInput = document.createElement("input");
      nameInput.value = name;
      nameInput.placeholder = "Attribute name";
      nameInput.setAttribute("aria-label", `Attribute ${index + 1} name`);
      const valueInput = document.createElement("input");
      valueInput.value = Array.isArray(values) ? values.join(", ") : "";
      valueInput.placeholder = "Ordered values, comma separated";
      valueInput.setAttribute("aria-label", `Attribute ${index + 1} ordered values`);
      row.append(nameInput, valueInput, makeButton("Move up", "up", index), makeButton("Move down", "down", index), makeButton("Remove", "remove", index));
      target.appendChild(row);
    });
    setControlState("attributes");
  }

  function attributeValues() {
    const result = {};
    guided.querySelectorAll("[data-attribute-row]").forEach((row) => {
      const inputs = row.querySelectorAll("input");
      const name = inputs[0].value.trim();
      const values = inputs[1].value.split(",").map((value) => value.trim()).filter(Boolean);
      if (name) result[name] = values;
    });
    return result;
  }

  function renderModifiers(value) {
    const target = guided.querySelector("[data-modifiers-editor]");
    target.replaceChildren();
    Object.entries(value && typeof value === "object" && !Array.isArray(value) ? value : {}).forEach(([expression, modifier], index) => {
      const row = document.createElement("div");
      row.className = "structured-editor-row modifier-editor-row";
      row.dataset.modifierRow = "";
      const fields = [
        ["expression", expression, "Modifier expression"], ["price", modifier.price, "Price"],
        ["sale_price", modifier.sale_price, "Sale price"], ["weight", modifier.weight, "Weight"],
        ["length", modifier.dimensions && modifier.dimensions.length, "Length"],
        ["width", modifier.dimensions && modifier.dimensions.width, "Width"],
        ["height", modifier.dimensions && modifier.dimensions.height, "Height"]
      ];
      fields.forEach(([name, fieldValue, label]) => {
        const input = document.createElement("input");
        input.dataset.modifierField = name;
        input.value = fieldValue == null ? "" : String(fieldValue);
        input.placeholder = label;
        input.setAttribute("aria-label", `${label} for modifier ${index + 1}`);
        row.appendChild(input);
      });
      row.append(makeButton("Move up", "up", index), makeButton("Move down", "down", index), makeButton("Remove", "remove", index));
      target.appendChild(row);
    });
    setControlState("variation_modifiers");
  }

  function modifierValues() {
    const result = {};
    guided.querySelectorAll("[data-modifier-row]").forEach((row) => {
      const value = (name) => row.querySelector(`[data-modifier-field="${name}"]`).value.trim();
      const expression = value("expression");
      if (!expression) return;
      const modifier = {};
      ["price", "sale_price", "weight"].forEach((name) => { if (value(name)) modifier[name] = value(name); });
      const dimensions = {};
      ["length", "width", "height"].forEach((name) => { if (value(name)) dimensions[name] = value(name); });
      if (Object.keys(dimensions).length) modifier.dimensions = dimensions;
      result[expression] = modifier;
    });
    return result;
  }

  function populate(documentValue) {
    authored = structuredClone(documentValue || {});
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      let value = valueFor(field);
      if (field === "live") value = value === true ? "true" : value === false ? "false" : "";
      if (control.dataset.arrayField === "true") value = Array.isArray(value) ? value.join(", ") : "";
      control.value = value == null ? "" : String(value);
    });
    const dimensions = valueFor("dimensions");
    guided.querySelectorAll("[data-dimension]").forEach((control) => { control.value = dimensions && dimensions[control.dataset.dimension] != null ? dimensions[control.dataset.dimension] : ""; });
    ["categories", "tags", "image_attributes"].forEach((field) => renderList(field, valueFor(field)));
    renderAttributes(valueFor("attributes"));
    renderModifiers(valueFor("variation_modifiers"));
    guided.querySelectorAll("[data-override-toggle]").forEach((toggle) => setControlState(toggle.dataset.overrideToggle));
    updateCharacterCounts();
  }

  function prune(value) {
    if (Array.isArray(value)) return value.map(prune).filter((item) => item !== "" && item != null && !(typeof item === "object" && !Object.keys(item).length));
    if (value && typeof value === "object") {
      const result = {};
      Object.entries(value).forEach(([key, item]) => {
        const clean = prune(item);
        if (clean !== "" && clean != null && !(typeof clean === "object" && !Object.keys(clean).length)) result[key] = clean;
      });
      return result;
    }
    return value;
  }

  function guidedDocument() {
    const result = {};
    Object.entries(authored).forEach(([key, value]) => { if (!knownFields.has(key)) result[key] = value; });
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      if (!overrideEnabled(field)) return;
      let value = control.value.trim();
      if (field === "live") value = value === "true" ? true : value === "false" ? false : "";
      if (control.dataset.arrayField === "true") value = value.split(",").map((item) => item.trim()).filter(Boolean);
      result[field] = value;
    });
    ["categories", "tags", "image_attributes"].forEach((field) => { if (overrideEnabled(field)) result[field] = listValues(field); });
    if (overrideEnabled("attributes")) result.attributes = attributeValues();
    if (overrideEnabled("variation_modifiers")) result.variation_modifiers = modifierValues();
    if (overrideEnabled("dimensions")) {
      result.dimensions = {};
      guided.querySelectorAll("[data-dimension]").forEach((control) => { result.dimensions[control.dataset.dimension] = control.value.trim(); });
    }
    if (!isShared) {
      delete result.collection_type;
      delete result.sku_prefix;
    }
    return prune(result);
  }

  function clearErrors() {
    document.querySelectorAll("[data-field-error], [data-json-error]").forEach((node) => { node.hidden = true; node.textContent = ""; });
    document.querySelectorAll(".has-field-error").forEach((node) => node.classList.remove("has-field-error"));
  }

  function showErrors(errors) {
    clearErrors();
    (errors || []).forEach((issue) => {
      const field = String(issue.path || "$").replace(/^\$\.?/, "").replace(/^dimensions\./, "dimensions.");
      const node = document.querySelector(`[data-field-error="${CSS.escape(field)}"]`) || document.querySelector(`[data-field-error="${CSS.escape(field.split(".")[0])}"]`);
      if (node) {
        node.textContent = issue.message;
        node.hidden = false;
        node.closest(".guided-field, .editor-workspace-section")?.classList.add("has-field-error");
      }
    });
    const count = (errors || []).length;
    showFeedback("error", "Validation blocked this save", `${count} ${count === 1 ? "issue needs" : "issues need"} attention. Your entered data has been retained.`);
  }

  async function validateDocument(documentValue) {
    const response = await fetch(boot.validate_url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: boot.kind, data: documentValue }) });
    const payload = await response.json().catch(() => ({ valid: false, errors: [{ path: "$", message: "Validation returned an unreadable response." }] }));
    if (!response.ok || !payload.valid) {
      showErrors(payload.errors || [{ path: "$", message: payload.error || "Metadata is invalid." }]);
      return null;
    }
    clearErrors();
    showFeedback(payload.warnings?.length ? "warning" : "success", "Validation complete", payload.warnings?.length ? `${payload.warnings.length} documented warning(s) remain. No authored values were changed.` : "No blocking metadata issues were found.");
    return payload;
  }

  function updateCodePresentation() {
    const text = jsonEditor.value;
    jsonLines.textContent = Array.from({ length: Math.max(1, text.split("\n").length) }, (_item, index) => index + 1).join("\n");
    const escaped = text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    jsonHighlight.innerHTML = escaped.replace(/("(?:\\.|[^"\\])*")(?=\s*:)/g, '<span class="json-token-key">$1</span>').replace(/(:\s*)("(?:\\.|[^"\\])*")/g, '$1<span class="json-token-string">$2</span>').replace(/\b(true|false|null)\b/g, '<span class="json-token-literal">$1</span>').replace(/-?\b\d+(?:\.\d+)?\b/g, '<span class="json-token-number">$&</span>');
  }

  function advancedDocument() {
    try {
      const value = JSON.parse(jsonEditor.value);
      if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("The document root must be a JSON object.");
      const error = root.querySelector("[data-json-error]");
      error.hidden = true;
      return value;
    } catch (error) {
      const match = /position (\d+)/.exec(error.message);
      let location = "";
      if (match) {
        const before = jsonEditor.value.slice(0, Number(match[1]));
        location = ` at line ${before.split("\n").length}, column ${before.length - before.lastIndexOf("\n")}`;
      }
      const node = root.querySelector("[data-json-error]");
      node.textContent = `${error.message}${location}`;
      node.hidden = false;
      showFeedback("error", "Invalid JSON", "Correct the highlighted source error. Your draft remains unchanged.");
      return null;
    }
  }

  function enterAdvanced() {
    const documentValue = guidedDocument();
    jsonEditor.value = JSON.stringify(documentValue, null, 2);
    updateCodePresentation();
    guided.hidden = true;
    advanced.hidden = false;
    activeMode = "advanced";
    jsonEditor.focus();
  }

  function returnGuided() {
    const documentValue = advancedDocument();
    if (!documentValue) return;
    if (dirty && !window.confirm("Replace the current Guided values with this Advanced JSON draft?")) return;
    populate(documentValue);
    guided.hidden = false;
    advanced.hidden = true;
    activeMode = "guided";
  }

  async function save() {
    if (busy) return;
    const documentValue = activeMode === "advanced" ? advancedDocument() : guidedDocument();
    if (!documentValue) return;
    if (!(await validateDocument(documentValue))) return;
    setBusy(true);
    try {
      const response = await fetch(boot.save_url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: boot.kind, data: documentValue, replace: true }) });
      const payload = await response.json().catch(() => ({ error: "The save response could not be read." }));
      if (!response.ok || payload.error) {
        if (payload.errors) showErrors(payload.errors);
        else showFeedback("error", "Metadata was not saved", payload.message || payload.error || "The established update operation could not be started.");
        return;
      }
      authored = structuredClone(documentValue);
      setDirty(false);
      showFeedback("success", "Metadata saved", "The authoritative source was replaced atomically and the established catalogue update operation has started.");
      startProgress(payload.run_id);
    } catch (_error) {
      showFeedback("error", "Metadata was not saved", "A network error interrupted the request. Your draft remains available.");
    } finally {
      setBusy(false);
    }
  }

  function startProgress(runId) {
    if (!runId || !window.OperationProgress || !window.bootstrap) return;
    const modalElement = document.getElementById("scanModal");
    const view = window.OperationProgress.create(modalElement.querySelector("[data-operation-progress]"));
    view.begin(isShared ? "shared_collection_update" : "product_update");
    new window.bootstrap.Modal(modalElement).show();
    const stream = new EventSource(boot.stream_url_tpl.replace("__RUNID__", runId));
    stream.onmessage = (event) => view.appendLog(event.data);
    const interval = window.setInterval(async () => {
      const response = await fetch(boot.progress_url_tpl.replace("__RUNID__", runId));
      const progress = await response.json();
      view.update(progress);
      if (["done", "error"].includes(progress.status)) {
        window.clearInterval(interval);
        stream.close();
        fetch(boot.done_url_tpl.replace("__RUNID__", runId)).catch(() => {});
      }
    }, 1000);
  }

  function updateCharacterCounts() {
    document.querySelectorAll("[data-character-count]").forEach((node) => {
      const control = guided.querySelector(`[data-field="${CSS.escape(node.dataset.characterCount)}"]`);
      node.textContent = control ? control.value.length : "0";
    });
  }

  guided.addEventListener("input", () => { setDirty(true); updateCharacterCounts(); });
  guided.addEventListener("change", (event) => {
    if (event.target.matches("[data-override-toggle]")) setControlState(event.target.dataset.overrideToggle);
    setDirty(true);
  });
  guided.addEventListener("click", (event) => {
    const addList = event.target.closest("[data-add-list]");
    if (addList) { renderList(addList.dataset.addList, [...listValues(addList.dataset.addList), ""]); setDirty(true); return; }
    if (event.target.closest("[data-add-attribute]")) { const value = attributeValues(); value[`Attribute ${Object.keys(value).length + 1}`] = [""]; renderAttributes(value); setDirty(true); return; }
    if (event.target.closest("[data-add-modifier]")) { const value = modifierValues(); value[`Attribute=Value ${Object.keys(value).length + 1}`] = {}; renderModifiers(value); setDirty(true); return; }
    const action = event.target.closest("[data-row-action]");
    if (!action) return;
    const row = action.closest(".repeatable-row, .structured-editor-row");
    const parent = row.parentElement;
    if (action.dataset.rowAction === "remove") row.remove();
    if (action.dataset.rowAction === "up" && row.previousElementSibling) parent.insertBefore(row, row.previousElementSibling);
    if (action.dataset.rowAction === "down" && row.nextElementSibling) parent.insertBefore(row.nextElementSibling, row);
    setDirty(true);
  });

  document.querySelectorAll("[data-open-advanced]").forEach((button) => button.addEventListener("click", enterAdvanced));
  root.querySelector("[data-return-guided]").addEventListener("click", returnGuided);
  root.querySelector("[data-format-json]").addEventListener("click", () => {
    const value = advancedDocument();
    if (value) { jsonEditor.value = JSON.stringify(value, null, 2); updateCodePresentation(); setDirty(true); }
  });
  root.querySelector("[data-json-search]").addEventListener("input", (event) => {
    const query = event.target.value;
    if (!query) return;
    const index = jsonEditor.value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
    if (index >= 0) { jsonEditor.focus(); jsonEditor.setSelectionRange(index, index + query.length); }
  });
  jsonEditor.addEventListener("input", () => { setDirty(true); updateCodePresentation(); });
  document.querySelectorAll("[data-validate-metadata]").forEach((button) => button.addEventListener("click", () => {
    const value = activeMode === "advanced" ? advancedDocument() : guidedDocument();
    if (value) validateDocument(value);
  }));
  saveButtons.forEach((button) => button.addEventListener("click", save));
  const loadTemplateButton = document.getElementById("loadTemplateBtn");
  if (loadTemplateButton) loadTemplateButton.addEventListener("click", async () => {
    const select = document.getElementById("templateSelect");
    if (!select.value) return;
    try {
      const response = await fetch(boot.template_url_tpl.replace("__NAME__", encodeURIComponent(select.value)));
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Template unavailable");
      populate(payload.data);
      setDirty(true);
      showFeedback("warning", "Template loaded", "The fictional template is now an unsaved draft. Review its scope before saving.");
    } catch (error) {
      showFeedback("error", "Template unavailable", error.message);
    }
  });
  const loadAffectedButton = document.querySelector("[data-load-affected]");
  if (loadAffectedButton) loadAffectedButton.addEventListener("click", async () => {
    if (loadAffectedButton.disabled) return;
    const target = document.querySelector("[data-affected-products]");
    const nextPage = Number(loadAffectedButton.dataset.page || "2");
    loadAffectedButton.disabled = true;
    loadAffectedButton.textContent = "Loading…";
    try {
      const url = new URL(target.dataset.url, window.location.href);
      url.searchParams.set("page", String(nextPage));
      const response = await fetch(url.toString());
      const payload = await response.json();
      if (!response.ok) throw new Error("Affected products could not be loaded.");
      payload.items.forEach((item) => {
        const link = document.createElement("a");
        link.href = item.detail_url;
        if (item.thumbnail) {
          const image = document.createElement("img");
          image.src = item.thumbnail;
          image.alt = item.thumbnail_alt || item.title;
          image.loading = "lazy";
          link.appendChild(image);
        }
        const title = document.createElement("span");
        title.textContent = item.title;
        const sku = document.createElement("code");
        sku.textContent = item.sku || "Not set";
        link.append(title, sku);
        target.appendChild(link);
      });
      if (nextPage >= payload.pagination.pages) loadAffectedButton.remove();
      else {
        loadAffectedButton.dataset.page = String(nextPage + 1);
        loadAffectedButton.disabled = false;
        loadAffectedButton.textContent = "Load more affected products";
      }
    } catch (error) {
      loadAffectedButton.disabled = false;
      loadAffectedButton.textContent = "Retry affected products";
      showFeedback("error", "Affected products unavailable", error.message);
    }
  });
  window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

  populate(authored);
  setDirty(false);
})();
