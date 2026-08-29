(function () {
  "use strict";

  document.addEventListener("click", async function (event) {
    const button = event.target.closest("[data-copy-value]");
    if (!button || button.disabled) return;
    const value = button.dataset.copyValue || "";
    try {
      await navigator.clipboard.writeText(value);
      const original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = original; }, 1500);
    } catch (_error) {
      button.textContent = "Copy unavailable";
    }
  });

  const loadButton = document.querySelector("[data-load-detail-variations]");
  if (loadButton) loadButton.addEventListener("click", async function () {
    if (loadButton.disabled) return;
    const target = document.querySelector("[data-detail-variations]");
    const page = Number(loadButton.dataset.page || "2");
    loadButton.disabled = true;
    loadButton.textContent = "Loading variations…";
    try {
      const url = new URL(target.dataset.url, window.location.href);
      url.searchParams.set("page", String(page));
      const response = await fetch(url.toString());
      const payload = await response.json();
      if (!response.ok) throw new Error("Variations could not be loaded.");
      payload.items.forEach(function (variation) {
        const details = document.createElement("details");
        details.className = "variation-detail-card";
        const summary = document.createElement("summary");
        summary.dataset.variationToggle = "";
        const identity = document.createElement("span");
        const sku = document.createElement("strong");
        sku.textContent = variation.sku || "SKU not set";
        const attributes = document.createElement("small");
        attributes.textContent = variation.attributes.map((item) => `${item.name}: ${item.value}`).join(" · ");
        identity.append(sku, attributes);
        const state = document.createElement("span");
        state.textContent = `${variation.sale_price || variation.regular_price ? `£${variation.sale_price || variation.regular_price}` : "Price not set"} · ${variation.status || "Unknown"}`;
        summary.append(identity, state);
        const body = document.createElement("div");
        body.className = "variation-detail-body";
        const copy = document.createElement("p");
        copy.textContent = variation.images.length ? `${variation.images.length} variation image reference(s).` : "No usable variation image or parent preview fallback is available.";
        body.appendChild(copy);
        if (variation.images.length) {
          const strip = document.createElement("div");
          strip.className = "variation-image-strip";
          variation.images.forEach((image, index) => {
            const card = document.createElement("article");
            const frame = document.createElement("div");
            if (image.preview_url) {
              const preview = document.createElement("img");
              preview.src = image.preview_url;
              preview.alt = image.alt_text || variation.sku || "Variation image";
              preview.loading = "lazy";
              frame.appendChild(preview);
            }
            const role = document.createElement("strong");
            role.textContent = image.fallback ? "Parent preview fallback" : image.role;
            const source = document.createElement("code");
            source.textContent = image.source_reference || "Source unavailable";
            card.append(frame, role, source);
            if (image.stored_url) {
              const label = document.createElement("label");
              label.textContent = `Stored website URL for image ${index + 1}`;
              const input = document.createElement("input");
              input.readOnly = true;
              input.value = image.stored_url;
              label.appendChild(input);
              card.appendChild(label);
            }
            strip.appendChild(card);
          });
          body.appendChild(strip);
        }
        details.append(summary, body);
        target.appendChild(details);
      });
      if (page >= payload.pagination.pages) loadButton.parentElement.remove();
      else {
        loadButton.dataset.page = String(page + 1);
        loadButton.disabled = false;
        loadButton.textContent = "Load more variations";
      }
    } catch (_error) {
      loadButton.disabled = false;
      loadButton.textContent = "Retry variations";
    }
  });
})();
