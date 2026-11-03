import type { RoomDocument, ScenePosition } from "./api";
import { zonesOverlap } from "./showFields";

export default function ScenePositionEditor({ name, position, room, onChange }: {
  name: string; position?: ScenePosition; room: RoomDocument; onChange: (position?: ScenePosition) => void;
}) {
  const mode = position?.target_id ? "target" : position?.zone_id ? "zone" : "raw";
  const base = position ?? { included_zone_ids: [], excluded_zone_ids: [] };
  const destination = (kind: string, id: string) => {
    if (kind === "target") onChange({ ...base, target_id: id, zone_id: null, spot: null });
    else onChange({
      ...base, target_id: null, zone_id: id, spot: id === base.zone_id ? base.spot : null,
      included_zone_ids: [...new Set([...base.included_zone_ids, id])],
      excluded_zone_ids: base.excluded_zone_ids.filter((z) => z !== id),
    });
  };
  const aimed = room.zones.find((z) => z.id === base.zone_id);
  const overlaps = aimed ? room.zones.filter((z) =>
    (!base.included_zone_ids.includes(z.id) || base.excluded_zone_ids.includes(z.id)) && zonesOverlap(aimed, z)) : [];
  return <div className="editor-position">
    <label>{name} positioning<select aria-label={`${name} position mode`} value={mode} onChange={(e) => {
      if (e.target.value === "raw") onChange(undefined);
      else destination(e.target.value, (e.target.value === "target" ? room.targets : room.zones)[0].id);
    }}>
      <option value="raw">Raw pan / tilt</option>
      <option value="target" disabled={!room.targets.length}>Named target</option>
      <option value="zone" disabled={!room.zones.length}>Named zone</option>
    </select></label>
    {mode !== "raw" && <label>Destination<select aria-label={`${name} destination`}
      value={(mode === "target" ? base.target_id : base.zone_id) ?? ""}
      onChange={(e) => destination(mode, e.target.value)}>
      {!(mode === "target" ? room.targets : room.zones).some((item) => item.id === (mode === "target" ? base.target_id : base.zone_id))
        && <option value={(mode === "target" ? base.target_id : base.zone_id) ?? ""}>Missing destination</option>}
      {(mode === "target" ? room.targets : room.zones).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>}
    {mode === "zone" && <small>The aimed zone is permitted automatically. Mechanical limits and exclusions still protect the beam.</small>}
    {mode === "zone" && (!base.included_zone_ids.includes(base.zone_id!) || base.excluded_zone_ids.includes(base.zone_id!))
      && <div role="alert" className="editor-warning">This older position does not permit its aimed zone.
        <button type="button" onClick={() => destination("zone", base.zone_id!)}>Permit aimed zone</button>
      </div>}
    {mode !== "raw" && <details><summary>Include other zones?</summary>
      <p className="muted">Optional movement areas. Exclusions win, even where they overlap the aimed zone.</p>
      {room.zones.filter((z) => z.id !== base.zone_id).map((z) => <div key={z.id}>
        <label>{z.name}<select aria-label={`${name}: ${z.name} permission`}
          value={base.included_zone_ids.includes(z.id) && !base.excluded_zone_ids.includes(z.id) ? "include" : "exclude"}
          onChange={(e) => onChange({
            ...base,
            included_zone_ids: [...base.included_zone_ids.filter((id) => id !== z.id), ...(e.target.value === "include" ? [z.id] : [])],
            excluded_zone_ids: [...base.excluded_zone_ids.filter((id) => id !== z.id), ...(e.target.value === "exclude" ? [z.id] : [])],
          })}>
          <option value="include">Included</option><option value="exclude">Excluded</option>
        </select></label>
        {base.included_zone_ids.includes(z.id) && base.excluded_zone_ids.includes(z.id)
          && <small role="alert" className="editor-error">Both included and excluded. Choose one permission explicitly.</small>}
      </div>)}
    </details>}
    {!!overlaps.length && <small role="status" className="editor-warning">
      Excluded area overlaps this aim: {overlaps.map((z) => z.name).join(", ")}. Beam protection may keep it dark.
    </small>}
  </div>;
}
