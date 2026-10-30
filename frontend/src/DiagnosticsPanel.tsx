import { useEffect, useState } from "react";
import { getDiagnostics } from "./api";
import type { Diagnostics } from "./api";

/** Read-only overview of output, patch, setup warnings and recent errors. Refreshes every 5 s. */
export default function DiagnosticsPanel() {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      getDiagnostics()
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, []);

  if (!data) return <p className={error ? "error-text" : "muted"}>{error ?? "Loading…"}</p>;

  return (
    <>
      <section>
        <h2>Output</h2>
        <p>
          Mode: <strong>{data.output_mode}</strong>
        </p>
        <p>
          Real DMX armed on universe(s):{" "}
          <strong>{data.armed_universes.length ? data.armed_universes.join(", ") : "none"}</strong>
        </p>
        <p className="muted">
          Simulated only: {data.simulated_universes.length ? data.simulated_universes.join(", ") : "none"}
          {" · "}Universe numbers are the same on the Botex labels, Art-Net and OLA.
        </p>
      </section>
      <section>
        <h2>Patch</h2>
        <table>
          <thead>
            <tr>
              <th>Fixture</th>
              <th>Universe</th>
              <th>Addresses</th>
              <th>Mode</th>
            </tr>
          </thead>
          <tbody>
            {data.patch.map((f) => (
              <tr key={f.id} className={f.enabled ? "" : "muted"}>
                <td>{f.name}{!f.enabled && " (disabled)"}</td>
                <td>{f.universe}</td>
                <td>{f.first_address}–{f.last_address}</td>
                <td>{f.mode}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <h2>Setup warnings</h2>
        {data.warnings.length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <ul>{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        )}
      </section>
      <section>
        <h2>Recent errors</h2>
        {data.recent_errors.length === 0 ? (
          <p className="muted">None since the backend started.</p>
        ) : (
          <ul>
            {data.recent_errors.map((e, i) => (
              <li key={i}>
                <span className="muted">{e.time}</span> {e.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
