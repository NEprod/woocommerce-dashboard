import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(new URL("../../app/static/assets/js/relationships-workspace.js", import.meta.url), "utf8"));
const client = globalThis.RelationshipsWorkspaceClient;

test("selection is ordered, unique, and bounded", () => {
  assert.deepEqual(client.normaliseSelection([" B ", "A", "B", "C"], 2), ["B", "A"]);
});

test("mutual directed count excludes self links", () => {
  assert.equal(client.directedCount(4), 12);
  assert.equal(client.directedCount(1), 0);
});

test("safe JSON handles success and controlled failures", async () => {
  const response = (status, type, body) => ({status, ok: status < 400, redirected: false, headers: {get: () => type}, json: async () => body});
  assert.deepEqual(await client.safeJson(response(200, "application/json", {ok: true, value: 1})), {ok: true, value: 1});
  await assert.rejects(client.safeJson(response(409, "application/json", {ok: false, error: "Another operation is active."})), /operation is active/i);
  await assert.rejects(client.safeJson(response(200, "text/html", "login")), /unexpected response/i);
});
