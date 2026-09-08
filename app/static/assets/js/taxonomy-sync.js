/* Presentation/selection only. Server revalidates every reviewed identity. */
(function () {
  'use strict';
  function selectRows(all, targets, action, limit) {
    const eligible = targets.filter(row => !row.disabled && row.dataset.action === action);
    const existing = all.filter(row => row.checked);
    if (existing.some(row => row.dataset.action !== action)) return 'Clear selection before choosing a different action.';
    if (new Set([...existing, ...eligible]).size > limit) return `Select at most ${limit} definitions; use a smaller group.`;
    eligible.forEach(row => { row.checked = true; });
    return '';
  }
  if (typeof module !== 'undefined') module.exports = {selectRows};
  if (typeof document === 'undefined') return;
  const workspace = document.querySelector('[data-sync-workspace]');
  if (!workspace) return;
  const form = workspace.querySelector('[data-sync-selection]');
  const rows = form ? Array.from(form.querySelectorAll('input[name="selected"]')) : [];
  const limit = form ? Number(form.dataset.limit) : 50;
  function update(message = '') {
    if (!form) return;
    form.querySelector('[data-selected-count]').textContent = `${rows.filter(r => r.checked).length} / ${limit} selected`;
    form.querySelector('[data-selection-message]').textContent = message;
  }
  function show(view) {
    workspace.querySelectorAll('[data-sync-panel]').forEach(panel => { panel.hidden = panel.dataset.syncPanel !== view; });
    workspace.querySelector('[data-preview-view]').value = view;
    workspace.querySelectorAll('[data-sync-view]').forEach(link => { link.setAttribute('aria-current', link.dataset.syncView === view ? 'page' : 'false'); });
  }
  workspace.querySelectorAll('[data-sync-view]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault(); show(link.dataset.syncView);
  }));
  show(workspace.dataset.activeView);
  if (!form) return;
  form.addEventListener('click', event => {
    const button = event.target.closest('[data-group-action], [data-section-action], [data-clear-selection]');
    if (!button) return;
    if (button.hasAttribute('data-clear-selection')) { rows.forEach(row => { row.checked = false; }); update(); return; }
    const scope = button.closest(button.hasAttribute('data-group-action') ? '[data-sync-node]' : '[data-sync-panel]');
    update(selectRows(rows, Array.from(scope.querySelectorAll('input[name="selected"]')), button.dataset.groupAction || button.dataset.sectionAction, limit));
  });
  rows.forEach(row => row.addEventListener('change', () => {
    if (!row.checked) { update(); return; }
    row.checked = false;
    update(selectRows(rows, [row], row.dataset.action, limit));
  }));
  form.querySelectorAll('[data-sync-filter]').forEach(filter => filter.addEventListener('change', () => {
    const nodes = Array.from(filter.closest('[data-sync-panel]').querySelectorAll('[data-sync-node]')).reverse();
    nodes.forEach(node => { node.hidden = filter.value !== 'all' && node.dataset.bucket !== filter.value && !Array.from(node.querySelectorAll('[data-sync-node]')).some(child => !child.hidden); });
  }));
  form.addEventListener('submit', event => {
    const count = rows.filter(row => row.checked).length;
    if (!count || count > limit) { event.preventDefault(); update(`Select 1–${limit} definitions to review.`); }
  });
}());
