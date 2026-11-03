import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/sequenceCountdown.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { sequenceCountdown } = context.exports;

test("integer beat timings count down in whole beats without showing zero early", () => {
  assert.equal(sequenceCountdown(4, "beats", 1), "4 beats");
  assert.equal(sequenceCountdown(3.2, "beats", 1), "4 beats");
  assert.equal(sequenceCountdown(3, "beats", 1), "3 beats");
  assert.equal(sequenceCountdown(0.01, "beats", 1), "1 beats");
});

test("fractional timings count down in half beats, never quarter beats", () => {
  assert.equal(sequenceCountdown(1.5, "beats", 0.5), "1.5 beats");
  assert.equal(sequenceCountdown(1, "beats", 0.5), "1.0 beats");
  assert.equal(sequenceCountdown(0.5, "beats", 0.5), "0.5 beats");
  assert.equal(sequenceCountdown(0.125, "beats", 0.5), "0.5 beats");
  assert.equal(sequenceCountdown(1.00000000001, "beats", 0.5), "1.0 beats");
  assert.equal(sequenceCountdown(-0.2, "beats", 0.5), "0.0 beats");
});

test("seconds remain a decimal countdown", () => {
  assert.equal(sequenceCountdown(1.56, "seconds"), "1.6 s");
});
