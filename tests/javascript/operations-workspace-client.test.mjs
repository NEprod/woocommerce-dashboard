import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import fs from "node:fs";

const source = fs.readFileSync("app/static/assets/js/operations-workspace.js", "utf8");
const context = {URLSearchParams};
context.globalThis = context;
vm.runInNewContext(source, context);
const client = context.OperationsWorkspaceClient;

test("live endpoint URLs encode operation identity and retain cursor query", () => {
  const query = new URLSearchParams({after: "42"});
  assert.equal(client.liveUrl("operation / ü", "logs", query), "/api/operations/operation%20%2F%20%C3%BC/logs?after=42");
});

test("log cursor advances monotonically and empty polls do not reset it", () => {
  assert.equal(client.nextCursor(8, {next_cursor: 12}), 12);
  assert.equal(client.nextCursor(12, {next_cursor: 8}), 12);
  assert.equal(client.nextCursor(12, {}), 12);
});

test("automatic polling pauses only after three consecutive failures", () => {
  assert.equal(client.shouldPause(1), false);
  assert.equal(client.shouldPause(2), false);
  assert.equal(client.shouldPause(3), true);
});
