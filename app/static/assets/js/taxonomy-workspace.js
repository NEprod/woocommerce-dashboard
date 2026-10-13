/* Local workspace enhancement only. Server-signed review remains authoritative. */
(function () {
  "use strict";
  function scope(keys, parents) {
    const result = new Set(keys);
    for (const key of keys) {
      let parent = parents.get(key);
      const visited = new Set([key]);
      while (parent && !visited.has(parent)) {
        visited.add(parent); result.add(parent); parent = parents.get(parent);
      }
    }
    return result;
  }
  function descendants(key, parents) {
    return [...parents.keys()].filter(candidate => scope([candidate], parents).has(key));
  }
  if (typeof module !== "undefined") module.exports = {scope, descendants};
  if (typeof document === "undefined") return;
  const workspace = document.querySelector("[data-taxonomy-workspace]");
  if (!workspace) return;
  const panels = [...workspace.querySelectorAll("[data-taxonomy-panel]")];
  const tabs = [...workspace.querySelectorAll("[data-taxonomy-tab]")];
  function activate(tab) {
    panels.forEach(panel => { panel.hidden = panel.dataset.taxonomyPanel !== tab; });
    tabs.forEach(link => {
      const active = link.dataset.taxonomyTab === tab;
      link.classList.toggle("btn-primary", active); link.classList.toggle("btn-ghost", !active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
  }
  tabs.forEach(link => link.addEventListener("click", event => {
    // Eligibility/usage is built locally only when its server-rendered tab opens.
    if (["navigation", "attributes"].includes(link.dataset.taxonomyTab)) return;
    event.preventDefault(); activate(link.dataset.taxonomyTab);
    window.history.replaceState(null, "", link.href);
  }));
  workspace.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", () => {
    const target = document.getElementById(link.getAttribute("href").slice(1));
    if (target?.tagName === "DETAILS") target.open = true;
  }));
  const search = workspace.querySelector("[data-taxonomy-search]");
  const filter = workspace.querySelector("[data-taxonomy-filter]");
  function applyFilter() {
    const query = search.value.trim().toLocaleLowerCase();
    const rows = [...workspace.querySelectorAll("[data-taxonomy-row]")];
    // Work bottom-up so matching descendants keep their ancestors visible.
    rows.reverse().forEach(row => {
      const matches = row.dataset.search.includes(query) && (filter.value === "all" || row.dataset.mapping === filter.value);
      const childMatches = [...row.querySelectorAll("[data-taxonomy-row]")].some(child => !child.hidden);
      row.hidden = !matches && !childMatches;
      if ((query || filter.value !== "all") && childMatches) row.open = true;
    });
    updateCount();
  }
  search.addEventListener("input", applyFilter); filter.addEventListener("change", applyFilter);
  workspace.querySelectorAll("[data-taxonomy-expand]").forEach(button => button.addEventListener("click", () => {
    workspace.querySelectorAll("details.taxonomy-tree-node").forEach(row => { row.open = button.dataset.taxonomyExpand === "yes"; });
  }));
  const form = workspace.querySelector("[data-taxonomy-selection]");
  const choices = form ? [...form.querySelectorAll("[data-taxonomy-choice]")] : [];
  const parents = new Map(choices.map(choice => [choice.value, choice.dataset.parent]));
  function updateCount() {
    if (!form) return;
    const selected = choices.filter(choice => choice.checked && !choice.disabled).map(choice => choice.value);
    const total = scope(selected, parents).size;
    form.querySelector("[data-taxonomy-selection-count]").textContent = `${selected.length} selected · ${total} groups including required ancestors. ${total > 20 ? "Choose a smaller scope (maximum 20)." : "Actual operation counts appear in the read-only proposal."}`;
    form.querySelector("[data-taxonomy-review]").disabled = total === 0 || total > 20;
  }
  choices.forEach(choice => choice.addEventListener("change", updateCount));
  workspace.querySelectorAll("[data-taxonomy-select]").forEach(button => button.addEventListener("click", () => {
    choices.forEach(choice => {
      if (choice.disabled) return;
      if (button.dataset.taxonomySelect === "none") choice.checked = false;
      else if (!choice.closest("[hidden]")) choice.checked = button.dataset.taxonomySelect === "all" || choice.dataset.mapping === "Not mapped";
    }); updateCount();
  }));
  workspace.querySelectorAll("[data-taxonomy-descendants]").forEach(button => button.addEventListener("click", () => {
    const wanted = new Set(descendants(button.dataset.taxonomyDescendants, parents));
    choices.forEach(choice => { if (!choice.disabled && wanted.has(choice.value)) choice.checked = true; }); updateCount();
  }));
  // No-JS submission is still validated by the backend. Never approve via selection.
  activate(workspace.dataset.activeTab); applyFilter();
})();
