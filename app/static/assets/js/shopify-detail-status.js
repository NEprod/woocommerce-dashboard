(function () {
  "use strict";
  const panel = document.querySelector("[data-shopify-detail]");
  const button = panel?.querySelector("[data-shopify-detail-refresh]");
  if (!button) return;
  button.addEventListener("click", async function () {
    button.disabled = true;
    const progress = panel.querySelector("[data-shopify-detail-progress]");
    progress.textContent = "Checking Shopify — read-only…";
    try {
      const response = await fetch(panel.dataset.statusUrl, {method: "POST", headers: {
        "Content-Type": "application/json", "X-CSRFToken": panel.dataset.csrf},
        body: JSON.stringify({ids: [Number(panel.dataset.productId)], force: true})});
      if (!response.ok) throw new Error("Status refresh unavailable. Retry later; no store writes.");
      window.location.reload();
    } catch (error) { progress.textContent = error.message; button.disabled = false; }
  });
})();
