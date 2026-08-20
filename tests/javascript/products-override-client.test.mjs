import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

globalThis.window = globalThis;
vm.runInThisContext(
  fs.readFileSync(new URL("../../app/static/assets/js/override-client.js", import.meta.url), "utf8"),
  { filename: "override-client.js" }
);

const client = globalThis.ProductsOverrideClient;

function response({status = 200, redirected = false, contentType = "application/json", body = {ok: true}} = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    redirected,
    url: redirected ? "https://catalogue.test/login?next=%2Fproducts" : "https://catalogue.test/api/override/create/7",
    headers: {get: (name) => name.toLowerCase() === "content-type" ? contentType : null},
    json: async () => {
      if (body instanceof Error) throw body;
      return body;
    },
  };
}

test("route URL replacement preserves the route shape without adding a slash", () => {
  assert.equal(
    client.routeUrl("/api/override/create/0", 17, "https://catalogue.test/products"),
    "https://catalogue.test/api/override/create/17"
  );
  assert.equal(
    client.routeUrl("/api/override/folders/0?rel=Cards%20%26%20Gifts", 17, "https://catalogue.test/products"),
    "https://catalogue.test/api/override/folders/17?rel=Cards%20%26%20Gifts"
  );
});

test("route parameters are encoded and unsupported identifiers are rejected", () => {
  assert.equal(
    client.routeUrl("/api/example/0/details", "item / ü", "https://catalogue.test/products"),
    "https://catalogue.test/api/example/item%20%2F%20%C3%BC/details"
  );
  assert.throws(() => client.routeUrl("/api/example", 7, "https://catalogue.test/products"), /could not be prepared/i);
});

test("successful and idempotent JSON responses remain valid", async () => {
  assert.deepEqual(await client.readJson(response({body: {ok: true, created: true}})), {ok: true, created: true});
  assert.deepEqual(await client.readJson(response({body: {ok: true, created: false}})), {ok: true, created: false});
});

test("editor destinations must match the expected same-origin product route", () => {
  assert.equal(
    client.editorDestination(
      "/edit_products/17/edit/override",
      "/edit_products/0/edit/override",
      17,
      "https://catalogue.test/products"
    ),
    "https://catalogue.test/edit_products/17/edit/override"
  );
  assert.throws(
    () => client.editorDestination("https://malicious.invalid/", "/edit_products/0/edit/override", 17, "https://catalogue.test/products"),
    /valid editor destination/i
  );
  assert.throws(
    () => client.editorDestination("not a destination", "/edit_products/0/edit/override", 17, "not a base URL"),
    /valid editor destination/i
  );
});

test("authentication redirects and HTML are mapped to a clear session message", async () => {
  await assert.rejects(
    client.readJson(response({redirected: true, contentType: "text/html", body: new Error("not json")})),
    /session has expired/i
  );
  await assert.rejects(
    client.readJson(response({status: 401, contentType: "text/html", body: new Error("not json")})),
    /session has expired/i
  );
});

test("404 and 409 responses are mapped before attempting HTML or JSON parsing", async () => {
  await assert.rejects(client.readJson(response({status: 404, contentType: "text/html"})), /no longer available/i);
  await assert.rejects(client.readJson(response({status: 409, body: {error: "catalogue_operation_active"}})), /another catalogue operation/i);
});

test("malformed and non-JSON responses never expose parser exceptions", async () => {
  await assert.rejects(client.readJson(response({contentType: "application/json", body: new SyntaxError("raw parser detail")})), /unexpected response/i);
  await assert.rejects(client.readJson(response({contentType: "text/html"})), /unexpected response/i);
});

test("network failures are mapped without exposing the browser exception", async () => {
  await assert.rejects(
    client.requestJson(async () => { throw new TypeError("The string did not match the expected pattern."); }, "/api/override/create/7"),
    /could not reach the server/i
  );
});
