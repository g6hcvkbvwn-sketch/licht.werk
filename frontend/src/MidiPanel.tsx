import { useEffect, useState } from "react";
import { getMidi } from "./api";
import type { MidiStatus } from "./api";

const hex = (n: number) => `0x${n.toString(16).toUpperCase().padStart(2, "0")}`;

/** Fixed APC40 MKII mapping, mirroring backend/lichtwerk/apc40.py. Not changed by Learn. */
const APC_MAPPING: { control: string; message: string; function: string }[] = [
  { control: "Track fader 1-8", message: `CC ${hex(0x07)} · ch = track`, function: "Strip intensity (track 8: haze pump)" },
  { control: "Master fader", message: `CC ${hex(0x0e)}`, function: "Master intensity" },
  { control: "MASTER button", message: `Note ${hex(0x50)}`, function: "Blackout toggle" },
  { control: "Track knob 1-8", message: `CC ${hex(0x30)}-${hex(0x37)}`, function: "Saturation / ring intensity / haze fan" },
  { control: "Device knobs 1, 2, 5, 6", message: `CC ${hex(0x10)}, ${hex(0x11)}, ${hex(0x14)}, ${hex(0x15)}`, function: "Contextual: colour, FX or ring parameters" },
  { control: "Tempo knob", message: `CC ${hex(0x0d)} (relative)`, function: "Tempo (BPM)" },
  { control: "Cue level knob", message: `CC ${hex(0x2f)} (relative)`, function: "Strobe rate" },
  { control: "TAP TEMPO", message: `Note ${hex(0x63)}`, function: "Tap tempo" },
  { control: "METRONOME", message: `Note ${hex(0x5a)}`, function: "Beat indicator (LED only)" },
  { control: "Clip pads 5 × 8", message: `Notes ${hex(0x00)}-${hex(0x27)}`, function: "Looks / sequences / targets / zones / FX presets, by page" },
  { control: "SCENE LAUNCH 1-5", message: `Notes ${hex(0x52)}-${hex(0x56)}`, function: "Open FX page: effect type 1-5" },
  { control: "STOP ALL CLIPS", message: `Note ${hex(0x51)}`, function: "Stop the effects of the selected FX strip" },
  { control: "Track CLIP STOP", message: `Note ${hex(0x34)} · ch = track`, function: "Select the strip for colour / ring knobs" },
  { control: "Track SELECT", message: `Note ${hex(0x33)} · ch = track`, function: "Select the strip for FX knobs; SHIFT + SELECT: target/zone menu" },
  { control: "Track ACTIVATOR", message: `Note ${hex(0x32)} · ch = track`, function: "Switch family between group and single strips" },
  { control: "Track SOLO", message: `Note ${hex(0x31)} · ch = track`, function: "Strobe on/off" },
  { control: "Track REC ARM", message: `Note ${hex(0x30)} · ch = track`, function: "FLASH (while held)" },
  { control: "Track CROSSFADER A/B", message: `Note ${hex(0x42)} · ch = track`, function: "RING editing on/off" },
  { control: "DEVICE <  /  DEVICE >", message: `Notes ${hex(0x3a)} / ${hex(0x3b)}`, function: "LOOKS page / SEQUENCES page (LED shows the active one)" },
  { control: "DEVICE ON/OFF  /  DEVICE LOCK", message: `Notes ${hex(0x3e)} / ${hex(0x3f)}`, function: "AIM / PERMISSIONS in the target/zone menu (LED shows the active one)" },
  { control: "PLAY", message: `Note ${hex(0x5b)}`, function: "Sequence play/pause; SHIFT + PLAY restarts" },
  { control: "RECORD", message: `Note ${hex(0x5d)}`, function: "Stop sequence at the current values" },
  { control: "SESSION RECORD", message: `Note ${hex(0x66)}`, function: "Preview / Go" },
  { control: "SHIFT", message: `Note ${hex(0x62)}`, function: "Edit the function behind a pad; with SELECT: target/zone menu" },
];

/** MIDI device health, the fixed APC40 mapping and a live message monitor. */
function ApcMapping() {
  return <section>
    <h2>APC40 MKII mapping</h2>
    <p className="muted">Fixed mapping of the APC40 command surface.
      Notes are on MIDI channel 1 unless a track channel is stated.</p>
    <table>
      <thead><tr><th>Control</th><th>MIDI message</th><th>Function</th></tr></thead>
      <tbody>{APC_MAPPING.map((row) => (
        <tr key={row.control}><td>{row.control}</td><td>{row.message}</td><td>{row.function}</td></tr>
      ))}</tbody>
    </table>
  </section>;
}

export default function MidiPanel() {
  const [midi, setMidi] = useState<MidiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      getMidi()
        .then((m) => {
          setMidi(m);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    load();
    const timer = window.setInterval(load, 700);
    return () => window.clearInterval(timer);
  }, []);

  if (!midi) return <p className={error ? "error-text" : "muted"}>{error ?? "Loading…"}</p>;
  if (!midi.enabled)
    return <>
      <p className="muted">MIDI is switched off, so no controller is connected. To enable it, set
        {" "}<code>"midi": {"{"}"enabled": true{"}"}</code> in <code>config/app.json</code> and restart the backend.</p>
      <ApcMapping />
    </>;

  return (
    <>
      {error && <div className="banner error">{error}</div>}
      <section>
        <h2>Device health</h2>
        <p>
          {midi.connected ? (
            <strong className="ok-text">Connected: {midi.connected_device}</strong>
          ) : (
            <strong>Not connected</strong>
          )}{" "}
          <span className="muted">(looking for “{midi.device_filter}”; the lights keep working without it)</span>
        </p>
        {midi.last_error && <p className="error-text">{midi.last_error}</p>}
        {midi.apc40_alternate_mode.length > 0 && (
          <p className="ok-text">APC40 set to Alternate mode: {midi.apc40_alternate_mode.join(", ")}</p>
        )}
        {midi.apc40_error && <p className="error-text">{midi.apc40_error}</p>}
        <p className="muted">
          Visible MIDI devices: {midi.available_devices.length ? midi.available_devices.join(", ") : "none"}
        </p>
      </section>
      <ApcMapping />
      <section>
        <h2>Monitor</h2>
        {midi.monitor.length === 0 ? (
          <p className="muted">No messages yet. Touch a control on the controller.</p>
        ) : (
          <ul className="monitor">
            {midi.monitor.map((m, i) => (
              <li key={i}>
                {m.kind === "cc" ? "CC" : "Note"} {m.number} · value {m.value} · ch {m.channel}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
