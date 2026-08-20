(function () {
  "use strict";

  const browser = document.querySelector("[data-products-browser]");
  if (!browser) return;

  const groupsTarget = browser.querySelector("[data-products-groups]");
  const loadingState = browser.querySelector("[data-products-loading]");
  const errorState = browser.querySelector("[data-products-error]");
  const emptyState = browser.querySelector("[data-products-empty]");
  const filteredEmptyState = browser.querySelector("[data-products-filtered-empty]");
  const paginationTarget = browser.querySelector("[data-products-pagination]");
  const currentParams = new URLSearchParams(window.location.search);
  let pickerState = { selectedRel: "" };

  function text(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? "" : String(value);
    return node;
  }

  function button(label, className) {
    const node = text("button", className, label);
    node.type = "button";
    return node;
  }

  function replaceId(template, id) {
    return template.replace(/\/0(?:\/|$)/, `/${id}/`);
  }

  function formatDate(value) {
    if (!value) return "Not recorded";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "Not recorded";
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"
    }).format(parsed);
  }

  function formatPrice(price) {
    if (!price || price.minimum == null) return "Not set";
    if (price.maximum != null && price.maximum !== price.minimum) return `£${price.minimum}–£${price.maximum}`;
    return `£${price.minimum}`;
  }

  function sourceLabel(source) {
    return source === "override" ? "Product override" : source === "shared" ? "Shared only" : "No metadata";
  }

  function pill(label, kind) {
    return text("span", `catalogue-pill is-${kind}`, label);
  }

  function thumbnail(product) {
    const frame = document.createElement("span");
    frame.className = "product-thumbnail";
    frame.appendChild(text("span", "product-thumbnail-fallback", (product.title || "P").trim().slice(0, 1).toUpperCase()));
    if (product.thumbnail) {
      const image = document.createElement("img");
      image.src = product.thumbnail;
      image.alt = product.thumbnail_alt || product.title || "";
      image.loading = "lazy";
      image.addEventListener("load", function () { frame.classList.add("has-image"); });
      image.addEventListener("error", function () { image.remove(); frame.classList.remove("has-image"); });
      frame.appendChild(image);
    }
    return frame;
  }

  function metadataActions(product) {
    const actions = document.createElement("div");
    actions.className = "product-actions";
    if (product.view_url) {
      const view = text("a", "btn btn-sm btn-outline-primary", "View");
      view.href = product.view_url;
      view.target = "_blank";
      view.rel = "noopener";
      actions.appendChild(view);
    } else {
      const view = button("View", "btn btn-sm btn-outline-secondary");
      view.disabled = true;
      actions.appendChild(view);
    }
    if (product.edit_url) {
      const edit = text("a", "btn btn-sm btn-primary", "Edit metadata");
      edit.href = product.edit_url;
      actions.appendChild(edit);
    } else {
      const edit = button("Edit metadata", "btn btn-sm btn-outline-secondary");
      edit.disabled = true;
      actions.appendChild(edit);
    }
    if (!product.override_present) {
      const create = button("Create override", "btn btn-sm btn-ghost");
      create.dataset.action = "create-override";
      create.dataset.productId = product.id;
      actions.appendChild(create);
    } else {
      const remove = button("Delete override", "btn btn-sm btn-ghost product-delete-action");
      remove.dataset.action = "delete-override";
      remove.dataset.productId = product.id;
      remove.dataset.sku = product.sku;
      actions.appendChild(remove);
    }
    return actions;
  }

  function variationToggle(product, compact) {
    if (!product.variation_count) return text("span", "product-variation-empty", "—");
    const trigger = button(`${product.variation_count} variation${product.variation_count === 1 ? "" : "s"}`, compact ? "variation-toggle is-mobile" : "variation-toggle");
    trigger.dataset.action = "toggle-variations";
    trigger.dataset.productId = product.id;
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `product-variations-${product.id}${compact ? "-mobile" : ""}`);
    return trigger;
  }

  function variationRegion(product, compact) {
    const region = document.createElement("section");
    region.className = compact ? "variation-region is-mobile" : "variation-region";
    region.id = `product-variations-${product.id}${compact ? "-mobile" : ""}`;
    region.dataset.variationRegion = product.id;
    region.hidden = true;
    region.tabIndex = -1;
    return region;
  }

  function desktopProduct(product) {
    const fragment = document.createDocumentFragment();
    const row = document.createElement("div");
    row.className = "product-row";
    row.setAttribute("role", "row");
    const identity = document.createElement("div");
    identity.className = "product-identity";
    identity.setAttribute("role", "cell");
    identity.appendChild(thumbnail(product));
    const copy = document.createElement("span");
    copy.appendChild(text("strong", "product-title", product.title));
    copy.appendChild(text("small", "product-id", `ID ${product.id}`));
    identity.appendChild(copy);
    row.appendChild(identity);
    row.appendChild(text("code", "product-sku", product.sku || "Not set"));
    row.appendChild(pill(product.type === "variable" ? "Variable" : "Simple", "neutral"));
    row.appendChild(text("span", "product-price", formatPrice(product.price)));
    row.appendChild(pill(product.catalogue_status === "missing" ? "Missing" : "Active", product.catalogue_status));
    const variationCell = document.createElement("div");
    variationCell.appendChild(variationToggle(product, false));
    row.appendChild(variationCell);
    row.appendChild(pill(sourceLabel(product.metadata_source), product.metadata_source));
    row.appendChild(text("time", "product-updated", formatDate(product.updated_at)));
    row.appendChild(metadataActions(product));
    fragment.appendChild(row);
    fragment.appendChild(variationRegion(product, false));
    return fragment;
  }

  function labelledFact(label, valueNode) {
    const fact = document.createElement("div");
    fact.appendChild(text("dt", "", label));
    const definition = document.createElement("dd");
    definition.appendChild(valueNode);
    fact.appendChild(definition);
    return fact;
  }

  function mobileProduct(product) {
    const fragment = document.createDocumentFragment();
    const card = document.createElement("article");
    card.className = "product-mobile-card";
    card.dataset.mobileProductCard = "";
    const heading = document.createElement("div");
    heading.className = "product-mobile-heading";
    heading.appendChild(thumbnail(product));
    const identity = document.createElement("div");
    identity.appendChild(text("strong", "product-title", product.title));
    identity.appendChild(text("code", "product-sku", product.sku || "Not set"));
    heading.appendChild(identity);
    heading.appendChild(pill(product.catalogue_status === "missing" ? "Missing" : "Active", product.catalogue_status));
    card.appendChild(heading);
    const facts = document.createElement("dl");
    facts.className = "product-mobile-facts";
    facts.appendChild(labelledFact("Type", pill(product.type === "variable" ? "Variable" : "Simple", "neutral")));
    facts.appendChild(labelledFact("Price", text("span", "product-price", formatPrice(product.price))));
    facts.appendChild(labelledFact("Metadata", pill(sourceLabel(product.metadata_source), product.metadata_source)));
    facts.appendChild(labelledFact("Updated", text("time", "product-updated", formatDate(product.updated_at))));
    card.appendChild(facts);
    if (product.variation_count) card.appendChild(variationToggle(product, true));
    card.appendChild(metadataActions(product));
    fragment.appendChild(card);
    fragment.appendChild(variationRegion(product, true));
    return fragment;
  }

  function collectionGroup(group, index) {
    const article = document.createElement("article");
    article.className = "collection-group";
    const header = document.createElement("header");
    header.className = "collection-group-header";
    const trigger = button("", "collection-toggle");
    trigger.dataset.action = "toggle-collection";
    trigger.setAttribute("aria-expanded", "true");
    trigger.setAttribute("aria-controls", `collection-products-${index}`);
    trigger.appendChild(text("span", "collection-chevron", "⌄"));
    const heading = document.createElement("span");
    heading.className = "collection-heading";
    heading.appendChild(text("strong", "", group.name));
    heading.appendChild(text("small", "", `${group.product_count} parent product${group.product_count === 1 ? "" : "s"} · ${group.variation_count} variation${group.variation_count === 1 ? "" : "s"}`));
    trigger.appendChild(heading);
    header.appendChild(trigger);
    const facts = document.createElement("div");
    facts.className = "collection-facts";
    facts.appendChild(text("span", "collection-active-count", `${group.active_count} Active`));
    facts.appendChild(text("span", "collection-missing-count", `${group.missing_count} Missing`));
    facts.appendChild(text("time", "", `Updated ${formatDate(group.last_updated)}`));
    header.appendChild(facts);
    article.appendChild(header);
    const body = document.createElement("div");
    body.className = "collection-group-body";
    body.id = `collection-products-${index}`;
    const desktop = document.createElement("div");
    desktop.className = "products-desktop-table";
    desktop.setAttribute("role", "table");
    desktop.setAttribute("aria-label", `${group.name} parent products`);
    const columns = document.createElement("div");
    columns.className = "product-column-headings";
    columns.setAttribute("role", "row");
    ["Product", "SKU", "Type", "Price", "Status", "Variations", "Metadata", "Updated", "Actions"].forEach(function (label) {
      const cell = text("span", "", label);
      cell.setAttribute("role", "columnheader");
      columns.appendChild(cell);
    });
    desktop.appendChild(columns);
    const mobile = document.createElement("div");
    mobile.className = "products-mobile-cards";
    mobile.dataset.mobileProductCards = "";
    group.products.forEach(function (product) {
      desktop.appendChild(desktopProduct(product));
      mobile.appendChild(mobileProduct(product));
    });
    body.appendChild(desktop);
    body.appendChild(mobile);
    article.appendChild(body);
    return article;
  }

  function renderVariationItems(region, payload) {
    region.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "variation-region-heading";
    heading.appendChild(text("strong", "", `${payload.total} variation${payload.total === 1 ? "" : "s"}`));
    heading.appendChild(text("span", "", "Loaded from the local projection"));
    region.appendChild(heading);
    if (!payload.items.length) {
      region.appendChild(text("p", "variation-empty", "No projected variations are available for this parent."));
      return;
    }
    const list = document.createElement("div");
    list.className = "variation-list";
    payload.items.forEach(function (variation) {
      const row = document.createElement("article");
      row.className = "variation-preview-row";
      row.appendChild(text("code", "variation-sku", variation.sku || "Not set"));
      const attributes = document.createElement("div");
      attributes.className = "variation-attributes";
      if (variation.attributes.length) {
        variation.attributes.forEach(function (attribute) { attributes.appendChild(pill(`${attribute.name}: ${attribute.value}`, "attribute")); });
      } else attributes.appendChild(text("span", "product-variation-empty", "No attributes"));
      row.appendChild(attributes);
      row.appendChild(text("span", "variation-price", variation.price ? `£${variation.price}` : "Not set"));
      row.appendChild(text("span", "variation-stock", variation.stock_quantity == null ? "Stock not tracked" : `${variation.stock_quantity} in stock`));
      row.appendChild(pill(variation.catalogue_status === "missing" ? "Missing" : "Active", variation.catalogue_status));
      row.appendChild(pill(sourceLabel(variation.metadata_source), variation.metadata_source));
      row.appendChild(text("time", "variation-updated", formatDate(variation.updated_at)));
      list.appendChild(row);
    });
    region.appendChild(list);
    if (payload.truncated) {
      const more = button(`View all ${payload.total} variations`, "btn btn-sm btn-outline-primary variation-view-all");
      more.dataset.action = "view-all-variations";
      more.dataset.productId = payload.product_id;
      region.appendChild(more);
    }
  }

  function renderPagination(pagination) {
    paginationTarget.replaceChildren();
    if (!pagination.total) { paginationTarget.hidden = true; return; }
    paginationTarget.hidden = false;
    paginationTarget.appendChild(text("span", "products-results-summary", `Showing ${pagination.from}–${pagination.to} of ${pagination.total} parent products`));
    const controls = document.createElement("div");
    controls.className = "products-page-controls";
    function pageLink(label, page, disabled, current) {
      const link = text("a", `products-page-link${current ? " is-current" : ""}${disabled ? " is-disabled" : ""}`, label);
      if (!disabled) {
        const params = new URLSearchParams(currentParams);
        params.set("page", page);
        link.href = `${window.location.pathname}?${params.toString()}`;
      } else link.setAttribute("aria-disabled", "true");
      if (current) link.setAttribute("aria-current", "page");
      return link;
    }
    controls.appendChild(pageLink("Previous", pagination.page - 1, pagination.page <= 1, false));
    for (let page = Math.max(1, pagination.page - 1); page <= Math.min(pagination.pages, pagination.page + 1); page += 1) controls.appendChild(pageLink(String(page), page, false, page === pagination.page));
    controls.appendChild(pageLink("Next", pagination.page + 1, pagination.page >= pagination.pages, false));
    paginationTarget.appendChild(controls);
  }

  function render(payload) {
    document.querySelectorAll("[data-summary-value]").forEach(function (target) { target.textContent = Number(payload.summary[target.dataset.summaryValue] || 0).toLocaleString(); });
    groupsTarget.replaceChildren();
    errorState.hidden = true;
    emptyState.hidden = payload.empty_reason !== "catalogue";
    filteredEmptyState.hidden = payload.empty_reason !== "filtered";
    payload.groups.forEach(function (group, index) { groupsTarget.appendChild(collectionGroup(group, index)); });
    renderPagination(payload.pagination);
  }

  async function loadProducts() {
    loadingState.hidden = false;
    errorState.hidden = true;
    emptyState.hidden = true;
    filteredEmptyState.hidden = true;
    browser.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(`${browser.dataset.apiUrl}${window.location.search}`, { headers: { "Accept": "application/json" } });
      if (!response.ok) throw new Error(`The server returned HTTP ${response.status}.`);
      render(await response.json());
    } catch (error) {
      groupsTarget.replaceChildren();
      errorState.hidden = false;
      errorState.querySelector("[data-products-error-message]").textContent = error.message || "The catalogue remains unchanged. Retry when ready.";
      paginationTarget.hidden = true;
    } finally {
      loadingState.hidden = true;
      browser.setAttribute("aria-busy", "false");
    }
  }

  async function loadVariations(productId, region, includeAll) {
    region.hidden = false;
    region.setAttribute("aria-busy", "true");
    region.replaceChildren(text("div", "variation-loading", "Loading variation details…"));
    try {
      const endpoint = replaceId(browser.dataset.variationsUrl, productId) + (includeAll ? "?all=1" : "");
      const response = await fetch(endpoint, { headers: { "Accept": "application/json" } });
      if (!response.ok) throw new Error(`Variation details returned HTTP ${response.status}.`);
      renderVariationItems(region, await response.json());
      region.dataset.loaded = includeAll ? "all" : "preview";
    } catch (error) {
      region.replaceChildren();
      const message = text("div", "variation-error", error.message || "Variation details could not be loaded.");
      const retry = button("Retry", "btn btn-sm btn-outline-primary");
      retry.dataset.action = "retry-variations";
      retry.dataset.productId = productId;
      message.appendChild(retry);
      region.appendChild(message);
    } finally { region.setAttribute("aria-busy", "false"); }
  }

  function matchingRegions(productId) { return Array.from(browser.querySelectorAll(`[data-variation-region="${productId}"]`)); }

  async function openFolderPicker(productId, rel) {
    const endpoint = replaceId(browser.dataset.overrideFoldersUrl, productId) + (rel ? `?rel=${encodeURIComponent(rel)}` : "");
    const response = await fetch(endpoint);
    const output = await response.json();
    if (!response.ok || output.error) throw new Error(output.message || output.error || "Folder list could not be loaded.");
    pickerState = { productId: productId, selectedRel: "" };
    document.getElementById("folderBreadcrumb").textContent = output.path;
    const list = document.getElementById("folderList");
    list.replaceChildren();
    if (output.rel) {
      const up = text("li", "list-group-item list-group-item-action", "Up one folder");
      up.tabIndex = 0;
      up.addEventListener("click", function () { openFolderPicker(productId, output.rel.split("/").slice(0, -1).join("/")); });
      list.appendChild(up);
    }
    output.folders.forEach(function (name) {
      const item = text("li", "list-group-item list-group-item-action", name);
      item.tabIndex = 0;
      item.addEventListener("click", function () {
        list.querySelectorAll(".active").forEach(function (node) { node.classList.remove("active"); });
        item.classList.add("active");
        pickerState.selectedRel = output.rel ? `${output.rel}/${name}` : name;
        document.getElementById("chooseFolderBtn").disabled = false;
      });
      item.addEventListener("dblclick", function () { openFolderPicker(productId, output.rel ? `${output.rel}/${name}` : name); });
      list.appendChild(item);
    });
    document.getElementById("chooseFolderBtn").disabled = true;
  }

  async function openScanModal(runId) {
    const modalElement = document.getElementById("scanModal");
    const progress = window.OperationProgress.create(modalElement.querySelector("[data-operation-progress]"));
    progress.begin("product_update");
    bootstrap.Modal.getOrCreateInstance(modalElement).show();
    const source = new EventSource(browser.dataset.streamUrl.replace("__RUN_ID__", runId));
    source.onmessage = function (event) { progress.appendLog(event.data); };
    const interval = window.setInterval(async function () {
      const response = await fetch(browser.dataset.progressUrl.replace("__RUN_ID__", runId));
      const payload = await response.json();
      progress.update(payload);
      if (payload.status === "done" || payload.status === "error") {
        window.clearInterval(interval); source.close(); await loadProducts();
      }
    }, 1000);
  }

  browser.addEventListener("click", async function (event) {
    const trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    const action = trigger.dataset.action;
    const productId = trigger.dataset.productId;
    if (action === "toggle-collection") {
      const target = document.getElementById(trigger.getAttribute("aria-controls"));
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      trigger.setAttribute("aria-expanded", String(!expanded)); target.hidden = expanded; return;
    }
    if (action === "toggle-variations") {
      const region = document.getElementById(trigger.getAttribute("aria-controls"));
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      trigger.setAttribute("aria-expanded", String(!expanded));
      if (expanded) { region.hidden = true; return; }
      matchingRegions(productId).forEach(function (other) { if (other !== region) other.hidden = true; });
      if (!region.dataset.loaded) await loadVariations(productId, region, false); else region.hidden = false;
      return;
    }
    if (action === "view-all-variations" || action === "retry-variations") {
      await loadVariations(productId, trigger.closest("[data-variation-region]"), action === "view-all-variations"); return;
    }
    if (action === "create-override") {
      try { await openFolderPicker(productId, ""); bootstrap.Modal.getOrCreateInstance(document.getElementById("overrideModal")).show(); }
      catch (error) { window.alert(error.message); }
      return;
    }
    if (action === "delete-override") {
      if (!window.confirm(`Delete override JSON for ${trigger.dataset.sku}?`)) return;
      const response = await fetch(replaceId(browser.dataset.overrideDeleteUrl, productId), { method: "POST", headers: { "Content-Type": "application/json" } });
      const output = await response.json();
      if (!response.ok || output.error) { window.alert(output.message || output.error || "Override deletion failed."); return; }
      if (output.run_id) await openScanModal(output.run_id); else await loadProducts();
    }
  });

  browser.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const region = event.target.closest("[data-variation-region]");
    if (!region || region.hidden) return;
    const trigger = browser.querySelector(`[aria-controls="${region.id}"]`);
    region.hidden = true;
    if (trigger) { trigger.setAttribute("aria-expanded", "false"); trigger.focus(); }
  });

  browser.querySelector("[data-products-retry]").addEventListener("click", loadProducts);
  const chooseButton = document.getElementById("chooseFolderBtn");
  chooseButton.addEventListener("click", async function () {
    if (!pickerState.selectedRel) return;
    chooseButton.disabled = true;
    chooseButton.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(replaceId(browser.dataset.overrideCreateUrl, pickerState.productId), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rel: pickerState.selectedRel })
      });
      if (response.redirected) { window.location.assign(response.url); return; }
      const output = await response.json();
      if (!response.ok || output.error) throw new Error(output.message || output.error || "Override creation failed.");
      if (output.edit_url) window.location.assign(output.edit_url);
    } catch (error) { window.alert(error.message); chooseButton.disabled = false; }
    finally { chooseButton.removeAttribute("aria-busy"); }
  });

  loadProducts();
})();
