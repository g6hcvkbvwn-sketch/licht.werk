import { useState } from "react";
import type { RoomDocument, Scene, ScenePosition } from "./api";
import { NumberField } from "./EditorFields";
import ScenePositionEditor from "./ScenePositionEditor";
import RingSceneFields from "./RingSceneFields";
import {
  attributeLabel, editorFamilies, familyConflicts, familyPresent, resolveFamily,
  scopeAttributes, scopeValues, RING_FAMILY, isRingSegment, type EditorScope,
} from "./showFields";

export default function SceneTable({ draft, scopes, room, selected, onSelect, onChange }: {
  draft: Scene; scopes: EditorScope[]; room: RoomDocument; selected: Set<string>;
  onSelect: (scope: EditorScope, included: boolean) => void; onChange: (scene: Scene) => void;
}) {
  const families = editorFamilies(scopes);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleFamily = (family: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (!next.delete(family)) next.add(family);
    return next;
  });
  const setValues = (scope: EditorScope, values: Record<string, number>, next = draft) => onChange({
    ...next, [scope.group ? "groups" : "fixtures"]: { ...(scope.group ? next.groups : next.fixtures), [scope.id]: values },
  });
  const setPosition = (scope: EditorScope, position?: ScenePosition) => {
    const next = structuredClone(draft);
    next.positions ??= {};
    if (!position) {
      delete next.positions[scope.id];
      next.fixtures[scope.id] = { ...next.fixtures[scope.id], pan: scope.members[0].state.pan, tilt: scope.members[0].state.tilt };
    } else {
      next.positions[scope.id] = position;
      delete next.fixtures[scope.id]?.pan; delete next.fixtures[scope.id]?.tilt;
    }
    onChange(next);
  };
  return <div className="editor-table-scroll"><table className="editor-table">
    <caption className="muted">Controller track order · select participating columns, then the fields to store. Unselected fields stay unchanged.</caption>
    <thead><tr><th scope="col">Scene controls</th>{scopes.map((scope) => <th scope="col" key={scope.key} className={scope.group ? "editor-group" : ""}>
      <label><input type="checkbox" checked={selected.has(scope.key)} onChange={(e) => onSelect(scope, e.target.checked)} />
        {scope.name}</label>
    </th>)}</tr></thead>
    <tbody>{Object.entries(families).map(([family, fields]) => <tr key={family}>
      <th scope="row"><button type="button" className="family-toggle" aria-expanded={!collapsed.has(family)}
        onClick={() => toggleFamily(family)}>{collapsed.has(family) ? "▸" : "▾"} {family}</button></th>{scopes.map((scope) => {
        const attrs = scopeAttributes(scope).filter((a) => fields.includes(a));
        if (!attrs.length) return <td key={scope.key} aria-label={`${scope.name}: ${family} unavailable`}>—</td>;
        const values = scopeValues(draft, scope);
        const included = familyPresent(draft, scope, fields);
        const conflicts = familyConflicts(draft, scope, fields, scopes);
        const participating = selected.has(scope.key);
        if (collapsed.has(family)) {
          const stored = Object.entries(values).filter(([a]) => fields.includes(a));
          const summary = fields.includes("pan") && draft.positions?.[scope.id] ? "named position"
            : stored.length ? stored.map(([a, v]) => attrs.length === 1 ? `${Math.round(v * 100)}%` : `${attributeLabel(a)} ${Math.round(v * 100)}%`).join(", ") : "not stored";
          return <td key={scope.key} className={`${scope.group ? "editor-group " : ""}editor-summary`}>{summary}</td>;
        }
        const isPosition = fields.includes("pan");
        const named = !scope.group && !!draft.positions?.[scope.id];
        const prefix = family.startsWith("Ring colour") ? "ring_" : "";
        const rgb = ["red", "green", "blue"].map((a) => `${prefix}${a}`);
        const canColour = family.includes("colour") && rgb.every((a) => attrs.includes(a) && a in values)
          && rgb.every((a) => Number.isFinite(values[a]) && values[a] >= 0 && values[a] <= 1);
        const choose = (next = draft) => {
          if (isPosition && !scope.group && next.positions?.[scope.id]) { onChange(next); return; }
          const current = scopeValues(next, scope);
          if (family === RING_FAMILY && fields.some((a) => a in current)) { onChange(next); return; }
          const segmentedRing = family === RING_FAMILY && attrs.some(isRingSegment);
          const uniformOnlyMember = scope.members.some((member) => !member.attributes.some(isRingSegment));
          setValues(scope, { ...current, ...Object.fromEntries(attrs.filter((a) => !(a in current)
            && !(segmentedRing && !uniformOnlyMember && /^ring_(red|green|blue)$/.test(a)))
            .map((a) => [a, scope.members[0]?.state[a] ?? 0])) }, next);
        };
        const toggle = () => {
          if (!included) choose();
          else {
            const next = structuredClone(draft);
            if (isPosition && !scope.group && next.positions) delete next.positions[scope.id];
            setValues(scope, Object.fromEntries(Object.entries(values).filter(([a]) => !fields.includes(a))), next);
          }
        };
        return <td key={scope.key} className={`${scope.group ? "editor-group " : ""}${participating ? "" : "editor-inactive"}`}>
          <fieldset disabled={!participating} style={{ border: 0, padding: 0, margin: 0 }}>
            <label><input type="checkbox" checked={included} disabled={!included && !!conflicts.length}
              onChange={toggle} aria-label={`${scope.name}: store ${family.toLowerCase()}`} /></label>
            {!!conflicts.length && <div className="editor-warning" role={included ? "alert" : undefined}>
              {included ? "Legacy overlap: " : "Already set on "}{conflicts.map((s) => s.name).join(", ")}.
              {included && <small>Both saved values are retained until you choose.</small>}
              <button type="button" onClick={() => choose(resolveFamily(draft, scope, fields, scopes))}>
                Use {scope.group ? "group" : "individuals"} instead
              </button>
              <small>{scope.group ? "Replaces individual values in this family." : "Keeps individual overrides; copies group values to the other members."}</small>
            </div>}
            {included && family === RING_FAMILY && <RingSceneFields name={scope.name} attributes={attrs}
              values={values} current={scope.members[0]?.state ?? {}} onChange={(next) => setValues(scope, next)} />}
            {included && family !== RING_FAMILY && <>
              {isPosition && !scope.group && <ScenePositionEditor name={scope.name} room={room} position={draft.positions?.[scope.id]}
                onChange={(position) => setPosition(scope, position)} />}
              {isPosition && named && ["pan", "tilt"].some((a) => a in values)
                && <div role="alert" className="editor-warning">Legacy raw pan / tilt is also saved. Choose explicitly:
                  <button type="button" onClick={() => {
                    const next = { ...values }; delete next.pan; delete next.tilt; setValues(scope, next);
                  }}>Keep named position; remove raw pan / tilt</button>
                  <button type="button" onClick={() => {
                    const next = structuredClone(draft); delete next.positions?.[scope.id]; onChange(next);
                  }}>Keep raw pan / tilt; remove named position</button>
                </div>}
              {isPosition && scope.group && <small>For named targets or zones, use each head's column instead.</small>}
              {canColour && <>
                <label>Colour<input type="color" aria-label={`${scope.name} ${family} picker`}
                  value={`#${rgb.map((a) => Math.round(values[a] * 255).toString(16).padStart(2, "0")).join("")}`}
                  onChange={(e) => setValues(scope, { ...values, ...Object.fromEntries(rgb.map((a, i) =>
                    [a, parseInt(e.target.value.slice(1 + i * 2, 3 + i * 2), 16) / 255])) })} /></label>
                <div className="editor-swatch-row">{["#ff0000", "#00ff00", "#0000ff", "#ffbf00", "#ff00ff", "#00ffff", "#ffffff"].map((hex) =>
                  <button key={hex} type="button" aria-label={`${scope.name} colour ${hex}`} style={{ background: hex }}
                    onClick={() => setValues(scope, { ...values, ...Object.fromEntries(rgb.map((a, i) =>
                      [a, parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255])) })} />)}</div>
              </>}
              {!(isPosition && named) && attrs.map((a) => <div key={a}>
                {attrs.length > 1 && <label><input type="checkbox" checked={a in values} onChange={(e) => {
                  const next = { ...values };
                  if (e.target.checked) next[a] = scope.members[0]?.state[a] ?? 0; else delete next[a];
                  setValues(scope, next);
                }} />{attributeLabel(a)}</label>}
                {a in values && <NumberField label={`${scope.name} ${attributeLabel(a)}`} value={values[a] * 100}
                  onChange={(value) => setValues(scope, { ...values, [a]: value / 100 })} />}
              </div>)}
              {family === "Strobe" && <small>% of fixture's documented rate range; not Hz.</small>}
            </>}
          </fieldset>
        </td>;
      })}</tr>)}</tbody>
  </table></div>;
}
