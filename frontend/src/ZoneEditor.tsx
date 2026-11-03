import { useEffect, useState } from "react";
import { SURFACE_AXES, deleteZone, putZone } from "./api";
import type { RoomDocument, Surface, Zone } from "./api";

type ZoneDraft = {
  name: string;
  surface: Surface;
  cornerLength: string;
  cornerWidth: string;
  length: string;
  width: string;
};

const NEW = "__new__";
const EMPTY: ZoneDraft = {
  name: "", surface: "floor", cornerLength: "", cornerWidth: "", length: "", width: "",
};

export const toId = (name: string) =>
  name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

const toDraft = (zone: Zone): ZoneDraft => {
  const axes = SURFACE_AXES[zone.surface];
  return {
    name: zone.name,
    surface: zone.surface,
    cornerLength: String(zone.corner[axes.length]),
    cornerWidth: String(zone.corner[axes.width]),
    length: String(zone.length),
    width: String(zone.width),
  };
};

const number = (text: string, what: string): number => {
  const value = text.trim() === "" ? NaN : Number(text.replace(",", "."));
  if (!Number.isFinite(value)) throw new Error(`${what}: enter a number in meters.`);
  return value;
};

interface Props {
  room: RoomDocument;
  run: (action: () => Promise<unknown>) => Promise<void>;
}

/** Create, edit and delete zones: rectangles on the floor, ceiling or a wall. The backend validates. */
export default function ZoneEditor({ room, run }: Props) {
  const [drafts, setDrafts] = useState<Record<string, ZoneDraft>>({});

  useEffect(() => {
    const next: Record<string, ZoneDraft> = { [NEW]: EMPTY };
    for (const zone of room.zones) next[zone.id] = toDraft(zone);
    setDrafts(next);
  }, [room]);

  const update = (key: string, patch: Partial<ZoneDraft>) =>
    setDrafts((d) => ({ ...d, [key]: { ...(d[key] ?? EMPTY), ...patch } }));

  const save = (key: string) =>
    run(async () => {
      const d = drafts[key];
      if (!d.name.trim()) throw new Error("Give the zone a name.");
      const id = key === NEW ? toId(d.name) : key;
      if (!id) throw new Error("The zone name needs at least one letter or digit.");
      if (key === NEW && room.zones.some((z) => z.id === id))
        throw new Error(`A zone called "${d.name}" already exists.`);
      const axes = SURFACE_AXES[d.surface];
      await putZone({
        id,
        name: d.name.trim(),
        surface: d.surface,
        mode: room.zones.find((zone) => zone.id === id)?.mode ?? "exclude",
        corner: {
          [axes.length]: number(d.cornerLength, `Corner ${axes.length}`),
          [axes.width]: number(d.cornerWidth, `Corner ${axes.width}`),
        },
        length: number(d.length, "Length"),
        width: number(d.width, "Width"),
      });
    });

  const remove = (zone: Zone) => {
    if (window.confirm(`Delete zone "${zone.name}"? Heads using it lose this zone.`))
      void run(() => deleteZone(zone.id));
  };

  const row = (key: string, zone?: Zone) => {
    const d = drafts[key] ?? EMPTY;
    const axes = SURFACE_AXES[d.surface];
    return (
      <div key={key} className="zone-row">
        <input
          placeholder="Zone name" maxLength={80} value={d.name}
          onChange={(e) => update(key, { name: e.target.value })}
        />
        <select value={d.surface} onChange={(e) => update(key, { surface: e.target.value as Surface })}>
          {(Object.keys(SURFACE_AXES) as Surface[]).map((s) => (
            <option key={s} value={s}>{SURFACE_AXES[s].label}</option>
          ))}
        </select>
        <label>
          corner {axes.length}
          <input inputMode="decimal" value={d.cornerLength} onChange={(e) => update(key, { cornerLength: e.target.value })} />
        </label>
        <label>
          corner {axes.width}
          <input inputMode="decimal" value={d.cornerWidth} onChange={(e) => update(key, { cornerWidth: e.target.value })} />
        </label>
        <label>
          length ({axes.length})
          <input inputMode="decimal" value={d.length} onChange={(e) => update(key, { length: e.target.value })} />
        </label>
        <label>
          width ({axes.width})
          <input inputMode="decimal" value={d.width} onChange={(e) => update(key, { width: e.target.value })} />
        </label>
        <button className="primary" onClick={() => void save(key)}>{zone ? "Save" : "Add zone"}</button>
        {zone && <button onClick={() => remove(zone)}>Delete</button>}
      </div>
    );
  };

  return (
    <section className="setup">
      <h2>Zones</h2>
      <p className="muted">
        A zone is a rectangle on the floor, ceiling or a wall. Corner = the corner closest to the origin, in room
        coordinates (m); length and width run along the axes shown. New zones are excluded for every head.
        Include them per head/strip in Aim / Permissions; zone definitions have no global permission.
      </p>
      <div className="zone-list">
        {room.zones.map((zone) => row(zone.id, zone))}
        {row(NEW)}
      </div>
    </section>
  );
}
