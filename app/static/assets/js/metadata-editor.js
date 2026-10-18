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
    "tags", "storefront_collections", "live", "short_description", "description", "attributes",
    "image_attributes", "variation_attributes", "variation_modifiers", "shipping_class", "grouped_ids",
    "grouped_products", "upsell_ids", "cross_sell_ids", "upsells", "crosssells",
    "meta_title", "meta_description", "vendor", "handle", "shopify_product_type",
    "country_of_origin", "requires_shipping", "manage_stock", "backorders", "primary_category", "shopify_status", "shopify_channels", "packaging", "finished_dimensions"
  ]);
  let authored = structuredClone(boot.authored || {});
  let originalDocument = structuredClone(boot.authored || {});
  let initialValues = {};
  let dirty = false;
  let busy = false;
  let activeMode = "guided";
  let registry = boot.taxonomy_options || { categories: [], attributes: [] };
  let explicitVariation = false;
  let resolvedVariation = false;
  let driverNames = [];
  let channels = boot.channels || [];
  let channelDraft = {};
  let customisationDesigner = null;
  let packagingDesigner = null;
  let finishedDesigner = null;

  function channelValues() {
    const result = structuredClone(channelDraft);
    guided.querySelectorAll("[data-channel-intent]").forEach(control => {
      if (control.value === "") delete result[control.dataset.channelIntent];
      else result[control.dataset.channelIntent] = control.value === "true";
    });
    return result;
  }

  function renderChannels(values) {
    const target = guided.querySelector("[data-channel-choices]");
    if (!target) return;
    channelDraft = structuredClone(values || {});
    target.replaceChildren();
    channels.forEach(channel => {
      const label = document.createElement("label"); label.textContent = channel.name;
      const select = document.createElement("select"); select.className = "form-select";
      select.dataset.channelIntent = channel.key;
      select.add(new Option("Unmanaged — preserve remote membership", ""));
      select.add(new Option("Publish through reviewed confirmation", "true"));
      select.add(new Option("Unpublish through reviewed confirmation", "false"));
      select.value = Object.hasOwn(channelDraft,channel.key) ? String(channelDraft[channel.key]) : "";
      label.appendChild(select); target.appendChild(label);
    });
    if (!channels.length) target.textContent = "No discovered channel choices. Discover explicitly; no Shopify request was made to render this page.";
    setControlState("shopify_channels");
  }

  function registryChoices() {
    const category = guided.querySelector("[data-registry-category]");
    const attribute = guided.querySelector("[data-registry-attribute]");
    const term = guided.querySelector("[data-registry-term]");
    if (!category || !attribute || !term) return;
    const fill = (select, rows, label) => {
      const prior = select.value;
      select.replaceChildren(new Option(label, ""));
      rows.filter((row) => row.state !== "deprecated").forEach((row) => select.add(new Option(row.value, row.key)));
      if (Array.from(select.options).some((row) => row.value === prior)) select.value = prior;
    };
    fill(category, registry.categories, "Choose a registered category");
    fill(guided.querySelector("[data-registry-range]"), registry.storefront_collections || [], "Choose a Storefront Collection");
    fill(attribute, registry.attributes, "Choose a registered attribute");
    const selected = registry.attributes.find((row) => row.key === attribute.value);
    fill(term, selected ? selected.terms : [], "Choose a registered term");
    const link = guided.querySelector("[data-new-registry-term]");
    link.hidden = !selected;
    if (selected) link.href = `${boot.taxonomy_term_url}?attribute=${encodeURIComponent(selected.key)}`;
    guided.querySelectorAll("[data-registry-status]").forEach((node) => { node.textContent = `Registry: ${registry.status || "unavailable"}`; });
  }

  function setDirty(value) {
    if (value && activeMode === 'guided') value = JSON.stringify(guidedDocument()) !== JSON.stringify(originalDocument);
    dirty = value;
    saveStates.forEach((node) => { node.textContent = value ? "Unsaved changes" : "Saved"; });
    window.TLCSaleEditor?.refresh();
  }

  function setBusy(value) {
    busy = value;
    saveButtons.forEach((button) => {
      button.disabled = value;
      button.setAttribute("aria-busy", String(value));
      button.textContent = value ? "Saving…" : "Save Metadata";
    });
    window.TLCSaleEditor?.refresh();
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
    if (field === "shopify_status") {
      if (Object.hasOwn(authored,field)) return authored[field];
      if (typeof authored.live === "boolean") return authored.live ? "ACTIVE" : "DRAFT";
      if (!isShared) return boot.collection?.shopify_status || (typeof boot.collection?.live === "boolean" ? boot.collection.live ? "ACTIVE" : "DRAFT" : "");
      return "";
    }
    if (Object.prototype.hasOwnProperty.call(authored, field)) return authored[field];
    if (field === 'title') return ''; // shared title is a separate component, never a child override default
    if (!isShared && Object.prototype.hasOwnProperty.call(boot.collection || {}, field)) return boot.collection[field];
    return "";
  }

  function overrideEnabled(field) {
    if (isShared) return true;
    const toggle = guided.querySelector(`[data-override-toggle="${CSS.escape(field)}"]`);
    return !!(toggle && toggle.checked);
  }

  function setControlState(field) {
    if (field === "variation_attributes") return; // presence is controlled explicitly, not inherited-copy toggle
    if (isShared) return;
    const enabled = overrideEnabled(field);
    const wrapper = guided.querySelector(`[data-field-wrapper="${CSS.escape(field)}"]`);
    if (wrapper) {
      wrapper.classList.toggle("is-inherited", !enabled);
      wrapper.querySelectorAll("input:not([data-override-toggle]), textarea, select, button").forEach((control) => {
        if (control.matches("[data-discover-channels]")) return;
        control.disabled = !enabled;
      });
    }
    if (field === "attributes") {
      guided.querySelectorAll("[data-attributes-editor] input:not([data-use-variation]), [data-attributes-editor] button, [data-add-attribute]").forEach((control) => { control.disabled = !enabled; });
    }
    if (field === "variation_modifiers") {
      guided.querySelectorAll("[data-modifiers-editor] input, [data-modifiers-editor] button, [data-add-modifier]").forEach((control) => { if(!control.matches('[data-add-shipping-exception]'))control.disabled = !enabled; });
      // Shipping exceptions have independent sparse ownership, not this legacy
      // whole-table price/weight override gate.
      guided.querySelectorAll('[data-modifier-row]').forEach(row=>row._packagingEditor?.setEnabled(true));
      guided.querySelectorAll('[data-modifier-row]').forEach(row=>row._finishedEditor?.setEnabled(true));
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
      if (field === "categories" || field === "storefront_collections") {
        const status = document.createElement("div"); status.className = "taxonomy-row-status";
        const update = () => { status.replaceChildren(); recognition(status, input.value, registryMatch(input.value, registry[field] || []), field === "categories" ? "category" : "range"); };
        update(); input.addEventListener("change", update); row.append(status);
      }
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
      nameInput.dataset.attributeName = name;
      nameInput.placeholder = "Attribute name";
      nameInput.setAttribute("aria-label", `Attribute ${index + 1} name`);
      const valueInput = document.createElement("input");
      valueInput.value = Array.isArray(values) ? values.join(", ") : "";
      // Preserve exact authored terms (including commas) until deliberately edited.
      valueInput.dataset.terms = JSON.stringify(Array.isArray(values) ? values : []);
      valueInput.dataset.originalText = valueInput.value;
      valueInput.placeholder = "Ordered values, comma separated";
      valueInput.setAttribute("aria-label", `Attribute ${index + 1} ordered values`);
      const nameLabel = document.createElement("label"); nameLabel.textContent = "Attribute"; nameLabel.append(nameInput);
      const termLabel = document.createElement("label"); termLabel.textContent = "Terms"; termLabel.append(valueInput);
      const status = document.createElement("div"); status.className = "taxonomy-row-status";
      const updateStatus = () => {
        status.replaceChildren();
        const definition = registryMatch(nameInput.value, registry.attributes);
        recognition(status, nameInput.value, definition, "attribute");
        const terms = valueInput.value === valueInput.dataset.originalText ? JSON.parse(valueInput.dataset.terms) : valueInput.value.split(",").map((v) => v.trim()).filter(Boolean);
        terms.forEach((term) => { if (!definition || !registryMatch(term, definition.terms)) recognition(status, term, null, "term", definition); });
      };
      updateStatus();
      nameInput.addEventListener("change", () => {
        if (explicitVariation) driverNames = driverNames.map((driver) => driver === nameInput.dataset.attributeName ? nameInput.value.trim() : driver);
        nameInput.dataset.attributeName = nameInput.value.trim(); updateStatus(); renderVariationState();
      });
      valueInput.addEventListener("change", updateStatus);
      const actions = document.createElement("div"); actions.className = "taxonomy-row-actions";
      const use = document.createElement("label"); use.className = "variation-use-control";
      const check = document.createElement("input"); check.type = "checkbox"; check.dataset.useVariation = "";
      const caption = document.createElement("span"); caption.textContent = "Use for variations";
      const badge = document.createElement("span"); badge.className = "catalogue-pill is-neutral"; badge.dataset.variationLabel = "";
      check.addEventListener("change", () => { changeVariation(nameInput.value.trim(), check.checked); renderVariationState(); setDirty(true); });
      use.append(check, caption); actions.append(use, badge, makeButton("Move up", "up", index), makeButton("Move down", "down", index), makeButton("Remove", "remove", index));
      row.append(nameLabel, termLabel, status, actions);
      target.appendChild(row);
    });
    setControlState("attributes");
    renderVariationState();
  }

  function registryMatch(value, rows) {
    const normalize = (text) => String(text).normalize("NFC").trim().replace(/\s+/g, " ").toLocaleLowerCase();
    const found = rows.filter((row) => [row.value, row.name || row.value, ...(row.aliases || [])].some((name) => normalize(name) === normalize(value)));
    return found.length === 1 ? found[0] : null;
  }

  function recognition(target, value, definition, kind, attribute) {
    const badge = document.createElement("span"); badge.className = `catalogue-pill is-${definition ? "active" : "missing"}`;
    badge.textContent = definition ? "Registered" : kind === "term" ? `Legacy term: ${value}` : "Legacy";
    badge.title = definition ? "In taxonomy registry" : "Not in taxonomy registry";
    badge.setAttribute("aria-label", `${badge.textContent} · ${badge.title}`);
    target.append(badge);
    if (!definition && value && (kind !== "term" || attribute)) {
      const link = document.createElement("a"); link.target = "_blank"; link.rel = "noopener";
      const base = kind === "range" ? boot.taxonomy_range_url : kind === "category" ? boot.taxonomy_category_url : kind === "attribute" ? boot.taxonomy_attribute_url : boot.taxonomy_term_url;
      link.href = `${base}?name=${encodeURIComponent(value)}${attribute ? `&attribute=${encodeURIComponent(attribute.key)}` : ""}`;
      link.textContent = kind === "term" ? `Add ${value} to registry` : "Add to taxonomy registry";
      target.append(link);
    }
  }

  function simpleProduct() {
    const field = guided.querySelector('[data-field="collection_type"]');
    return (field ? field.value : boot.resolved.collection_type) === "Simple";
  }

  function changeVariation(name, checked) {
    if (!resolvedVariation) driverNames = simpleProduct() ? [] : Object.keys(attributeValues());
    explicitVariation = true; resolvedVariation = true;
    driverNames = checked ? Array.from(new Set([...driverNames, name])) : driverNames.filter((driver) => driver !== name);
  }

  function renderVariationState() {
    guided.querySelectorAll("[data-attribute-row]").forEach((row) => {
      const name = row.querySelector("[data-attribute-name]").value.trim();
      const check = row.querySelector("[data-use-variation]");
      check.checked = resolvedVariation ? driverNames.includes(name) : !simpleProduct();
      check.disabled = simpleProduct() || !name;
      row.querySelector("[data-variation-label]").textContent = check.checked ? "Variation-driving" : "Informational";
    });
    const unknown = driverNames.filter((name) => !Object.hasOwn(attributeValues(), name));
    guided.querySelector("[data-variation-contract-status]").textContent = (resolvedVariation ? `${explicitVariation ? "Explicit" : "Inherited explicit"} contract · Woo publishing requires verified controlled taxonomy identities.` : "Legacy contract · unchanged until you use a variation control.") + (unknown.length && resolvedVariation ? ` Unassigned drivers: ${unknown.join(", ")}. Correct these in Advanced JSON or restore the attribute.` : "");
    guided.querySelector("[data-adopt-variation]").hidden = explicitVariation;
  }

  function attributeValues() {
    const result = {};
    guided.querySelectorAll("[data-attribute-row]").forEach((row) => {
      const inputs = row.querySelectorAll("input");
      const name = inputs[0].value.trim();
      const values = inputs[1].value === inputs[1].dataset.originalText
        ? JSON.parse(inputs[1].dataset.terms)
        : inputs[1].value.split(",").map((value) => value.trim()).filter(Boolean);
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
      row._originalModifier = structuredClone(modifier);
      const addShippingEditor=()=>{
        const own=isShared?modifier:authored.variation_modifiers?.[expression] || {};
        const parent=isShared?{}:boot.collection?.variation_modifiers?.[expression] || {};
        const group=document.createElement('div');group.className='shipping-size-group';
        const title=document.createElement('h3');title.textContent=expression;group.append(title);(shippingGroups || row).append(group);
        const finished = document.createElement('div'); row.appendChild(finished);
        row._finishedEditor = new window.TLCPackaging.FinishedEditor(finished,{value:own.finished_dimensions,
          shared:parent.finished_dimensions || authored.finished_dimensions || boot.collection?.finished_dimensions,onChange:()=>setDirty(true)});
        const packaging = document.createElement('div'); group.appendChild(packaging);
        row._packagingEditor = new window.TLCPackaging.Editor(packaging, {value:own.packaging,inherited:!isShared,
          shared:window.TLCPackaging.merge(window.TLCPackaging.merge(boot.collection?.packaging,authored.packaging),parent.packaging),onChange:()=>setDirty(true),
          getShared:()=>window.TLCPackaging.merge(window.TLCPackaging.merge(isShared?{}:boot.collection?.packaging,packagingDesigner?.get()),parent.packaging),
          getAttributes:()=>Object.fromEntries(row.querySelector('[data-modifier-field="expression"]').value.split('|').map(v=>v.split('='))),
          getDocument:()=>window.TLCPackaging.sourceDocument(isShared?{}:boot.collection,guidedDocument())});
      };
      if (window.TLCPackaging && (modifier.dimensions || modifier.finished_dimensions || modifier.packaging)) addShippingEditor();
      else if(window.TLCPackaging){const shippingButton=document.createElement('button');shippingButton.type='button';shippingButton.dataset.addShippingException='';shippingButton.className='btn btn-sm btn-outline-primary';shippingButton.textContent='Add packed shipping exception';shippingButton.addEventListener('click',()=>{addShippingEditor();shippingButton.remove();setControlState('variation_modifiers');});row.append(shippingButton);}
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

  function modifierValues(shippingOnly = false) {
    if(shippingOnly && overrideEnabled('variation_modifiers') && Array.from(guided.querySelectorAll('[data-modifier-row]')).some(row=>
      ['price','sale_price','weight','length','width','height'].some(name=>{
        const original=['length','width','height'].includes(name)?row._originalModifier?.dimensions?.[name]:row._originalModifier?.[name];
        return row.querySelector(`[data-modifier-field="${name}"]`).value.trim()!==String(original ?? '');
      })))return modifierValues(false);
    const result = {};
    guided.querySelectorAll("[data-modifier-row]").forEach((row) => {
      const value = (name) => row.querySelector(`[data-modifier-field="${name}"]`).value.trim();
      const expression = value("expression");
      if (!expression) return;
      const modifier = structuredClone(shippingOnly ? authored.variation_modifiers?.[expression] || {} : row._originalModifier || {});
      ['price','sale_price','weight','dimensions'].forEach(k=>delete modifier[k]);
      if (row._packagingEditor) { const packing=row._packagingEditor.get();if(packing)modifier.packaging=packing;else delete modifier.packaging; }
      if(row._finishedEditor){const value=row._finishedEditor.get();if(value)modifier.finished_dimensions=value;else delete modifier.finished_dimensions;}
      if(shippingOnly){if(Object.keys(modifier).length)result[expression]=modifier;return;}
      const retained=(text,original)=>original!=null && String(original)===text?original:text;
      ["price", "sale_price", "weight"].forEach((name) => { if (value(name)) modifier[name] = retained(value(name),row._originalModifier?.[name]); });
      const dimensions = {};
      ["length", "width", "height"].forEach((name) => { if (value(name)) dimensions[name] = retained(value(name),row._originalModifier?.dimensions?.[name]); });
      if (Object.keys(dimensions).length) modifier.dimensions = dimensions;
      // A projected inherited shipping-only row is not an authored empty
      // commercial rule when all of its shipping controls still inherit.
      if(!isShared && !Object.keys(modifier).length && Object.keys(row._originalModifier || {}).length &&
          Object.keys(row._originalModifier).every(k=>['packaging','finished_dimensions'].includes(k)) && !Object.hasOwn(authored.variation_modifiers || {},expression))return;
      result[expression] = modifier;
    });
    return result;
  }

  function populate(documentValue) {
    authored = structuredClone(documentValue || {});
    const finishedRoot=guided.querySelector('[data-finished-controls]');
    if(finishedRoot && window.TLCPackaging)finishedDesigner=new window.TLCPackaging.FinishedEditor(finishedRoot,{
      value:authored.finished_dimensions,shared:isShared?{}:boot.collection?.finished_dimensions,onChange:()=>setDirty(true)});
    const packingRoot=guided.querySelector('[data-packaging-controls]');
    if(packingRoot && window.TLCPackaging) packagingDesigner=new window.TLCPackaging.Editor(packingRoot,{
      value:authored.packaging,shared:isShared?{}:boot.collection?.packaging,inherited:!isShared,onChange:()=>setDirty(true),
      getDocument:()=>window.TLCPackaging.sourceDocument(isShared?{}:boot.collection,guidedDocument())});
    if (!customisationDesigner && window.TLCCustomisations && guided.querySelector('[data-customisation-designer]')) {
      customisationDesigner = new window.TLCCustomisations.Designer(guided.querySelector('[data-customisation-designer]'), {
        value: authored.customisation, shared: isShared ? {} : boot.collection?.customisation || {},
        base: boot.customisation_template || {},
        reference: Object.hasOwn(authored.customisation || {}, 'template') ? authored.customisation.template : boot.collection?.customisation?.template,
        templatesURL: boot.customisation_templates_url, onChange: () => setDirty(true)
      });
    } else if (customisationDesigner) {
      customisationDesigner.value = structuredClone(authored.customisation);
      customisationDesigner.restoreBase(); customisationDesigner.render();
    }
    guided.querySelectorAll("[data-override-toggle]").forEach((toggle) => {
      toggle.checked = Object.prototype.hasOwnProperty.call(authored, toggle.dataset.overrideToggle) || toggle.dataset.overrideToggle === "shopify_status" && Object.hasOwn(authored,"live");
    });
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      let value = valueFor(field);
      if (["live", "requires_shipping", "manage_stock"].includes(field)) value = value === true ? "true" : value === false ? "false" : "";
      if (control.dataset.arrayField === "true") value = Array.isArray(value) ? value.join(", ") : "";
      control.value = value == null ? "" : String(value);
    });
    const dimensions = valueFor("dimensions");
    renderChannels(valueFor("shopify_channels"));
    guided.querySelectorAll("[data-dimension]").forEach((control) => { control.value = dimensions && dimensions[control.dataset.dimension] != null ? dimensions[control.dataset.dimension] : ""; });
    ["categories", "tags", "storefront_collections", "image_attributes"].forEach((field) => renderList(field, valueFor(field)));
    explicitVariation = Object.prototype.hasOwnProperty.call(authored, "variation_attributes");
    resolvedVariation = Array.isArray(valueFor("variation_attributes"));
    driverNames = resolvedVariation ? [...valueFor("variation_attributes")] : [];
    renderAttributes(valueFor("attributes"));
    renderModifiers(isShared?valueFor('variation_modifiers'):window.TLCPackaging.sourceDocument(boot.collection,authored).variation_modifiers);
    guided.querySelectorAll("[data-override-toggle]").forEach((toggle) => setControlState(toggle.dataset.overrideToggle));
    guided.querySelectorAll("[data-inherited-value]").forEach((node) => {
      const key = node.dataset.inheritedValue;
      const value = boot.collection?.[key];
      node.textContent = isShared ? "" : "Inherited: " + (value == null ? "Not set" : typeof value === "object" ? JSON.stringify(value) : String(value));
    });
    updateCharacterCounts();
    window.TLCSaleDates?.refresh(guided);
    initialValues = structuredClone(guidedValues());
    updateFinishedPreview();
  }

  function updateFinishedPreview(){
    const target=guided.querySelector('[data-product-finished-preview]');if(!target)return;
    const current=window.TLCPackaging.sourceDocument(isShared?{}:boot.collection,guidedDocument());
    const value=current.finished_dimensions?.unit?current.finished_dimensions:{...current.dimensions,unit:'mm'};
    target.textContent=value.length && value.width?`Finished size: ${['length','width','height'].map(k=>value[k] || 'Not set').join(' × ')} ${value.unit}. Packed size is independent.`:'Finished size incomplete; enter the actual product measurements.';
    packagingDesigner?.refreshFallback();
    guided.querySelectorAll('[data-modifier-row]').forEach(row=>row._packagingEditor?.refreshFallback());
  }

  function prune(value) {
    if (Array.isArray(value)) return value.map(prune).filter((item) => item !== "" && item != null && !(typeof item === "object" && !Object.keys(item).length));
    if (value && typeof value === "object") {
      const result = {};
      Object.entries(value).forEach(([key, item]) => {
        const clean = prune(item);
        if ((["variation_attributes", "storefront_collections"].includes(key) && Array.isArray(clean)) ||
            (key === "attributes" && Object.prototype.hasOwnProperty.call(value, "variation_attributes") && clean && typeof clean === "object") ||
            (clean !== "" && clean != null && !(typeof clean === "object" && !Object.keys(clean).length))) result[key] = clean;
      });
      return result;
    }
    return value;
  }

  function guidedValues() {
    window.TLCSaleDates?.sync(guided);
    const result = {};
    Object.entries(authored).forEach(([key, value]) => { if (!knownFields.has(key)) result[key] = value; });
    if (customisationDesigner) {
      const definition = customisationDesigner.get();
      if (definition === undefined) delete result.customisation; else result.customisation = definition;
    }
    if(packagingDesigner){const value=packagingDesigner.get();if(value)result.packaging=value;}
    if(finishedDesigner){const value=finishedDesigner.get();if(value)result.finished_dimensions=value;}
    guided.querySelectorAll("[data-field]").forEach((control) => {
      const field = control.dataset.field;
      if (!overrideEnabled(field)) return;
      let value = control.value.trim();
      if (["live", "requires_shipping", "manage_stock"].includes(field)) value = value === "true" ? true : value === "false" ? false : "";
      if (control.dataset.arrayField === "true") value = value.split(",").map((item) => item.trim()).filter(Boolean);
      result[field] = value;
    });
    ["categories", "tags", "storefront_collections", "image_attributes"].forEach((field) => {
      if (!overrideEnabled(field)) return;
      const values = listValues(field);
      if (field === "storefront_collections" && isShared && !Object.hasOwn(authored, field) && !values.length) return;
      result[field] = values;
    });
    if (overrideEnabled("attributes")) result.attributes = attributeValues();
    if (explicitVariation) result.variation_attributes = [...driverNames];
    if (overrideEnabled("variation_modifiers")) {
      const onlyShipping=Object.keys(authored.variation_modifiers || {}).length && Object.values(authored.variation_modifiers).every(v=>Object.keys(v).every(k=>['packaging','finished_dimensions'].includes(k)));
      result.variation_modifiers = modifierValues(!isShared && !!onlyShipping);
    } else if(!isShared) result.variation_modifiers = modifierValues(true);
    if (overrideEnabled("dimensions")) {
      result.dimensions = {};
      guided.querySelectorAll("[data-dimension]").forEach((control) => { result.dimensions[control.dataset.dimension] = control.value.trim(); });
    }
    if (!isShared) {
      delete result.collection_type;
      delete result.sku_prefix;
    }
    if (overrideEnabled("shopify_channels") && (Object.keys(channelValues()).length || Object.hasOwn(authored,"shopify_channels"))) result.shopify_channels = channelValues();
    Object.keys(result).forEach((key) => {
      if (result[key] === "" && !["description", "short_description", "meta_title", "meta_description"].includes(key)) delete result[key];
    });
    if (!explicitVariation && !Object.hasOwn(authored, "attributes") && !Object.keys(result.attributes || {}).length) delete result.attributes;
    if (!Object.keys(result.variation_modifiers || {}).length && (!Object.hasOwn(authored, "variation_modifiers") ||
        !isShared && Object.values(authored.variation_modifiers).every(v=>Object.keys(v).every(k=>['packaging','finished_dimensions'].includes(k))))) delete result.variation_modifiers;
    return result;
  }

  function guidedDocument() {
    const values = guidedValues();
    const result = structuredClone(authored);
    for (const key of new Set([...Object.keys(initialValues), ...Object.keys(values)])) {
      if (JSON.stringify(values[key]) === JSON.stringify(initialValues[key])) continue;
      if (Object.hasOwn(values, key)) result[key] = structuredClone(values[key]);
      else delete result[key];
    }
    if (JSON.stringify(values.shopify_status) !== JSON.stringify(initialValues.shopify_status)) {
      if (values.shopify_status) result.live = values.shopify_status === "ACTIVE";
      else { delete result.shopify_status; delete result.live; }
    }
    return result;
  }

  function documentPatch(before, after, path = []) {
    const data = Object.create(null), remove_paths = [];
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (!Object.hasOwn(after, key)) { remove_paths.push([...path, key]); continue; }
      if (JSON.stringify(before[key]) === JSON.stringify(after[key])) continue;
      if (before[key] && after[key] && typeof before[key] === "object" && typeof after[key] === "object" &&
          !Array.isArray(before[key]) && !Array.isArray(after[key])) {
        const nested = documentPatch(before[key], after[key], [...path, key]);
        if (Object.keys(nested.data).length) data[key] = nested.data;
        remove_paths.push(...nested.remove_paths);
      } else data[key] = after[key];
    }
    return {data, remove_paths};
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
    const response = await fetch(boot.validate_url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: boot.kind, data: documentValue, product_id:boot.product_id }) });
    const payload = await response.json().catch(() => ({ valid: false, errors: [{ path: "$", message: "Validation returned an unreadable response." }] }));
    if (!response.ok || !payload.valid) {
      showErrors(payload.errors || [{ path: "$", message: payload.error || "Metadata is invalid." }]);
      return null;
    }
    clearErrors();
    showTitlePreview(payload.title_preview);
    showFeedback(payload.warnings?.length ? "warning" : "success", "Validation complete", payload.warnings?.length ? `${payload.warnings.length} documented warning(s) remain. No authored values were changed.` : "No blocking metadata issues were found.");
    return payload;
  }

  function showTitlePreview(value) {
    if (!value) return;
    root.querySelector('[data-title-preview]').textContent=value.title || 'Unavailable';
    root.querySelector('[data-title-preview-status]').textContent=' · Local draft preview only; save and scan before reviewing Shopify.';
  }
  let titleTimer, titleGeneration=0;
  function queueTitlePreview() {
    const generation=++titleGeneration;
    window.clearTimeout(titleTimer);
    root.querySelector('[data-title-preview-status]').textContent=' · Checking local draft…';
    titleTimer=window.setTimeout(async()=>{
      try {
        const data=activeMode==='advanced'?JSON.parse(jsonEditor.value):guidedDocument();
        const response=await fetch(boot.validate_url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:boot.kind,data,product_id:boot.product_id})});
        const payload=await response.json();
        if(generation!==titleGeneration)return;
        if(!response.ok||!payload.valid)throw new Error('invalid');
        showTitlePreview(payload.title_preview);
      }catch(_error){if(generation===titleGeneration)root.querySelector('[data-title-preview-status]').textContent=' · Preview unavailable; validate the current local draft.';}
    },250);
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
    if (!boot.can_save) return showFeedback("error", "Read-only source", "Restore the authorised metadata owner and reload.");
    const acknowledgement = document.querySelector("[data-acknowledge-shared]");
    if (acknowledgement && !acknowledgement.checked) return showFeedback("warning", "Confirm shared scope", "Confirm the affected shared products before saving.");
    const patch = documentPatch(originalDocument, documentValue);
    setBusy(true);
    try {
      const response = await fetch(boot.save_url, { method: "POST", headers: { "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        body: JSON.stringify({ kind: boot.kind, ...patch, revision: boot.revision, acknowledge_shared: !!acknowledgement?.checked }) });
      const payload = await response.json().catch(() => ({ error: "The save response could not be read." }));
      if (!response.ok || payload.error) {
        if (payload.errors) showErrors(payload.errors);
        else {
          showFeedback(payload.json_saved ? "warning" : "error", payload.json_saved ? "JSON saved — scan needs recovery" : response.status === 409 ? "Reload and review your draft" : "Metadata was not saved",
            payload.message || payload.error || "The established update operation could not be started.");
          const link = document.createElement("a");
          link.href = payload.json_saved ? payload.scanner_url : window.location.href;
          link.textContent = payload.json_saved ? "Open Scanner for recovery" : "Reload editor (copy your draft first)";
          feedback.appendChild(link);
          if (payload.json_saved && payload.revision) {
            boot.revision = payload.revision;
            const retry = document.createElement("button");
            retry.type = "button"; retry.className = "btn btn-sm btn-outline-primary";
            retry.textContent = "Retry local scan only";
            retry.addEventListener("click", retryScan);
            feedback.appendChild(retry);
          }
        }
        return;
      }
      boot.revision = payload.revision;
      originalDocument = structuredClone(payload.authored || documentValue);
      populate(originalDocument);
      setDirty(false);
      window.TLCSaleEditor?.saved(payload);
      window.dispatchEvent(new Event('tlc:local-metadata-saved'));
      showFeedback("success", payload.unchanged ? "No changes to save" : "JSON saved — scanning",
        payload.unchanged ? "Source and scan history are unchanged." : "Local source saved with backup. Scanner/database update is pending; Shopify has not been published.");
      startProgress(payload.run_id);
    } catch (_error) {
      showFeedback("warning", "Save outcome unavailable", "Copy your draft and reload to check the source and scanner history before retrying. A lost response does not prove the source was unchanged.");
    } finally {
      setBusy(false);
    }
  }

  async function retryScan() {
    if (busy) return;
    setBusy(true);
    try {
      const response = await fetch(boot.save_url, { method: "POST", headers: {
        "Content-Type": "application/json", "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        body: JSON.stringify({kind: boot.kind, data: {}, revision: boot.revision, retry_scan: true, acknowledge_shared: true}) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || "Reload the editor and inspect scanner history.");
      boot.revision = payload.revision;
      originalDocument = structuredClone(payload.authored); populate(originalDocument); setDirty(false);
      window.TLCSaleEditor?.saved(payload);
      showFeedback("success", "Local scan queued", "No JSON or Shopify values were rewritten. Check scanner completion before publishing.");
      startProgress(payload.run_id);
    } catch (error) {
      showFeedback("warning", "Local scan needs attention", error.message);
    } finally { setBusy(false); }
  }

  function startProgress(runId) {
    if (!runId || !window.OperationProgress || !window.bootstrap) return;
    const modalElement = document.getElementById("scanModal");
    const view = window.OperationProgress.create(modalElement.querySelector("[data-operation-progress]"));
    view.begin(isShared ? "shared_collection_update" : "product_update");
    new window.bootstrap.Modal(modalElement).show();
    const stream = new EventSource(boot.stream_url_tpl.replace("__RUNID__", runId));
    stream.onmessage = (event) => view.appendLog(event.data);
    let polls = 0, failures = 0;
    const interval = window.setInterval(async () => {
      if (++polls > 300) {
        window.clearInterval(interval); stream.close();
        showFeedback("warning", "Scan continues independently", "Polling stopped. Check Scanner history before reviewing Shopify.");
        return;
      }
      try {
      const response = await fetch(boot.progress_url_tpl.replace("__RUNID__", runId));
      const progress = await response.json();
      view.update(progress);
      if (["done", "error"].includes(progress.status)) {
        window.clearInterval(interval);
        stream.close();
        const incomplete = progress.status === "error" || progress.errors > 0 || ["failed", "partial"].includes(progress.outcome);
        window.TLCSaleEditor?.scanned(!incomplete);
        showFeedback(incomplete ? "warning" : "success", incomplete ? "Local JSON retained — check scan results" : "Local scan completed",
          incomplete ? "Some projection work needs recovery. Inspect Scanner history; do not assume Shopify is ready." : "The scanner completed its local projection. Review Shopify Changes separately; no Shopify mutation was submitted.");
        if (incomplete) {
          const retry = document.createElement("button");
          retry.type = "button"; retry.className = "btn btn-sm btn-outline-primary";
          retry.textContent = "Retry local scan only"; retry.addEventListener("click", retryScan);
          feedback.appendChild(retry);
        }
        fetch(boot.done_url_tpl.replace("__RUNID__", runId)).catch(() => {});
      }
      failures = 0;
      } catch (_error) {
        if (++failures >= 3) {
          window.clearInterval(interval); stream.close();
          showFeedback("warning", "Scan progress unavailable", "Your saved JSON is retained. The server may still be scanning; check Scanner history before publishing.");
        }
      }
    }, 1000);
  }

  function updateCharacterCounts() {
    document.querySelectorAll("[data-character-count]").forEach((node) => {
      const control = guided.querySelector(`[data-field="${CSS.escape(node.dataset.characterCount)}"]`);
      node.textContent = control ? control.value.length : "0";
    });
  }

  guided.addEventListener("input", (event) => { setDirty(true); updateCharacterCounts(); updateFinishedPreview(); if(event.target.matches('[data-field="title"]'))queueTitlePreview(); });
  guided.addEventListener("change", (event) => {
    if (event.target.matches("[data-override-toggle]")) setControlState(event.target.dataset.overrideToggle);
    if (event.target.matches('[data-override-toggle="title"]')) queueTitlePreview();
    if (event.target.matches('[data-field="collection_type"]')) renderVariationState();
    setDirty(true);
    updateFinishedPreview();
  });
  guided.addEventListener("click", (event) => {
    if(event.target.closest('[data-copy-product-dimensions]')){
      const control=event.target.closest('[data-copy-product-dimensions]');control.disabled=true;
      window.TLCPackaging.copyDimensions(guidedDocument,value=>{populate(value);setDirty(true);},()=>window.TLCPackaging.sourceDocument(isShared?{}:boot.collection,guidedDocument()))
        .catch(error=>showFeedback('warning','Dimensions not copied',error.message)).finally(()=>{control.disabled=false;});return;
    }
    const addList = event.target.closest("[data-add-list]");
    if (addList) { renderList(addList.dataset.addList, [...listValues(addList.dataset.addList), ""]); setDirty(true); return; }
    if (event.target.closest("[data-add-attribute]")) { const value = attributeValues(); value[`Attribute ${Object.keys(value).length + 1}`] = [""]; renderAttributes(value); setDirty(true); return; }
    if (event.target.closest("[data-add-modifier]")) { const value = modifierValues(); value[`Attribute=Value ${Object.keys(value).length + 1}`] = {}; renderModifiers(value); setDirty(true); return; }
    const action = event.target.closest("[data-row-action]");
    if (!action) return;
    const row = action.closest(".repeatable-row, .structured-editor-row");
    const parent = row.parentElement;
    if (action.dataset.rowAction === "remove") {
      if (explicitVariation && row.hasAttribute("data-attribute-row")) driverNames = driverNames.filter((name) => name !== row.querySelector("[data-attribute-name]").value.trim());
      row.remove();
    }
    if (action.dataset.rowAction === "up" && row.previousElementSibling) parent.insertBefore(row, row.previousElementSibling);
    if (action.dataset.rowAction === "down" && row.nextElementSibling) parent.insertBefore(row.nextElementSibling, row);
    if (row.hasAttribute("data-attribute-row")) renderVariationState();
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
  jsonEditor.addEventListener("input", () => { setDirty(true); updateCodePresentation(); queueTitlePreview(); });
  document.querySelectorAll("[data-validate-metadata]").forEach((button) => button.addEventListener("click", () => {
    const value = activeMode === "advanced" ? advancedDocument() : guidedDocument();
    if (value) validateDocument(value);
  }));
  saveButtons.forEach((button) => button.addEventListener("click", save));
  guided.querySelector("[data-discover-channels]")?.addEventListener("click", async () => {
    try {
      const values = channelValues();
      const response = await fetch(boot.discover_channels_url,{method:"POST",headers:{"X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""}});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Channel discovery unavailable; existing intent is unchanged.");
      channels = payload.channels; renderChannels(values);
      showFeedback("success","Available Shopify channels discovered","Read-only discovery only. Review actual remote status/publication separately before confirming. No product visibility changed.");
    } catch (error) { showFeedback("error","Channel discovery unavailable",error.message); }
  });
  const loadTemplateButton = document.getElementById("loadTemplateBtn");
  if (loadTemplateButton) loadTemplateButton.addEventListener("click", async () => {
    const select = document.getElementById("templateSelect");
    if (!select.value) return;
    try {
      const response = await fetch(boot.template_url_tpl.replace("__NAME__", encodeURIComponent(select.value)));
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Template unavailable");
      populate({...payload.data, ...authored});
      setDirty(true);
      showFeedback("warning", "Template proposal loaded", "Only missing fields were proposed; existing and unrepresented data was preserved. This fictional example is an unsaved draft—review every added field before saving.");
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
  if (!boot.can_save) saveButtons.forEach((button) => { button.disabled = true; });
  registryChoices();
  guided.querySelector("[data-assign-range]").addEventListener("click", () => {
    if (!overrideEnabled("storefront_collections")) return showFeedback("error", "Enable range override", "Enable the Storefront Collection override before replacing inherited assignments.");
    const selected = (registry.storefront_collections || []).find((row) => row.key === guided.querySelector("[data-registry-range]").value);
    if (!selected) return;
    renderList("storefront_collections", Array.from(new Set([...listValues("storefront_collections"), selected.value])));
    setDirty(true);
  });
  guided.querySelector("[data-registry-attribute]").addEventListener("change", registryChoices);
  guided.querySelector("[data-assign-category]").addEventListener("click", () => {
    if (!overrideEnabled("categories")) return showFeedback("error", "Enable category override", "Inherited categories remain unchanged. Enable the override before adding product categories.");
    const selected = registry.categories.find((row) => row.key === guided.querySelector("[data-registry-category]").value);
    if (!selected) return;
    renderList("categories", Array.from(new Set([...listValues("categories"), selected.value])));
    setDirty(true);
  });
  guided.querySelector("[data-assign-term]").addEventListener("click", () => {
    if (!overrideEnabled("attributes")) return showFeedback("error", "Enable attributes override", "Enable the attributes override before changing inherited attributes.");
    const selected = registry.attributes.find((row) => row.key === guided.querySelector("[data-registry-attribute]").value);
    if (!selected) return;
    const term = selected.terms.find((row) => row.key === guided.querySelector("[data-registry-term]").value);
    if (!term) return showFeedback("error", "Choose a term", "Select a term under this attribute before assigning it.");
    const values = attributeValues();
    values[selected.value] = Array.from(new Set([...(values[selected.value] || []), ...(term ? [term.value] : [])]));
    renderAttributes(values);
    setDirty(true);
  });
  guided.querySelectorAll("[data-refresh-taxonomy]").forEach((button) => button.addEventListener("click", async () => {
    try {
      const response = await fetch(boot.taxonomy_options_url, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Registry choices could not be refreshed. Your draft is unchanged.");
      registry = await response.json();
      registryChoices();
      renderAttributes(attributeValues()); renderList("categories", listValues("categories"));
      renderList("storefront_collections", listValues("storefront_collections"));
      showFeedback("success", "Registry choices refreshed", "No assignment was changed. If the registry was edited since this page opened, copy your Advanced JSON draft and reload before saving; the source review protects the original registry revision.");
    } catch (error) { showFeedback("error", "Refresh failed", error.message); }
  }));
  guided.querySelector("[data-adopt-variation]").addEventListener("click", () => {
    if (!resolvedVariation) driverNames = simpleProduct() ? [] : Object.keys(attributeValues());
    explicitVariation = true; resolvedVariation = true;
    renderVariationState();
    setDirty(true);
  });
  setDirty(false);
  window.TLCMetadataSaleEditor={snapshot:()=>({kind:boot.kind,revision:boot.revision,can_save:boot.can_save,dirty,busy,
    data:dirty ? null : activeMode==="advanced" ? JSON.parse(jsonEditor.value) : guidedDocument()})};
  window.TLCSaleEditor?.check();
})();
