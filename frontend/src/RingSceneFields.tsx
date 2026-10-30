import { useState } from "react";
import { NumberField } from "./EditorFields";
import { attributeLabel, RING_RGB, RING_SECTIONS, isRingSegment, writeRingValues } from "./showFields";

export default function RingSceneFields({ name, attributes, values, current, onChange }: {
  name: string; attributes: string[]; values: Record<string, number>; current: Record<string, number>;
  onChange: (values: Record<string, number>) => void;
}) {
  const [mode, setMode] = useState("");
  const segmented = attributes.some(isRingSegment);
  const storedSections = Object.keys(values).some(isRingSegment);
  const fallback = (attribute: string) => values[attribute]
    ?? values[attribute.replace(/^ring_\d_/, "ring_")]
    ?? current[attribute] ?? 0;
  const sectionColours = RING_SECTIONS.map((section) => RING_RGB.map((channel) => fallback(`ring_${section}_${channel}`)));
  const mixedSections = storedSections && sectionColours.some((colour) =>
    colour.some((value, index) => value !== sectionColours[0][index]));
  const field = (attribute: string) => <div key={attribute}>
    <label><input type="checkbox" checked={attribute in values} onChange={(e) => {
      if (e.target.checked) onChange(writeRingValues(values, { [attribute]: fallback(attribute) }));
      else { const next = { ...values }; delete next[attribute]; onChange(next); }
    }} />{attributeLabel(attribute)}</label>
    {attribute in values && <NumberField label={`${name} ${attributeLabel(attribute)}`} value={values[attribute] * 100}
      onChange={(value) => onChange(writeRingValues(values, { [attribute]: value / 100 }))} />}
  </div>;
  const colour = (section?: number) => {
    const rgb = RING_RGB.map((channel) => `ring_${section ? `${section}_` : ""}${channel}`);
    const available = rgb.filter((attribute) => attributes.includes(attribute));
    if (!available.length) return null;
    const label = section ? `Section ${section}` : "Uniform ring";
    const levels = !section && storedSections ? sectionColours[0] : rgb.map((attribute) => fallback(attribute));
    const valid = levels.every((value) => Number.isFinite(value) && value >= 0 && value <= 1);
    return <div key={label}>
      {!section && mixedSections && <small>Mixed section colours; editing makes all sections uniform. Picker starts at section 1.</small>}
      {available.length === 3 && <label>{label}<input type="color" aria-label={`${name} ${label} colour`}
        disabled={!valid} value={valid ? `#${levels.map((value) => Math.round(value * 255).toString(16).padStart(2, "0")).join("")}` : "#000000"}
        onChange={(e) => onChange(writeRingValues(values, Object.fromEntries(rgb.map((attribute, i) =>
          [attribute, parseInt(e.target.value.slice(1 + i * 2, 3 + i * 2), 16) / 255]))))} /></label>}
      <small>{available.filter((attribute) => attribute in values).length}/3 RGB fields stored</small>
      <details><summary>{label} · partial RGB fields</summary>{available.map(field)}</details>
    </div>;
  };
  return <div>
    {segmented && <label>Ring colour mode<select aria-label={`${name} ring colour mode`} value={mode} onChange={(e) => setMode(e.target.value)}>
      <option value="">Choose Uniform ring / Individual sections</option>
      <option value="uniform">Uniform ring</option><option value="individual">Individual sections</option>
    </select></label>}
    {segmented && <small>Changing this view does not change saved values. A colour edit stores RGB; expand partial RGB fields to store only selected channels.</small>}
    {storedSections && mode !== "individual" && <small>Saved section colours are retained. Editing a uniform channel explicitly replaces that channel in all sections.</small>}
    {(!segmented || mode === "uniform") && colour()}
    {segmented && mode === "individual" && <>
      <small>Unstored section channels use saved uniform channels, or remain unchanged on recall.</small>
      {RING_SECTIONS.map((section) => colour(section))}
    </>}
    {["ring_white", "ring_intensity", "ring_strobe"].filter((attribute) => attributes.includes(attribute)).map(field)}
    {attributes.includes("ring_strobe") && "ring_strobe" in values && <small>Shared ring strobe: % of the documented rate range, not Hz.</small>}
  </div>;
}
