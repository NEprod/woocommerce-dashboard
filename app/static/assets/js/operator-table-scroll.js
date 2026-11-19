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
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wrapTables);
  else wrapTables();
  document.addEventListener('tlc:review-ready', wrapTables);
})();
