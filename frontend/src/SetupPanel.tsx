import { useCallback, useEffect, useState } from "react";
import { deleteTarget, getHeads, getRoom, putRoom, saveHead } from "./api";
import type { HeadInfo, RoomDocument } from "./api";
import ZoneEditor, { toId } from "./ZoneEditor";

type Draft = Record<string, string>;

const toNumber = (text: string): number | null => {
  const value = text.trim() === "" ? NaN : Number(text.replace(",", "."));
  return Number.isFinite(value) ? value : null;
};

/** Edit room size, targets, zones and head mounting positions (meters). The backend validates. */
export default function SetupPanel() {
  const [room, setRoom] = useState<RoomDocument | null>(null);
  const [heads, setHeads] = useState<HeadInfo[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [message, setMessage] = useState<string | null>(null);
  const [newTarget, setNewTarget] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, h] = await Promise.all([getRoom(), getHeads()]);
      setRoom(r);
      setHeads(h);
      const next: Draft = {};
      for (const axis of ["x", "y", "z"] as const) {
        const size = r.room[`size_${axis}`];
        next[`room.${axis}`] = size === null ? "" : String(size);
      }
      for (const t of r.targets)
        for (const axis of ["x", "y", "z"] as const)
          next[`t.${t.id}.${axis}`] = t[axis] === null ? "" : String(t[axis]);
      for (const head of h)
        for (const axis of ["x", "y", "z"] as const)
          next[`h.${head.id}.${axis}`] = head.setup.position ? String(head.setup.position[axis]) : "";
      setDraft(next);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => void load(), [load]);

  const field = (key: string) => (
    <input
      inputMode="decimal"
      value={draft[key] ?? ""}
      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
    />
  );

  // Returns null for "all empty", the numbers for "all filled", or throws for a partial entry.
  const triple = (prefix: string, what: string): [number, number, number] | null => {
    const values = (["x", "y", "z"] as const).map((a) => draft[`${prefix}.${a}`] ?? "");
    if (values.every((v) => v.trim() === "")) return null;
    const numbers = values.map(toNumber);
    if (numbers.some((n) => n === null)) throw new Error(`${what}: enter x, y and z as numbers (meters), or leave all three empty.`);
    return numbers as [number, number, number];
  };

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    setMessage(null);
    try {
      await action();
      await load();
      setMessage("Saved.");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // The room document with the edited size and target coordinates.
  const editedRoom = (): RoomDocument => {
    if (!room) throw new Error("Room not loaded.");
    const size = (["x", "y", "z"] as const).map((a) => {
        const text = draft[`room.${a}`] ?? "";
        const n = toNumber(text);
        if (text.trim() !== "" && n === null) throw new Error("Room size must be a number in meters.");
        return n;
      });
    const targets = room.targets.map((t) => {
      const p = triple(`t.${t.id}`, t.name);
      return { ...t, x: p?.[0] ?? null, y: p?.[1] ?? null, z: p?.[2] ?? null };
    });
    return { ...room, room: { size_x: size[0], size_y: size[1], size_z: size[2] }, targets };
  };

  const saveRoom = () => run(() => putRoom(editedRoom()));

  const addTarget = () =>
    run(async () => {
      const name = newTarget.trim();
      const id = toId(name);
      if (!id) throw new Error("Give the target a name with at least one letter or digit.");
      const edited = editedRoom();
      if (edited.targets.some((t) => t.id === id)) throw new Error(`A target called "${name}" already exists.`);
      await putRoom({ ...edited, targets: [...edited.targets, { id, name, x: null, y: null, z: null }] });
      setNewTarget("");
    });

  const removeTarget = (id: string, name: string) => {
    if (window.confirm(`Delete target "${name}"? Heads aiming at it lose their aim.`))
      void run(() => deleteTarget(id));
  };

  const saveMounting = (head: HeadInfo) =>
    run(async () => {
      const p = triple(`h.${head.id}`, head.name);
      await saveHead(head.id, {
        ...head.setup,
        position: p ? { x: p[0], y: p[1], z: p[2] } : null,
      });
    });

  if (!room) return <p className={error ? "error-text" : "muted"}>{error ?? "Loading…"}</p>;

  return (
    <>
      {error && <div className="banner error">{error}</div>}
      {message && <div className="banner ok">{message}</div>}
      <section className="setup">
        <h2>Room and targets</h2>
        <p className="muted">
          Meters. Origin and axes as defined in project.md. Leave a target empty until it is measured.
        </p>
        <div className="coords">
          <span>Room size</span>
          {field("room.x")}
          {field("room.y")}
          {field("room.z")}
          {room.targets.map((t) => (
            <div key={t.id} className="coord-row">
              <span>{t.name}</span>
              {field(`t.${t.id}.x`)}
              {field(`t.${t.id}.y`)}
              {field(`t.${t.id}.z`)}
              <button className="small" title="Delete target" onClick={() => removeTarget(t.id, t.name)}>✕</button>
            </div>
          ))}
        </div>
        <p className="muted">Columns: x · y · z</p>
        <div className="add-target">
          <input
            placeholder="New target name" maxLength={80} value={newTarget}
            onChange={(e) => setNewTarget(e.target.value)}
          />
          <button disabled={!newTarget.trim()} onClick={() => void addTarget()}>Add target</button>
        </div>
        <button className="primary" onClick={() => void saveRoom()}>Save room and targets</button>
      </section>
      <ZoneEditor room={room} run={run} />
      {heads.map((head) => (
        <section key={head.id} className="setup">
          <h2>{head.name} — mounting position</h2>
          <div className="coords">
            <div className="coord-row">
              <span>x · y · z (m)</span>
              {field(`h.${head.id}.x`)}
              {field(`h.${head.id}.y`)}
              {field(`h.${head.id}.z`)}
            </div>
          </div>
          <p className="muted">
            Calibration, limits and rest position are set on the Positions page or in config/heads.json.
          </p>
          <button className="primary" onClick={() => void saveMounting(head)}>Save mounting position</button>
        </section>
      ))}
    </>
  );
}
