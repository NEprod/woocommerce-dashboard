/* Keep comparison tables intact and scroll each one within its own region. */
(function () {
  function wrapTables() {
    document.querySelectorAll('.operator-workspace table.shopify-review-table').forEach(table => {
      if (table.parentElement.classList.contains('operator-table-scroll')) return;
      const region = document.createElement('div');
      region.className = 'table-responsive operator-table-scroll';
      region.setAttribute('role', 'region');
      region.setAttribute('aria-label', table.classList.contains('operator-effective-table') ? 'Resolved metadata table' : 'Shopify comparison table');
      region.tabIndex = 0;
      table.parentNode.insertBefore(region, table);
      region.appendChild(table);
    });
    // Ordinary comparison belongs in the main review, not behind technical help.
    const plan = document.querySelector('[data-review-plan]');
    const variantGroups = plan?.querySelectorAll('[data-review-group^="Variant "]').length || 0;
    plan?.querySelectorAll('details').forEach(group => {
      if (group.closest('.operator-help')) return;
      const label = group.querySelector(':scope > summary')?.textContent?.trim() || '';
      if (/^(Status, sales channels and Markets|Collections & standard category|Attributes and definitions|Packages|Deferred fields|Already Matching)/.test(label)) group.open = true;
      if (group.hasAttribute('data-review-group') && label.startsWith('Variant ') && /\b[1-9]\d* proposed changes/.test(label)) {
        const count = Number(label.match(/\b(\d+) proposed changes/)?.[1] || 0);
        if (variantGroups <= 4 && count <= 10) group.open = true;
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wrapTables);
  else wrapTables();
  document.addEventListener('tlc:review-ready', wrapTables);
})();
