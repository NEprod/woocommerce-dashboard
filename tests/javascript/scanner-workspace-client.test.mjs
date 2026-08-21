import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

globalThis.window = globalThis;
globalThis.location = new URL("https://catalogue.test/scanner");
globalThis.document = {querySelector: () => null};
vm.runInThisContext(
  fs.readFileSync(new URL("../../app/static/assets/js/scanner-workspace.js", import.meta.url), "utf8"),
  {filename: "scanner-workspace.js"}
);

const client = globalThis.ScannerWorkspaceClient;

test("canonical destination is tied to the returned operation identifier", () => {
  assert.equal(
    client.operationDestination(
      {ok: true, operation_id: "abc123", destination: "/operations/abc123"},
      "https://catalogue.test/scanner"
    ),
    "https://catalogue.test/operations/abc123"
  );
});

test("cross-origin, malformed, mismatched, and trailing-slash destinations are rejected", () => {
  for (const destination of [
    "https://malicious.invalid/operations/abc123",
    "/operations/not-abc123",
    "/operations/abc123/",
    "not a URL",
  ]) {
    assert.throws(
      () => client.operationDestination({ok: true, operation_id: "abc123", destination}, "https://catalogue.test/scanner"),
      /open the operation directly/i
    );
  }
});

test("non-JSON, authentication, conflict, and malformed success responses are controlled", async () => {
  const response = (status, contentType, body, redirected = false) => ({
    status,
    ok: status >= 200 && status < 300,
    redirected,
    headers: {get: () => contentType},
    json: async () => body,
  });
  await assert.rejects(client.readStartResponse(response(401, "text/html", {})), /session has expired/i);
  await assert.rejects(client.readStartResponse(response(409, "application/json", {message: "Another operation is active"})), /another operation is active/i);
  await assert.rejects(client.readStartResponse(response(500, "text/html", {})), /could not be started/i);
  await assert.rejects(client.readStartResponse(response(202, "application/json", {ok: true})), /valid operation identity/i);
});
