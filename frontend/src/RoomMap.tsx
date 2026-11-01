import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { getRoomMap, placeOnMap, removeFromMap, type MapFixture, type RoomMapState } from "./api";
import { boostedLevel, shortName } from "./monitorDisplay";

/**
 * Room seen from above: the DJ booth at the bottom, the far side at the top, x to the DJ's right.
 * Live and preview use separate monitors; positioning and colour are polled from the engine.
 */

const POLL_MS = 400;
/** Room-y (m) of the point both booth spots aim at, slightly in front of the booth wall centre. */
const SPOT_TARGET_Y = 0.35;
const DOT_M = 0.22; // dot radius in metres (scaled with the room for readability)
// The screen blinks at most this fast (easier on the eyes); the label shows the real rate.
const MAX_BLINK_HZ = 8;

function strobeStyle(hz: number | null): { className?: string; style?: React.CSSProperties } {
  if (!hz) return {};
  return { className: "map-strobe", style: { animationDuration: `${1 / Math.min(hz, MAX_BLINK_HZ)}s` } };
}

function hzText(hz: number | null): string {
  return hz ? `${hz < 10 ? hz.toFixed(1) : Math.round(hz)} Hz` : "off";
}

function level(rgb: [number, number, number] | null): number {
  return rgb ? Math.min(Math.max(...rgb, 0), 1) : 0;
}

function css(rgb: [number, number, number] | null): string {
  if (!rgb) return "#2a2a32";
  const actual = level(rgb);
  if (actual <= 0.001) return "rgb(0, 0, 0)";
  const gain = boostedLevel(actual) / actual;
  const [r, g, b] = rgb.map((c) => Math.round(Math.min(Math.max(c * gain, 0), 1) * 255));
  return `rgb(${r}, ${g}, ${b})`;
}

function pct(rgb: [number, number, number] | null): string {
  return `${Math.round(level(rgb) * 100)} %`;
}

export default function RoomMap({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<RoomMapState | null>(null);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const next = await getRoomMap();
        if (!stop) setState(next);
      } catch {
        // The app header shows lost connections; keep the last state.
      }
    };
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!state) return null;
  return <div className="room-monitors">
    <RoomMonitor compact={compact} mode="live" state={state} onState={setState} />
    <RoomMonitor compact={compact} mode="preview" state={state} onState={setState} />
  </div>;
}

function RoomMonitor({ compact, mode, state, onState, big = false }: {
  compact: boolean; mode: "live" | "preview"; state: RoomMapState;
  onState: (state: RoomMapState) => void; big?: boolean;
}) {
  const [enlarged, setEnlarged] = useState(false);
  const [editing, setEditing] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const inactive = mode === "preview" && !state.preview_active;

  const toRoom = useCallback(
    (e: { clientX: number; clientY: number }): { x: number; y: number } | null => {
      const element = svg.current;
      const matrix = element?.getScreenCTM();
      if (!element || !matrix || !state) return null;
      const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse());
      const x = Math.min(Math.max(point.x, 0), state.width);
      const y = Math.min(Math.max(state.depth - point.y, 0), state.depth);
      return { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
    },
    [state],
  );

  const save = async (action: () => Promise<RoomMapState>) => {
    try {
      onState(await action());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const placed = state.fixtures.filter((f) => f.x !== null && f.y !== null);
  const unplaced = state.fixtures.filter((f) => f.x === null || f.y === null);
  const radius = DOT_M * Math.max(1, Math.min(state.width, state.depth) / 4);
  const pad = radius * 1.6;

  const position = (f: MapFixture) =>
    drag && drag.id === f.id ? { x: drag.x, y: drag.y } : { x: f.x as number, y: f.y as number };

  const roomClick = (e: React.MouseEvent) => {
    if (!editing || !chosen || drag) return;
    const point = toRoom(e);
    if (point) void save(() => placeOnMap(chosen, point.x, point.y));
  };

  const dotDown = (f: MapFixture) => (e: ReactPointerEvent) => {
    if (!editing) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setChosen(f.id);
    setDrag({ id: f.id, ...position(f) });
  };
  const dotMove = (e: ReactPointerEvent) => {
    if (!drag) return;
    const point = toRoom(e);
    if (point) setDrag({ id: drag.id, ...point });
  };
  const dotUp = () => {
    if (!drag) return;
    const { id, x, y } = drag;
    setDrag(null);
    void save(() => placeOnMap(id, x, y));
  };

  const haze = (f: MapFixture) => {
    const command = mode === "preview" ? f.preview_haze : f.live_haze;
    if (inactive || !command || command.pump <= 0) return null;
    const { x, y } = position(f);
    const cy = state.depth - y;
    const angle = Math.atan2(state.depth / 2 - cy, state.width / 2 - x) * 180 / Math.PI;
    const distance = Math.min(state.width, state.depth) * 0.45;
    const fan = Math.min(1, Math.max(0, command.fan));
    const duration = 4 / Math.max(fan, 0.1);
    return <g key={`haze-${f.id}`} transform={`translate(${x} ${cy}) rotate(${angle})`} className="map-haze"
      opacity={Math.min(1, Math.max(0, command.pump))}>
      <title>{`${f.name} · commanded haze: pump ${Math.round(command.pump * 100)}%, fan ${Math.round(command.fan * 100)}%. Not a physical fog simulation.`}</title>
      {Array.from({ length: 12 }, (_, i) => <circle key={i} r={radius * 1.8}
        className="map-haze-bubble" style={{
          "--haze-duration": `${duration}s`,
          "--haze-delay": `${-duration * i / 12}s`,
          "--haze-distance": `${distance}px`,
          "--haze-spread": `${((i % 3) - 1) * distance * 0.3}px`,
          "--haze-play-state": fan > 0 ? "running" : "paused",
        } as CSSProperties} />)}
    </g>;
  };

  const dot = (f: MapFixture) => {
    const { x, y } = position(f);
    const cy = state.depth - y;
    const projected = mode === "preview";
    const rgb = inactive ? null : projected ? f.preview : f.live;
    const command = inactive ? null : projected ? f.preview_haze : f.live_haze;
    const outputText = inactive ? "inactive — enter Preview to display staged output" : command
      ? `commanded haze: pump ${Math.round(command.pump * 100)}%, fan ${Math.round(command.fan * 100)}%`
      : `output ${pct(rgb)} (map is brightened for visibility)`;
    const live = css(rgb);
    const hz = inactive ? null : projected ? f.preview_strobe_hz : f.live_strobe_hz;
    const ring = projected ? f.preview_ring : f.live_ring;
    const ringHz = inactive ? null : projected ? f.preview_ring_strobe_hz : f.live_ring_strobe_hz;
    const ringRadius = radius * 1.35;
    const ringPoint = (angle: number) => {
      const radians = angle * Math.PI / 180;
      return `${x + Math.cos(radians) * ringRadius},${cy + Math.sin(radians) * ringRadius}`;
    };
    const rectangular = f.shape === "rectangle";
    const spot = f.shape === "spot";
    // Both booth spots aim at one fixed point just inside the centre of the DJ booth wall.
    const spotAngle = spot
      ? Math.atan2(state.depth - SPOT_TARGET_Y - cy, state.width / 2 - x) * 180 / Math.PI : 0;
    // Fixed emission shapes: spots face the DJ booth (bottom of the map), the wash faces the room centre.
    const emission = (() => {
      if (spot) {
        // Drawn along +x in the rotated spot frame: narrow at the outlet (short end), widening outward.
        const start = radius * 0.9;
        const end = start + radius * 2.2;
        return `${x + start},${cy - radius * 0.2} ${x + start},${cy + radius * 0.2} ${x + end},${cy + radius * 0.9} ${x + end},${cy - radius * 0.9}`;
      }
      if (rectangular) {
        const a = Math.atan2(state.depth / 2 - cy, state.width / 2 - x);
        const at = (along: number, across: number) =>
          `${x + Math.cos(a) * along - Math.sin(a) * across},${cy + Math.sin(a) * along + Math.cos(a) * across}`;
        const near = radius * 0.5;
        const far = near + radius * 1.8;
        return [at(near, -radius * 0.45), at(near, radius * 0.45), at(far, radius * 1.3), at(far, -radius * 1.3)].join(" ");
      }
      return null;
    })();
    const beam = (end: [number, number] | null | undefined, colour: string) => {
      if (!end) return null;
      const dx = end[0] - x;
      const dy = (state.depth - end[1]) - cy;
      const length = Math.hypot(dx, dy);
      if (length < radius * 0.3)
        return <circle cx={x} cy={cy} r={radius * 1.4} fill="none" stroke={colour} strokeWidth={0.04} />;
      const halfWidth = radius * 0.7;
      const ex = x + dx;
      const ey = cy + dy;
      const wx = (-dy / length) * halfWidth;
      const wy = (dx / length) * halfWidth;
      return <polygon points={`${x},${cy} ${ex + wx},${ey + wy} ${ex - wx},${ey - wy}`}
        fill={colour} fillOpacity={0.35}
        stroke={colour} strokeOpacity={0.8}
        strokeWidth={0.02} />;
    };
    return (
      <g
        key={f.id}
        className={[ "map-dot", editing ? "editable" : "", chosen === f.id && editing ? "chosen" : "", f.enabled ? "" : "disabled" ].join(" ")}
        onPointerDown={dotDown(f)}
        onPointerMove={dotMove}
        onPointerUp={dotUp}
        onPointerCancel={dotUp}
        onClick={(e) => e.stopPropagation()}
      >
        <title>{`${f.name} (${x.toFixed(2)} m, ${y.toFixed(2)} m)${f.enabled ? "" : " — disabled"}\n${mode} ${outputText}${f.guard_reason ? `\n${f.guard_reason}` : ""}`}</title>
        {!inactive && beam(projected ? f.preview_beam : f.live_beam, live)}
        <g transform={spot ? `rotate(${spotAngle} ${x} ${cy})` : undefined}>
        {emission && <polygon points={emission} fill={live} fillOpacity={0.4} stroke={live} strokeOpacity={0.8}
          strokeWidth={0.02} className="map-emission" />}
        {spot ? (
          <rect x={x - radius * 0.9} y={cy - radius * 0.5}
            width={radius * 1.8} height={radius} fill={live} {...strobeStyle(hz)} />
        ) : rectangular ? (
          <rect x={x - radius * 1.4} y={cy - radius * 0.45}
            width={radius * 2.8} height={radius * 0.9} fill={live} {...strobeStyle(hz)} />
        ) : (
          <circle cx={x} cy={cy} r={radius} fill={live} {...strobeStyle(hz)} />
        )}
        </g>
        {hz && (
          <text x={x} y={cy - radius * (ring.length ? 1.9 : 1.25)} className="map-strobe-label" fontSize={radius * 0.85}>
            ⚡ {hzText(hz)}
          </text>
        )}
        {spot
          ? <rect x={x - radius * 0.9} y={cy - radius * 0.5} width={radius * 1.8} height={radius} className="map-ring"
              transform={`rotate(${spotAngle} ${x} ${cy})`} />
          : rectangular
          ? <rect x={x - radius * 1.4} y={cy - radius * 0.45} width={radius * 2.8} height={radius * 0.9} className="map-ring" />
          : <circle cx={x} cy={cy} r={radius} className="map-ring" />}
        {ring.length > 0 && <g role="img" aria-label={`${f.name} ${mode} ring output`}
          className="map-ring-output">
          <title>{`Ring sections shown schematically, not physical orientation. Ring strobe: ${hzText(ringHz)}.`}</title>
          <g {...strobeStyle(ringHz)}>
            {ring.map((colour, i) => {
              const output = inactive ? null : colour;
              const start = -90 + i * 360 / ring.length + 4;
              const end = -90 + (i + 1) * 360 / ring.length - 4;
              const title = `${ring.length === 1 ? "Uniform ring" : `Section ${i + 1}`}: ${inactive ? "inactive" : pct(output)}`;
              return ring.length === 1
                ? <circle key={i} cx={x} cy={cy} r={ringRadius} fill="none"
                    stroke={level(output) > 0.001 ? css(output) : "#2a2a32"} strokeWidth={radius * 0.32}>
                    <title>{title}</title>
                  </circle>
                : <path key={i} d={`M ${ringPoint(start)} A ${ringRadius} ${ringRadius} 0 0 1 ${ringPoint(end)}`}
                    fill="none" stroke={level(output) > 0.001 ? css(output) : "#2a2a32"} strokeWidth={radius * 0.32}>
                    <title>{title}</title>
                  </path>;
            })}
          </g>
        </g>}
        <text x={x} y={cy + radius * 2.1} className="map-label" fontSize={radius * 0.95}>
          <title>{f.name}</title>
          {shortName(f)}
        </text>
        {level(rgb) > 0.001 && (
          <text x={x} y={cy + radius * 3.1} className="map-level" fontSize={radius * 0.8}>
            {pct(rgb)}
          </text>
        )}
      </g>
    );
  };

  return (
    <>
    {enlarged && !big && (
      <div className="dialog-backdrop" onClick={() => setEnlarged(false)}>
        <div className="monitor-enlarged" role="dialog" aria-label={`${mode} monitor, enlarged`}
          onClick={(e) => e.stopPropagation()}>
          <RoomMonitor compact={false} mode={mode} state={state} onState={onState} big />
          <p className="muted hint">Commanded output only; ring sections are schematic, not sensor feedback.</p>
          <button type="button" onClick={() => setEnlarged(false)}>Close</button>
        </div>
      </div>
    )}
    <div className={compact ? "room-map compact" : "room-map"}>
      <div className="room-map-head">
        <span className="panel-title">{mode === "live" ? "LIVE" : "PREVIEW"}</span>
        {inactive && <span className="map-legend">Inactive</span>}
        {!big && <button type="button" className="map-edit map-zoom" style={{ marginLeft: "auto" }}
          aria-label={`Enlarge ${mode} monitor`} title="Enlarge monitor" onClick={() => setEnlarged(true)}>⤢</button>}
        {mode === "live" && !big && <button
          className={editing ? "map-edit active" : "map-edit"}
          aria-label={editing ? "Finish editing fixture positions" : "Edit fixture positions"}
          title={editing ? "Finish editing fixture positions" : "Edit fixture positions"}
          onClick={() => {
            setEditing(!editing);
            setChosen(null);
          }}
        >
          {editing ? "DONE" : "EDIT"}
        </button>}
      </div>
      <svg
        ref={svg}
        className={editing && chosen ? "room-map-svg placing" : "room-map-svg"}
        viewBox={`${-pad} ${-pad} ${state.width + 2 * pad} ${state.depth + 2 * pad}`}
        preserveAspectRatio="xMidYMid meet"
        onClick={roomClick}
        role="img"
        aria-label={`${mode === "live" ? "Live" : "Preview"} room monitor${inactive ? " — inactive" : ""}`}
      >
        <rect x={0} y={0} width={state.width} height={state.depth} className="map-room" />
        <rect x={0} y={state.depth - radius * 0.5} width={state.width} height={radius * 0.5} className="map-booth" />
        <text x={state.width / 2} y={state.depth - radius * 0.8} className="map-caption" fontSize={radius * 0.9}>
          DJ booth
        </text>
        <text x={state.width / 2} y={radius * 1.2} className="map-caption" fontSize={radius * 0.9}>
          far side
        </text>
        {placed.map(haze)}
        {placed.map(dot)}
      </svg>
      {!inactive && state.fixtures.some((f) => f.setup_override) && (
        <p className="error">CALIBRATION OVERRIDE active - area blanking bypassed. Disable in Positions after calibration.</p>
      )}
      {!state.measured && <p className="muted hint">Room size not measured yet: placeholder 5 × 4 m.</p>}
      {editing && (
        <div className="map-tray">
          <p className="muted hint">
            {chosen
              ? "Click in the room to place it, or drag a dot."
              : "Choose a fixture, then click in the room. Dots can be dragged."}
          </p>
          <div className="map-chips">
            {state.fixtures.map((f) => (
              <button
                key={f.id}
                className={[
                  "map-chip",
                  chosen === f.id ? "chosen" : "",
                  unplaced.includes(f) ? "unplaced" : "",
                ].join(" ")}
                onClick={() => setChosen(chosen === f.id ? null : f.id)}
              >
                {f.name}
              </button>
            ))}
          </div>
          {chosen && placed.some((f) => f.id === chosen) && (
            <button className="secondary" onClick={() => void save(() => removeFromMap(chosen))}>
              Remove from map
            </button>
          )}
        </div>
      )}
      {!editing && unplaced.length > 0 && (
        <details className="muted hint">
          <summary>{unplaced.length} not on the map</summary>
          {unplaced.map((f) => f.name).join(", ")}
        </details>
      )}
      {error && (
        <div className="banner error small-banner" onClick={() => setError(null)}>
          {error}
        </div>
      )}
    </div>
    </>
  );
}
