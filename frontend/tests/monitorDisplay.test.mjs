import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/monitorDisplay.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { boostedLevel, shortName } = context.exports;

test("club monitor labels are stable despite custom full names", () => {
  for (const [id, expected] of [
    ["led-bar-1", "W"], ["moving-head-1", "M1"], ["moving-head-2", "M2"],
    ["dj-spot-1", "B1"], ["dj-spot-2", "B2"], ["hazer-1", "H"],
  ]) assert.equal(shortName({ id, name: "Custom fixture name" }), expected);
  assert.equal(shortName({ id: "other", name: "Other" }), "Other");
});

test("5–10 percent colours get sufficient display lift while black stays black", () => {
  assert.equal(boostedLevel(0), 0);
  assert.equal(boostedLevel(0.001), 0);
  assert.ok(boostedLevel(0.05) >= 0.55);
  assert.ok(boostedLevel(0.10) >= 0.65);
  assert.equal(boostedLevel(1), 1);
  assert.equal(boostedLevel(2), 1);
  for (let value = 0.01; value <= 1; value += 0.01)
    assert.ok(boostedLevel(value) >= boostedLevel(value - 0.01));
});
