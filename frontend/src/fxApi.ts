import { ConnectionError } from "./api";
import type { ZoneStatus } from "./tourBuilder";

export type FxPage = "chase" | "rainbow" | "circle";
export type FxComponent = "beam" | "ring";
export type FxKind = "chase" | "rainbow" | "two_colour" | "circle" | "figure8";
export interface EffectParams {
  beats: number;
  size: number;
  spread: number;
  softness: number;
  timing: "beats" | "seconds";
  seconds: number;
  shape: "legacy" | "sine" | "triangle" | "square" | "saw_up" | "saw_down" | "one_by_one";
  maximum: number;
  selection: "all" | "odd" | "even" | "alternating";
  order: string[];
  reverse: boolean;
  mirror: "none" | "x" | "y" | "xy";
  mirror_alternate: boolean;
  colour_mode: "base" | "palette";
  palette: string[];
  transition: "step" | "smooth";
  target_ids: string[];
  zone_ids: string[];
  travel: number;
  hold: number;
  pattern: "current" | "none" | "circle" | "figure8";
}
export interface FxDefinition {
  name: string;
  kind: FxKind;
  params: EffectParams;
}
export interface TourTarget {
  id: string; name: string;
  x: number | null; y: number | null; z: number | null;
  reachable: boolean; pattern_ok: boolean; note: string;
}
export interface FxSlots {
  slots: FxDefinition[];
  factory: FxDefinition[];
  fixtures: { id: string; name: string }[];
  targets: { id: string; name: string }[];
  running: { slot: number; definition: FxDefinition; active: boolean; combo_name: string | null } | null;
  warnings: string[];
  preview: boolean;
  component: FxComponent;
  tour_targets?: TourTarget[];
  tour_zones?: ZoneStatus[];
  current_aims?: { head: string; name: string; target_id: string | null; zone_id: string | null; include_zones: string[] }[];
  anchors?: string[];
  room?: [number, number, number] | null;
}
export const FX_FAMILIES: Record<FxPage, { name: string; kinds: FxKind[] }> = {
  chase: { name: "Dimmer", kinds: ["chase"] },
  rainbow: { name: "Colour", kinds: ["rainbow", "two_colour"] },
  circle: { name: "Move", kinds: ["circle", "figure8"] },
};
export const isFxPage = (page: string): page is FxPage =>
  page === "chase" || page === "rainbow" || page === "circle";

export interface ComboDefinition {
  name: string;
  effects: FxDefinition[];
}
export interface ComboSlots {
  slots: ComboDefinition[];
  families: Partial<Record<FxPage, FxSlots>>;
  running: { slot: number; definition: ComboDefinition; active: boolean; modified: boolean } | null;
  preview: boolean;
  component: FxComponent;
}

async function request<T = FxSlots>(path: string, method = "GET", definition?: FxDefinition | ComboDefinition): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      ...(definition ? { body: JSON.stringify(definition) } : {}),
    });
  } catch {
    throw new ConnectionError();
  }
  if (!response.ok) {
    let detail = `FX request failed (${response.status}). Check the selected strip and retry.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map(
        (issue: { loc?: (string | number)[]; msg?: string }) =>
          `${issue.loc?.filter((part) => part !== "body").join(".") || "Input"}: ${issue.msg ?? "invalid value"}`,
      ).join("; ");
    } catch {
      // Retain the actionable HTTP error when there is no JSON detail.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
const base = (track: number, page: FxPage) => `/api/fx/${track}/${page}`;
export const getFxSlots = (track: number, page: FxPage, component: FxComponent = "beam") => request(`${base(track, page)}/slots?component=${component}`);
export const saveFxSlot = (track: number, page: FxPage, row: number, definition: FxDefinition, apply = false, component: FxComponent = "beam") =>
  request(`${base(track, page)}/slots/${row}${apply ? "/apply" : ""}?component=${component}`, apply ? "POST" : "PUT", definition);
export const resetFxSlot = (track: number, page: FxPage, row: number, component: FxComponent = "beam") =>
  request(`${base(track, page)}/slots/${row}?component=${component}`, "DELETE");
export const startFxSlot = (track: number, page: FxPage, row: number, component: FxComponent = "beam") =>
  request(`${base(track, page)}/slots/${row}/start?component=${component}`, "POST");
export const storeCurrentFx = (track: number, page: FxPage, row: number, component: FxComponent = "beam") =>
  request(`${base(track, page)}/slots/${row}/store?component=${component}`, "POST");
/** Permission only: adds the zone to this strip's included areas. Heads do not move. */
export const includeMoveZone = (track: number, zoneId: string) =>
  request(`/api/fx/${track}/circle/include-zone/${encodeURIComponent(zoneId)}`, "POST");
export const stopFxFamily = (track: number, page: FxPage, component: FxComponent = "beam") => request(`${base(track, page)}/stop?component=${component}`, "POST");

const comboBase = (track: number) => `/api/fx/combo/${track}`;
export const getComboSlots = (track: number, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/slots?component=${component}`);
export const saveComboSlot = (track: number, row: number, definition: ComboDefinition, apply: boolean, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/slots/${row}${apply ? "/apply" : ""}?component=${component}`, apply ? "POST" : "PUT", definition);
export const startComboSlot = (track: number, row: number, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/slots/${row}/start?component=${component}`, "POST");
export const stopCombo = (track: number, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/stop?component=${component}`, "POST");
export const storeCombo = (track: number, row: number, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/slots/${row}/store?component=${component}`, "POST");
export const resetCombo = (track: number, row: number, component: FxComponent) =>
  request<ComboSlots>(`${comboBase(track)}/slots/${row}?component=${component}`, "DELETE");
export const selectComboKnobs = (track: number, page: FxPage) =>
  request<{page: FxPage}>(`${comboBase(track)}/knobs/${page}`, "POST");
