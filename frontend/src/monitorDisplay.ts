import type { MapFixture } from "./api";

// Display-only: retain hue and lift dim colours without changing output or readouts.
export function boostedLevel(actual: number): number {
  if (actual <= 0.001) return 0;
  return 0.3 + 0.7 * Math.log1p(100 * Math.min(actual, 1)) / Math.log1p(100);
}

export function shortName(fixture: Pick<MapFixture, "id" | "name">): string {
  if (fixture.id === "led-bar-1") return "W";
  if (fixture.id === "hazer-1") return "H";
  const head = /^moving-head-(\d+)$/.exec(fixture.id);
  if (head) return `M${head[1]}`;
  const spot = /^dj-spot-(\d+)$/.exec(fixture.id);
  if (spot) return `B${spot[1]}`;
  return fixture.name.length > 12 ? `${fixture.name.slice(0, 11)}…` : fixture.name;
}
