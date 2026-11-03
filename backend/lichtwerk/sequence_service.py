"""One independent show timeline per live/preview context, using the central tick."""

import time
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from threading import RLock
from typing import Optional

from .scene_service import SceneService
from .scenes import Scene, resolve_scene
from .sequences import BEAT_VALUES, Sequence, SequenceStore
from .tempo import TempoClock
from .positioning import PositioningError
from .rings import (
    expand_legacy_ring_attributes,
    expand_legacy_ring_values,
    ring_segments,
)


@dataclass
class Run:
    definition: Sequence
    scenes: list[Scene]
    fades: list[float]
    attributes: dict[str, set[str]]
    last_time: float
    last_beat: float
    status: str = "running"
    index: int = 0
    phase: str = "fade"
    elapsed: float = 0.0
    starts: dict[str, dict[str, float]] = field(default_factory=dict)
    targets: dict[str, dict[str, float]] = field(default_factory=dict)
    released: dict[str, set[str]] = field(default_factory=dict)
    overridden: dict[str, set[str]] = field(default_factory=dict)
    error: Optional[str] = None


class SequenceService:
    def __init__(
        self, scenes: SceneService, store: SequenceStore, tempo: TempoClock,
        clock: Callable[[], float] = time.monotonic,
        output_error: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self._scenes = scenes
        self._controller = scenes._controller
        self.store = store
        self._tempo = tempo
        scenes._tempo = tempo
        self._clock = clock
        self._output_error = output_error or (lambda: None)
        self._live: Optional[Run] = None
        self._preview: Optional[Run] = None
        self._lock = RLock()
        self._speed = 1.0
        scenes.sequences = self
        scenes.store.delete_guard = self._guard_scene_delete
        self._controller.add_tick_listener(self.tick)

    def _guard_scene_delete(self, scene_id: str) -> None:
        references = self.store.scene_references(scene_id)
        if references:
            raise ValueError(f"Scene is referenced by sequences: {', '.join(references)}.")

    def set_speed(self, speed: float) -> None:
        """Scale timed holds without changing fades or the shared beat clock."""
        if isinstance(speed, bool) or speed not in (0.25, 0.5, 1.0, 2.0, 4.0):
            raise ValueError("Sequence speed must be 0.25, 0.5, 1, 2 or 4.")
        with self._locked():
            if speed == self._speed:
                return
            self.tick()
            for run in (self._live, self._preview):
                if run is not None and run.phase == "hold":
                    run.elapsed *= self._speed / speed
            self._speed = speed

    def _duration(self, run: Run) -> float:
        if run.phase == "fade":
            return run.fades[run.index]
        return 0.0 if run.phase == "wait" else run.definition.steps[run.index].hold / self._speed

    # Lock order: APC strip lock (looks) before the sequence lock. A look step switches strip
    # modes and starts FX, which needs the strip lock while the sequence lock is held.
    def _host_lock(self):
        looks = self._scenes.looks
        return looks.lock if looks is not None else None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Blocking: for user actions that may enter a step (start, NEXT)."""
        host = self._host_lock()
        with host if host is not None else nullcontext(), self._lock:
            yield

    def _run(self, live: bool = False) -> Optional[Run]:
        return self._preview if self._controller.editing_preview() and not live else self._live

    def _set_run(self, run: Optional[Run]) -> None:
        if self._controller.editing_preview():
            self._preview = run
        else:
            self._live = run

    def start_preview(self) -> None:
        with self._lock:
            self._preview = deepcopy(self._live)

    def go(self) -> None:
        with self._lock:
            self._live = self._preview
            self._preview = None

    def _snapshot(self, sequence_id: str) -> Run:
        definition = self.store.get(sequence_id)
        snapshots = [self._scenes.store.get(step.scene_id) for step in definition.steps]
        fades = []
        attributes: dict[str, set[str]] = {}
        for step, scene in zip(definition.steps, snapshots):
            duration = step.fade
            if duration is None:
                duration = scene.fade_beats if scene.fade_timing == "beats" else scene.fade_seconds
                if definition.timing != scene.fade_timing:
                    if definition.timing == "beats" and duration:
                        beats = duration * self._tempo.bpm() / 60.0
                        duration = next((value for value in BEAT_VALUES if value >= beats), 32.0)
                    elif definition.timing == "seconds":
                        duration *= 60.0 / self._tempo.bpm()
            fades.append(duration)
            for fixture_id, names in self._scenes.scene_attributes(scene).items():
                fixture = self._controller.patch.get(fixture_id)
                controls = expand_legacy_ring_attributes(
                    fixture, set(definition.live_controls.get(fixture_id, []))
                )
                attributes.setdefault(fixture_id, set()).update(names - controls)
            if scene.positions and self._scenes._positions is not None:
                self._scenes._positions.validate_scene_positions({
                    key: value.model_dump() for key, value in scene.positions.items()
                })
        return Run(definition, snapshots, fades, attributes, self._clock(), self._tempo.beats())

    def start(self, sequence_id: str) -> dict:
        with self._locked():
            run = self._snapshot(sequence_id)
            if not self._controller.editing_preview() and self._output_error():
                raise ValueError("Output has failed. Restore output before starting the sequence.")
            self.tick()
            try:
                self._enter(run)
                self.stop(advance=False)
                self._set_run(run)
                self._scenes.claim({key: set(values) for key, values in run.targets.items()}, f"sequence:{sequence_id}")
                self._advance(run, 0.0)
            except (ValueError, KeyError, PositioningError) as error:
                if self._run() is run or self._run() is None:
                    self._set_run(run)
                    run.status, run.error = "paused", str(error)
                raise
            return self.status()

    def restart(self) -> Optional[dict]:
        run = self._run()
        if run is None:
            return None
        return self.start(run.definition.id)

    def toggle(self) -> Optional[dict]:
        run = self._run()
        if run is None:
            return None
        if run.status == "running":
            return self.pause()
        if run.status == "paused":
            return self.resume()
        return self.start(run.definition.id)

    def launch_pad(self, sequence_id: str) -> Optional[dict]:
        run = self._run()
        if run is not None and run.definition.id == sequence_id and run.status in ("running", "paused"):
            return self.toggle()
        return self.start(sequence_id)

    def apply_now(self) -> Optional[dict]:
        return self.restart()

    def pause(self) -> Optional[dict]:
        with self._lock:
            self.tick()
            run = self._run()
            if run is not None and run.status == "running":
                run.status = "paused"
            return self.status()

    def resume(self) -> Optional[dict]:
        with self._lock:
            run = self._run()
            if run is not None and run.status == "paused":
                error = None if self._controller.editing_preview() else self._output_error()
                if error:
                    raise ValueError(f"Output is still unavailable: {error}")
                run.status = "running"
                run.error = None
                run.last_time, run.last_beat = self._clock(), self._tempo.beats()
            return self.status()

    def stop(self, *, advance: bool = True) -> Optional[dict]:
        with self._lock:
            if advance:
                self.tick()
            run = self._run()
            if run is not None and run.status != "stopped":
                run.status = "stopped"
                self._scenes.release_control(owner=f"sequence:{run.definition.id}")
            return self.status()

    def stop_if_overlap(self, attributes: dict[str, set[str]], *, advance: bool = True) -> None:
        run = self._run()
        if run is not None and run.status not in ("stopped",):
            if any(names & run.attributes.get(key, set()) - run.released.get(key, set()) - self._scenes.global_mask().get(key, set()) for key, names in attributes.items()):
                self.stop(advance=advance)

    def release_attributes(self, released: dict[str, set[str]]) -> None:
        run = self._run()
        if run is not None:
            for key, names in released.items():
                run.released.setdefault(key, set()).update(names)

    def override_attributes(self, attributes: dict[str, set[str]]) -> None:
        run = self._run()
        if run is not None and run.status in ("running", "paused"):
            for key, names in attributes.items():
                run.overridden.setdefault(key, set()).update(names)

    def _enter(self, run: Run) -> None:
        scene = run.scenes[run.index]
        looks = self._scenes.looks if scene.fx is not None else None
        if looks is not None:
            looks.prepare(scene)  # strip modes first, as a direct look recall does
        run.overridden = {}
        selected = self._scenes.scene_attributes(scene)
        allowed = {
            key: names
            - run.released.get(key, set())
            - self._scenes.global_mask().get(key, set())
            - expand_legacy_ring_attributes(
                self._controller.patch.get(key),
                set(run.definition.live_controls.get(key, [])),
            )
            for key, names in selected.items()
        }
        resolved = resolve_scene(scene, self._controller.patch)
        resolved = {
            key: expand_legacy_ring_values(self._controller.patch.get(key), values)
            for key, values in resolved.items()
        }
        for key, values in resolved.items():
            if len(ring_segments(self._controller.patch.get(key))) == 6:
                for colour in ("red", "green", "blue"):
                    values.pop(f"ring_{colour}", None)
        scene = scene.model_copy(update={
            "groups": {},
            "fixtures": {
                key: {name: value for name, value in values.items() if name in allowed.get(key, set())}
                for key, values in resolved.items()
            },
            "positions": {
                key: value for key, value in scene.positions.items()
                if {"pan", "tilt"} <= allowed.get(key, set())
            },
        }, deep=True)
        run.phase, run.elapsed = "fade", 0.0
        before = self._controller.snapshot()
        with self._controller.controlled_writes(), self._controller.batch():
            self._scenes.apply_snapshot(scene, 0.0, owner=f"sequence:{run.definition.id}", claim=False)
            after = self._controller.snapshot()
            run.starts, run.targets = {}, {}
            for key, names in selected.items():
                run.starts[key] = {name: before[key][name] for name in allowed[key]}
                run.targets[key] = {name: after[key][name] for name in allowed[key]}
            if run.fades[run.index] > 0:
                self._controller.apply(run.starts, 0.0, preserve_aim=True)
        if looks is not None:
            self._scenes.report_look(scene, looks.apply(scene))

    def _render_progress(self, run: Run, fraction: float) -> None:
        changes = {}
        for key, values in run.targets.items():
            allowed = set(values) - run.released.get(key, set()) - run.overridden.get(key, set()) - self._scenes.global_mask().get(key, set())
            changes[key] = {
                name: values[name] if fraction >= 1.0 else run.starts[key][name] + (values[name] - run.starts[key][name]) * fraction
                for name in allowed
            }
        with self._controller.controlled_writes():
            self._controller.apply(changes, 0.0, preserve_aim=True)

    def _step_forward(self, run: Run) -> None:
        """Enter the following step (or complete a one-shot sequence after its last step)."""
        next_index = run.index + 1
        if next_index == len(run.scenes):
            if not run.definition.loop:
                run.status = "completed"
                return
            next_index = 0
        run.index = next_index
        self._enter(run)
        self._scenes.claim({key: set(values) for key, values in run.targets.items()}, f"sequence:{run.definition.id}")

    def _advance(self, run: Run, delta: float) -> None:
        # Zero-duration seconds steps are valid. Bound a zero-time loop to one cycle.
        zero_steps = 0
        while run.status == "running":
            if run.phase == "wait":
                return  # only NEXT moves on
            duration = self._duration(run)
            remaining = max(0.0, duration - run.elapsed)
            consumed = min(delta, remaining)
            run.elapsed += consumed
            delta -= consumed
            if run.phase == "fade":
                self._render_progress(run, min(1.0, run.elapsed / duration) if duration else 1.0)
            if run.elapsed < duration:
                return
            if run.phase == "fade":
                run.phase, run.elapsed = ("wait" if run.definition.steps[run.index].wait else "hold"), 0.0
            else:
                self._step_forward(run)
            if consumed == 0:
                zero_steps += 1
                if zero_steps > len(run.scenes) * 2:
                    return
            else:
                zero_steps = 0
            if delta == 0 and run.phase != "wait":
                duration = self._duration(run)
                if duration:
                    return

    def tick(self) -> None:
        # Never wait for the strip lock here: the tick may run while other locks are held.
        # If the APC is busy, skip this tick; the next one catches up on the elapsed time.
        host = self._host_lock()
        if host is not None and not host.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            if host is not None:
                host.release()

    def next(self) -> Optional[dict]:
        """NEXT: go to the following step now, whether the step waits or is timed (the user wins)."""
        with self._locked():
            self.tick()
            run = self._run()
            if run is None or run.status not in ("running", "paused"):
                raise ValueError("NEXT: no sequence is running.")
            if run.status == "paused":
                error = None if self._controller.editing_preview() else self._output_error()
                if error:
                    raise ValueError(f"Output is still unavailable: {error}")
                run.status, run.error = "running", None
            run.last_time, run.last_beat = self._clock(), self._tempo.beats()
            try:
                self._step_forward(run)
                self._advance(run, 0.0)
            except (ValueError, KeyError, PositioningError) as error:
                run.status, run.error = "paused", str(error)
                raise
            return self.status()

    def _tick(self) -> None:
        with self._lock:
            now, beat = self._clock(), self._tempo.beats()
            for preview, run in ((False, self._live), (True, self._preview)):
                if run is None or run.status != "running":
                    continue
                with self._controller.editing_context(preview):
                    error = None if preview else self._output_error()
                    if error:
                        run.status, run.error = "paused", str(error)
                        continue
                    delta = max(0.0, beat - run.last_beat) if run.definition.timing == "beats" else max(0.0, now - run.last_time)
                    run.last_time, run.last_beat = now, beat
                    try:
                        self._advance(run, delta)
                    except (ValueError, KeyError, PositioningError) as error:
                        run.status, run.error = "paused", str(error)

    def beat(self) -> dict:
        """Tempo for on-screen beat-synchronised indicators (e.g. the NEXT blink)."""
        return {"bpm": self._tempo.bpm(), "phase": self._tempo.beats() % 1.0}

    def status(self, live: bool = False) -> Optional[dict]:
        run = self._run(live)
        if run is None:
            return None
        step = run.definition.steps[run.index]
        waiting = run.phase == "wait" and run.status in ("running", "paused")
        duration = self._duration(run)
        remaining = max(0.0, duration - run.elapsed) if run.status not in ("completed", "stopped") else 0.0
        # Time until the next step starts by itself (None: it waits for NEXT).
        until_next: Optional[float] = None
        if run.status in ("running", "paused") and not step.wait:
            until_next = remaining + (step.hold / self._speed if run.phase == "fade" else 0.0)
        following = run.index + 1 if run.index + 1 < len(run.scenes) else (0 if run.definition.loop else None)
        return {
            "waiting": waiting, "step_count": len(run.scenes), "step_wait": step.wait,
            "until_next": until_next,
            "countdown_step": 1.0 if all(
                abs(value - round(value)) < 1e-9
                for value in (run.fades[run.index], 0.0 if step.wait else step.hold / self._speed)
            ) else 0.5,
            "until_next_seconds": None if until_next is None else (
                until_next * 60.0 / self._tempo.bpm() if run.definition.timing == "beats" else until_next),
            "next_scene_name": run.scenes[following].name if following is not None else None,
            "id": run.definition.id, "name": run.definition.name, "status": run.status,
            "step_index": run.index, "phase": run.phase, "progress": min(1.0, run.elapsed / duration) if duration else 1.0,
            "remaining": remaining,
            "remaining_seconds": remaining * 60.0 / self._tempo.bpm() if run.definition.timing == "beats" else remaining,
            "scene_name": run.scenes[run.index].name, "error": run.error,
            "timing": run.definition.timing, "loop": run.definition.loop,
        }
