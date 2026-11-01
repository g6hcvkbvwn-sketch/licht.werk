import { useCallback, useEffect, useState } from "react";
import {
  ConnectionError,
  getFixtures,
  getGroups,
  getScenes,
  getStatus,
  retryOutput,
  renameFixture,
  renameGroup,
  setBlackout,
  setFixtureState,
  setMaster,
  setPreview,
  type FixtureInfo,
  type GroupInfo,
  type Scene,
  type Status,
  type ShowState,
} from "./api";
import ControllerPage from "./ControllerPage";
import DiagnosticsPanel from "./DiagnosticsPanel";
import FixturePanel, { DisplayNameEditor } from "./FixturePanel";
import OlaSetupNotice from "./OlaSetupNotice";
import MidiPanel from "./MidiPanel";
import PositioningPanel from "./PositioningPanel";
import ShowPanel, { ShowStatusStrip } from "./ShowPanel";
import RoomMap from "./RoomMap";
import SetupPanel from "./SetupPanel";

type Page = "controller" | "show" | "live" | "positions" | "midi" | "setup" | "diagnostics";
const PAGES: [Page, string][] = [
  ["controller", "Controller"],
  ["show", "Show"],
  ["live", "Fixtures"],
  ["positions", "Positions"],
  ["midi", "MIDI"],
  ["setup", "Dimensions"],
  ["diagnostics", "Diagnostics"],
];

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [fixtures, setFixtures] = useState<FixtureInfo[] | null>(null);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(true);
  const [page, setPage] = useState<Page>("controller");
  const [locks, setLocks] = useState<Record<string, string[]>>({});
  const receiveShow = useCallback((show: ShowState) => setLocks(show.locks), []);

  // Runs an action, then refreshes status (and fixture values when the action may change them).
  const run = useCallback(async (action: () => Promise<unknown>, refreshFixtures = false) => {
    try {
      await action();
      setStatus(await getStatus());
      if (refreshFixtures) {
        const [fixtures, groups] = await Promise.all([getFixtures(), getGroups()]);
        setFixtures(fixtures);
        setGroups(groups);
      }
      setError(null);
      setConnected(true);
    } catch (e) {
      if (e instanceof ConnectionError) setConnected(false);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void run(async () => {
      setFixtures(await getFixtures());
      setGroups(await getGroups());
      setScenes(await getScenes());
    });
  }, [run]);

  // Poll so sliders follow fades and changes made from the MIDI controller. The controller
  // page polls its own data, so fixture values are only fetched where they are shown.
  const fading = status?.transition_active ?? false;
  const showsFixtures = page === "live" || page === "positions";
  useEffect(() => {
    const timer = window.setInterval(
      () => void run(async () => {}, showsFixtures),
      fading ? 250 : 600,
    );
    return () => window.clearInterval(timer);
  }, [fading, run, showsFixtures]);

  // Slow heartbeat so a lost or restarted backend is noticed; the UI recovers by itself.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          setStatus(await getStatus());
          setConnected((was) => {
            if (!was) {
              void getFixtures().then(setFixtures).catch(() => {});
              void getGroups().then(setGroups).catch(() => {});
              void getScenes().then(setScenes).catch(() => {});
            }
            return true;
          });
        } catch {
          setConnected(false);
        }
      })();
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  const blackout = status?.blackout_active ?? false;
  const simulated = status ? !status.physical_output_enabled : true;
  const master = status?.master ?? 1;
  const previewing = status?.preview_active ?? false;
  // The Controller page shows scene, master and blackout on the controller itself.
  const onController = page === "controller";

  const panel = (fixture: FixtureInfo) => (
    <FixturePanel
      key={fixture.id}
      fixture={fixture}
      lockedAttributes={locks[fixture.id] ?? []}
      onChange={(id, values) => void run(() => setFixtureState(id, values))}
      onRename={(id, name) => void run(() => renameFixture(id, name), true)}
    />
  );

  return (
    <main className={[onController ? "wide" : "", previewing ? "previewing" : ""].join(" ")}>
      <OlaSetupNotice />
      <header className="topbar">
        <img className="app-logo" src="/lichtwerk-header.png" alt="licht.werk" width={40} height={40} />
        {simulated && <div className="chip simulated">
          {status?.output_mode === "ola" ? "OUTPUT DISABLED" : "SIMULATED"}
        </div>}
        <nav className="tabs">
          {PAGES.map(([id, title]) => (
            <button key={id} className={page === id ? "tab active" : "tab"} onClick={() => setPage(id)}>
              {title}
            </button>
          ))}
        </nav>
        <button
          className={previewing ? "preview-button go" : "preview-button"}
          title={previewing ? "Send the prepared preview live now" : "Prepare changes without sending them"}
          onClick={() => void run(() => setPreview(!previewing), true)}
        >
          {previewing ? "GO" : "PREVIEW"}
        </button>
        {previewing && <span className="chip preview">PREVIEW — not live</span>}
        {!onController && (<>
        <span className="scene-indicator">
          Scene: {status?.active_scene ? scenes.find((s) => s.id === status.active_scene)?.name ?? status.active_scene : "—"}
          {fading && <span className="fading"> FADING…</span>}
        </span>
        <label className="master">
          Master {Math.round(master * 100)}%
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(master * 100)}
            onChange={(e) => void run(() => setMaster(Number(e.target.value) / 100))}
          />
        </label>
        </>)}
        {status?.real_output_available && simulated && (
          <button
            className="arm-button"
            title={`Retry real DMX output on universe(s) ${status.real_output_universes.join(", ")}`}
            onClick={() => void run(retryOutput)}
          >
            RETRY OUTPUT
          </button>
        )}
        {!onController && (
          <button
            className={blackout ? "blackout-button active" : "blackout-button"}
            onClick={() => void run(() => setBlackout(!blackout))}
          >
            {blackout ? "RELEASE BLACKOUT" : "BLACKOUT"}
          </button>
        )}
      </header>
      <ShowStatusStrip preview={previewing} onState={receiveShow} />

      {!connected && (
        <div className="banner error">
          Connection to the LichtWerk backend lost. Lights keep their last output. Retrying…
        </div>
      )}
      {blackout && <div className="banner blackout">BLACKOUT ACTIVE</div>}
      {status && status.setup_overrides.length > 0 && (
        <div className="banner error" role="alert">
          BEAM SAFETY OVERRIDE ACTIVE: {status.setup_overrides.map((id) =>
            fixtures?.find((fixture) => fixture.id === id)?.name ?? id).join(", ")}.
          {" "}Area protection is bypassed for these heads. Disable the temporary override in Positions.
        </div>
      )}
      {status?.output_error && (
        <div className="banner error">Real output stopped: {status.output_error}</div>
      )}
      {error && (
        <div className="banner error" onClick={() => setError(null)}>
          {error} <small>(tap to dismiss)</small>
        </div>
      )}

      {page === "controller" && (
        <ControllerPage
          scenes={scenes}
          onScenesChanged={() => void getScenes().then(setScenes).catch(() => {})}
        />
      )}
      {page === "diagnostics" && <DiagnosticsPanel />}
      {page === "show" && <>
        <RoomMap />
        <ShowPanel onScenesChanged={() => void getScenes().then(setScenes).catch(() => {})} />
      </>}
      {page === "setup" && <SetupPanel />}
      {page === "midi" && <MidiPanel />}

      {(page === "live" || page === "positions") && fixtures === null && (
        <p className="muted">
          {connected
            ? "Connecting to backend…"
            : "Cannot load fixtures from the LichtWerk backend. Is it running (python -m lichtwerk serve, port 8010)?"}
        </p>
      )}

      {page === "live" && fixtures !== null && (
        <>
          {groups.length > 0 && (
            <section className="fixture">
              <h2>Group display names</h2>
              {groups.map((group) => (
                <p key={group.id}>
                  <DisplayNameEditor name={group.name}
                    onSave={(name) => void run(() => renameGroup(group.id, name), true)} />
                  <small className="muted"> · {group.fixtures.map((id) =>
                    fixtures.find((fixture) => fixture.id === id)?.name ?? id).join(", ")}</small>
                </p>
              ))}
            </section>
          )}
          {fixtures.length === 0 ? (
            <p className="muted">No fixtures patched. Add fixtures in config/patch.json.</p>
          ) : (
            fixtures.map(panel)
          )}
        </>
      )}

      {page === "positions" && fixtures !== null && (
        <>
          <PositioningPanel refreshKey={fixtures} onMoved={() => void run(async () => undefined, true)} />
        </>
      )}
    </main>
  );
}
