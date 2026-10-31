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
  let packagingDesigner = null;
  let finishedDesigner = null;

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
    const shippingGroups=guided.querySelector('[data-shipping-configurations]');shippingGroups?.replaceChildren();
    Object.entries(value && typeof value === "object" && !Array.isArray(value) ? value : {}).forEach(([expression, modifier], index) => {
      const row = document.createElement("div");
      row.className = "structured-editor-row modifier-editor-row";
      row.dataset.modifierRow = "";
      row._originalModifier=structuredClone(modifier);
      const addShippingEditor=()=>{const finished=document.createElement('div');row.appendChild(finished);
        row._finishedEditor=new window.TLCPackaging.FinishedEditor(finished,{value:modifier.finished_dimensions,shared:authored.finished_dimensions,onChange:()=>{dirty=true;}});
        const group=document.createElement('div'),title=document.createElement('h3');title.textContent=expression;
        group.className='shipping-size-group';group.append(title);(shippingGroups || row).append(group);
        const target=document.createElement('div');group.appendChild(target);
        row._packagingEditor=new window.TLCPackaging.Editor(target,{value:modifier.packaging,shared:authored.packaging,
          getShared:()=>packagingDesigner?.get(),
          getAttributes:()=>Object.fromEntries(row.querySelector('[data-modifier-field="expression"]').value.split('|').map(v=>v.split('='))),
          onChange:()=>{dirty=true;},getDocument:()=>guidedDocument()});};
      if(window.TLCPackaging && (modifier.dimensions || modifier.finished_dimensions || modifier.packaging))addShippingEditor();
      else if(window.TLCPackaging){const control=document.createElement('button');control.type='button';control.className='btn btn-sm btn-outline-primary';control.textContent='Add packed shipping exception';control.addEventListener('click',()=>{addShippingEditor();control.remove();});row.append(control);}
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
      const modifier = structuredClone(row._originalModifier || {});
      ['price','sale_price','weight','dimensions'].forEach(k=>delete modifier[k]);
      if(row._packagingEditor){const value=row._packagingEditor.get();if(value)modifier.packaging=value;else delete modifier.packaging;}
      if(row._finishedEditor){const value=row._finishedEditor.get();if(value)modifier.finished_dimensions=value;else delete modifier.finished_dimensions;}
      const retained=(text,original)=>original!=null && String(original)===text?original:text;
      ["price", "sale_price", "weight"].forEach((name) => { if (get(name)) modifier[name] = retained(get(name),row._originalModifier?.[name]); });
      const dimensions = {};
      ["length", "width", "height"].forEach((name) => { if (get(name)) dimensions[name] = retained(get(name),row._originalModifier?.dimensions?.[name]); });
      if (Object.keys(dimensions).length) modifier.dimensions = dimensions;
      result[expression] = modifier;
    });
    return result;
  }

  function populate(documentValue) {
    authored = structuredClone(documentValue || {});
    const finishedRoot=guided.querySelector('[data-finished-controls]');
    if(finishedRoot && window.TLCPackaging)finishedDesigner=new window.TLCPackaging.FinishedEditor(finishedRoot,{value:authored.finished_dimensions,onChange:()=>{dirty=true;}});
    const target=guided.querySelector('[data-packaging-controls]');
    if(target && window.TLCPackaging)packagingDesigner=new window.TLCPackaging.Editor(target,{value:authored.packaging,
      onChange:()=>{dirty=true;},getDocument:()=>guidedDocument()});
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      let value = authored[field];
      if (["live","requires_shipping"].includes(field)) value = value === true ? "true" : value === false ? "false" : "";
      if (control.dataset.arrayField === "true") value = Array.isArray(value) ? value.join(", ") : "";
      control.value = value == null ? "" : String(value);
    });
    const dimensions = authored.dimensions || {};
    guided.querySelectorAll("[data-dimension]").forEach((control) => { control.value = dimensions[control.dataset.dimension] == null ? "" : dimensions[control.dataset.dimension]; });
    ["categories", "tags", "image_attributes"].forEach((field) => renderList(field, authored[field]));
    renderAttributes(authored.attributes);
    renderModifiers(authored.variation_modifiers);
    packagingDesigner?.refreshFallback();
    guided.querySelectorAll('[data-modifier-row]').forEach(row=>row._packagingEditor?.refreshFallback());
  }

  function prune(value) {
    if (Array.isArray(value)) return value.map(prune).filter((item) => item !== "" && item != null);
    if (value && typeof value === "object") {
      const result = {};
      Object.entries(value).forEach(([key, item]) => {
        const clean = prune(item);
        if ((key==='selected_package_id' && clean===null) || (clean !== "" && clean != null && !(typeof clean === "object" && !Array.isArray(clean) && !Object.keys(clean).length))) result[key] = clean;
      });
      return result;
    }
    return value;
  }

  function guidedDocument() {
    const result = root.hasAttribute('data-intake-direct-editor') ? structuredClone(authored) : {};
    Object.entries(authored).forEach(([key, value]) => { if (!known.has(key)) result[key] = value; });
    if(packagingDesigner){const value=packagingDesigner.get();if(value)result.packaging=value;else delete result.packaging;}
    if(finishedDesigner){const value=finishedDesigner.get();if(value)result.finished_dimensions=value;else delete result.finished_dimensions;}
    guided.querySelectorAll("[data-field]").forEach((control) => {
      let value = control.value.trim();
      if (["live","requires_shipping"].includes(control.dataset.field)) value = value === "true" ? true : value === "false" ? false : "";
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
    const clean = prune(result);
    if (root.hasAttribute('data-intake-direct-editor')) {
      const represented = new Set([...guided.querySelectorAll('[data-field]')].map(control => control.dataset.field));
      ['dimensions','categories','tags','image_attributes','attributes','variation_modifiers'].forEach(key => represented.add(key));
      if (packagingDesigner) represented.add('packaging');
      if (finishedDesigner) represented.add('finished_dimensions');
      Object.entries(authored).forEach(([key,value]) => { if (!represented.has(key)) clean[key] = structuredClone(value); });
      Object.entries(clean).forEach(([key,value]) => { if (!(key in authored) && Array.isArray(value) && !value.length) delete clean[key]; });
    }
    return clean;
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
    if(event.target.closest('[data-copy-product-dimensions]')){
      const control=event.target.closest('[data-copy-product-dimensions]');control.disabled=true;
      window.TLCPackaging.copyDimensions(guidedDocument,value=>{populate(value);dirty=true;})
        .catch(error=>{const node=root.querySelector('[data-json-error]');node.textContent=error.message;node.hidden=false;}).finally(()=>{control.disabled=false;});return;
    }
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
  const refreshShipping=()=>{packagingDesigner?.refreshFallback();guided.querySelectorAll('[data-modifier-row]').forEach(row=>row._packagingEditor?.refreshFallback());};
  root.addEventListener("input", () => { dirty = true;refreshShipping(); });
  root.addEventListener("change",refreshShipping);
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
  // Intake live preview reads the existing editor, not a second JSON model.
  root.intakeDocument = () => advanced.hidden ? guidedDocument() : JSON.parse(editor.value);
  root.intakeAccepted = () => { dirty = false; };
})();
