import { useCallback, useEffect, useState } from "react";
import {
  aimHead,
  fitCalibration,
  getHeads,
  getRoom,
  getApc40,
  getStripAssignment,
  headToRest,
  saveHead,
  setSetupOverride,
} from "./api";
import type { CalibrationFit, HeadAssignment, HeadInfo, HeadsResult, RoomDocument, StripAssignmentResult } from "./api";
import AimEditor from "./AimEditor";
import FineAim from "./FineAim";

type Observation = { pan: number; tilt: number; target: string };

interface Props {
  /** Bumped by the parent after fixture commands so pan/tilt readouts refresh. */
  refreshKey: unknown;
  /** Groups made only of moving heads; their aim and zones can be set at once. */
  groups: { name: string; members: string[] }[];
  /** Called after heads were moved, so the pan/tilt sliders refresh. */
  onMoved: () => void;
}

const emptyAssignment: HeadAssignment = {
  aim_target: null, aim_zone: null, include_zones: [], exclude_zones: [],
};

/** Aim moving heads at targets and zones, and calibrate them. All geometry is done by the backend. */
export default function PositioningPanel({ refreshKey, groups, onMoved }: Props) {
  const [room, setRoom] = useState<RoomDocument | null>(null);
  const [heads, setHeads] = useState<HeadInfo[]>([]);
  const [observations, setObservations] = useState<Record<string, Observation[]>>({});
  const [fits, setFits] = useState<Record<string, CalibrationFit>>({});
  const [fade, setFade] = useState(2);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [strips, setStrips] = useState<(StripAssignmentResult & { label: string })[]>([]);
  const [overrideHead, setOverrideHead] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([getRoom(), getHeads(), getApc40().then(async (apc) => {
      const positions = apc.strips.filter((strip) => strip.family === "heads");
      return Promise.all(positions.flatMap((strip) => strip.target
        ? [getStripAssignment(`apc40:${strip.track}`, strip.target).then((result) => ({
          ...result, label: strip.label,
        }))] : []));
    })])
      .then(([r, h, settings]) => {
        setRoom(r);
        setHeads(h);
        setStrips(settings);
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

  const onResult = (result: HeadsResult, assigned: boolean) => {
    setError(null);
    setHeads((current) => current.map((h) => result.heads.find((r) => r.id === h.id) ?? h));
    if (assigned) {
      setNotes(result.notes);
      reload();
      onMoved();
    } else if (result.notes.length) {
      setNotes(result.notes);
    }
  };

  if (!room) return error ? <p className="error">{error}</p> : null;

  return (
    <section className="fixture">
      <h2>Moving-head positioning</h2>
      {error && <p className="error">{error}</p>}
      {notes.map((note) => (
        <p key={note} className="muted">⚠ {note}</p>
      ))}
      <label>
        Move time: {fade} s
        <input type="range" min={0} max={10} step={0.5} value={fade}
          onChange={(e) => setFade(Number(e.target.value))} />
      </label>
      {groups.map((group) => {
        const members = heads.filter((h) => group.members.includes(h.id));
        const strip = strips.find((s) => s.target === group.name);
        return (
          <AimEditor
            key={group.name}
            target={group.name}
            title={`All heads in “${strip?.label ?? group.name}”`}
            current={strip?.assignment ?? room.assignments[group.name] ?? emptyAssignment}
            owner={strip?.owner}
            active={strip?.active}
            spot={[members[0]?.spot_u ?? 0.5, members[0]?.spot_v ?? 0.5]}
            room={room}
            fade={fade}
            onResult={onResult}
            onError={setError}
          />
        );
      })}
      {heads.map((head) => {
        const recorded = observations[head.id] ?? [];
        const fit = fits[head.id];
        const strip = strips.find((s) => s.target === head.id);
        return (
          <div key={head.id} className="head">
            <h3>{head.name}</h3>
            <p className="muted">
              {head.calibrated ? "Calibrated" : "NOT calibrated"} · pan {head.pan.toFixed(3)} · tilt{" "}
              {head.tilt.toFixed(3)}
              {head.floor_point &&
                ` · beam on floor at x ${head.floor_point.x.toFixed(2)} m, y ${head.floor_point.y.toFixed(2)} m`}
              {!head.setup.position && " · mounting position missing (Setup page)"}
            </p>
            <p className={head.setup_override || head.guard_reason || head.beam_blocked_by ? "error" : "muted"}>
              {head.setup_override ? "CALIBRATION OVERRIDE – area blanking bypassed"
                : head.guard_reason ? `BEAM OFF – ${head.guard_reason}`
                : head.beam_blocked_by ? `BEAM OFF – excluded zone “${head.beam_blocked_by}”`
                : `Area mask active${head.beam_hit ? ` · beam hits x ${head.beam_hit.x.toFixed(2)}, y ${head.beam_hit.y.toFixed(2)}, z ${head.beam_hit.z.toFixed(2)} m` : ""}`}
            </p>
            <AimEditor
              target={head.id}
              title="Aim and zones"
              current={strip?.assignment ?? head.assignment}
              owner={strip?.owner}
              active={strip?.active}
              spot={[head.spot_u, head.spot_v]}
              room={room}
              fade={fade}
              onResult={onResult}
              onError={setError}
            />
            <button className={head.setup_override ? "danger" : ""}
              onClick={() => head.setup_override
                ? void run(() => setSetupOverride(head.id, false))
                : setOverrideHead(head.id)}>
              {head.setup_override ? "Disable calibration override" : "Calibration override..."}
            </button>
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
                    <button
                      disabled={!measured || !head.calibrated}
                      title={measured ? "" : "No coordinates yet (config/room.json)"}
                      onClick={() => run(() => aimHead(head.id, target.id, fade))}
                    >
                      {target.name}
                    </button>
                    {measured && (
                      <button
                        className="small"
                        title="Record the head's current pan/tilt as pointing at this target"
                        onClick={() => record(head, target.id)}
                      >
                        {recorded.some((o) => o.target === target.id) ? "✔ rec" : "rec"}
                      </button>
                    )}
                  </span>
                );
              })}
              <button disabled={!head.setup.rest} onClick={() => run(() => headToRest(head.id, fade))}>
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
          </div>
        );
      })}
    </section>
  );
}
