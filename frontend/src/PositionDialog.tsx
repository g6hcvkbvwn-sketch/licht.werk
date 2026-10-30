import { useEffect, useState } from "react";
import {
  deleteTarget, deleteZone, getCurrentSurfacePoint, getHeads, getRoom, putRoom, putZone,
  SURFACE_AXES, type HeadInfo, type RoomDocument, type Surface,
} from "./api";
import DialogFrame from "./DialogFrame";
import { toId } from "./ZoneEditor";

export default function PositionDialog({ onClose, headIds, manage = false, initialKind = "target", initialItem }: {
  onClose: () => void; headIds: string[]; manage?: boolean; initialKind?: "target" | "zone"; initialItem?: string | null;
}) {
  const [kind, setKind] = useState<"target" | "zone">(initialKind);
  const [name, setName] = useState("");
  const [surface, setSurface] = useState<Surface>("floor");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [room, setRoom] = useState<RoomDocument | null>(null);
  const [heads, setHeads] = useState<HeadInfo[]>([]);
  const [head, setHead] = useState("");
  const [editing, setEditing] = useState("");
  const [estimate, setEstimate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const snapshot = (k: string, n: string, sf: string, v: Record<string, string>) => JSON.stringify([k, n, sf, v]);
  const [baseline, setBaseline] = useState(() => snapshot(initialKind, "", "floor", {}));
  const headKey = headIds.join(",");
  useEffect(() => {
    let active = true;
    Promise.all([getRoom(), getHeads()]).then(([document, available]) => {
      if (!active) return;
      setRoom(document);
      if (initialItem) {
        const item = initialKind === "target" ? document.targets.find((t) => t.id === initialItem)
          : document.zones.find((z) => z.id === initialItem);
        if (!item) throw new Error("This target/zone no longer exists. Reopen the menu.");
        setEditing(item.id);
        setName(item.name);
        let loaded: Record<string, string>, loadedSurface: string = "floor";
        if ("surface" in item) {
          setSurface(item.surface);
          loadedSurface = item.surface;
          const axes = SURFACE_AXES[item.surface];
          loaded = { cornerLength: String(item.corner[axes.length]), cornerWidth: String(item.corner[axes.width]),
            length: String(item.length), width: String(item.width) };
        } else loaded = { x: item.x === null ? "" : String(item.x),
          y: item.y === null ? "" : String(item.y), z: item.z === null ? "" : String(item.z) };
        setValues(loaded);
        setBaseline(JSON.stringify([initialKind, item.name, loadedSurface, loaded]));
      }
      const candidates = available.filter((h) => headKey.split(",").includes(h.id));
      setHeads(candidates);
      setHead(candidates.length === 1 ? candidates[0].id : "");
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [headKey, initialItem, initialKind]);
  const choose = (id: string, nextKind = kind) => {
    setEditing(id);
    setConfirmDelete(false);
    setEstimate(false);
    setError(null);
    const item = nextKind === "target" ? room?.targets.find((t) => t.id === id)
      : room?.zones.find((z) => z.id === id);
    const nextName = item?.name ?? "";
    let nextSurface: Surface = surface, nextValues: Record<string, string> = {};
    if (item && "surface" in item) {
      nextSurface = item.surface;
      const a = SURFACE_AXES[item.surface];
      nextValues = {
        cornerLength: String(item.corner[a.length]), cornerWidth: String(item.corner[a.width]),
        length: String(item.length), width: String(item.width),
      };
    } else if (item && "x" in item) {
      nextValues = { x: item.x === null ? "" : String(item.x),
        y: item.y === null ? "" : String(item.y), z: item.z === null ? "" : String(item.z) };
    }
    setName(nextName); setSurface(nextSurface); setValues(nextValues);
    setBaseline(snapshot(nextKind, nextName, nextSurface, nextValues));
  };
  const storePosition = async () => {
    setSaving(true);
    setError(null);
    try {
      if (!head) throw new Error("Choose which moving head to use.");
      const point = await getCurrentSurfacePoint(head);
      setValues({ x: String(point.x), y: String(point.y), z: String(point.z) });
      setEstimate(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };
  const remove = async () => {
    setSaving(true);
    setError(null);
    try {
      if (kind === "target") await deleteTarget(editing);
      else await deleteZone(editing);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };
  const axes = SURFACE_AXES[surface];
  const fieldProblem = (key: string): string | null => {
    const text = values[key]?.trim();
    const value = text ? Number(text.replace(",", ".")) : NaN;
    if (!Number.isFinite(value)) return "Enter a number in metres.";
    const dimension = key === "length" || key === "width";
    if (dimension ? value <= 0 : value < 0) return dimension ? "Must be greater than 0 m." : "Must be 0 m or greater.";
    const axis = key === "cornerLength" || key === "length" ? axes.length
      : key === "cornerWidth" || key === "width" ? axes.width : key as "x" | "y" | "z";
    const limit = room?.room[`size_${axis}`];
    if (limit !== null && limit !== undefined) {
      const cornerKey = key === "length" ? "cornerLength" : "cornerWidth";
      const corner = dimension ? Number(values[cornerKey]?.replace(",", ".")) : 0;
      if (value + (Number.isFinite(corner) ? corner : 0) > limit)
        return `Must fit within the room's ${axis} dimension (${limit} m).`;
    }
    return null;
  };
  const keys = kind === "target" ? ["x", "y", "z"] : ["cornerLength", "cornerWidth", "length", "width"];
  const invalid = !name.trim() || !toId(name) || keys.some((key) => fieldProblem(key) !== null);
  const field = (key: string, label: string) => {
    const problem = fieldProblem(key);
    return <label key={key}>{label}
      <input required inputMode="decimal" value={values[key] ?? ""}
        aria-invalid={Boolean(problem)}
        onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
      {problem && <small role="status" className="error-text">{problem}</small>}
    </label>;
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const id = editing || toId(name);
      if (!id) throw new Error("Enter a name with at least one letter or digit.");
      const number = (key: string) => {
        const text = values[key]?.trim();
        const result = text ? Number(text.replace(",", ".")) : NaN;
        if (!Number.isFinite(result)) throw new Error(`Enter ${key} in metres.`);
        return result;
      };
      const room = await getRoom();
      if (kind === "target") {
        if (!editing && room.targets.length >= 24) throw new Error("All 24 target pads are occupied.");
        if (!editing && room.targets.some((t) => t.id === id)) throw new Error("A target with this name already exists.");
        const target = {
          id, name: name.trim(), x: number("x"), y: number("y"), z: number("z"),
        };
        if (editing && !room.targets.some((t) => t.id === id)) throw new Error("This target was deleted. Reopen the dialog.");
        await putRoom({ ...room, targets: editing
          ? room.targets.map((t) => t.id === id ? target : t) : [...room.targets, target] });
      } else {
        if (!editing && room.zones.length >= 16) throw new Error("All 16 zone pads are occupied.");
        if (!editing && room.zones.some((z) => z.id === id)) throw new Error("A zone with this name already exists.");
        if (editing && !room.zones.some((z) => z.id === id)) throw new Error("This zone was deleted. Reopen the dialog.");
        await putZone({
          id, name: name.trim(), surface, mode: room.zones.find((z) => z.id === editing)?.mode ?? "exclude",
          corner: { [axes.length]: number("cornerLength"), [axes.width]: number("cornerWidth") },
          length: number("length"), width: number("width"),
        });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const dirty = snapshot(kind, name, surface, values) !== baseline;
  const single = manage && !!initialItem;
  const title = single ? `Edit ${kind} · ${name || initialItem}`
    : manage ? "Edit/delete target or zone" : "Add target/zone";
  return <DialogFrame id="position-dialog" size="compact" dirty={dirty} busy={saving} onClose={onClose}
    title={title} context="Changes apply when you press Save; no head moves."
    status={error ? <p role="alert" className="error-text">{error}</p> : undefined}
    footer={<>
      <button type="submit" form="position-form" disabled={saving || !room || invalid || (manage && !editing)}>{saving ? "Saving..." : "Save"}</button>
      <button type="button" disabled={saving} onClick={() => { if (!dirty || window.confirm("Discard unsaved changes?")) onClose(); }}>Cancel</button>
      {manage && <details className="dialog-more"><summary>More</summary><div className="dialog-more-menu">
        <button type="button" className="danger" disabled={saving || !editing} onClick={() => setConfirmDelete(true)}>Delete…</button>
      </div></details>}
    </>}>
    <div className="position-dialog-body">
      <form id="position-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <fieldset disabled={saving || !room}>
        {!single && <label>Type <select value={kind} onChange={(e) => {
          const next = e.target.value as "target" | "zone";
          setKind(next); choose("", next);
        }}>
          <option value="target">Target</option><option value="zone">Zone</option>
        </select></label>}
        {manage && !single && <label>Existing {kind}
          <select required value={editing} onChange={(e) => choose(e.target.value)}>
            <option value="">Choose {kind}...</option>
            {(kind === "target" ? room?.targets : room?.zones)?.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>}
        <label>Name <input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></label>
        {kind === "target" ? <>
          <label>Moving head for current position
            <select value={head} onChange={(e) => setHead(e.target.value)}>
              <option value="">Choose moving head...</option>
              {heads.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          </label>
          <button type="button" disabled={!head} onClick={() => void storePosition()}>Store current position</button>
          {field("x", "x (m)")}{field("y", "y (m)")}{field("z", "z (m)")}
          <p className="muted">{estimate ? "Calculated surface estimate loaded; review and Save. " : ""}
            Calculated estimate, not a measured position.</p>
          <details className="muted"><summary>How the estimate works</summary>
            Assumes the commanded beam hits the first wall, floor or ceiling of the measured room.
            Wait for the real head to settle. Use manual coordinates for objects inside the room.</details>
          {room && <p className="muted">Valid range: x 0-{room.room.size_x ?? "?"} m, y 0-{room.room.size_y ?? "?"} m, z 0-{room.room.size_z ?? "?"} m.</p>}
        </> : <>
          <label>Surface <select value={surface} onChange={(e) => setSurface(e.target.value as Surface)}>
            {(Object.keys(SURFACE_AXES) as Surface[]).map((s) => <option key={s} value={s}>{SURFACE_AXES[s].label}</option>)}
          </select></label>
          {field("cornerLength", `Corner ${axes.length} (m)`)}
          {field("cornerWidth", `Corner ${axes.width} (m)`)}
          {field("length", `Length (${axes.length}, m)`)}
          {field("width", `Width (${axes.width}, m)`)}
          <p className="muted">{editing ? "Changes affect every strip using this zone." : "New zones do not grant beam permission. Include them using the strip's zone pads."}</p>
        </>}
        </fieldset>
        {confirmDelete && <div role="alert">
          Delete "{name}"? Its assignments will be cleared from all strips. This cannot be undone.
          <button type="button" disabled={saving} onClick={() => void remove()}>Confirm delete</button>
          <button type="button" disabled={saving} onClick={() => setConfirmDelete(false)}>Keep</button>
        </div>}
      </form>
    </div>
  </DialogFrame>;
}
