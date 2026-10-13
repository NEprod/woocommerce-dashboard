import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../../app/static/assets/js/taxonomy-workspace.js", import.meta.url), "utf8");
const module = {exports: {}};
vm.runInNewContext(source, {module});
const {scope, descendants} = module.exports;
const parents = new Map([["cards", ""], ["birthday", "cards"], ["boys", "birthday"], ["anniversary", "cards"], ["range", ""]]);
test("parent selection does not silently select descendants", () => {
  assert.deepEqual([...scope(["cards"], parents)], ["cards"]);
});
test("child adds required ancestors, not siblings or storefront collections", () => {
  assert.deepEqual([...scope(["boys"], parents)], ["boys", "birthday", "cards"]);
});
test("explicit parent and descendants is bounded to its branch", () => {
  assert.deepEqual([...descendants("birthday", parents)], ["birthday", "boys"]);
});
test("selection counts deduplicate shared parents", () => {
  assert.equal(scope(["birthday", "anniversary", "range"], parents).size, 4);
});
test("bad parent cycles terminate rather than freezing controls", () => {
  assert.equal(scope(["a"], new Map([["a", "b"], ["b", "a"]])).size, 2);
});
test("mobile selection uses shared styling and starts with no broadened selection", () => {
  const html = fs.readFileSync(new URL("../../app/templates/taxonomy/shopify_panel.html", import.meta.url), "utf8");
  assert.match(html, /name="sources"/);
  assert.doesNotMatch(html, /\bchecked\b/);
  assert.match(html, /data-taxonomy-selection-count/);
  const css = fs.readFileSync(new URL("../../app/static/assets/css/taxonomy.css", import.meta.url), "utf8");
  assert.match(css, /grid-template-columns: 20px minmax\(0,1fr\)/);
  assert.match(css, /env\(safe-area-inset-bottom\)/);
  assert.doesNotMatch(css, /#[a-f0-9]{6}\b/i);
});
