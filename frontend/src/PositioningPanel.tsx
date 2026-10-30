import { useCallback, useEffect, useState } from "react";
import {
  fitCalibration,
  getHeads,
  getRoom,
  headToRest,
  saveHead,
  setSetupOverride,
} from "./api";
import type { CalibrationFit, HeadInfo, RoomDocument } from "./api";
import FineAim from "./FineAim";

type Observation = { pan: number; tilt: number; target: string };

interface Props {
  /** Bumped by the parent after fixture commands so pan/tilt readouts refresh. */
  refreshKey: unknown;
  /** Called after heads were moved, so the pan/tilt sliders refresh. */
  onMoved: () => void;
}

/** Fine aim, rest position and calibration of moving heads. Aiming at targets lives on the controller. */
export default function PositioningPanel({ refreshKey, onMoved }: Props) {
  const [room, setRoom] = useState<RoomDocument | null>(null);
  const [heads, setHeads] = useState<HeadInfo[]>([]);
  const [observations, setObservations] = useState<Record<string, Observation[]>>({});
  const [fits, setFits] = useState<Record<string, CalibrationFit>>({});
  const [error, setError] = useState<string | null>(null);
  const [overrideHead, setOverrideHead] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([getRoom(), getHeads()])
      .then(([r, h]) => {
        setRoom(r);
        setHeads(h);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(reload, [reload, refreshKey]);

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const record = (head: HeadInfo, target: string) =>
    setObservations((current) => ({
      ...current,
      [head.id]: [
        ...(current[head.id] ?? []).filter((o) => o.target !== target),
        { pan: head.pan, tilt: head.tilt, target },
      ],
    }));

  if (!room) return error ? <p className="error">{error}</p> : null;

  return (
    <section className="fixture">
      <h2>Moving heads: fine aim, rest and calibration</h2>
      {error && <p className="error">{error}</p>}
      {heads.map((head) => {
        const recorded = observations[head.id] ?? [];
        const fit = fits[head.id];
        return (
          <div key={head.id} className="head">
            <h3>{head.name}</h3>
            <p className="muted">
              {head.calibrated ? "Calibrated" : "NOT calibrated"} · pan {head.pan.toFixed(3)} · tilt{" "}
              {head.tilt.toFixed(3)}
              {head.floor_point &&
                ` · beam on floor at x ${head.floor_point.x.toFixed(2)} m, y ${head.floor_point.y.toFixed(2)} m`}
              {!head.setup.position && " · mounting position missing (Dimensions page)"}
            </p>
            <p className={head.setup_override || head.guard_reason || head.beam_blocked_by ? "error" : "muted"}>
              {head.setup_override ? "CALIBRATION OVERRIDE – area blanking bypassed"
                : head.guard_reason ? `BEAM OFF – ${head.guard_reason}`
                : head.beam_blocked_by ? `BEAM OFF – excluded zone “${head.beam_blocked_by}”`
                : `Area mask active${head.beam_hit ? ` · beam hits x ${head.beam_hit.x.toFixed(2)}, y ${head.beam_hit.y.toFixed(2)}, z ${head.beam_hit.z.toFixed(2)} m` : ""}`}
            </p>
            {head.setup_override && <button className="danger"
              onClick={() => void run(() => setSetupOverride(head.id, false))}>
              Disable calibration override
            </button>}
            <FineAim
              head={head}
              onMoved={() => {
                reload();
                onMoved();
              }}
              onError={setError}
            />
            <div className="targets">
              {room.targets.map((target) => {
                const measured = target.x !== null;
                return (
                  <span key={target.id} className="target">
                    {measured ? (
                      <button
                        title="Record the head's current pan/tilt as pointing at this target"
                        onClick={() => record(head, target.id)}
                      >
                        {recorded.some((o) => o.target === target.id) ? `✔ ${target.name}` : `Record ${target.name}`}
                      </button>
                    ) : <span className="muted" title="No coordinates yet (Dimensions page)">{target.name}: no coordinates</span>}
                  </span>
                );
              })}
              <button disabled={!head.setup.rest} onClick={() => run(() => headToRest(head.id, 2))}>
                Rest
              </button>
              <button
                className="small"
                title="Save the head's current pan/tilt as its rest position"
                onClick={() =>
                  run(() =>
                    saveHead(head.id, { ...head.setup, rest: { pan: head.pan, tilt: head.tilt } }),
                  )
                }
              >
                Save as rest
              </button>
            </div>
            {head.setup.rest && (
              <p className="muted">
                Rest: pan {head.setup.rest.pan.toFixed(3)} · tilt {head.setup.rest.tilt.toFixed(3)}
              </p>
            )}
            <details className="head-setup">
              <summary>Rest, calibration override and fit</summary>
            {!head.setup_override && <button onClick={() => setOverrideHead(head.id)}>Calibration override...</button>}
            {overrideHead === head.id && (
              <div role="alertdialog" aria-label="Enable calibration override" className="banner error">
                <p>Temporarily bypass area blanking for {head.name} during supervised calibration.
                  Keep people clear, check limits and disable this immediately afterwards.
                  Blackout and mechanical limits still apply. Finish preview before enabling.</p>
                <button onClick={() => void run(async () => {
                  await setSetupOverride(head.id, true);
                  setOverrideHead(null);
                })}>Enable temporary override</button>
                <button onClick={() => setOverrideHead(null)}>Cancel</button>
              </div>
            )}
            <p className="muted">
              Calibration: steer the beam with Fine aim until its centre is on a target, press “rec”
              next to it, repeat for 4–6 targets spread around the head (not straight below it),
              then fit. Keep preview off so the real head moves.
            </p>
            <button
              disabled={recorded.length < 3 || !head.setup.position}
              onClick={() =>
                run(async () => {
                  const result = await fitCalibration(head.id, recorded);
                  setFits((c) => ({ ...c, [head.id]: result }));
                })
              }
            >
              Fit calibration ({recorded.length} recorded)
            </button>
            {fit && (
              <div>
                <p className={fit.acceptable ? "muted" : "error"}>
                  Worst error {fit.max_deg.toFixed(1)}° (average {fit.rms_deg.toFixed(1)}°) —{" "}
                  {fit.acceptable ? "acceptable" : "too large: re-check the recorded targets"}
                </p>
                <button
                  disabled={!fit.acceptable}
                  onClick={() =>
                    run(() => saveHead(head.id, { ...head.setup, calibration: fit.calibration }))
                  }
                >
                  Save calibration
                </button>
              </div>
            )}
            </details>
          </div>
        );
      })}
    </section>
  );
}
