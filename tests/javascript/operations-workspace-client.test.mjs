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

test("Intake result refresh is requested exactly on the first terminal transition", () => {
  assert.equal(client.shouldRefreshIntakeResult(false, true, true), true);
  assert.equal(client.shouldRefreshIntakeResult(true, true, true), false);
  assert.equal(client.shouldRefreshIntakeResult(false, false, true), false);
  assert.equal(client.shouldRefreshIntakeResult(false, true, false), false);
});

test("result refresh failure has a controlled manual-refresh message", () => {
  assert.match(client.resultRefreshFallback, /Operation completed/);
  assert.match(client.resultRefreshFallback, /Refresh this page/);
});

test("authoritative result fragment request returns terminal action markup", async () => {
  const calls = [];
  const html = await client.requestResultFragment(async (url, options) => {
    calls.push({url, options});
    return {ok: true, text: async () => '<a href="/image-preparation/next/signed">Rename Images</a>'};
  }, "/operations/safe-id/intake-result");
  assert.match(html, /Rename Images/);
  assert.equal(calls[0].url, "/operations/safe-id/intake-result");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.headers.Accept, "text/html");
});

test("non-success and empty result fragments are rejected for controlled fallback", async () => {
  await assert.rejects(
    client.requestResultFragment(async () => ({ok: false, text: async () => "login"}), "/result"),
    /result request failed/,
  );
  await assert.rejects(
    client.requestResultFragment(async () => ({ok: true, text: async () => "  "}), "/result"),
    /empty result response/,
  );
});
