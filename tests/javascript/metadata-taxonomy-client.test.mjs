import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise the actual editor functions without introducing a DOM dependency.
const source = fs.readFileSync(new URL("../../app/static/assets/js/metadata-editor.js", import.meta.url), "utf8");
function extract(name, next) {
  return source.slice(source.indexOf(`  function ${name}(`), source.indexOf(`  function ${next}(`));
}
function draft({explicit = false, names = [], shared = true, authored = {}} = {}) {
  const context = vm.createContext({
    authored, isShared: shared, explicitVariation: explicit, driverNames: names,
    knownFields: new Set(["attributes", "variation_attributes", "categories"]),
    guided: {
      querySelectorAll: () => [],
      querySelector: () => ({checked: explicit}),
    },
    overrideEnabled: () => shared,
    attributeValues: () => ({}), modifierValues: () => ({}),
    listValues: (field) => field === "variation_attributes" ? names : [],
  });
  vm.runInContext(extract("prune", "guidedDocument") + extract("guidedDocument", "clearErrors"), context);
  return JSON.parse(JSON.stringify(vm.runInContext("guidedDocument()", context)));
}
test("missing field stays legacy; explicit empty is not pruned", () => {
  assert.deepEqual(draft(), {});
  assert.deepEqual(draft({explicit: true}), {attributes: {}, variation_attributes: []});
});
test("explicit drivers preserve order, sparse override does not copy inherited attributes", () => {
  assert.deepEqual(draft({explicit: true, names: ["Size", "Finish"], shared: false}), {variation_attributes: ["Size", "Finish"]});
  assert.deepEqual(draft({shared: false, authored: {future_field: "keep"}}), {future_field: "keep"});
});
test("terms containing commas remain exact during an unrelated guided save", () => {
  const terms = ["Warm, natural", "Gloss"];
  const inputs = [{value: "Finish"}, {value: terms.join(", "), dataset: {originalText: terms.join(", "), terms: JSON.stringify(terms)}}];
  const context = vm.createContext({guided: {querySelectorAll: () => [{querySelectorAll: () => inputs}]}});
  vm.runInContext(extract("attributeValues", "renderModifiers"), context);
  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext("attributeValues()", context))), {Finish: terms});
});
test("returning from Advanced JSON updates sparse override toggles from the actual draft", () => {
  const toggles = [{dataset: {overrideToggle: "attributes"}, checked: false}, {dataset: {overrideToggle: "categories"}, checked: true}];
  const context = vm.createContext({
    authored: {}, structuredClone, explicitVariation: false, resolvedVariation: false, driverNames: [],
    guided: {querySelectorAll: (selector) => selector === "[data-override-toggle]" ? toggles : []},
    valueFor: () => [], renderList: () => {}, renderAttributes: () => {}, renderModifiers: () => {}, setControlState: () => {}, updateCharacterCounts: () => {},
  });
  vm.runInContext(extract("populate", "prune"), context);
  vm.runInContext('populate({attributes: {Unknown: ["Original"]}, variation_attributes: []})', context);
  assert.equal(toggles[0].checked, true);
  assert.equal(toggles[1].checked, false);
  assert.equal(context.explicitVariation, true);
});

function variationContext({explicit = false, resolved = false, drivers = [], simple = false} = {}) {
  const context = vm.createContext({explicitVariation: explicit, resolvedVariation: resolved, driverNames: drivers,
    simpleProduct: () => simple, attributeValues: () => ({Size: ["Small"], Occasion: ["Birthday"]})});
  vm.runInContext(extract("changeVariation", "renderVariationState"), context);
  return context;
}
test("unchecking a legacy row explicitly adopts remaining drivers, not all attributes again", () => {
  const ctx = variationContext();
  vm.runInContext('changeVariation("Occasion", false)', ctx);
  assert.equal(ctx.explicitVariation, true);
  assert.deepEqual(Array.from(ctx.driverNames), ["Size"]);
  vm.runInContext('changeVariation("Size", false)', ctx);
  assert.deepEqual(Array.from(ctx.driverNames), []);
  assert.deepEqual(draft({explicit: ctx.explicitVariation, names: Array.from(ctx.driverNames)}), {attributes: {}, variation_attributes: []});
});
test("checking adds one driver without duplicates and inherited designation remains sparse until changed", () => {
  const ctx = variationContext({resolved: true, drivers: ["Size"]});
  assert.equal(ctx.explicitVariation, false);
  vm.runInContext('changeVariation("Occasion", true); changeVariation("Occasion", true)', ctx);
  assert.deepEqual(Array.from(ctx.driverNames), ["Size", "Occasion"]);
  assert.equal(ctx.explicitVariation, true);
});
test("Simple informational opt-in retains an empty driver array", () => {
  const ctx = variationContext({simple: true});
  vm.runInContext('changeVariation("Occasion", false)', ctx);
  assert.deepEqual(Array.from(ctx.driverNames), []);
});
test("registry recognition is scoped, preserves legacy and never matches an ambiguous definition", () => {
  const context = vm.createContext({});
  vm.runInContext(extract("registryMatch", "recognition"), context);
  context.rows = [{value: "Finish", aliases: ["Surface"]}];
  assert.equal(vm.runInContext('registryMatch("Surface", rows).value', context), "Finish");
  assert.equal(vm.runInContext('registryMatch("Legacy", rows)', context), null);
  context.rows.push({value: "Other", aliases: ["Surface"]});
  assert.equal(vm.runInContext('registryMatch("Surface", rows)', context), null);
});
