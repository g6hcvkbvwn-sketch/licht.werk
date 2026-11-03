import { useState } from "react";
import { attributeLabel, editorFamilies, isRingSegment, RING_FAMILY, RING_RGB, RING_SECTIONS, scopeAttributes, type EditorScope } from "./showFields";

type LiveControls = Record<string, string[]>;
type LiveFieldProps = { scope: EditorScope; value: LiveControls; onChange: (value: LiveControls) => void };

function LiveChoice({ scope, value, onChange, fields, label }: LiveFieldProps & { fields: string[]; label: string }) {
  const applicable = scope.members.flatMap((f) => fields.filter((a) => f.attributes.includes(a)).map((a) => [f.id, a]));
  if (!applicable.length) return null;
  const count = applicable.filter(([id, attr]) => value[id]?.includes(attr)).length;
  return <label><input type="checkbox" checked={count === applicable.length}
    aria-label={`${scope.name}: ${label} live control`}
    ref={(el) => { if (el) el.indeterminate = count > 0 && count < applicable.length; }}
    onChange={(e) => {
      const next = structuredClone(value);
      for (const [id, attr] of applicable) {
        const attrs = new Set(next[id] ?? []);
        if (e.target.checked) attrs.add(attr); else attrs.delete(attr);
        if (attrs.size) next[id] = [...attrs]; else delete next[id];
      }
      onChange(next);
    }} />{label}{count > 0 && count < applicable.length ? " · partly selected" : ""}</label>;
}

function RingLiveChoices(props: LiveFieldProps & { fields: string[] }) {
  const [mode, setMode] = useState("");
  const attributes = scopeAttributes(props.scope);
  const segmented = attributes.some(isRingSegment);
  const rgb = props.fields.filter((a) => a !== "ring_intensity" && a !== "ring_strobe" && a !== "ring_white");
  const selectedColours = props.scope.members.reduce((count, fixture) =>
    count + (props.value[fixture.id] ?? []).filter((a) => rgb.includes(a)).length, 0);
  return <div>
    <strong>Ring</strong>
    {segmented && <label>Ring colour mode<select aria-label={`${props.scope.name} ring live colour mode`}
      value={mode} onChange={(e) => setMode(e.target.value)}>
      <option value="">Choose Uniform ring / Individual sections</option>
      <option value="uniform">Uniform ring</option><option value="individual">Individual sections</option>
    </select></label>}
    {segmented && <small>{selectedColours} ring colour fields selected. Changing this view retains all choices.</small>}
    {(!segmented || mode === "uniform") && <LiveChoice {...props} fields={rgb} label="Uniform ring colour" />}
    {segmented && mode === "individual" && <>
      {props.scope.members.some((fixture) => (props.value[fixture.id] ?? []).some((a) => /^ring_(red|green|blue)$/.test(a)))
        && <small>Uniform colour choices are also retained. To clear all ring colour choices, uncheck Uniform ring colour first, then choose sections.</small>}
      {RING_SECTIONS.map((section) => <LiveChoice key={section} {...props}
        fields={RING_RGB.map((channel) => `ring_${section}_${channel}`)} label={`Ring section ${section} colour`} />)}
    </>}
    {["ring_white", "ring_intensity", "ring_strobe"].map((attribute) =>
      <LiveChoice key={attribute} {...props} fields={[attribute]} label={attributeLabel(attribute)} />)}
  </div>;
}

export default function SequenceLiveControls({ scopes, value, onChange }: {
  scopes: EditorScope[]; value: LiveControls; onChange: (value: LiveControls) => void;
}) {
  const families = editorFamilies(scopes);
  return <fieldset className="editor-live-controls">
    <legend>Live controls for this sequence</legend>
    <p>Choose what you want to adjust while the sequence runs. These fields keep their current values at launch and are not overwritten by its looks. No initial value needs to be defined.</p>
    <p className="muted">Group choices apply to all its members. Individual head choices let you control just one head. These are stored with this sequence, not as global show settings.</p>
    {scopes.map((scope) => <div className="editor-live-scope" key={scope.key}>
      <strong>{scope.name}{scope.group ? " · group" : ""}</strong>
      <div className="editor-live-options">{Object.entries(families).map(([family, fields]) => {
        if (!scopeAttributes(scope).some((attribute) => fields.includes(attribute))) return null;
        return family === RING_FAMILY
          ? <RingLiveChoices key={family} scope={scope} value={value} onChange={onChange} fields={fields} />
          : <LiveChoice key={family} scope={scope} value={value} onChange={onChange} fields={fields} label={family} />;
      })}</div>
    </div>)}
  </fieldset>;
}
