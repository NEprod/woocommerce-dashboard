(function () {
  "use strict";

  const root = document.querySelector("[data-prepared-metadata-editor]");
  const bootNode = document.getElementById("prepared-metadata-bootstrap");
  if (!root || !bootNode) return;

  const boot = JSON.parse(bootNode.textContent || "{}");
  const guided = root.querySelector("[data-prepared-guided]");
  const advanced = root.querySelector("[data-prepared-advanced]");
  const editor = root.querySelector("[data-json-editor]");
  const lines = root.querySelector("[data-json-lines]");
  const known = new Set(boot.supported_fields || []);
  let authored = structuredClone(boot.document || {});
  let dirty = false;

  function button(label, action, index) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "btn btn-sm btn-ghost";
    node.dataset.rowAction = action;
    node.dataset.rowIndex = String(index);
    node.textContent = label;
    node.setAttribute("aria-label", `${label} item ${index + 1}`);
    return node;
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
      row.append(input, button("Move up", "up", index), button("Move down", "down", index), button("Remove", "remove", index));
      target.appendChild(row);
    });
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
      const valuesInput = document.createElement("input");
      valuesInput.value = Array.isArray(values) ? values.join(", ") : "";
      valuesInput.placeholder = "Ordered values, comma separated";
      valuesInput.setAttribute("aria-label", `Attribute ${index + 1} ordered values`);
      row.append(nameInput, valuesInput, button("Move up", "up", index), button("Move down", "down", index), button("Remove", "remove", index));
      target.appendChild(row);
    });
  }

  function attributesValue() {
    const result = {};
    guided.querySelectorAll("[data-attribute-row]").forEach((row) => {
      const inputs = row.querySelectorAll("input");
      const name = inputs[0].value.trim();
      const values = inputs[1].value.split(",").map((item) => item.trim()).filter(Boolean);
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
      [["expression", expression, "Attribute=Value expression"], ["price", modifier.price, "Price"], ["sale_price", modifier.sale_price, "Sale price"], ["weight", modifier.weight, "Weight"], ["length", modifier.dimensions && modifier.dimensions.length, "Length"], ["width", modifier.dimensions && modifier.dimensions.width, "Width"], ["height", modifier.dimensions && modifier.dimensions.height, "Height"]].forEach(([name, value, label]) => {
        const input = document.createElement("input");
        input.dataset.modifierField = name;
        input.value = value == null ? "" : String(value);
        input.placeholder = label;
        input.setAttribute("aria-label", `${label} for modifier ${index + 1}`);
        row.appendChild(input);
      });
      row.append(button("Move up", "up", index), button("Move down", "down", index), button("Remove", "remove", index));
      target.appendChild(row);
    });
  }

  function modifiersValue() {
    const result = {};
    guided.querySelectorAll("[data-modifier-row]").forEach((row) => {
      const get = (name) => row.querySelector(`[data-modifier-field="${name}"]`).value.trim();
      const expression = get("expression");
      if (!expression) return;
      const modifier = {};
      ["price", "sale_price", "weight"].forEach((name) => { if (get(name)) modifier[name] = get(name); });
      const dimensions = {};
      ["length", "width", "height"].forEach((name) => { if (get(name)) dimensions[name] = get(name); });
      if (Object.keys(dimensions).length) modifier.dimensions = dimensions;
      result[expression] = modifier;
    });
    return result;
  }

  function populate(documentValue) {
    authored = structuredClone(documentValue || {});
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      let value = authored[field];
      if (field === "live") value = value === true ? "true" : value === false ? "false" : "";
      if (control.dataset.arrayField === "true") value = Array.isArray(value) ? value.join(", ") : "";
      control.value = value == null ? "" : String(value);
    });
    const dimensions = authored.dimensions || {};
    guided.querySelectorAll("[data-dimension]").forEach((control) => { control.value = dimensions[control.dataset.dimension] == null ? "" : dimensions[control.dataset.dimension]; });
    ["categories", "tags", "image_attributes"].forEach((field) => renderList(field, authored[field]));
    renderAttributes(authored.attributes);
    renderModifiers(authored.variation_modifiers);
  }

  function prune(value) {
    if (Array.isArray(value)) return value.map(prune).filter((item) => item !== "" && item != null);
    if (value && typeof value === "object") {
      const result = {};
      Object.entries(value).forEach(([key, item]) => {
        const clean = prune(item);
        if (clean !== "" && clean != null && !(typeof clean === "object" && !Array.isArray(clean) && !Object.keys(clean).length)) result[key] = clean;
      });
      return result;
    }
    return value;
  }

  function guidedDocument() {
    const result = {};
    Object.entries(authored).forEach(([key, value]) => { if (!known.has(key)) result[key] = value; });
    guided.querySelectorAll("[data-field]").forEach((control) => {
      let value = control.value.trim();
      if (control.dataset.field === "live") value = value === "true" ? true : value === "false" ? false : "";
      if (control.dataset.arrayField === "true") value = value.split(",").map((item) => item.trim()).filter(Boolean);
      result[control.dataset.field] = value;
    });
    result.categories = listValues("categories");
    result.tags = listValues("tags");
    result.image_attributes = listValues("image_attributes");
    result.attributes = attributesValue();
    result.variation_modifiers = modifiersValue();
    const dimensions = {};
    guided.querySelectorAll("[data-dimension]").forEach((control) => { if (control.value.trim()) dimensions[control.dataset.dimension] = control.value.trim(); });
    result.dimensions = dimensions;
    return prune(result);
  }

  function updateLines() {
    const count = (editor.value.match(/\n/g) || []).length + 1;
    lines.textContent = Array.from({ length: count }, (_, index) => index + 1).join("\n");
  }

  function switchMode(mode) {
    const error = root.querySelector("[data-json-error]");
    if (mode === "advanced") {
      authored = guidedDocument();
      editor.value = JSON.stringify(authored, null, 2);
      updateLines();
    } else {
      try {
        const parsed = JSON.parse(editor.value);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Root must be an object.");
        populate(parsed);
        error.hidden = true;
      } catch (parseError) {
        error.textContent = `Advanced JSON remains authoritative: ${parseError.message}`;
        error.hidden = false;
        editor.focus();
        return;
      }
    }
    guided.hidden = mode !== "guided";
    advanced.hidden = mode !== "advanced";
    root.querySelectorAll("[data-prepared-mode]").forEach((node) => node.classList.toggle("is-active", node.dataset.preparedMode === mode));
  }

  function moveRow(row, direction) {
    const sibling = direction === "up" ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    if (direction === "up") row.parentNode.insertBefore(row, sibling);
    else row.parentNode.insertBefore(sibling, row);
    dirty = true;
  }

  root.addEventListener("click", (event) => {
    const mode = event.target.closest("[data-prepared-mode]");
    if (mode) switchMode(mode.dataset.preparedMode);
    if (event.target.closest("[data-return-guided]")) switchMode("guided");
    if (event.target.closest("[data-format-json]")) {
      try { editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); updateLines(); }
      catch (error) { const node = root.querySelector("[data-json-error]"); node.textContent = error.message; node.hidden = false; }
    }
    const addList = event.target.closest("[data-add-list]");
    if (addList) { const values = listValues(addList.dataset.addList); values.push(""); renderList(addList.dataset.addList, values); }
    if (event.target.closest("[data-add-attribute]")) { const values = attributesValue(); values[""] = [""]; renderAttributes(values); }
    if (event.target.closest("[data-add-modifier]")) { const values = modifiersValue(); values[""] = {}; renderModifiers(values); }
    const action = event.target.closest("[data-row-action]");
    if (action) {
      const row = action.closest("[data-list-row], [data-attribute-row], [data-modifier-row]");
      if (action.dataset.rowAction === "remove") row.remove(); else moveRow(row, action.dataset.rowAction);
    }
    if (event.target.closest("[data-preview-metadata]")) {
      const hidden = guided.querySelector("[data-prepared-document]");
      hidden.value = JSON.stringify(guidedDocument());
      guided.requestSubmit();
    }
  });

  guided.addEventListener("submit", () => { dirty = false; guided.querySelector("[data-prepared-document]").value = JSON.stringify(guidedDocument()); });
  advanced.querySelector("form").addEventListener("submit", (event) => {
    try {
      const parsed = JSON.parse(editor.value);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("product_info.json must contain an object.");
      advanced.querySelector("[data-advanced-document]").value = editor.value;
      dirty = false;
    } catch (error) {
      event.preventDefault();
      const node = root.querySelector("[data-json-error]");
      node.textContent = error.message;
      node.hidden = false;
      editor.focus();
    }
  });
  root.addEventListener("input", () => { dirty = true; });
  editor.addEventListener("input", updateLines);
  root.querySelector("[data-json-search]").addEventListener("input", (event) => {
    const query = event.target.value;
    if (!query) return;
    const index = editor.value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
    if (index >= 0) {
      editor.focus();
      editor.setSelectionRange(index, index + query.length);
    }
  });
  document.querySelectorAll("[data-preview-metadata]").forEach((node) => {
    if (root.contains(node)) return;
    node.addEventListener("click", () => {
      guided.querySelector("[data-prepared-document]").value = JSON.stringify(guidedDocument());
      guided.requestSubmit();
    });
  });
  window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  populate(authored);
})();
