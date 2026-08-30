import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

globalThis.window = globalThis;
vm.runInThisContext(
  fs.readFileSync(new URL("../../app/static/assets/js/product-relationships.js", import.meta.url), "utf8"),
  {filename: "product-relationships.js"}
);

const client = globalThis.ProductRelationshipsClient;

function response({status = 200, redirected = false, contentType = "application/json", body = {ok: true}} = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    redirected,
    headers: {get: () => contentType},
    json: async () => {
      if (body instanceof Error) throw body;
      return body;
    },
  };
}

test("safe JSON responses are accepted", async () => {
  assert.deepEqual(await client.readJson(response({body: {ok: true, items: []}})), {ok: true, items: []});
});

test("authentication and operation conflicts have controlled messages", async () => {
  await assert.rejects(client.readJson(response({status: 401, contentType: "text/html"})), /session has expired/i);
  await assert.rejects(client.readJson(response({status: 409, contentType: "text/html"})), /another catalogue operation/i);
  await assert.rejects(client.readJson(response({status: 404, contentType: "text/html"})), /no longer available/i);
});

test("malformed and HTML responses do not expose parser errors", async () => {
  await assert.rejects(client.readJson(response({body: new SyntaxError("raw parser detail")})), /malformed data/i);
  await assert.rejects(client.readJson(response({contentType: "text/html"})), /unexpected response/i);
});

test("selected and ordered SKU helpers preserve explicit UI order", () => {
  const selected = {
    querySelectorAll() {
      return [{dataset: {productSku: "SKU-8"}}, {dataset: {productSku: "SKU-3"}}, {dataset: {}}];
    },
  };
  const ordered = {
    querySelectorAll() {
      return [{dataset: {targetSku: "SKU-4"}}, {dataset: {targetSku: "SKU-2"}}];
    },
  };
  assert.deepEqual(client.selectedSkus(selected), ["SKU-8", "SKU-3"]);
  assert.deepEqual(client.orderedSkus(ordered), ["SKU-4", "SKU-2"]);
});

test("preview copy distinguishes ordinary and mutual operations", () => {
  assert.match(client.previewCopy({selected_count: 3, new_count: 2, already_linked_count: 1, invalid_count: 0}), /3 products selected.*2 new relationships.*1 already linked.*0 invalid/);
  assert.match(client.previewCopy({selected_count: 4, exact_relationship_count: 12, new_count: 9, already_linked_count: 3, invalid_count: 0}), /12 directed relationships/);
});
