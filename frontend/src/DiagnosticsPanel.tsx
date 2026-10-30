import { useCallback, useEffect, useState } from "react";
import { getDiagnostics } from "./api";
import type { Diagnostics } from "./api";

/** Read-only overview of output, patch, setup warnings and recent errors. Refreshes every 5 s. */
export default function DiagnosticsPanel() {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() =>
      getDiagnostics()
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)), []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy failed: clipboard access was refused by the browser.");
    }
  };

  if (!data) return <>
    <p className={error ? "error-text" : "muted"}>{error ?? "Loading…"}</p>
    {error && <button type="button" onClick={() => void load()}>Retry</button>}
  </>;

  const isBlocking = (w: string) => /collision|overflow|overlap|invalid|exceed|outside|unknown/i.test(w);
  const blocking = data.warnings.filter(isBlocking);
  const info = data.warnings.filter((w) => !isBlocking(w));
  const where = (w: string) => /position|mount|calibrat|rest|pan|tilt/i.test(w) ? "Positions or Dimensions" : "Dimensions or config/patch.json";

  return (
    <>
      <section>
        <button type="button" onClick={() => void copy()}>{copied ? "Copied" : "Copy diagnostics"}</button>
        {" "}<button type="button" onClick={() => void load()}>Retry</button>
        {error && <p className="error-text" role="alert">{error}</p>}
        <h2>Output</h2>
        <p>
          Mode: <strong>{data.output_mode}</strong>
        </p>
        <p>
          Real DMX enabled on universe(s):{" "}
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
        <h2>Setup issues</h2>
        {data.warnings.length === 0 && <p className="muted">None.</p>}
        {blocking.length > 0 && <>
          <h3 className="error-text">Blocking</h3>
          <ul>{blocking.map((w) => <li key={w}>{w} <small className="muted">· fix in {where(w)}</small></li>)}</ul>
        </>}
        {info.length > 0 && <>
          <h3>Informational</h3>
          <ul>{info.map((w) => <li key={w}>{w} <small className="muted">· see {where(w)}</small></li>)}</ul>
        </>}
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
