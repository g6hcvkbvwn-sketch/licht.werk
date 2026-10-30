import { useEffect, useState } from "react";
import { SURFACE_AXES, putAssignment, putStripAssignment, setSpot } from "./api";
import type { HeadAssignment, HeadsResult, RoomDocument } from "./api";

interface Props {
  /** Head id or group name. */
  target: string;
  title: string;
  /** Current assignment, or null when group members differ. */
  current: HeadAssignment | null;
  owner?: string;
  active?: boolean;
  /** Current include-zone spot (shown only when an include zone is the aim). */
  spot: [number, number];
  room: RoomDocument;
  fade: number;
  /** `assigned` is true after Apply, false after a spot move. */
  onResult: (result: HeadsResult, assigned: boolean) => void;
  onError: (message: string) => void;
}

const aimValue = (a: HeadAssignment | null) =>
  a === null ? "mixed" : a.aim_target ? `t:${a.aim_target}` : a?.aim_zone ? `z:${a.aim_zone}` : "none";

/** Aim and permitted surface areas; all geometry stays in the backend. */
export default function AimEditor({ target, title, current, owner, active = true, spot, room, fade, onResult, onError }: Props) {
  const [aim, setAim] = useState(aimValue(current));
  const [excludes, setExcludes] = useState<string[]>(current?.exclude_zones ?? []);
  const [includes, setIncludes] = useState<string[]>(current?.include_zones ?? []);
  const [u, setU] = useState(spot[0]);
  const [v, setV] = useState(spot[1]);
  const currentKey = JSON.stringify(current);

  useEffect(() => {
    setAim(aimValue(current));
    setExcludes(current?.exclude_zones ?? []);
    setIncludes(current?.include_zones ?? []);
  }, [currentKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setU(spot[0]);
    setV(spot[1]);
  }, [spot[0], spot[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  const changed = aim !== aimValue(current)
    || JSON.stringify(excludes) !== JSON.stringify(current?.exclude_zones ?? [])
    || JSON.stringify(includes) !== JSON.stringify(current?.include_zones ?? []);
  const savedZone = current?.aim_zone ? room.zones.find((z) => z.id === current.aim_zone) : undefined;

  const apply = () => {
    const assignment: HeadAssignment = {
        aim_target: aim.startsWith("t:") ? aim.slice(2) : null,
        aim_zone: aim.startsWith("z:") ? aim.slice(2) : null,
        include_zones: includes,
        exclude_zones: excludes,
    };
    return (owner ? putStripAssignment(owner, target, assignment, fade) : putAssignment(target, assignment, fade))
      .then((result) => onResult(result, true))
      .catch((e: Error) => onError(e.message));
  };

  const moveSpot = (nextU: number, nextV: number) => {
    setU(nextU);
    setV(nextV);
    setSpot(target, nextU, nextV)
      .then((result) => onResult(result, false))
      .catch((e: Error) => onError(e.message));
  };

  return (
    <div className="aim-editor">
      <div className="panel-title">{title}</div>
      {!active && <p className="muted">Inactive strip: edits save its own settings; activate it on the controller to apply them.</p>}
      <label>
        Aim
        <select value={aim} onChange={(e) => {
          const next = e.target.value;
          setAim(next);
          if (next.startsWith("z:")) {
            const id = next.slice(2);
            setIncludes((list) => list.includes(id) ? list : [...list, id]);
            setExcludes((list) => list.filter((zone) => zone !== id));
          }
        }}>
          <option value="none">Nothing (free pan/tilt)</option>
          {current === null && <option value="mixed" disabled>— heads differ —</option>}
          <optgroup label="Target (point)">
            {room.targets.map((t) => (
              <option key={t.id} value={`t:${t.id}`}>
                {t.name}{t.x === null ? " (not measured)" : ""}
              </option>
            ))}
          </optgroup>
          <optgroup label="Zone centre (also permits this zone)">
            {room.zones.map((z) => (
              <option key={z.id} value={`z:${z.id}`}>{z.name} · {SURFACE_AXES[z.surface].label}</option>
            ))}
          </optgroup>
        </select>
      </label>
      <div className="exclude-list">
        <span className="muted">Area permissions (undefined areas stay dark; exclusion wins):</span>
        {room.zones.length === 0 && <span className="muted">no zones defined (Setup page)</span>}
        {room.zones.map((z) => (
          <label key={z.id}>
            {z.name}
            <select
              value={excludes.includes(z.id) ? "exclude" : includes.includes(z.id) ? "include" : "undefined"}
              onChange={(e) => {
                const next = e.target.value;
                setIncludes((list) => [
                  ...list.filter((id) => id !== z.id), ...(next === "include" ? [z.id] : []),
                ]);
                setExcludes((list) => [
                  ...list.filter((id) => id !== z.id), ...(next === "exclude" ? [z.id] : []),
                ]);
                if (next !== "include" && aim === `z:${z.id}`) setAim("none");
              }}
            >
              <option value="undefined">Not permitted</option>
              <option value="include">Included</option>
              <option value="exclude">Explicitly excluded</option>
            </select>
          </label>
        ))}
      </div>
      <p className="muted">A selected static target is an exception, not permission for its travel path or surrounding movement FX.</p>
      <button className={changed ? "primary" : ""} disabled={!changed || aim === "mixed"} onClick={() => void apply()}>
        Apply
      </button>
      {savedZone && (
        <div className="spot-sliders">
          <label>
            Spot along {SURFACE_AXES[savedZone.surface].length}: {Math.round(u * 100)} %
            <input type="range" min={0} max={1} step={0.01} value={u} disabled={!active}
              onChange={(e) => moveSpot(Number(e.target.value), v)} />
          </label>
          <label>
            Spot along {SURFACE_AXES[savedZone.surface].width}: {Math.round(v * 100)} %
            <input type="range" min={0} max={1} step={0.01} value={v} disabled={!active}
              onChange={(e) => moveSpot(u, Number(e.target.value))} />
          </label>
        </div>
      )}
    </div>
  );
}
