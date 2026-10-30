import { ConnectionError } from "./api";

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
  travel: number;
  hold: number;
  pattern: "current" | "none" | "circle" | "figure8";
}
export interface FxDefinition {
  name: string;
  kind: FxKind;
  params: EffectParams;
}
export interface FxSlots {
  slots: FxDefinition[];
  factory: FxDefinition[];
  fixtures: { id: string; name: string }[];
  targets: { id: string; name: string }[];
  running: { slot: number; definition: FxDefinition; active: boolean } | null;
  warnings: string[];
  preview: boolean;
  component: FxComponent;
}
export const FX_FAMILIES: Record<FxPage, { name: string; kinds: FxKind[] }> = {
  chase: { name: "Dimmer", kinds: ["chase"] },
  rainbow: { name: "Colour", kinds: ["rainbow", "two_colour"] },
  circle: { name: "Move", kinds: ["circle", "figure8"] },
};
export const isFxPage = (page: string): page is FxPage =>
  page === "chase" || page === "rainbow" || page === "circle";

async function request(path: string, method = "GET", definition?: FxDefinition): Promise<FxSlots> {
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
  return response.json() as Promise<FxSlots>;
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
export const stopFxFamily = (track: number, page: FxPage, component: FxComponent = "beam") => request(`${base(track, page)}/stop?component=${component}`, "POST");
