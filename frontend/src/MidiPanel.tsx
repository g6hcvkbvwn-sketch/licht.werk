import { useEffect, useState } from "react";
import { cancelLearnMidi, getMidi, learnMidi, putMidiBindings } from "./api";
import type { FixtureInfo, MidiAction, MidiKey, MidiStatus, Scene } from "./api";

interface Props {
  fixtures: FixtureInfo[];
  scenes: Scene[];
}

const same = (a: MidiAction, b: MidiAction) => a.type === b.type && a.target === b.target;
const describe = (k: MidiKey) => `${k.kind === "cc" ? "Fader/pot CC" : "Button note"} ${k.number} · ch ${k.channel}`;

/** MIDI status, live monitor and "learn" binding. Mappings are learned from the real controller. */
export default function MidiPanel({ fixtures, scenes }: Props) {
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

  const call = (action: () => Promise<MidiStatus>) =>
    action().then(setMidi).catch((e: Error) => setError(e.message));

  if (!midi) return <p className={error ? "error-text" : "muted"}>{error ?? "Loading…"}</p>;
  if (!midi.enabled)
    return <p className="muted">MIDI is off. Set "midi": {"{"}"enabled": true{"}"} in config/app.json and restart.</p>;

  const groups = [...new Set(fixtures.flatMap((f) => f.groups))];
  const rows: { label: string; action: MidiAction; hint: string }[] = [
    { label: "Master intensity", action: { type: "master", target: null }, hint: "fader/pot" },
    { label: "Blackout (toggle)", action: { type: "blackout", target: null }, hint: "button" },
    ...fixtures
      .filter((f) => f.attributes.includes("intensity"))
      .map((f) => ({ label: `${f.name} intensity`, action: { type: "intensity" as const, target: f.id }, hint: "fader/pot" })),
    ...groups.map((g) => ({ label: `Group “${g}” intensity`, action: { type: "intensity" as const, target: g }, hint: "fader/pot" })),
    ...scenes.map((s) => ({ label: `Scene: ${s.name}`, action: { type: "scene" as const, target: s.id }, hint: "button" })),
  ];

  return (
    <>
      {error && <div className="banner error">{error}</div>}
      <section>
        <h2>Controller</h2>
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
      <section>
        <h2>Mapping</h2>
        <p className="muted">
          Press “Learn”, then move the fader/pot or press the button on the controller. Nothing is
          assumed: only controls you teach are active.
        </p>
        <table>
          <tbody>
            {rows.map((row) => {
              const binding = midi.bindings.find((b) => same(b.action, row.action));
              const learning = midi.learning !== null && same(midi.learning, row.action);
              return (
                <tr key={`${row.action.type}:${row.action.target}`}>
                  <td>{row.label}</td>
                  <td className={binding ? "" : "muted"}>
                    {learning ? `Waiting for a ${row.hint}…` : binding ? describe(binding.key) : "not bound"}
                  </td>
                  <td>
                    {learning ? (
                      <button className="small" onClick={() => void call(cancelLearnMidi)}>Cancel</button>
                    ) : (
                      <button className="small" onClick={() => void call(() => learnMidi(row.action))}>Learn</button>
                    )}
                    {binding && !learning && (
                      <button
                        className="small"
                        onClick={() =>
                          void call(() => putMidiBindings(midi.bindings.filter((b) => b !== binding)))
                        }
                      >
                        Clear
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
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
