import { useEffect, useState } from "react";
import {
  assignApc40Pad, captureSceneDraft, clearApc40Pad, deleteScene, getApc40, getFixtures, getGroups, getHeads, getRoom,
  getScenes, getStatus, recallScene, saveScene,
  type FixtureInfo, type GroupInfo, type PadAccent, type RoomDocument, type Scene, type Apc40Strip,
} from "./api";
import { BeatField, NumberField, validNumber } from "./EditorFields";
import DialogFrame from "./DialogFrame";
import SceneTable from "./SceneTable";
import {
  BEATS, editorFamilies, editorScopes, familyConflicts, familyPresent, newShowId, scopeValues, type EditorScope,
} from "./showFields";
import "./editor.css";

export interface SceneDialogProps {
  scene?: Scene;
  pad?: number;
  /** Opened with SHIFT + empty pad: capture the live look (values and running FX) at once. */
  captureLive?: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

const FX_FAMILY: Record<string, string> = {
  chase: "Dimmer", rainbow: "Colour", two_colour: "Colour", circle: "Move", figure8: "Move",
};

export default function SceneDialog({ scene, pad, captureLive, onClose, onSaved }: SceneDialogProps) {
  const [compareAll, setCompareAll] = useState(false);
  const [draft, setDraft] = useState<Scene>(() => structuredClone(scene ?? {
    id: newShowId(), name: "", fade_seconds: 0, fade_timing: "beats", fade_beats: 1, fixtures: {}, groups: {}, positions: {},
  }));
  const [fixtures, setFixtures] = useState<FixtureInfo[]>([]);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [strips, setStrips] = useState<Apc40Strip[]>([]);
  const [room, setRoom] = useState<RoomDocument | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [source, setSource] = useState("current logical state");
  const [colour, setColour] = useState("blue");
  const [accent, setAccent] = useState<PadAccent>("off");
  const [accentBeats, setAccentBeats] = useState(2);
  const [existing, setExisting] = useState("");
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [persisted, setPersisted] = useState(!!scene);
  const [preview, setPreview] = useState(false);
  const [baseline, setBaseline] = useState(() => JSON.stringify(draft));
  const [colours, setColours] = useState(["blue", "green", "red", "amber", "purple", "cyan", "white"]);
  useEffect(() => {
    let active = true;
    Promise.all([getFixtures(), getGroups(), getRoom(), getStatus(), getScenes(), getApc40()])
      .then(([f, g, r, status, all, apc]) => {
        if (!active) return;
        setFixtures(f); setGroups(g); setRoom(r); setScenes(all); setStrips(apc.strips);
        const scopes = editorScopes(f, g, apc.strips);
        setSelected(new Set(scopes.filter((s) => Object.keys(scopeValues(draft, s)).length
          || (!s.group && !!draft.positions?.[s.id])).map((s) => s.key)));
        setPreview(status.preview_active);
        setSource(status.preview_active ? "PREVIEW logical state (not live)" : "LIVE logical state (before master / blackout / FX)");
        if (pad !== undefined) {
          const assigned = apc.pads.find((p) => p.pad === pad);
          if (assigned?.scene_colour) setColour(assigned.scene_colour);
          if (assigned?.accent) setAccent(assigned.accent);
          if (assigned?.accent_beats) setAccentBeats(assigned.accent_beats);
        }
        if (Object.keys(apc.scene_colours).length) setColours(Object.keys(apc.scene_colours));
        if (captureLive && !scene) void captureLook(null, editorScopes(f, g, apc.strips)).catch((e: Error) => setError(e.message));
      }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const scopes = editorScopes(fixtures, groups, strips);
  const change = (next: Scene) => { setDraft(next); setSaved(false); };
  const select = (scope: EditorScope, included: boolean) => {
    const next = new Set(selected);
    if (included) next.add(scope.key);
    else {
      next.delete(scope.key);
      const updated = structuredClone(draft);
      delete (scope.group ? updated.groups : updated.fixtures)[scope.id];
      if (!scope.group && updated.positions) delete updated.positions[scope.id];
      change(updated);
    }
    setSelected(next);
  };
  const validation: string[] = [];
  if (!draft.name.trim()) validation.push("Enter a look name.");
  if (draft.name.length > 80) validation.push("Look names can have at most 80 characters.");
  if ((draft.fade_timing ?? "seconds") === "beats" ? ![0, ...BEATS].includes(draft.fade_beats ?? 1)
    : !validNumber(draft.fade_seconds, 0, 600)) validation.push("Choose a valid incoming fade.");
  if (Object.values(draft.fixtures).concat(Object.values(draft.groups)).some((v) =>
    Object.values(v).some((n) => !validNumber(n, 0, 1)))) validation.push("All percentage fields must be between 0 and 100.");
  for (const scope of scopes) {
    const fields = new Set(scope.members.flatMap((f) => f.attributes));
    if (Object.keys(scopeValues(draft, scope)).some((a) => !fields.has(a)))
      validation.push(`${scope.name}: saved controls are unavailable in the current patch. Their values are retained.`);
  }
  for (const [family, fields] of Object.entries(editorFamilies(scopes))) {
    if (scopes.some((s) => familyPresent(draft, s, fields) && familyConflicts(draft, s, fields, scopes).length))
      validation.push(`Resolve the group / individual overlap for ${family.toLowerCase()}.`);
  }
  for (const [id, position] of Object.entries(draft.positions ?? {})) {
    if ((position.target_id && !room?.targets.some((t) => t.id === position.target_id))
      || (position.zone_id && !room?.zones.some((z) => z.id === position.zone_id)))
      validation.push(`${id}: repair the missing destination.`);
    if (position.zone_id && (!position.included_zone_ids.includes(position.zone_id) || position.excluded_zone_ids.includes(position.zone_id)))
      validation.push(`${id}: explicitly permit the aimed zone.`);
    if (position.included_zone_ids.some((z) => position.excluded_zone_ids.includes(z)))
      validation.push(`${id}: choose either include or exclude for each zone.`);
    if (position.included_zone_ids.concat(position.excluded_zone_ids).some((id) => !room?.zones.some((z) => z.id === id)))
      validation.push(`${id}: a saved permission references a missing zone.`);
    if (["pan", "tilt"].some((a) => a in (draft.fixtures[id] ?? {})))
      validation.push(`${id}: choose named positioning or raw pan / tilt, not both.`);
    if (position.spot && (!validNumber(position.spot.u, 0, 1) || !validNumber(position.spot.v, 0, 1)))
      validation.push(`${id}: the saved zone spot is invalid. Choose a different zone to replace it.`);
  }
  const canSave = !!room && !validation.length;
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const save = async (apply = false) => {
    if (!canSave) return;
    const stored = await saveScene({
      ...draft, name: draft.name.trim(),
      groups: Object.fromEntries(Object.entries(draft.groups).filter(([, values]) => Object.keys(values).length)),
      fixtures: Object.fromEntries(Object.entries(draft.fixtures).filter(([, values]) => Object.keys(values).length)),
    }, !persisted);
    setPersisted(true); setDraft(stored); setBaseline(JSON.stringify(stored)); setSaved(true);
    if (pad !== undefined) await assignApc40Pad(pad, stored.id, colour, accent, accentBeats);
    onSaved?.();
    if (apply) await recallScene(stored.id);
  };
  const capture = async () => {
    const captured = [...new Set(scopes.filter((s) => Object.keys(scopeValues(draft, s)).length || (!s.group && draft.positions?.[s.id]))
      .flatMap((s) => s.members.map((m) => m.id)))];
    const [current, status, live] = await Promise.all([getFixtures(), getStatus(), captureSceneDraft(captured, true)]);
    const next = structuredClone(draft);
    next.fx = live.fx ?? null;
    for (const scope of scopes) {
      const fields = Object.keys(scopeValues(draft, scope));
      if (!fields.length) continue;
      for (const member of scope.members) {
        const fixture = current.find((f) => f.id === member.id)!;
        next.fixtures[member.id] = { ...next.fixtures[member.id], ...Object.fromEntries(
          fields.filter((a) => a in fixture.state).map((a) => [a, fixture.state[a]]),
        ) };
      }
      if (scope.group) delete next.groups[scope.id];
    }
    if (Object.keys(next.positions ?? {}).length) {
      const heads = await getHeads();
      for (const head of heads) if (next.positions?.[head.id]) {
        if (head.assignment.aim_target || head.assignment.aim_zone) {
          next.positions[head.id] = {
            target_id: head.assignment.aim_target, zone_id: head.assignment.aim_zone,
            spot: head.assignment.aim_zone ? { u: head.spot_u, v: head.spot_v } : null,
            included_zone_ids: [...new Set([...head.assignment.include_zones, ...(head.assignment.aim_zone ? [head.assignment.aim_zone] : [])])],
            excluded_zone_ids: head.assignment.exclude_zones.filter((z) => z !== head.assignment.aim_zone),
          };
          delete next.fixtures[head.id]?.pan; delete next.fixtures[head.id]?.tilt;
        } else {
          delete next.positions[head.id];
          next.fixtures[head.id] = { ...next.fixtures[head.id], pan: head.pan, tilt: head.tilt };
        }
      }
    }
    setSelected(new Set(scopes.filter((s) => Object.keys(scopeValues(next, s)).length || (!s.group && next.positions?.[s.id])).map((s) => s.key)));
    setSource(status.preview_active ? "PREVIEW logical state (not live)" : "LIVE logical state (before master / blackout / FX)");
    change(next);
  };
  // Capture live: the full current state, running FX and strip modes of the ticked fixtures, or of all.
  const captureLook = async (fixtureIds: string[] | null, allScopes = scopes) => {
    const [live, status] = await Promise.all([captureSceneDraft(fixtureIds, true), getStatus()]);
    const next = structuredClone(draft);
    next.fixtures = live.fixtures;
    next.groups = {};
    next.positions = live.positions ?? {};
    next.fx = live.fx ?? null;
    setSelected(new Set(allScopes.filter((s) => !s.group && (next.fixtures[s.id] || next.positions?.[s.id])).map((s) => s.key)));
    setSource(status.preview_active ? "PREVIEW logical state (not live)" : "LIVE logical state (before master / blackout / FX)");
    change(next);
  };
  const selectedFixtureIds = () => {
    const ids = scopes.filter((s) => selected.has(s.key)).flatMap((s) => s.members.map((m) => m.id));
    return ids.length ? [...new Set(ids)] : null;
  };
  const unknown = [
    ...Object.keys(draft.fixtures).filter((id) => !fixtures.some((f) => f.id === id)),
    ...Object.keys(draft.groups).filter((id) => !groups.some((g) => g.id === id)),
  ];
  const dirty = JSON.stringify(draft) !== baseline;
  const target = preview ? "Preview" : "Live";
  const deleteScope = persisted && <button type="button" className="danger" disabled={busy} onClick={() => {
    if (window.confirm(`Delete "${draft.name}" everywhere?`)) void perform(async () => { await deleteScene(draft.id); onSaved?.(); onClose(); });
  }}>Delete look everywhere</button>;
  const unassign = pad !== undefined && <button type="button" disabled={busy}
    onClick={() => void perform(async () => { await clearApc40Pad(pad); onSaved?.(); onClose(); })}>Unassign pad</button>;
  return <DialogFrame id="scene-dialog" size="wide" dirty={dirty && !existing} busy={busy} onClose={onClose}
    title={`${scene ? "Edit look" : "Create look"}${scene ? ` · ${scene.name}` : ""}${pad !== undefined ? ` · pad ${pad + 1}` : ""}`}
    context={`Editing a look definition. Nothing is recalled until you apply it.`} preview={preview} saveNote
    status={error ? <div role="alert" className="banner error">{error}</div> : undefined}
    footer={<>
      <button type="submit" form="scene-form" disabled={busy || (!existing && !canSave)}>
        {saved ? "Saved" : existing ? "Assign look" : "Save for next launch"}</button>
      {!existing && <button type="button" disabled={busy || !canSave} onClick={() => void perform(() => save(true))}>
        Save &amp; apply to {target}</button>}
      {!existing && !canSave && !!validation.length && <small className="dialog-hint editor-error">
        Save unavailable: {validation[0]}{validation.length > 1 ? ` (+${validation.length - 1} more above)` : ""}</small>}
      {(unassign || deleteScope) && <details className="dialog-more"><summary>More</summary>
        <div className="dialog-more-menu">{unassign}{deleteScope}</div></details>}
    </>}>
    {pad !== undefined && !scene && <label>Assign an existing look<select value={existing} onChange={(e) => setExisting(e.target.value)}>
      <option value="">Create new below</option>{scenes.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select></label>}
    <form id="scene-form" noValidate onSubmit={(e) => { e.preventDefault(); void perform(async () => {
      if (existing && pad !== undefined) { await assignApc40Pad(pad, existing, colour, accent, accentBeats); onSaved?.(); onClose(); }
      else await save();
    }); }}>
      <fieldset disabled={busy}>
        <details className="scene-meta" open>
          <summary>Look details · {draft.name.trim() || "unnamed"} · fade {(draft.fade_timing ?? "seconds") === "beats" ? `${draft.fade_beats ?? 1} beats` : `${draft.fade_seconds} s`}</summary>
        <label>Name<input required={!existing} maxLength={80} aria-invalid={!draft.name.trim()} value={draft.name} onChange={(e) => change({ ...draft, name: e.target.value })} /></label>
        <label>Incoming fade unit<select value={draft.fade_timing ?? "seconds"} onChange={(e) =>
          change({ ...draft, fade_timing: e.target.value as "seconds" | "beats" })}>
          <option value="beats">Beats · follows tempo</option><option value="seconds">Seconds · legacy values preserved</option>
        </select></label>
        {(draft.fade_timing ?? "seconds") === "beats"
          ? <BeatField label="Incoming fade" cut value={draft.fade_beats ?? 1} onChange={(fade_beats) => change({ ...draft, fade_beats })} />
          : <NumberField label="Incoming fade" unit="s" max={600} value={draft.fade_seconds} onChange={(fade_seconds) => change({ ...draft, fade_seconds })} />}
        {pad !== undefined && <label>Pad colour<select value={colour} onChange={(e) => setColour(e.target.value)}>
          {colours.map((c) => <option key={c}>{c}</option>)}
        </select></label>}
        {pad !== undefined && <label>Pad action<select value={accent} onChange={(e) => setAccent(e.target.value as PadAccent)}>
          <option value="off">Recall look</option>
          <option value="hold">Accent while held</option>
          <option value="beats">Accent for a number of beats</option>
        </select></label>}
        {pad !== undefined && accent === "beats" && <label>Accent length<select value={accentBeats}
          onChange={(e) => setAccentBeats(Number(e.target.value))}>
          {BEATS.map((b) => <option key={b} value={b}>{b} {b === 1 ? "beat" : "beats"}</option>)}
        </select></label>}
        {pad !== undefined && accent !== "off" && <small className="dialog-hint">
          Accent: the complete look (values, positions and FX) plays over the output {accent === "hold" ? "while you hold the pad" : `for ${accentBeats} ${accentBeats === 1 ? "beat" : "beats"}`}.
          Everything underneath keeps running and is back exactly when the accent ends.
        </small>}
        </details>
        {!existing && <>
          <div className="show-actions"><button type="button" disabled={!room || !scopes.some((s) => Object.keys(scopeValues(draft, s)).length || (!s.group && draft.positions?.[s.id]))}
            onClick={() => void perform(capture)}>Update selected controls</button>
            <small>Refreshes only the controls already in this look, plus the running FX and group / single modes of their strips.</small>
          </div>
          <div className="show-actions">
            <button type="button" disabled={!room} onClick={() => void perform(() => captureLook(selectedFixtureIds()))}>
              Capture live</button>
            <small>Replaces everything with the live state, running FX and group / single modes of the ticked fixtures (all, if none is ticked). Never master / blackout / FLASH.</small>
          </div>
          {draft.fx && <div className="editor-warning look-summary" role="status">
            <strong>FX and modes</strong> · recall sets the group / single mode and FX of the strips in this look and stops their other FX.
            {Object.entries(draft.fx.modes).map(([family, mode]) => <div key={family}>{family}: {mode} mode</div>)}
            {draft.fx.strips.length ? draft.fx.strips.map((entry, index) => <div key={index}>
              {strips.find((s) => s.target === entry.strip)?.label ?? entry.strip}{entry.component === "ring" ? " ring" : ""}:{" "}
              {entry.combo ? `Combo "${entry.combo}" (` : ""}
              {entry.effects.map((e) => `${FX_FAMILY[e.kind] ?? e.kind} "${e.name}"`).join(", ")}{entry.combo ? ")" : ""}
            </div>) : <div>No FX: recall stops the FX of these strips.</div>}
            <button type="button" onClick={() => change({ ...draft, fx: null })}>Values only (leave FX and modes alone)</button>
          </div>}
          <small className="muted">Source: {source}. Haze is opt-in.</small>
          <fieldset className="scope-row"><legend>Participating fixtures and groups (controller track order)</legend>
            {scopes.map((scope) => <label key={scope.key}><input type="checkbox" checked={selected.has(scope.key)}
              onChange={(e) => select(scope, e.target.checked)} />{scope.name}</label>)}</fieldset>
          <label className="compare-toggle"><input type="checkbox" checked={compareAll} onChange={(e) => setCompareAll(e.target.checked)} />Show all fixtures and groups</label>
          {room ? <SceneTable draft={draft} scopes={compareAll ? scopes : scopes.filter((s) => selected.has(s.key) || Object.keys(scopeValues(draft, s)).length || (!s.group && draft.positions?.[s.id]))} room={room} selected={selected} onSelect={select} onChange={change} /> : <p>Loading fixtures…</p>}
          {!!unknown.length && <div role="alert" className="editor-warning">Saved entries not in the current patch are retained: {unknown.join(", ")}.</div>}
          {!!validation.length && <div className="editor-error" role="status">{validation.map((message) => <p key={message}>{message}</p>)}</div>}
        </>}
      </fieldset>
    </form>
  </DialogFrame>;
}
