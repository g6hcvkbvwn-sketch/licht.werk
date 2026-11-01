import { useEffect, useState } from "react";

interface OlaSetup {
  installed: boolean;
  running: boolean;
  install_command: string;
  download_url: string;
}

// Shown only when the DMX software OLA is neither installed nor running.
export default function OlaSetupNotice() {
  const [setup, setSetup] = useState<OlaSetup | null>(null);
  useEffect(() => {
    fetch("/api/ola/setup").then((r) => (r.ok ? r.json() : null)).then(setSetup).catch(() => setSetup(null));
  }, []);
  if (!setup || setup.installed || setup.running) return null;
  return (
    <div className="ola-notice" role="alert">
      <strong>OLA (DMX software) is not installed.</strong> LichtWerk runs in simulation only until it is.
      In Terminal run <code>{setup.install_command}</code> (needs <a href="https://brew.sh" target="_blank" rel="noreferrer">Homebrew</a>),
      or see <a href={setup.download_url} target="_blank" rel="noreferrer">OLA downloads</a>. Then restart LichtWerk.
    </div>
  );
}
