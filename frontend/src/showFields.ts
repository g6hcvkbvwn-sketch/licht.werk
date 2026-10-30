export const RING_RGB = ["red", "green", "blue"];
export const RING_SECTIONS = [1, 2, 3, 4, 5, 6];
export const RING_SEGMENT_ATTRIBUTES = RING_SECTIONS.flatMap((section) => RING_RGB.map((channel) => `ring_${section}_${channel}`));
export const RING_FAMILY = "Ring colour / intensity";
export const isRingSegment = (attribute: string) => RING_SEGMENT_ATTRIBUTES.includes(attribute);

// Uniform writes replace only the edited channels; unrelated partial scene values survive.
export function writeRingValues(values: Record<string, number>, edits: Record<string, number>) {
  const next = { ...values };
  for (const channel of RING_RGB) {
    if (`ring_${channel}` in edits) {
      for (const section of RING_SECTIONS) delete next[`ring_${section}_${channel}`];
    }
  }
  return { ...next, ...edits };
}

export const ATTRIBUTE_FAMILIES: Record<string, string[]> = {
  Intensity: ["intensity"],
  "Main colour / white": ["red", "green", "blue", "white", "amber", "uv"],
  [RING_FAMILY]: ["ring_red", "ring_green", "ring_blue", ...RING_SEGMENT_ATTRIBUTES, "ring_white", "ring_intensity", "ring_strobe"],
  Strobe: ["strobe"],
  Position: ["pan", "tilt"],
  "Movement speed": ["speed"],
  "Haze (opt-in)": ["pump", "fan", "haze", "haze_pump", "haze_fan"],
};

export const BEATS = [0.5, 1, 2, 4, 8, 16, 32];
export const isHaze = (attribute: string) => ATTRIBUTE_FAMILIES["Haze (opt-in)"].includes(attribute);
export const newShowId = () => crypto.randomUUID();

export interface EditorScope {
  key: string;
  id: string;
  name: string;
  group: boolean;
  members: FixtureInfo[];
}

export function editorScopes(fixtures: FixtureInfo[], groups: GroupInfo[], strips: Apc40Strip[]): EditorScope[] {
  const result: EditorScope[] = [];
  const add = (id: string, group: boolean, name: string) => {
    const members = group ? fixtures.filter((f) => groups.find((g) => g.id === id)?.fixtures.includes(f.id))
      : fixtures.filter((f) => f.id === id);
    if (members.length && !result.some((s) => s.key === `${group ? "group" : "fixture"}:${id}`)) {
      result.push({ key: `${group ? "group" : "fixture"}:${id}`, id, name, group, members });
    }
  };
  [...strips].sort((a, b) => a.track - b.track).forEach((s) => { if (s.target) add(s.target, s.group, s.label); });
  fixtures.forEach((f) => add(f.id, false, f.name));
  groups.forEach((g) => add(g.id, true, g.name));
  return result;
}

export const scopeAttributes = (scope: EditorScope) => [...new Set(scope.members.flatMap((f) => f.attributes))];
export function editorFamilies(scopes: EditorScope[]): Record<string, string[]> {
  const known = Object.values(ATTRIBUTE_FAMILIES).flat();
  const extra = [...new Set(scopes.flatMap(scopeAttributes))].filter((a) => !known.includes(a));
  return { ...ATTRIBUTE_FAMILIES, ...(extra.length ? { "Other controls": extra } : {}) };
}
export const scopeValues = (scene: Scene, scope: EditorScope) => (scope.group ? scene.groups : scene.fixtures)[scope.id] ?? {};
export function familyPresent(scene: Scene, scope: EditorScope, fields: string[]) {
  return fields.some((a) => a in scopeValues(scene, scope))
    || (!scope.group && fields.includes("pan") && !!scene.positions?.[scope.id]);
}

export function familyConflicts(scene: Scene, scope: EditorScope, fields: string[], scopes: EditorScope[]) {
  return scopes.filter((other) => other.key !== scope.key && (scope.group || other.group)
    && scope.members.some((f) => other.members.some((m) => m.id === f.id))
    && familyPresent(scene, other, fields));
}

// Explicitly choosing a scope keeps existing effective values when moving group fields to individuals.
export function resolveFamily(scene: Scene, scope: EditorScope, fields: string[], scopes: EditorScope[]): Scene {
  const next = structuredClone(scene);
  const conflicts = familyConflicts(scene, scope, fields, scopes);
  for (const other of conflicts) {
    if (other.group && !scope.group) {
      for (const member of other.members) {
        const values = next.fixtures[member.id] ?? {};
        const inherited = Object.fromEntries(Object.entries(scopeValues(next, other)).filter(([a]) => fields.includes(a) && member.attributes.includes(a)
          && !(next.positions?.[member.id] && (a === "pan" || a === "tilt"))));
        next.fixtures[member.id] = writeRingValues(inherited, values);
      }
    }
    const entries = other.group ? next.groups : next.fixtures;
    for (const attr of fields) delete entries[other.id]?.[attr];
    if (fields.includes("pan") && !other.group && next.positions) delete next.positions[other.id];
  }
  return next;
}

export const attributeLabel = (attr: string) => ({
  intensity: "Intensity", ring_intensity: "Ring intensity", pan: "Pan travel", tilt: "Tilt travel",
  speed: "Movement speed", strobe: "Strobe rate", ring_strobe: "Ring strobe rate",
  red: "Red", green: "Green", blue: "Blue", white: "White", amber: "Amber", uv: "UV",
  pump: "Pump", fan: "Fan", ring_red: "Ring red", ring_green: "Ring green",
  ring_blue: "Ring blue", ring_white: "Ring white",
} as Record<string, string>)[attr] ?? attr.replace(/^ring_(\d)_/, "Ring section $1 ").replaceAll("_", " ");

export function zonesOverlap(a: Zone, b: Zone): boolean {
  if (a.surface !== b.surface) return false;
  const axes = a.surface === "floor" || a.surface === "ceiling" ? ["x", "y"]
    : a.surface === "booth_wall" || a.surface === "far_wall" ? ["x", "z"] : ["y", "z"];
  return axes.every((axis, i) => {
    const first = a.corner[axis], second = b.corner[axis];
    const sizeA = i === 0 ? a.length : a.width, sizeB = i === 0 ? b.length : b.width;
    return first < second + sizeB && second < first + sizeA;
  });
}
import type { Apc40Strip, FixtureInfo, GroupInfo, Scene, Zone } from "./api";
