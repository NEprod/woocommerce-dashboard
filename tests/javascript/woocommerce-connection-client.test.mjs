import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("app/static/assets/js/woocommerce-connection.js", "utf8");

test("connection form blocks duplicate submissions and updates its accessible state", () => {
  const listeners = {};
  const button = {
    disabled: false,
    textContent: "Test Connection",
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  const form = {
    dataset: {},
    addEventListener(name, handler) { listeners[name] = handler; },
    querySelector() { return button; },
  };
  const context = {
    document: {querySelectorAll() { return [form]; }},
  };
  vm.runInNewContext(source, context);
  let prevented = 0;
  listeners.submit({preventDefault() { prevented += 1; }});
  assert.equal(form.dataset.submitting, "true");
  assert.equal(button.disabled, true);
  assert.equal(button.attributes["aria-disabled"], "true");
  assert.equal(button.textContent, "Testing…");
  listeners.submit({preventDefault() { prevented += 1; }});
  assert.equal(prevented, 1);
});
