import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Tiny DOM fixture: exercises the real browser renderer without new dependencies.
class Node {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.dataset = {}; this.events = {}; this.attrs = {}; this.textContent = ""; this.classList = {add() {}, remove() {}}; }
  appendChild(node) { node.parent = this; this.children.push(node); return node; }
  replaceWith(node) { this.parent.children[this.parent.children.indexOf(this)] = node; node.parent = this.parent; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  addEventListener(name, callback) { this.events[name] = callback; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const match = selector.match(/^\[data-([a-z-]+)(?:="([^"]+)")?\]$/);
    const key = match?.[1]?.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return this.children.flatMap(node => [...(key && key in node.dataset && (match[2] == null || String(node.dataset[key]) === match[2]) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
}
const source = fs.readFileSync(new URL("../../app/static/assets/js/products-browser.js", import.meta.url), "utf8");
async function render(automatic = false, syncState = "update", productType = "simple", productCount = 1) {
  const browser = new Node();
  browser.dataset.apiUrl = "/api/edit_products";
  browser.dataset.overrideEditorUrl = "/edit_products/0/edit/override";
  if (automatic) { browser.dataset.statusUrl = "/api/shopify/status"; browser.dataset.csrfToken = "fictional_csrf"; }
  for (const name of ["productsGroups", "productsLoading", "productsError", "productsEmpty", "productsFilteredEmpty", "productsPagination", "productsRetry", "shopifySelectionCount", "shopifyReviewQueue", "shopifySelectVisible", "shopifyClearSelection", "productsErrorMessage", "shopifyRefreshProgress", "shopifyRefreshVisible", "shopifyRefreshAll"]) {
    const node = new Node(); node.dataset[name] = ""; browser.appendChild(node);
  }
  const choose = new Node();
  const document = {querySelector: () => browser, querySelectorAll: () => [], getElementById: () => choose,
    createElement: tag => new Node(tag), createDocumentFragment: () => new Node("fragment")};
  const product = {id: 1, sku: "TLC-1", title: "Local product", type: productType, catalogue_status: "active", price: {minimum: "3.50"}, variation_count: productType === "variable" ? 4 : 0,
    view_url: "/products/1", metadata_source: "shared", publishing_intent_label: "Draft intended",
    shopify: {state: syncState, label: "Update Available", intent: "Draft intended", intent_value: false, remote_status: "Draft (last compared)", compared_at: "2026-10-12T09:00:00Z", change_count: 2, review_url: "/products/1/shopify/review", identity_url: "/shopify/drafts/identity/1"}};
  const calls = [];
  const products = Array.from({length: productCount}, (_, index) => ({...product, id: index + 1}));
  const fetch = async (...args) => { calls.push(args); return {ok: true, json: async () => args[0] === "/api/shopify/status" ? {checked: [1], pending: [], states: {1: {...product.shopify, state: "no_change", label: "Up to Date", change_count: 0}}, errors: []} : ({summary: {}, groups: [{name: "Cards", product_count: productCount, variation_count: 0, products}], pagination: {total: productCount, from: 1, to: productCount, page: 1, pages: 1}})}; };
  const navigations = [];
  const window = {location: {search: "", pathname: "/products", href: "https://local.test/products", assign: url => navigations.push(url)}, fetch,
    ProductsOverrideClient: {routeUrl: (template, id) => template.replace("/0/", `/${id}/`)}};
  vm.runInNewContext(source, {document, window, fetch, URLSearchParams, Intl, Map, Number, String, Date});
  await new Promise(resolve => setImmediate(resolve));
  return {browser, calls, navigations};
}
function all(node) { return [node, ...node.children.flatMap(all)]; }

test("Create Override navigates directly without a creation request or scan", async () => {
  const {browser, calls, navigations} = await render();
  const trigger = {dataset: {action: "create-override", productId: "1"}};
  await browser.events.click({target: {closest: () => trigger}});
  assert.deepEqual(navigations, ["/edit_products/1/edit/override"]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/edit_products");
});

test("desktop and mobile render Shopify actions without Woo or remote discovery", async () => {
  const {browser, calls} = await render();
  const nodes = all(browser);
  assert.equal(nodes.filter(n => n.textContent === "Review Shopify Changes").length, 2);
  assert.equal(nodes.filter(n => n.textContent === "Update Available").length, 2);
  assert.equal(nodes.filter(n => n.className === "shopify-list-status").length, 2);
  assert.equal(nodes.filter(n => String(n.textContent).includes("2 changes")).length, 2);
  assert.equal(nodes.filter(n => n.textContent === "Product tools & history").length, 2);
  assert.equal(nodes.filter(n => n.className === "product-mobile-card").length, 1);
  assert.equal(nodes.some(n => String(n.textContent).includes("Woo state")), false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/edit_products");
  assert.equal(calls[0][1].method, undefined);
});

test("selection deduplicates desktop/mobile and prepares individual reviews only", async () => {
  const {browser, calls} = await render();
  browser.querySelector("[data-shopify-select-visible]").events.click();
  assert.equal(browser.querySelector("[data-shopify-selection-count]").textContent, 1);
  const links = browser.querySelector("[data-shopify-review-queue]").children;
  assert.equal(links.length, 1);
  assert.equal(links[0].href, "/products/1/shopify/review");
  assert.equal(browser.querySelectorAll("[data-shopify-select]").every(n => n.checked), true);
  browser.querySelector("[data-shopify-clear-selection]").events.click();
  assert.equal(browser.querySelector("[data-shopify-review-queue]").children.length, 0);
  assert.equal(calls.length, 1);
});

test("collection selection queues only its supported single-product reviews", async () => {
  const {browser, calls} = await render();
  all(browser).find(n => n.textContent === "Select for Shopify review").events.click();
  assert.equal(browser.querySelector("[data-shopify-selection-count]").textContent, 1);
  assert.equal(calls.length, 1);
});

test("Products navigation renders saved evidence without starting status checks", async () => {
  const {browser, calls} = await render(true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/edit_products");
  assert.equal(calls.some(([url]) => url === "/api/shopify/status"), false);
});

test("explicit manual refresh still updates desktop and mobile", async () => {
  const {browser, calls} = await render(true);
  await browser.querySelector("[data-shopify-refresh-visible]").events.click();
  assert.equal(calls[1][0], "/api/shopify/status");
  assert.equal(calls[1][1].headers["X-CSRFToken"], "fictional_csrf");
  assert.deepEqual(JSON.parse(calls[1][1].body), {ids: [1], force: true});
  assert.equal(all(browser).filter(n => n.textContent === "Up to Date").length, 2);
  assert.equal(all(browser).filter(n => n.className === "catalogue-pill is-shopify-success").length, 2);
  assert.match(browser.querySelector("[data-shopify-refresh-progress]").textContent, /No store writes/);
});

test("compact seven-column rows keep technical evidence collapsed and intent once", async () => {
  const {browser,calls} = await render(false,"update","variable");
  const nodes=all(browser);
  const row=nodes.find(n=>n.className==="product-row");
  assert.equal(row.children.length,7);
  assert.equal(nodes.filter(n=>n.textContent==="Shopify Draft · Intended Draft").length,2);
  assert.equal(nodes.filter(n=>(n.className || "").startsWith("catalogue-pill is-intent-")).length,0);
  for (const status of nodes.filter(n=>n.className==="shopify-list-status")) {
    assert.equal(status.children.length,3);
    assert.equal(status.children[2].tag,"details");
    assert.equal(status.children[2].attrs.open,undefined);
  }
  assert.equal(nodes.filter(n=>n.textContent==="4 variations").length,2);
  assert.equal(calls.length,1);
});

test("explicit visible refresh includes rows beyond the first bounded batch", async () => {
  const {browser, calls} = await render(true, "update", "simple", 30);
  await browser.querySelector("[data-shopify-refresh-visible]").events.click();
  const requests = calls.filter(([url]) => url === "/api/shopify/status").map(([, options]) => JSON.parse(options.body));
  assert.equal(requests.length, 2);
  assert.equal(requests[0].ids.length, 25);
  assert.deepEqual(requests[1].ids, [26, 27, 28, 29, 30]);
  assert.ok(requests.every(request => request.force === true));
});

for (const [state,meaning] of Object.entries({no_change:"success",update:"warning",local_changes:"info",create:"info",processing:"info",attention:"warning",recovery_required:"danger",unknown:"neutral",unavailable:"neutral"})) {
  test(`status ${state} uses shared ${meaning} semantics and readable text`,async()=>{
    const {browser}=await render(false,state);
    const nodes=all(browser);
    const badges=nodes.filter(n=>n.className===`catalogue-pill is-shopify-${meaning}`);
    assert.equal(badges.length,2);
    assert.ok(badges.every(n=>n.textContent));
    if(state==="processing") assert.equal(nodes.filter(n=>n.className==="shopify-activity" && n.attrs["aria-hidden"]==="true").length,2);
  });
}
