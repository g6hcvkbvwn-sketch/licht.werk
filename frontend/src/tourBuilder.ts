// Pure Move route helpers: no React and no imports, so Node tests can load this file directly.

export interface TourParams {
  target_ids: string[];
  zone_ids?: string[];
  travel: number;
  hold: number;
  pattern: "current" | "none" | "circle" | "figure8";
  timing: "beats" | "seconds";
}
export interface TourStatus {
  id: string; name: string;
  x: number | null; y: number | null; z: number | null;
  reachable: boolean; pattern_ok: boolean; note: string;
}
export interface ZoneStatus {
  id: string; name: string; surface: string;
  box: [number, number, number, number] | null;
  state: "included" | "excluded" | "unreachable";
  note: string;
}
/** Where a map label sits relative to its zone box or target dot; walls label towards the room. */
export type LabelSide = "inside" | "above" | "below" | "left" | "right";
export interface MapZone {
  id: string; name: string; left: number; top: number; width: number; height: number; order: number[];
  labelSide: LabelSide;
}
/** a) current / single point, b) named target tour, c) named zone tour. */
export type MoveMode = "current" | "targets" | "zones";
export interface MapPoint {
  id: string; name: string; left: number; top: number; label: string; order: number[];
  labelSide: "left" | "right" | "below" | "above";
}

// Same orientation as the Live/Preview room monitors: x to the right, y = 0 (DJ booth) at the bottom.
const WALL_LABEL: Record<string, LabelSide> = {
  booth_wall: "above", far_wall: "below", left_wall: "right", right_wall: "left",
};
const pct = (value: number, size: number) => Math.min(100, Math.max(0, (value / size) * 100));

const HOLD_NAMES = { none: "still", circle: "circle", figure8: "figure eight", current: "still" };
const unit = (value: number, timing: TourParams["timing"]) =>
  `${value} ${timing === "beats" ? (value === 1 ? "beat" : "beats") : (value === 1 ? "second" : "seconds")}`;

export function toggleTarget(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id];
}

/** Mode of a saved definition. A single stop with pattern "current" is mode a (anchored point). */
export function moveMode(p: TourParams): MoveMode {
  const zones = p.zone_ids ?? [];
  if (p.pattern === "current" && p.target_ids.length + zones.length <= 1) return "current";
  return zones.length ? "zones" : "targets";
}

export function tourSummary(p: TourParams, kind: "target" | "zone" = "target"): string {
  const count = (kind === "zone" ? p.zone_ids ?? [] : p.target_ids).length;
  if (!count) return `No route yet. Tap ${kind}s in the order the beams should visit them.`;
  const hold = `hold ${unit(p.hold, p.timing)} each (${HOLD_NAMES[p.pattern]}${
    kind === "zone" && p.pattern === "none" ? " at the centre" : ""})`;
  return `Visit ${count} ${kind}${count === 1 ? "" : "s"} · travel ${unit(p.travel, p.timing)} · ${hold} · then repeat.`;
}

export function zoneWarnings(p: TourParams, statuses: ZoneStatus[]): string[] {
  const byId = new Map(statuses.map((s) => [s.id, s]));
  return (p.zone_ids ?? []).flatMap((id) => {
    const status = byId.get(id);
    if (!status || status.state === "included") return [];
    if (status.state === "excluded")
      return [`${status.name} is excluded for at least one head. Tap its tile to include it, or remove it.`];
    return [`${status.name}: ${status.note || status.state} Remove it from the route.`];
  });
}

export function mapZones(statuses: ZoneStatus[], ids: string[], room: [number, number, number]): MapZone[] {
  const [width, depth] = room;
  return statuses.flatMap((s) => {
    if (!s.box) return [];
    const [x0, y0, x1, y1] = s.box;
    // Wall zones are flat when seen from above; give them a visible thickness.
    const w = Math.max(2, pct(x1, width) - pct(x0, width));
    const h = Math.max(2, pct(y1, depth) - pct(y0, depth));
    return [{
      id: s.id, name: s.name,
      left: Math.min(pct(x0, width), 100 - w), top: Math.min(100 - pct(y1, depth), 100 - h), width: w, height: h,
      order: ids.flatMap((id, index) => id === s.id ? [index + 1] : []),
      labelSide: WALL_LABEL[s.surface] ?? "inside",
    }];
  });
}

export function tourWarnings(p: TourParams, statuses: TourStatus[]): string[] {
  const byId = new Map(statuses.map((s) => [s.id, s]));
  const warnings: string[] = [];
  for (const id of p.target_ids) {
    const status = byId.get(id);
    if (!status) continue;
    if (!status.reachable) warnings.push(`${status.name}: ${status.note || "not reachable"} Remove it from the route.`);
    else if (p.pattern !== "none" && !status.pattern_ok)
      warnings.push(`${status.name} is outside the included areas, so it allows only a still hold. `
        + "Set During hold to Still, or remove it from the route.");
  }
  return warnings;
}

export function mapPoints(statuses: TourStatus[], ids: string[], room: [number, number, number]): MapPoint[] {
  const [width, depth] = room;
  const placed = new Map<string, number>();
  const points: MapPoint[] = [];
  for (const s of statuses) {
    if (s.x === null || s.y === null || s.z === null) continue;
    const key = `${s.x.toFixed(2)}:${s.y.toFixed(2)}`;
    const stacked = placed.get(key) ?? 0;
    placed.set(key, stacked + 1);
    const left = pct(s.x, width);
    points.push({
      id: s.id, name: s.name, left,
      // Targets sharing a top-down position are offset so each stays visible; the label names the height.
      top: Math.min(100, 100 - pct(s.y, depth) + stacked * 6),
      label: `${s.name} · ${s.z.toFixed(1)} m`,
      order: ids.flatMap((id, index) => id === s.id ? [index + 1] : []),
      // Default side; placeLabels() moves it away from collisions.
      labelSide: "below",
    });
  }
  return points;
}

type Rect = [number, number, number, number];
const CHAR_PX = 5.8;
const LINE_PX = 11.5;
const DOT: [number, number] = [20, 12];

function textBox(text: string, maxWidth: number): [number, number] {
  const raw = text.length * CHAR_PX + 6;
  return [Math.min(raw, maxWidth), Math.ceil(raw / maxWidth) * LINE_PX];
}

function overlap(a: Rect, b: Rect): number {
  return Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
}

function zoneLabelRect(zone: MapZone, w: number, h: number): Rect {
  const [x, y, zw, zh] = [zone.left * w / 100, zone.top * h / 100, zone.width * w / 100, zone.height * h / 100];
  const [tw, th] = textBox(zone.name, zone.labelSide === "inside" ? Math.max(zw, 40) : 120);
  switch (zone.labelSide) {
    case "above": return [x, y - th, x + tw, y];
    case "below": return [x, y + zh, x + tw, y + zh + th];
    case "right": return [x + zw, y, x + zw + tw, y + th];
    case "left": return [x - tw, y, x, y + th];
    default: return [x + zw / 2 - tw / 2, y, x + zw / 2 + tw / 2, y + th + 2];
  }
}

/**
 * Picks a side for each target label that avoids dots, zone names and earlier labels.
 * Sizes are estimates for a map about `height` pixels tall; good enough for a schematic.
 */
export function placeLabels(points: MapPoint[], zones: MapZone[], room: [number, number, number], height = 380): MapPoint[] {
  const h = height;
  const w = height * room[0] / room[1];
  const dots: Rect[] = points.map((p) => {
    const [x, y] = [p.left * w / 100, p.top * h / 100];
    return [x - DOT[0] / 2, y - DOT[1] / 2, x + DOT[0] / 2, y + DOT[1] / 2];
  });
  const taken: Rect[] = [...dots, ...zones.map((z) => zoneLabelRect(z, w, h))];
  return points.map((point, index) => {
    const [x, y] = [point.left * w / 100, point.top * h / 100];
    const [bw, bh] = textBox(point.name, 90);
    const [sw, sh] = textBox(point.name, 120);
    const candidates: [MapPoint["labelSide"], Rect][] = [
      ["below", [x - bw / 2, y + DOT[1] / 2, x + bw / 2, y + DOT[1] / 2 + bh]],
      ["above", [x - bw / 2, y - DOT[1] / 2 - bh, x + bw / 2, y - DOT[1] / 2]],
      ["right", [x + DOT[0] / 2, y - sh / 2, x + DOT[0] / 2 + sw, y + sh / 2]],
      ["left", [x - DOT[0] / 2 - sw, y - sh / 2, x - DOT[0] / 2, y + sh / 2]],
    ];
    let best = candidates[0];
    let bestScore = Infinity;
    for (const candidate of candidates) {
      const [, r] = candidate;
      const outside = (r[2] - r[0]) * (r[3] - r[1]) - overlap(r, [0, 0, w, h]);
      const score = taken.reduce((sum, other, j) => sum + (j === index ? 0 : overlap(r, other)), 0) + outside * 2;
      if (score < bestScore) [best, bestScore] = [candidate, score];
      if (score === 0) break;
    }
    taken.push(best[1]);
    return { ...point, labelSide: best[0] };
  });
}

/** Mode a (one anchored point): warnings for the picked target or zone. */
export function pointWarnings(p: TourParams & { size: number }, targets: TourStatus[], zones: ZoneStatus[]): string[] {
  const target = targets.find((t) => t.id === p.target_ids[0]);
  if (target && !target.reachable) return [`${target.name}: ${target.note || "not reachable"} Pick another point.`];
  if (target && !target.pattern_ok && p.size > 0)
    return [`${target.name} is outside the included areas, so only a still point is allowed. Set Size to 0, or pick another point.`];
  return zoneWarnings(p, zones);
}
