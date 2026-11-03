import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/tourBuilder.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { toggleTarget, tourSummary, tourWarnings, mapPoints, moveMode, zoneWarnings, mapZones, pointWarnings, placeLabels } = context.exports;

const base = { target_ids: [], travel: 4, hold: 8, pattern: "none", timing: "beats" };
const statuses = [
  { id: "bar", name: "Bar", x: 1, y: 2, z: 0, reachable: true, pattern_ok: true, note: "" },
  { id: "booth", name: "Booth", x: 3, y: 2, z: 0, reachable: true, pattern_ok: false, note: "Outside" },
  { id: "dj", name: "DJ wall", x: 3, y: 2, z: 2.5, reachable: true, pattern_ok: true, note: "" },
  { id: "far", name: "Far", x: null, y: null, z: null, reachable: false, pattern_ok: false, note: "No measured coordinates yet." },
];

test("tapping appends in order and tapping again removes", () => {
  let ids = toggleTarget([], "bar");
  ids = toggleTarget(ids, "dj");
  assert.deepEqual([...ids], ["bar", "dj"]);
  assert.deepEqual([...toggleTarget(ids, "bar")], ["dj"]);
});

test("summary reads like the route", () => {
  assert.match(tourSummary(base), /No route yet/);
  assert.equal(tourSummary({ ...base, target_ids: ["bar", "dj", "booth"], pattern: "circle" }),
    "Visit 3 targets · travel 4 beats · hold 8 beats each (circle) · then repeat.");
  assert.equal(tourSummary({ ...base, target_ids: ["bar"], travel: 1, hold: 1, timing: "seconds" }),
    "Visit 1 target · travel 1 second · hold 1 second each (still) · then repeat.");
});

test("warnings are actionable and only for route members", () => {
  assert.equal(tourWarnings({ ...base, target_ids: ["booth"] }, statuses).length, 0);
  const [warning] = tourWarnings({ ...base, target_ids: ["bar", "booth"], pattern: "figure8" }, statuses);
  assert.match(warning, /Booth .*Set During hold to Still, or remove it/);
  assert.match(tourWarnings({ ...base, target_ids: ["far"] }, statuses)[0], /Remove it from the route/);
});

test("schematic map skips unmeasured targets and separates stacked ones by height label", () => {
  const points = mapPoints(statuses, ["dj", "bar", "dj"], [4, 4, 3]);
  assert.deepEqual([...points.map((p) => p.id)], ["bar", "booth", "dj"]);
  const booth = points.find((p) => p.id === "booth");
  const dj = points.find((p) => p.id === "dj");
  assert.equal(booth.left, dj.left);
  assert.notEqual(booth.top, dj.top);
  // Monitor orientation: y = 2 m of a 4 m room sits half-way; larger y would be higher up.
  assert.equal(points.find((p) => p.id === "bar").top, 50);
  assert.equal(mapPoints([{ ...statuses[0], y: 3 }], [], [4, 4, 3])[0].top, 25);
  assert.equal(points.find((p) => p.id === "bar").labelSide, "below");
  assert.equal(dj.label, "DJ wall · 2.5 m");
  assert.deepEqual([...dj.order], [1, 3]);
});

test("target labels move away from neighbouring dots and labels", () => {
  const two = [
    { id: "a", name: "Left upper corner", x: 1, y: 3, z: 0, reachable: true, pattern_ok: true, note: "" },
    { id: "b", name: "Disco ball", x: 1, y: 2.8, z: 1, reachable: true, pattern_ok: true, note: "" },
  ];
  const placed = placeLabels(mapPoints(two, [], [4, 4, 3]), [], [4, 4, 3]);
  // "a" is placed first underneath, right on top of dot "b", so it must move to another side.
  assert.notEqual(placed[0].labelSide, "below");
  const lone = placeLabels(mapPoints([two[0]], [], [4, 4, 3]), [], [4, 4, 3]);
  assert.equal(lone[0].labelSide, "below");
});

const zones = [
  { id: "floor", name: "Floor", surface: "floor", box: [1, 1, 3, 5], state: "included", note: "" },
  { id: "bar", name: "Bar area", surface: "floor", box: [4, 6, 5, 7], state: "excluded", note: "Excluded" },
  { id: "wall", name: "Wall", surface: "back", box: [0, 8, 6, 8], state: "excluded", note: "Excluded for 'Head 1'." },
];

test("mode is derived from the saved definition", () => {
  assert.equal(moveMode({ ...base, pattern: "current" }), "current");
  assert.equal(moveMode({ ...base, pattern: "current", zone_ids: ["floor"] }), "current");
  assert.equal(moveMode({ ...base, pattern: "current", target_ids: ["bar"] }), "current");
  assert.equal(moveMode({ ...base, target_ids: ["bar"] }), "targets"); // legacy one-stop tour
  assert.equal(moveMode({ ...base, zone_ids: ["floor", "bar"], pattern: "circle" }), "zones");
});

test("zone route summary and warnings are actionable", () => {
  assert.match(tourSummary({ ...base, zone_ids: [] }, "zone"), /Tap zones/);
  assert.equal(tourSummary({ ...base, zone_ids: ["floor", "bar"] }, "zone"),
    "Visit 2 zones · travel 4 beats · hold 8 beats each (still at the centre) · then repeat.");
  const warnings = zoneWarnings({ ...base, zone_ids: ["floor", "bar", "wall"] }, zones);
  assert.equal(warnings.length, 2);
  assert.match(warnings[0], /Bar area .*Tap its tile to include it/);
  assert.match(warnings[1], /Wall .*Tap its tile to include it/);
});

test("single point warns only when the pick cannot run", () => {
  const p = { ...base, pattern: "current", size: 0.3 };
  assert.equal(pointWarnings({ ...p, target_ids: ["bar"] }, statuses, zones).length, 0);
  assert.match(pointWarnings({ ...p, target_ids: ["booth"] }, statuses, zones)[0], /Set Size to 0/);
  assert.equal(pointWarnings({ ...p, size: 0, target_ids: ["booth"] }, statuses, zones).length, 0);
  assert.match(pointWarnings({ ...p, target_ids: ["far"] }, statuses, zones)[0], /Pick another point/);
  assert.match(pointWarnings({ ...p, zone_ids: ["bar"] }, statuses, zones)[0], /include/);
});

test("zones map to top-view boxes like the room monitors; flat walls stay visible and label inward", () => {
  const boxes = mapZones(zones, ["bar", "floor"], [6, 8, 4.5]);
  const floor = boxes.find((z) => z.id === "floor");
  assert.ok(Math.abs(floor.left - 100 / 6) < 1e-9 && Math.abs(floor.width - 200 / 6) < 1e-9);
  // y runs upwards: the floor box spans y 1…5 m of 8 m, so its top edge is at (8 - 5) / 8.
  assert.ok(Math.abs(floor.top - 37.5) < 1e-9 && Math.abs(floor.height - 50) < 1e-9);
  assert.equal(floor.labelSide, "inside");
  assert.deepEqual([...floor.order], [2]);
  const wall = boxes.find((z) => z.id === "wall");
  assert.equal(wall.height, 2);
  assert.ok(wall.top >= 0 && wall.top + wall.height <= 100);
  const sides = mapZones([
    { ...zones[2], id: "b", surface: "booth_wall", box: [1, 0, 2, 0] },
    { ...zones[2], id: "f", surface: "far_wall", box: [1, 8, 2, 8] },
    { ...zones[2], id: "l", surface: "left_wall", box: [0, 2, 0, 3] },
  ], [], [6, 8, 4.5]);
  assert.deepEqual(sides.map((z) => [z.id, z.labelSide, z.top]), [["b", "above", 98], ["f", "below", 0], ["l", "right", 62.5]]);
});
