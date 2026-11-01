export interface FixtureInfo {
  id: string;
  name: string;
  manufacturer: string;
  model: string;
  fixture_type: string;
  mode: string;
  universe: number;
  start_address: number;
  channel_count: number;
  enabled: boolean;
  groups: string[];
  attributes: string[];
  state: Record<string, number>;
}

export interface Status {
  output_mode: string;
  physical_output_enabled: boolean;
  blackout_active: boolean;
  universes: number[];
  real_output_available: boolean;
  real_output_universes: number[];
  output_error: string | null;
  setup_overrides: string[];
  master: number;
  active_scene: string | null;
  transition_active: boolean;
  preview_active: boolean;
}

export interface Scene {
  id: string;
  name: string;
  fade_seconds: number;
  fade_timing?: "seconds" | "beats";
  fade_beats?: number;
  groups: Record<string, Record<string, number>>;
  fixtures: Record<string, Record<string, number>>;
  positions?: Record<string, ScenePosition>;
}

export interface ScenePosition {
  target_id?: string | null;
  zone_id?: string | null;
  spot?: { u: number; v: number } | null;
  included_zone_ids: string[];
  excluded_zone_ids: string[];
}

export interface SequenceStep {
  scene_id: string;
  fade?: number | null;
  hold: number;
}

export interface Sequence {
  id: string;
  name: string;
  timing: "seconds" | "beats";
  loop: boolean;
  steps: SequenceStep[];
  live_controls?: Record<string, string[]>;
}

export interface SequenceRuntime {
  id: string;
  name: string;
  status: "running" | "paused" | "stopped" | "completed";
  step_index: number;
  scene_name?: string;
  phase: "fade" | "hold";
  progress: number;
  remaining?: number;
  remaining_seconds?: number;
  timing: "seconds" | "beats";
  loop: boolean;
  error?: string | null;
}

export interface ShowState {
  globals: Record<string, string[]>;
  locks: Record<string, string[]>;
  sequence: SequenceRuntime | null;
  live_sequence: SequenceRuntime | null;
}

export const getShow = () => request<ShowState>("/api/show");
export const setGlobals = (fixtures: Record<string, string[]>) =>
  request<ShowState>("/api/show/globals", { method: "PUT", body: JSON.stringify({ fixtures }) });
export const releaseControl = (fixtureIds?: string[], attributes?: string[]) =>
  request<ShowState>("/api/show/release", {
    method: "POST", body: JSON.stringify({ fixture_ids: fixtureIds, attributes }),
  });
export const saveScene = (scene: Scene, create = false) => {
  const { id, ...body } = scene;
  return request<Scene>(create ? "/api/scenes" : `/api/scenes/${encodeURIComponent(id)}`, {
    method: create ? "POST" : "PUT", body: JSON.stringify(body),
  });
};
export const getSequences = () => request<Sequence[]>("/api/sequences");
export const saveSequence = (sequence: Sequence, create = false) => {
  const { id, ...body } = sequence;
  return request<Sequence>(create ? "/api/sequences" : `/api/sequences/${encodeURIComponent(id)}`, {
    method: create ? "POST" : "PUT", body: JSON.stringify(body),
  });
};
export const deleteSequence = (id: string) =>
  request(`/api/sequences/${encodeURIComponent(id)}`, { method: "DELETE" });
export const sequenceCommand = (
  id: string, command: "start" | "pause" | "resume" | "stop" | "restart" | "apply", preview?: boolean,
) => request(`/api/sequences/${encodeURIComponent(id)}/${command}`, {
  method: "POST", body: JSON.stringify({ preview }),
});

/** The backend could not be reached at all (as opposed to rejecting a request). */
export class ConnectionError extends Error {
  constructor() {
    super("Cannot reach the LichtWerk backend. The lights keep their last output while it is away.");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    throw new ConnectionError();
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((issue: { loc?: (string | number)[]; msg?: string }) =>
        `${issue.loc?.filter((part) => part !== "body").join(".") || "Input"}: ${issue.msg ?? "invalid value"}`,
      ).join("; ");
    } catch {
      // keep the generic message
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const getStatus = () => request<Status>("/api/status");

export const setBlackout = (active: boolean) =>
  request("/api/output/blackout", { method: "POST", body: JSON.stringify({ active }) });

export const setArmed = (armed: boolean, confirmUniverses: number[]) =>
  request("/api/output/arm", {
    method: "POST",
    body: JSON.stringify({ armed, confirm_universes: confirmUniverses }),
  });

export const getFixtures = () => request<FixtureInfo[]>("/api/fixtures");

/** Logical values are 0.0-1.0; only the given attributes change. */
export const setFixtureState = (fixtureId: string, values: Record<string, number>) =>
  request(`/api/fixtures/${encodeURIComponent(fixtureId)}/state`, {
    method: "POST",
    body: JSON.stringify({ values }),
  });

export async function getUniverse(universe: number): Promise<number[]> {
  const body = await request<{ channels: number[] }>(`/api/universes/${universe}`);
  return body.channels;
}

/** true = start preview mode; false = Go (send the preview live at once). */
export const setPreview = (active: boolean) =>
  request<{ active: boolean }>("/api/preview", { method: "POST", body: JSON.stringify({ active }) });

export interface MapFixture {
  id: string;
  name: string;
  enabled: boolean;
  x: number | null; // metres, x = to the DJ's right
  y: number | null; // metres, y = away from the booth
  live: [number, number, number] | null; // RGB 0-1 actually output
  preview: [number, number, number] | null; // RGB 0-1 being edited
  live_strobe_hz: number | null; // native strobe on the output (approximate), null = off
  preview_strobe_hz: number | null; // same for the state being edited
  live_beam?: [number, number] | null;
  preview_beam?: [number, number] | null;
  shape?: "circle" | "rectangle" | "spot";
  guard_reason?: string | null;
  setup_override?: boolean;
  live_haze?: { pump: number; fan: number } | null;
  preview_haze?: { pump: number; fan: number } | null;
  live_ring: [number, number, number][];
  preview_ring: [number, number, number][];
  live_ring_strobe_hz: number | null;
  preview_ring_strobe_hz: number | null;
}

export interface RoomMapState {
  width: number;
  depth: number;
  measured: boolean;
  preview_active: boolean;
  fixtures: MapFixture[];
}

export const getRoomMap = () => request<RoomMapState>("/api/map");

export const getCurrentSurfacePoint = (headId: string) =>
  request<Point>(`/api/heads/${encodeURIComponent(headId)}/surface-point`);

export const placeOnMap = (fixtureId: string, x: number, y: number) =>
  request<RoomMapState>(`/api/map/fixtures/${encodeURIComponent(fixtureId)}`, {
    method: "PUT",
    body: JSON.stringify({ x, y }),
  });

export const removeFromMap = (fixtureId: string) =>
  request<RoomMapState>(`/api/map/fixtures/${encodeURIComponent(fixtureId)}`, { method: "DELETE" });

export const setMaster = (level: number) =>
  request("/api/output/master", { method: "POST", body: JSON.stringify({ level }) });

export const getScenes = () => request<Scene[]>("/api/scenes");

/** Stores the current live values of all enabled fixtures as a new scene. */
export const captureScene = (name: string, fadeSeconds: number) =>
  request<Scene>("/api/scenes/capture", {
    method: "POST",
    body: JSON.stringify({ name, fade_seconds: fadeSeconds }),
  });

export const recallScene = (sceneId: string) =>
  request<Scene>(`/api/scenes/${encodeURIComponent(sceneId)}/recall`, { method: "POST" });

export const deleteScene = (sceneId: string) =>
  request(`/api/scenes/${encodeURIComponent(sceneId)}`, { method: "DELETE" });

export interface Point {
  x: number;
  y: number;
  z: number;
}

export interface Target {
  id: string;
  name: string;
  x: number | null;
  y: number | null;
  z: number | null;
}

export type Surface = "floor" | "ceiling" | "booth_wall" | "far_wall" | "left_wall" | "right_wall";

/** Length/width axes of each surface (see project.md 5.4). */
export const SURFACE_AXES: Record<Surface, { label: string; length: "x" | "y"; width: "y" | "z" }> = {
  floor: { label: "Floor", length: "x", width: "y" },
  ceiling: { label: "Ceiling", length: "x", width: "y" },
  booth_wall: { label: "Booth wall", length: "x", width: "z" },
  far_wall: { label: "Far wall", length: "x", width: "z" },
  left_wall: { label: "Left wall", length: "y", width: "z" },
  right_wall: { label: "Right wall", length: "y", width: "z" },
};

export interface Zone {
  id: string;
  name: string;
  surface: Surface;
  mode: "include" | "exclude";
  /** The two in-plane room coordinates of the corner closest to the origin. */
  corner: Record<string, number>;
  length: number;
  width: number;
}

export interface HeadAssignment {
  aim_target: string | null;
  aim_zone: string | null;
  include_zones: string[];
  exclude_zones: string[];
}

export interface RoomDimensions {
  size_x: number | null;
  size_y: number | null;
  size_z: number | null;
}

export interface RoomDocument {
  room: RoomDimensions;
  targets: Target[];
  zones: Zone[];
  assignments: Record<string, HeadAssignment>;
  strip_assignments: Record<string, HeadAssignment>;
}

export interface HeadCalibration {
  pan_sign: 1 | -1;
  tilt_sign: 1 | -1;
  pan_zero_azimuth_deg: number | null;
  tilt_axis_deg: number | null;
}

export interface HeadSetup {
  orientation: "ceiling" | "floor";
  position: Point | null;
  calibration: HeadCalibration;
  limits: Record<string, number>;
  rest: { pan: number; tilt: number } | null;
}

export interface HeadInfo {
  id: string;
  name: string;
  setup: HeadSetup;
  calibrated: boolean;
  pan: number;
  tilt: number;
  pan_degrees: number;
  tilt_degrees: number;
  floor_point: Point | null;
  assignment: HeadAssignment;
  spot_u: number;
  spot_v: number;
  guard_active: boolean;
  guard_reason: string | null;
  setup_override: boolean;
  beam_hit: Point | null;
  beam_blocked_by: string | null;
}

export interface HeadsResult {
  heads: HeadInfo[];
  notes: string[];
}

export interface CalibrationFit {
  calibration: HeadCalibration;
  residuals_deg: number[];
  rms_deg: number;
  max_deg: number;
  acceptable: boolean;
}

export const getRoom = () => request<RoomDocument>("/api/room");

export const putZone = (zone: Zone) =>
  request<Zone>(`/api/zones/${encodeURIComponent(zone.id)}`, { method: "PUT", body: JSON.stringify(zone) });

export const deleteZone = (zoneId: string) =>
  request(`/api/zones/${encodeURIComponent(zoneId)}`, { method: "DELETE" });

export const deleteTarget = (targetId: string) =>
  request(`/api/targets/${encodeURIComponent(targetId)}`, { method: "DELETE" });

/** `target` is a head id or a group name (e.g. "moving-heads"). */
export const putAssignment = (target: string, assignment: HeadAssignment, fadeSeconds: number) =>
  request<HeadsResult>(`/api/positions/${encodeURIComponent(target)}/assignment`, {
    method: "PUT",
    body: JSON.stringify({ ...assignment, fade_seconds: fadeSeconds }),
  });

export const setSpot = (target: string, u: number, v: number, fadeSeconds = 0) =>
  request<HeadsResult>(`/api/positions/${encodeURIComponent(target)}/spot`, {
    method: "POST",
    body: JSON.stringify({ u, v, fade_seconds: fadeSeconds }),
  });

export const getHeads = () => request<HeadInfo[]>("/api/heads");

export const aimHead = (headId: string, target: string, fadeSeconds: number) =>
  request<HeadInfo>(`/api/heads/${headId}/aim`, {
    method: "POST",
    body: JSON.stringify({ target, fade_seconds: fadeSeconds }),
  });

export const headToRest = (headId: string, fadeSeconds: number) =>
  request<HeadInfo>(`/api/heads/${headId}/rest`, {
    method: "POST",
    body: JSON.stringify({ fade_seconds: fadeSeconds }),
  });

export const fitCalibration = (
  headId: string,
  observations: { pan: number; tilt: number; target: string }[],
) =>
  request<CalibrationFit>(`/api/heads/${headId}/calibration/fit`, {
    method: "POST",
    body: JSON.stringify({ observations }),
  });

export const saveHead = (headId: string, setup: HeadSetup) =>
  request<HeadInfo>(`/api/heads/${headId}`, { method: "PUT", body: JSON.stringify(setup) });

export const putRoom = (document: RoomDocument) =>
  request<RoomDocument>("/api/room", { method: "PUT", body: JSON.stringify(document) });

export interface Diagnostics {
  output_mode: string;
  armed_universes: number[];
  simulated_universes: number[];
  patch: {
    id: string;
    name: string;
    universe: number;
    first_address: number;
    last_address: number;
    mode: string;
    enabled: boolean;
  }[];
  warnings: string[];
  recent_errors: { time: string; message: string }[];
}

export const getDiagnostics = () => request<Diagnostics>("/api/diagnostics");

export interface MidiKey {
  kind: "note" | "cc";
  channel: number;
  number: number;
}

export type ActionType =
  | "master"
  | "intensity"
  | "hue"
  | "saturation"
  | "white"
  | "strobe"
  | "scene"
  | "blackout"
  | "flash"
  | "strobe_hold"
  | "preview";

export interface MidiAction {
  type: ActionType;
  target: string | null;
}

export interface MidiBinding {
  key: MidiKey;
  action: MidiAction;
  relative?: boolean;
}

export interface MidiStatus {
  enabled: boolean;
  device_filter: string | null;
  connected: boolean;
  connected_device: string | null;
  available_devices: string[];
  last_error: string | null;
  apc40_alternate_mode: string[];
  apc40_error: string | null;
  learning: MidiAction | null;
  bindings: MidiBinding[];
  monitor: { kind: string; channel: number; number: number; value: number }[];
}

export const getMidi = () => request<MidiStatus>("/api/midi");

export const learnMidi = (action: MidiAction) =>
  request<MidiStatus>("/api/midi/learn", { method: "POST", body: JSON.stringify(action) });

export const cancelLearnMidi = () => request<MidiStatus>("/api/midi/learn", { method: "DELETE" });

export const putMidiBindings = (bindings: MidiStatus["bindings"]) =>
  request<MidiStatus>("/api/midi/bindings", { method: "PUT", body: JSON.stringify({ bindings }) });

export type LedColour = "off" | "red" | "amber" | "green";

export interface SurfaceControl {
  id: string;
  type: "encoder" | "pot" | "fader" | "button";
  kind: "note" | "cc";
  number: number;
  area: "strip" | "matrix" | "bottom";
  row: string;
  column: number;
  label: string;
  relative: boolean;
  action: MidiAction | null;
  action_label: string;
  target_label: string;
  value: number | null;
  led: LedColour;
}

export interface ControllerState {
  channel: number;
  blackout: boolean;
  master: number;
  active_scene: string | null;
  preview: boolean;
  columns: { column: number; title: string; target: string | null; colour: [number, number, number] | null }[];
  controls: SurfaceControl[];
  targets: { id: string; label: string; actions: ActionType[] }[];
  actions: { type: ActionType; label: string; continuous: boolean; target: "none" | "scene" | "fixture" }[];
}

export const getController = () => request<ControllerState>("/api/controller");

export const sendControllerEvent = (kind: "note" | "cc", number: number, value: number) =>
  request("/api/controller/event", {
    method: "POST",
    body: JSON.stringify({ kind, number, value }),
  });

// --- AKAI APC40 MKII (fixed logic; see backend/lichtwerk/apc40_control.py) ---------------------

/** MIDI numbers from the APC40 MKII protocol (channels 1-based; channel = track for strip buttons). */
export const APC40 = {
  recordArm: 0x30,
  solo: 0x31,
  activator: 0x32,
  trackSelect: 0x33,
  shift: 0x62,
  stopAllClips: 0x51,
  clipStop: 0x34,
  master: 0x50,
  sceneLaunchFirst: 0x52,
  ring: 0x42,
  tapTempo: 0x63,
  sessionRecord: 0x66,
  trackFader: 0x07,
  tempo: 0x0d,
  masterFader: 0x0e,
  deviceKnobFirst: 0x10,
  cueLevel: 0x2f,
  trackKnobFirst: 0x30,
  deviceOnOff: 0x3e,
  deviceLock: 0x3f,
  deviceLeft: 0x3a,
  deviceRight: 0x3b,
  play: 91,
  record: 93,
} as const;

export interface Apc40Strip {
  track: number;
  target: string | null;
  label: string;
  available: boolean;
  group: boolean;
  family: string | null;
  active: boolean;
  intensity: number | null;
  saturation: number | null;
  strobe_on: boolean;
  flash_held: boolean;
  selected: boolean;
  selectable: boolean;
  colours: ([number, number, number] | null)[]; // per fixture, intensity ignored
  fixture_names: string[];
  colour_set: boolean;
  fx_knobs: boolean;
  fx_running: boolean;
  position_selected: boolean;
  hazer: boolean;
  ring_available: boolean;
  ring_selected: boolean;
  ring_colours: [number, number, number][];
}

export interface Apc40EffectType {
  index: number;
  type: string;
  label: string;
  colour: string;
  selected: boolean;
  available: boolean;
  running: boolean;
}

export interface Apc40Pad {
  pad: number;
  note: number;
  colour: string | null;
  label: string | null;
  scene: string | null;
  scene_colour: string | null;
  missing: boolean;
  active: boolean;
  kind: string | null;
  included: boolean;
  excluded: boolean;
  explicitly_excluded: boolean;
  sequence: string | null;
  sequence_loop?: boolean | null;
  fixture_badges: string[];
}

export interface Apc40State {
  fx_palette: boolean;
  colour_fx_palette: string[];
  position_dialog_id: number;
  position_dialog_kind: "target" | "zone" | null;
  position_dialog_item: string | null;
  position_mode: "aim" | "permissions";
  show_dialog: { id: number; kind: "scene" | "sequence" | null; item: string | null; pad: number };
  fx_dialog: { id: number; track: number | null; page: string | null; slot: number; component: "beam" | "ring" | null };
  connected: boolean;
  device: string | null;
  other_connected: boolean;
  strips: Apc40Strip[];
  master: number;
  blackout: boolean;
  preview: boolean;
  strobe_hz: number;
  selected: number | null;
  pad_mode: "scenes" | "sequences" | "colours" | "effects" | "positions";
  fx_track: number | null;
  effect_types: Apc40EffectType[];
  bpm: number | null;
  beat_phase: number | null;
  position_track: number | null;
  shift: boolean;
  error: string | null;
  fx_notice: string | null;
  pads: Apc40Pad[];
  knobs: { knob: number; function: string | null; label: string | null; value: number | null; display: string | null }[];
  scene_colours: Record<string, string>;
  last_event: { kind: string; channel: number; number: number; value: number } | null;
}

export const getApc40 = () => request<Apc40State>("/api/apc40");

export const sendApc40Event = (kind: "note" | "cc", channel: number, number: number, value: number) =>
  request<Apc40State>("/api/apc40/event", {
    method: "POST",
    body: JSON.stringify({ kind, channel, number, value }),
  });

export const setApc40Tempo = (bpm: number) =>
  request<Apc40State>("/api/apc40/tempo", { method: "PUT", body: JSON.stringify({ bpm }) });

export const assignApc40Pad = (pad: number, scene: string, colour: string) =>
  request<Apc40State>(`/api/apc40/pads/${pad}`, {
    method: "PUT",
    body: JSON.stringify({ scene, colour }),
  });

export const clearApc40Pad = (pad: number) =>
  request<Apc40State>(`/api/apc40/pads/${pad}`, { method: "DELETE" });

export const assignApc40SequencePad = (pad: number, sequence: string, colour: string) =>
  request<Apc40State>(`/api/apc40/sequence-pads/${pad}`, {
    method: "PUT", body: JSON.stringify({ sequence, colour }),
  });
export const clearApc40SequencePad = (pad: number) =>
  request<Apc40State>(`/api/apc40/sequence-pads/${pad}`, { method: "DELETE" });

export interface GroupInfo {
  id: string;
  name: string;
  fixtures: string[];
}

export const getGroups = () => request<GroupInfo[]>("/api/groups");

export interface StripAssignmentResult extends HeadsResult {
  owner: string;
  target: string;
  assignment: HeadAssignment;
  active: boolean;
}

export const getStripAssignment = (owner: string, target: string) =>
  request<StripAssignmentResult>(
    `/api/positions/strips/${encodeURIComponent(owner)}?target=${encodeURIComponent(target)}`,
  );
export const putStripAssignment = (
  owner: string, target: string, assignment: HeadAssignment, fadeSeconds: number,
) => request<StripAssignmentResult>(`/api/positions/strips/${encodeURIComponent(owner)}`, {
  method: "PUT",
  body: JSON.stringify({ target, ...assignment, fade_seconds: fadeSeconds }),
});
export const setSetupOverride = (headId: string, enabled: boolean) =>
  request<HeadInfo>(`/api/heads/${encodeURIComponent(headId)}/setup-override`, {
    method: "PUT", body: JSON.stringify({ enabled }),
  });
export const renameFixture = (id: string, name: string) =>
  request<FixtureInfo>(`/api/fixtures/${encodeURIComponent(id)}/name`, {
    method: "PUT", body: JSON.stringify({ name }),
  });
export const renameGroup = (id: string, name: string) =>
  request<GroupInfo>(`/api/groups/${encodeURIComponent(id)}/name`, {
    method: "PUT", body: JSON.stringify({ name }),
  });
export const retryOutput = () =>
  request<Pick<Status, "output_mode" | "physical_output_enabled" | "blackout_active">>(
    "/api/output/retry", { method: "POST" },
  );
