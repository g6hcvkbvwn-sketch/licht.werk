"""Fixed operating logic of the AKAI APC40 MKII (project.md section 6.3).

Pure application logic: no MIDI library. The hardware and the on-screen APC40 send the same
`MidiEvent`s. The LED state the controller should show is computed here too (`leds`) and sent by
apc40_feedback.py.

Strips (tracks 1-8): LED bar, heads group, head 1, head 2, spots group, spot 1, spot 2, hazer.
Heads and spots each have a group/single mode: only the active strips act. Every strip keeps its
own controller set points (fader, knobs, colour, strobe): group and single strips never follow
each other, because the faders and knobs are not motorised. Moving a control of an inactive strip
only stores its set point; when a strip becomes active, all its known set points apply.

SESSION RECORD toggles preview mode (press again = Go). Flash acts live, also in preview.

Effects (Milestone 10): SCENE LAUNCH 1-5 open the effects page of one effect type (press the
shown type again = close; CLIP STOP closes it too). Each track column of the pads then holds
that type's 5 presets (top row = preset 1); a pad starts or stops a preset. A SCENE LAUNCH
button blinks while any strip runs an effect of its type. The device knobs edit the running
effect of the strip picked with TRACK SELECT (or the strip whose pad was pressed last); that
TRACK SELECT blinks slowly for running FX and faster for FX editing. SHIFT + TRACK SELECT
opens target/zone pads on head strips. STOP ALL CLIPS stops the chosen strip's effects.
TAP TEMPO and the TEMPO knob set the shared BPM; METRONOME displays the beat.
"""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal, Optional

from . import apc40 as hw
from .apc40_pads import PadStore
from .colour import ColourControl, has_rgb
from .effect_engine import EffectEngine, RunningEffect
from .effects import (
    EFFECT_TYPES,
    MOVEMENT_TYPES,
    PARAM_LABELS,
    EffectType,
    fits,
    knob_params,
    speed_level,
    family_presets,
    FxDefinition,
)
from .fx_service import FxService
from .fixture_service import FixtureController, FixtureDisabledError
from .midi_control import MidiEvent, relative_steps
from .midi_mapping import target_fixtures
from .patch import ResolvedFixture
from .position_service import PositionService
from .positioning import PositioningError
from .preview import PreviewService
from .room import HeadAssignment
from .rings import ring_segments, segment_participant
from .scene_service import SceneService
from .service import LightingService
from .strobe import strobe_level

logger = logging.getLogger(__name__)

# After (re)connect the APC40 reports where its faders are. Those reports only update the
# remembered positions, so lights do not jump just because the controller was plugged in.
CONNECT_QUIET_SECONDS = 1.0

STROBE_MIN_HZ = 1.0
STROBE_MAX_HZ = 20.0
STROBE_DEFAULT_HZ = 10.0
STROBE_STEP_HZ = 0.5  # per CUE LEVEL detent

BLINK_SECONDS = 0.5  # app-side blinking of the active scene's pad
TEMPO_STEP_BPM = 0.1  # per TEMPO knob detent
RING_PALETTE = tuple(
    (name.title(), velocity)
    for name, velocity in hw.SCENE_PAD_COLOURS.items()
) + (("Black", 1),)
RING_LED_COLOURS = tuple(
    (velocity, tuple(int(hw.PALETTE[velocity][i:i + 2], 16) / 255 for i in (0, 2, 4)))
    for _, velocity in RING_PALETTE
)


@dataclass(frozen=True)
class Strip:
    track: int  # 1-8
    label: str
    target: Optional[str] = None  # fixture id or group name
    family: Optional[str] = None  # "heads" / "spots": strips sharing a group/single mode
    group: bool = False  # the group strip of its family


STRIPS: tuple[Strip, ...] = (
    Strip(1, "LED bar", "led-bar-1"),
    Strip(2, "Heads", "moving-heads", "heads", group=True),
    Strip(3, "Head 1", "moving-head-1", "heads"),
    Strip(4, "Head 2", "moving-head-2", "heads"),
    Strip(5, "DJ spots", "dj-spots", "spots", group=True),
    Strip(6, "Spot 1", "dj-spot-1", "spots"),
    Strip(7, "Spot 2", "dj-spot-2", "spots"),
    Strip(8, "Hazer", "hazer-1"),
)

@dataclass
class StripSettings:
    """One strip's own controller set points; None = never set on this strip."""

    intensity: Optional[float] = None  # fader
    hue: Optional[float] = None
    saturation: Optional[float] = None
    white_pad: bool = False  # the white colour pad was chosen last
    colour_pad: Optional[int] = None  # colour pad chosen last (cleared by the colour knobs)
    strobe: Optional[bool] = None
    knobs: dict[str, float] = field(default_factory=dict)  # white, speed, pan, tilt
    ring_intensity: Optional[float] = None
    ring_strobe: Optional[bool] = None
    ring_colours: dict[int, tuple[float, float, float]] = field(default_factory=dict)


# Device knob functions by kind of fixture (knob number 1-8 -> function).
HEAD_KNOBS = {1: "pan", 2: "tilt", 5: "speed", 6: "white"}
RGBW_KNOBS = {1: "white", 2: "hue"}
RGB_KNOBS = {1: "hue"}
KNOB_LABELS = {"pan": "Pan", "tilt": "Tilt", "speed": "Speed", "white": "White", "hue": "Fine hue"}
KNOB_RINGS = {"pan": hw.RING_SINGLE, "tilt": hw.RING_SINGLE, "hue": hw.RING_SINGLE,
              "speed": hw.RING_VOLUME, "white": hw.RING_VOLUME}

# (kind, channel, number) -> value: the LED state of the whole controller.
LedKey = tuple[str, int, int]
LedState = dict[LedKey, int]

EXPECTED_ERRORS = (KeyError, ValueError, FixtureDisabledError, PositioningError)


def effect_owner(track: int) -> str:
    return f"apc40:{track}"


def param_level(effect: RunningEffect, name: str) -> float:
    """Knob position (0-1) of an effect parameter."""
    if name == "speed":
        if effect.params.target_ids:
            return (min(1.0, max(0.0, math.log(120 / effect.params.travel) / math.log(120 / 0.25)))
                    if effect.params.timing == "seconds" else speed_level(effect.params.travel))
        if effect.params.timing == "seconds":
            return math.log(120 / effect.params.seconds) / math.log(120 / 0.25)
        return speed_level(effect.params.beats)
    return float(getattr(effect.params, name))


def effect_knobs(page: EffectType, running: Optional[RunningEffect]) -> dict[int, str]:
    knobs = knob_params(running.kind if running else page)
    if running is not None:
        if (running.participant_count or len(running.fixtures)) < 2:
            knobs.pop(5, None)
        if page is EffectType.CHASE:
            if running.params.shape != "legacy":
                knobs.pop(6, None)
            if running.params.maximum == 0:
                knobs.pop(2, None)
        if running.params.target_ids and running.params.pattern == "none":
            knobs.pop(2, None)
    return knobs


def knob_functions(fixtures: list[ResolvedFixture]) -> dict[int, str]:
    attributes = set().union(*(f.mode.attributes() for f in fixtures)) if fixtures else set()
    if {"pan", "tilt"} <= attributes:
        return HEAD_KNOBS
    if "white" in attributes and any(has_rgb(f) for f in fixtures):
        return RGBW_KNOBS
    if any(has_rgb(f) for f in fixtures):
        return RGB_KNOBS
    return {}


def white_pad_changes(fixtures: list[ResolvedFixture]) -> dict[str, dict[str, float]]:
    """The white colour pad: the W LED where present, else full RGB."""
    return {
        f.patch.id: (
            {"red": 0.0, "green": 0.0, "blue": 0.0, "white": 1.0}
            if "white" in f.mode.attributes()
            else {"red": 1.0, "green": 1.0, "blue": 1.0}
        )
        for f in fixtures
    }


class Apc40Control:
    def __init__(
        self,
        controller: FixtureController,
        scenes: SceneService,
        service: LightingService,
        positions: PositionService,
        pads: PadStore,
        clock: Callable[[], float] = time.monotonic,
        strips: tuple[Strip, ...] = STRIPS,
        preview: Optional[PreviewService] = None,
        effects: Optional[EffectEngine] = None,
        fx_service: Optional[FxService] = None,
    ) -> None:
        self._controller = controller
        self._effects = effects
        self.fx_service = fx_service
        self._scenes = scenes
        self._service = service
        self._positions = positions
        self._pads = pads
        self._preview = preview
        self._clock = clock
        self._strips = {s.track: s for s in strips}
        self._colour = ColourControl(controller)
        self._lock = RLock()
        self._modes = {"heads": "group", "spots": "group"}
        self._selected: Optional[int] = None
        self._fx_open = False  # effects page shown on the pads (SCENE LAUNCH)
        self._fx_type = EffectType.CHASE
        self._fx_track: Optional[int] = None  # strip the device knobs edit on the effects page
        self._ring_tracks: set[int] = set()
        self._ring_sections: dict[int, int] = {}  # 0 = all sections
        self._position_track: Optional[int] = None
        self._position_dialog_id = 0
        self._position_dialog_kind: Optional[str] = None
        self._position_dialog_item: Optional[str] = None
        self._position_mode = "aim"
        self._show_page = "scenes"
        self._show_dialog_id = 0
        self._show_dialog_kind: Optional[str] = None
        self._show_dialog_item: Optional[str] = None
        self._show_dialog_pad = 0
        self._fx_dialog_id = 0
        self._fx_dialog: Optional[tuple[int, str, int, str]] = None
        self._shift = False
        self._error: Optional[str] = None
        self._settings = {s.track: StripSettings() for s in strips}
        self._master_fader: Optional[float] = None
        self._held: set[int] = set()  # tracks whose FLASH button is held
        self._strobe_hz = STROBE_DEFAULT_HZ
        self._quiet_until = float("-inf")
        self._last_event: Optional[MidiEvent] = None
        for strip in strips:
            if strip.target is not None and not target_fixtures(controller.patch, strip.target):
                logger.warning(
                    "APC40 track %d: '%s' is not in the patch; the strip does nothing.",
                    strip.track, strip.target,
                )
        head_strips = {
            effect_owner(strip.track): strip.target
            for strip in strips
            if strip.target is not None
            and any({"pan", "tilt"} <= set(fixture.mode.attributes()) for fixture in self.fixtures(strip.track))
        }
        active_head_strips = {
            effect_owner(strip.track): strip.target
            for strip in strips
            if effect_owner(strip.track) in head_strips
            and strip.target is not None and self.is_active(strip.track)
        }
        self._positions.initialize_strips(head_strips, active_head_strips)

    # --- state for display ------------------------------------------------------------------

    @property
    def strips(self) -> list[Strip]:
        return [self._strips[t] for t in sorted(self._strips)]

    @property
    def pads(self) -> PadStore:
        return self._pads

    @property
    def scenes(self) -> SceneService:
        return self._scenes

    @property
    def controller(self) -> FixtureController:
        return self._controller

    def master(self) -> float:
        return self._controller.master()

    @property
    def positions(self) -> PositionService:
        return self._positions

    def label(self, track: int) -> str:
        strip = self._strips[track]
        if strip.target is None:
            return strip.label
        if strip.group:
            return self._controller.patch.group_name(strip.target)
        fixtures = self.fixtures(track)
        return fixtures[0].patch.name if fixtures else strip.label

    def position_track(self) -> Optional[int]:
        return self._position_track

    def position_mode(self) -> str:
        return self._position_mode

    def show_page(self) -> str:
        return self._show_page

    def show_dialog_request(self) -> tuple[int, Optional[str], Optional[str], int]:
        return self._show_dialog_id, self._show_dialog_kind, self._show_dialog_item, self._show_dialog_pad

    def _request_show_dialog(self, kind: str, pad: int, item: Optional[str] = None) -> None:
        self._show_dialog_id += 1
        self._show_dialog_kind = kind
        self._show_dialog_item = item
        self._show_dialog_pad = pad

    def fx_dialog_request(self) -> tuple[int, Optional[tuple[int, str, int, str]]]:
        return self._fx_dialog_id, self._fx_dialog

    def shift_active(self) -> bool:
        return self._shift

    def last_error(self) -> Optional[str]:
        return self._error

    def beat_phase(self) -> Optional[float]:
        return self._effects.tempo.beats() % 1.0 if self._effects is not None else None

    def position_assignment(self, track: int) -> HeadAssignment:
        return self._positions.strip_assignment(effect_owner(track))

    def selected(self) -> Optional[int]:
        with self._lock:
            return self._selected

    def fx_open(self) -> bool:
        with self._lock:
            return self._fx_open

    def fx_track(self) -> Optional[int]:
        """Remembered strip for FX editing and STOP ALL CLIPS."""
        with self._lock:
            return self._fx_track

    def fx_type(self) -> EffectType:
        with self._lock:
            return self._fx_type

    @property
    def effects(self) -> Optional[EffectEngine]:
        return self._effects

    def bpm(self) -> Optional[float]:
        return self._effects.tempo.bpm() if self._effects is not None else None

    def effect_fixtures(self, track: int, effect_type: EffectType) -> list[ResolvedFixture]:
        """The strip's fixtures that the effect type can act on (none without an engine)."""
        if self._effects is None:
            return []
        return [f for f in self.fixtures(track) if fits(effect_type, f, component=self.fx_component(track, effect_type))]

    def ring_available(self, track: int) -> bool:
        return any("ring_intensity" in f.mode.attributes() for f in self.fixtures(track))

    def ring_selected(self, track: int) -> bool:
        return track in self._ring_tracks

    def fx_component(self, track: int, page: Optional[EffectType] = None) -> Literal["beam", "ring"]:
        return "ring" if self.ring_selected(track) and page is not EffectType.CIRCLE else "beam"

    def _toggle_ring(self, track: int) -> None:
        if not self.ring_available(track):
            return
        if track in self._ring_tracks:
            self._ring_tracks.remove(track)
        else:
            self._ring_tracks.add(track)
        self._position_track = None

    def ring_section_count(self, track: int) -> int:
        return max((len(ring_segments(f)) for f in self.fixtures(track)), default=0)

    def ring_pad(self, track: int, pad: int) -> tuple[Optional[str], bool]:
        if pad < 8:
            count = self.ring_section_count(track)
            return ("ALL" if pad == 0 else f"Section {pad}" if pad <= count else None,
                    pad == self._ring_sections.get(track, 0) and pad <= count)
        return (RING_PALETTE[pad - 8][0], False) if pad - 8 < len(RING_PALETTE) else (None, False)

    def ring_colours(self, track: int) -> list[list[float]]:
        return [
            [self._controller.state(f.patch.id).get(f"ring_{section}_{c}",
              self._controller.state(f.patch.id).get(f"ring_{c}", 0.0)) for c in ("red", "green", "blue")]
            for f in self.fixtures(track) if "ring_intensity" in f.mode.attributes()
            for section in ring_segments(f)
        ]

    def running_effect(self, track: int, effect_type: EffectType) -> Optional[RunningEffect]:
        if self._effects is None:
            return None
        return self._effects.running(effect_owner(track), effect_type, component=self.fx_component(track, effect_type))

    def effect_running(self, track: int, effect_type: Optional[EffectType] = None) -> bool:
        """An effect of the strip (of the type, or any) running and not suspended by the mode."""
        types = EFFECT_TYPES if effect_type is None else (effect_type,)
        return any(
            (effect := self.running_effect(track, t)) is not None and effect.active
            for t in types
        )

    def type_running(self, effect_type: EffectType) -> bool:
        """Any strip runs an effect of this type (its SCENE LAUNCH button blinks)."""
        return any(self.effect_running(track, effect_type) for track in range(1, hw.TRACKS + 1))

    def colour_set(self, track: int) -> bool:
        """The strip got a colour from the colour pads or knobs."""
        with self._lock:
            settings = self._settings[track]
            if self.ring_selected(track):
                return bool(settings.ring_colours)
            return settings.white_pad or settings.hue is not None

    def device_knobs(self) -> dict[int, tuple[str, str, Optional[float]]]:
        """Device knobs in use now: knob -> (function, label, value; None = unknown)."""
        with self._lock:
            if self._fx_open:
                track = self._fx_track
                running = self.running_effect(track, self._fx_type) if track is not None else None
                return {
                    knob: (
                        name, ("Travel" if name == "speed" and running and running.params.target_ids
                               else "Rate" if name == "speed" else "Depth"
                               if name == "size" and self._fx_type is EffectType.CHASE else PARAM_LABELS[name]),
                        param_level(running, name) if running else None,
                    )
                    for knob, name in effect_knobs(self._fx_type, running).items()
                }
            track = self._selected
            return {
                knob: (function, KNOB_LABELS[function],
                       self.knob_value(track, function) if track is not None else None)
                for knob, function in self.knob_functions(track).items()
            }

    def strobe_hz(self) -> float:
        with self._lock:
            return self._strobe_hz

    def mode(self, family: str) -> str:
        with self._lock:
            return self._modes[family]

    def last_event(self) -> Optional[MidiEvent]:
        with self._lock:
            return self._last_event

    def flash_held(self, track: int) -> bool:
        with self._lock:
            return track in self._held

    def is_active(self, track: int) -> bool:
        strip = self._strips.get(track)
        if strip is None or strip.target is None:
            return False
        if not self.fixtures(track):
            return False
        if strip.family is None:
            return True
        with self._lock:
            return (self._modes[strip.family] == "group") == strip.group

    def fixtures(self, track: int) -> list[ResolvedFixture]:
        """Enabled fixtures of a strip."""
        strip = self._strips.get(track)
        if strip is None or strip.target is None:
            return []
        return [f for f in target_fixtures(self._controller.patch, strip.target) if f.patch.enabled]

    def knob_functions(self, track: Optional[int]) -> dict[int, str]:
        if track is not None and self.ring_selected(track):
            return {}
        return knob_functions(self.fixtures(track)) if track is not None else {}

    def preview_active(self) -> bool:
        return self._preview is not None and self._preview.active()

    def settings(self, track: int) -> StripSettings:
        with self._lock:
            return self._settings[track]

    def fader(self, track: int) -> Optional[float]:
        """The strip's fader set point; an active strip without one shows its fixtures' level."""
        with self._lock:
            own = self._settings[track].intensity
        if own is not None or not self.is_active(track):
            return own
        return self.level(track, "pump" if track == 8 else "intensity")

    def level(self, track: int, attribute: str) -> Optional[float]:
        """Average current value of an attribute over a strip's fixtures."""
        values = [
            self._controller.state(f.patch.id)[attribute]
            for f in self.fixtures(track)
            if attribute in f.mode.attributes()
        ]
        return sum(values) / len(values) if values else None

    def _fixture_colour(self, track: int) -> Optional[tuple[float, float]]:
        rgb = [f for f in self.fixtures(track) if has_rgb(f)]
        return self._colour.hue_saturation(rgb[0].patch.id) if rgb else None

    def saturation(self, track: int) -> Optional[float]:
        """Saturation knob set point (an active strip without one shows its fixtures')."""
        if track == 8:
            return self.level(track, "fan")
        if self.ring_selected(track):
            own = self._settings[track].ring_intensity
            return own if own is not None else self.level(track, "ring_intensity")
        colour = self._fixture_colour(track)
        if colour is None:
            return None
        with self._lock:
            own = self._settings[track].saturation
        if own is not None or not self.is_active(track):
            return own
        return colour[1]

    def strobe_on(self, track: int) -> bool:
        """Active strips show whether their fixtures strobe; inactive ones their set point."""
        if not self.is_active(track):
            with self._lock:
                return bool(self._settings[track].ring_strobe if self.ring_selected(track) else self._settings[track].strobe)
        return self._strobing(track)

    def _strobing(self, track: int) -> bool:
        return any(
            self._controller.state(f.patch.id).get("ring_strobe" if self.ring_selected(track) else "strobe", 0.0) > 0.0 for f in self.fixtures(track)
        )

    def knob_value(self, track: int, function: str) -> Optional[float]:
        fixtures = self.fixtures(track)
        if not fixtures:
            return None
        with self._lock:
            settings = self._settings[track]
            own = settings.hue if function == "hue" else settings.knobs.get(function)
        if own is not None or not self.is_active(track):
            return own
        if function == "hue":
            colour = self._fixture_colour(track)
            return colour[0] if colour else None
        if function in ("pan", "tilt"):
            head = fixtures[0].patch.id
            if self._positions.has_include_zone(head):
                return self._positions.spot(head)[0 if function == "pan" else 1]
        return self.level(track, function)

    # --- input ------------------------------------------------------------------------------

    def connected(self) -> None:
        """Called on every (re)connect: ignore the fader report that follows the introduction."""
        with self._lock:
            self._quiet_until = self._clock() + CONNECT_QUIET_SECONDS
            self._shift = False

    def handle(self, event: MidiEvent, virtual: bool = False) -> None:
        """Process one event. Expected problems are logged; they never stop the lighting engine."""
        with self._lock:
            if not virtual:
                self._last_event = event
            quiet = not virtual and self._clock() < self._quiet_until
            if event.kind == "cc" or event.value > 0:
                self._error = None
            try:
                if event.kind == "cc":
                    self._cc(event, quiet)
                elif event.value > 0:
                    self._press(event)
                else:
                    self._release(event)
            except EXPECTED_ERRORS as error:
                self._error = str(error)
                logger.warning(
                    "APC40 control ignored (%s %d ch %d): %s",
                    event.kind, event.number, event.channel, error,
                )

    def _cc(self, event: MidiEvent, quiet: bool) -> None:
        number, value = event.number, event.value
        if number == hw.CC_TRACK_FADER and 1 <= event.channel <= hw.TRACKS:
            self._settings[event.channel].intensity = value / 127.0
            if not quiet and self.is_active(event.channel):
                self._apply_fader(event.channel)
        elif number == hw.CC_MASTER_FADER:
            self._master_fader = value / 127.0
            if not quiet:
                self._controller.set_master(self._master_fader)
        elif hw.CC_TRACK_KNOB_FIRST <= number < hw.CC_TRACK_KNOB_FIRST + hw.TRACKS:
            track = number - hw.CC_TRACK_KNOB_FIRST + 1
            if not quiet and track in self._settings:
                self._saturation_knob(track, value / 127.0)
        elif hw.CC_DEVICE_KNOB_FIRST <= number < hw.CC_DEVICE_KNOB_FIRST + 8:
            if quiet:
                return
            if self._fx_open:
                self._effect_knob(number - hw.CC_DEVICE_KNOB_FIRST + 1, value / 127.0)
            elif self._selected is not None:
                self._device_knob(number - hw.CC_DEVICE_KNOB_FIRST + 1, value / 127.0)
        elif number == hw.CC_CUE_LEVEL:
            self._change_strobe_rate(relative_steps(value) * STROBE_STEP_HZ)
        elif number == hw.CC_TEMPO and self._effects is not None:
            self._effects.tempo.adjust(relative_steps(value) * TEMPO_STEP_BPM)

    def _press(self, event: MidiEvent) -> None:
        number, track = event.number, event.channel
        if number == hw.NOTE_SHIFT:
            self._shift = True
        elif number in (hw.NOTE_DEVICE_ON_OFF, hw.NOTE_DEVICE_LOCK) and self._position_track is not None:
            self._position_mode = "aim" if number == hw.NOTE_DEVICE_ON_OFF else "permissions"
        elif number in (hw.NOTE_DEVICE_LEFT, hw.NOTE_DEVICE_RIGHT):
            self._show_page = "scenes" if number == hw.NOTE_DEVICE_LEFT else "sequences"
            self._position_track = None
            self._selected = None
            self._fx_open = False
        elif number == hw.NOTE_PLAY:
            sequences = self._scenes.sequences
            if sequences is None:
                raise ValueError("Sequences are not available.")
            if self._shift:
                sequences.restart()
            else:
                sequences.toggle()
        elif number == hw.NOTE_RECORD:
            if self._scenes.sequences is not None:
                self._scenes.sequences.stop()
        elif number == hw.NOTE_STOP_ALL_CLIPS:
            if self._fx_track is None:
                raise ValueError("Select an FX strip with TRACK SELECT before stopping its effects.")
            if self._effects is not None:
                self._effects.stop_all(effect_owner(self._fx_track), component=self.fx_component(self._fx_track))
        elif number == hw.NOTE_CROSSFADER_AB:
            self._toggle_ring(track)
        elif number == hw.NOTE_ACTIVATOR:
            self._switch_mode(track)
        elif number == hw.NOTE_SOLO and self.is_active(track):
            self._toggle_strobe(track)
        elif number == hw.NOTE_RECORD_ARM and self.is_active(track):
            self._flash(track, pressed=True)
        elif number == hw.NOTE_CLIP_STOP:
            self._fx_open = False
            self._position_track = None
            if self._selected == track:
                self._selected = None
            elif self.is_active(track) and (self.ring_selected(track) or bool(self.knob_functions(track)) or self._has_colour(track)):
                self._selected = track
        elif number == hw.NOTE_TRACK_SELECT:
            if self._shift:
                self._open_positions(track)
            else:
                self._select_fx_track(track)
        elif hw.NOTE_SCENE_LAUNCH_FIRST <= number < hw.NOTE_SCENE_LAUNCH_FIRST + hw.SCENE_LAUNCH_COUNT:
            index = number - hw.NOTE_SCENE_LAUNCH_FIRST
            if index < len(EFFECT_TYPES):
                self._open_effects(EFFECT_TYPES[index])
        elif number == hw.NOTE_TAP_TEMPO:
            if self._effects is not None:
                self._effects.tempo.tap()
        elif number == hw.NOTE_MASTER:
            self._service.set_blackout(not self._service.blackout_active())
        elif number == hw.NOTE_SESSION_RECORD:
            if self._preview is None:
                logger.info("APC40 SESSION RECORD: preview mode is not available.")
            else:
                self._preview.toggle()
        else:
            pad = hw.note_pad(number)
            if pad is not None:
                self._pad(pad)

    def _release(self, event: MidiEvent) -> None:
        if event.number == hw.NOTE_SHIFT:
            self._shift = False
        elif event.number == hw.NOTE_RECORD_ARM:
            self._flash(event.channel, pressed=False)

    # --- actions ----------------------------------------------------------------------------

    def _has_colour(self, track: int) -> bool:
        return self.is_active(track) and any(has_rgb(f) for f in self.fixtures(track))

    def _apply_fader(self, track: int) -> None:
        level = self._settings[track].intensity
        if level is None:
            return  # position unknown until the fader is moved or reported
        attribute = "pump" if track == 8 else "intensity"
        changes = {f.patch.id: {attribute: level} for f in self.fixtures(track)
                   if attribute in f.mode.attributes()}
        if changes:
            self._controller.apply(changes, fade_seconds=0.0)

    def _switch_mode(self, track: int) -> None:
        strip = self._strips.get(track)
        if strip is None or strip.family is None:
            return
        mode = "group" if strip.group else "single"
        if self._modes[strip.family] == mode:
            return
        for held in [t for t in self._held if self._strips[t].family == strip.family]:
            self._flash(held, pressed=False)
        self._modes[strip.family] = mode
        if self._selected is not None and not self.is_active(self._selected):
            self._selected = None
        if self._position_track is not None and not self.is_active(self._position_track):
            self._position_track = None
        for other in self._strips.values():
            if other.family != strip.family:
                continue
            if self._effects is not None:
                self._effects.set_active(effect_owner(other.track), self.is_active(other.track))
            if self.is_active(other.track):
                self._apply_settings(other.track)
        if strip.family == "heads":
            self._activate_position_strips()

    def _apply_settings(self, track: int) -> None:
        """Send every known set point of a strip that just became active."""
        settings = self._settings[track]
        fixtures = self.fixtures(track)
        if not fixtures:
            return
        self._apply_fader(track)
        rgb = [f for f in fixtures if has_rgb(f)]
        if rgb and settings.white_pad:
            self._controller.apply(white_pad_changes(rgb), fade_seconds=0.0)
        elif rgb and (settings.hue is not None or settings.saturation is not None):
            self._colour.set(rgb, hue=settings.hue, saturation=settings.saturation)
        if settings.strobe is not None:
            self._set_strobe(track, settings.strobe)
        for function, level in settings.knobs.items():
            if function in ("pan", "tilt"):
                self._move(fixtures, function, level)
            else:
                changes = {
                    f.patch.id: {function: level} for f in fixtures if function in f.mode.attributes()
                }
                if changes:
                    self._controller.apply(changes, fade_seconds=0.0)
        ring_changes = {}
        for fixture in fixtures:
            if "ring_intensity" not in fixture.mode.attributes():
                continue
            values = {}
            if settings.ring_intensity is not None:
                values["ring_intensity"] = settings.ring_intensity
            if settings.ring_strobe is not None:
                values["ring_strobe"] = (strobe_level(fixture, self._strobe_hz, "ring_strobe") or 0.0) if settings.ring_strobe else 0.0
            for section, rgb in settings.ring_colours.items():
                if section not in ring_segments(fixture):
                    continue
                prefix = f"ring_{section}_" if f"ring_{section}_red" in fixture.mode.attributes() else "ring_"
                values.update({prefix + c: v for c, v in zip(("red", "green", "blue"), rgb)})
            if values:
                ring_changes[fixture.patch.id] = values
        if ring_changes:
            self._controller.apply(ring_changes, fade_seconds=0.0)

    def _saturation_knob(self, track: int, level: float) -> None:
        if self.ring_selected(track):
            self._settings[track].ring_intensity = level
            if self.is_active(track):
                self._controller.apply({
                    f.patch.id: {"ring_intensity": level} for f in self.fixtures(track)
                    if "ring_intensity" in f.mode.attributes()
                }, fade_seconds=0.0)
            return
        if track == 8:
            changes = {f.patch.id: {"fan": level} for f in self.fixtures(track)
                       if "fan" in f.mode.attributes()}
            if changes:
                self._controller.apply(changes, fade_seconds=0.0)
            return
        settings = self._settings[track]
        settings.saturation = level
        settings.colour_pad = None
        if not self.is_active(track):
            return  # stored only; applies when the strip becomes active
        rgb = [f for f in self.fixtures(track) if has_rgb(f)]
        if not rgb:
            return
        if settings.hue is None:
            settings.hue = self._colour.hue_saturation(rgb[0].patch.id)[0]
        settings.white_pad = False
        self._colour.set(rgb, hue=settings.hue, saturation=level)

    def _toggle_strobe(self, track: int) -> None:
        attribute = "ring_strobe" if self.ring_selected(track) else "strobe"
        fixtures = [f for f in self.fixtures(track) if attribute in f.mode.attributes()]
        if not fixtures:
            return
        on = not self._strobing(track)
        if attribute == "ring_strobe":
            self._settings[track].ring_strobe = on
        else:
            self._settings[track].strobe = on
        self._set_strobe(track, on, attribute)

    def _set_strobe(self, track: int, on: bool, attribute: str = "strobe") -> None:
        fixtures = [f for f in self.fixtures(track) if attribute in f.mode.attributes()]
        if not fixtures:
            return
        changes = {
            f.patch.id: {attribute: (strobe_level(f, self._strobe_hz, attribute) or 0.0) if on else 0.0}
            for f in fixtures
        }
        self._controller.apply(changes, fade_seconds=0.0)

    def _change_strobe_rate(self, delta_hz: float) -> None:
        rate = min(max(self._strobe_hz + delta_hz, STROBE_MIN_HZ), STROBE_MAX_HZ)
        if rate == self._strobe_hz:
            return
        self._strobe_hz = rate
        # One shared rate: every fixture that is strobing right now follows it.
        changes = {}
        for fixture in self._controller.fixtures():
            if not fixture.patch.enabled:
                continue
            values = {
                attribute: strobe_level(fixture, rate, attribute) or 0.0
                for attribute in ("strobe", "ring_strobe")
                if self._controller.state(fixture.patch.id).get(attribute, 0.0) > 0.0
            }
            if values:
                changes[fixture.patch.id] = values
        if changes:
            self._controller.apply(changes, fade_seconds=0.0)

    def _flash(self, track: int, pressed: bool) -> None:
        """Full intensity while held: a live render-time hold that never changes any state."""
        owner = f"apc40:flash:{track}"
        if not pressed:
            if track in self._held:
                self._held.discard(track)
                self._controller.release(owner)
            return
        if track in self._held:
            return
        fixtures = self.fixtures(track)
        if not fixtures:
            return
        self._held.add(track)
        values = {"pump": 1.0, "fan": 1.0} if track == 8 else {"intensity": 1.0}
        self._controller.hold(owner, {f.patch.id: values for f in fixtures})

    def _pad(self, pad: int) -> None:
        if self._position_track is not None:
            self._position_pad(pad)
            return
        if self._selected is not None:
            self._colour_pad(self._selected, pad)
            return
        if self._fx_open:
            self._effect_pad(pad)
            return
        if self._show_page == "sequences":
            assignment = self._pads.get_sequence(pad)
            if assignment is None:
                self._request_show_dialog("sequence", pad)
                return
            if self._shift:
                self._request_show_dialog("sequence", pad, assignment.sequence)
                return
            sequences = self._scenes.sequences
            if sequences is None:
                raise ValueError("Sequences are not available.")
            sequences.launch_pad(assignment.sequence)
            return
        assignment = self._pads.get(pad)
        if assignment is None:
            self._request_show_dialog("scene", pad)
            return
        if self._shift:
            self._request_show_dialog("scene", pad, assignment.scene)
            return
        self._scenes.recall(assignment.scene)

    def _colour_pad(self, track: int, pad: int) -> None:
        if self.ring_selected(track):
            self._ring_colour_pad(track, pad)
            return
        fixtures = [f for f in self.fixtures(track) if has_rgb(f)]
        if not fixtures:
            return
        settings = self._settings[track]
        settings.colour_pad = pad
        hue_saturation = hw.colour_pad(pad)
        if hue_saturation is None:  # white: the W LED where present, else full RGB
            settings.white_pad = True
            settings.knobs.pop("white", None)
            self._controller.apply(white_pad_changes(fixtures), fade_seconds=0.0)
            return
        settings.white_pad = False
        settings.hue, settings.saturation = hue_saturation
        settings.knobs.pop("white", None)
        self._colour.set(fixtures, hue=hue_saturation[0], saturation=hue_saturation[1])
        white = {f.patch.id: {"white": 0.0} for f in fixtures if "white" in f.mode.attributes()}
        if white:
            self._controller.apply(white, fade_seconds=0.0)

    def _ring_colour_pad(self, track: int, pad: int) -> None:
        count = self.ring_section_count(track)
        if pad < 8:
            if pad <= count:
                self._ring_sections[track] = pad
            return
        index = pad - 8
        if index >= len(RING_PALETTE):
            return
        name, velocity = RING_PALETTE[index]
        rgb = (0.0, 0.0, 0.0) if name == "Black" else tuple(
            int(hw.PALETTE[velocity][offset:offset + 2], 16) / 255 for offset in (0, 2, 4)
        )
        selected = self._ring_sections.get(track, 0)
        sections = range(1, count + 1) if selected == 0 else (selected,)
        for section in sections:
            self._settings[track].ring_colours[section] = rgb
        changes = {}
        for fixture in self.fixtures(track):
            if "ring_intensity" not in fixture.mode.attributes():
                continue
            values = {}
            for section in sections:
                if section not in ring_segments(fixture):
                    continue
                prefix = f"ring_{section}_" if f"ring_{section}_red" in fixture.mode.attributes() else "ring_"
                values.update({prefix + colour: value for colour, value in zip(("red", "green", "blue"), rgb)})
            if values:
                changes[fixture.patch.id] = values
        if changes:
            self._controller.apply(changes, fade_seconds=0.0)

    def _device_knob(self, knob: int, level: float) -> None:
        track = self._selected
        assert track is not None
        function = self.knob_functions(track).get(knob)
        if function is None:
            return
        fixtures = self.fixtures(track)
        settings = self._settings[track]
        if function == "hue":
            settings.hue = level
            settings.white_pad = False
            settings.colour_pad = None
            rgb = [f for f in fixtures if has_rgb(f)]
            if rgb and settings.saturation is None:
                settings.saturation = self._colour.hue_saturation(rgb[0].patch.id)[1]
            self._colour.set(fixtures, hue=level, saturation=settings.saturation)
            return
        settings.knobs[function] = level
        if function in ("pan", "tilt"):
            self._move(fixtures, function, level)
        else:
            changes = {f.patch.id: {function: level} for f in fixtures if function in f.mode.attributes()}
            if changes:
                self._controller.apply(changes, fade_seconds=0.0)

    # --- effects ----------------------------------------------------------------------------

    def _activate_position_strips(self) -> None:
        self._positions.activate_strips({
            effect_owner(s.track): s.target
            for s in self._strips.values()
            if s.family == "heads" and s.target is not None and self.is_active(s.track)
        })

    def _open_positions(self, track: int) -> None:
        strip = self._strips.get(track)
        if strip is None or strip.family != "heads":
            raise ValueError("Target/zone selection is available on moving-head strips only.")
        if not self.is_active(track):
            raise ValueError("Activate this group/single strip before selecting its target or zones.")
        self._position_track = None if self._position_track == track else track
        self._position_mode = "aim"
        self._fx_open = False
        self._selected = None

    def position_pads(self) -> dict[int, tuple[str, str, bool, bool]]:
        """Pad -> (name, kind, selected, excluded). List order is stable."""
        track = self._position_track
        if track is None:
            return {}
        document = self._positions.rig.room_file()
        assignment = self.position_assignment(track)
        moving = any(self.effect_running(track, kind) for kind in MOVEMENT_TYPES)
        aim_reached = all(
            self._positions.assignment_aim_reached(f.patch.id) for f in self.fixtures(track)
        )
        pads = {
            index: (target.name, "target", assignment.aim_target == target.id and aim_reached, False)
            for index, target in enumerate(document.targets[:24])
        }
        pads.update({
            24 + index: (
                zone.name, "zone", (
                    zone.id in assignment.include_zones and zone.id not in assignment.exclude_zones
                    if moving else assignment.aim_zone == zone.id and aim_reached
                ),
                zone.id not in assignment.include_zones or zone.id in assignment.exclude_zones,
            )
            for index, zone in enumerate(document.zones[:16])
        })
        return pads

    def position_dialog_request(self) -> tuple[int, Optional[str]]:
        return self._position_dialog_id, self._position_dialog_kind

    def position_dialog_item(self) -> Optional[str]:
        return self._position_dialog_item

    def _request_position_dialog(self, kind: str, item: Optional[str] = None) -> None:
        if self.preview_active():
            raise ValueError("Finish preview with Go before editing targets or zones.")
        self._position_dialog_id += 1
        self._position_dialog_kind = kind
        self._position_dialog_item = item

    def _position_pad(self, pad: int) -> None:
        track = self._position_track
        assert track is not None
        strip = self._strips[track]
        assert strip.target is not None
        document = self._positions.rig.room_file()
        current = self.position_assignment(track)
        values = current.model_dump()
        if pad < 24:
            if pad >= len(document.targets):
                self._request_position_dialog("target")
                return
            if self._shift:
                self._request_position_dialog("target", document.targets[pad].id)
                return
            values.update(aim_target=document.targets[pad].id, aim_zone=None)
        else:
            index = pad - 24
            if index >= len(document.zones):
                self._request_position_dialog("zone")
                return
            zone = document.zones[index]
            if self._shift:
                self._request_position_dialog("zone", zone.id)
                return
            included = list(current.include_zones)
            excluded = list(current.exclude_zones)
            if self._position_mode == "permissions":
                if zone.id in excluded:
                    excluded.remove(zone.id)
                    included = [z for z in included if z != zone.id]
                elif zone.id in included:
                    included.remove(zone.id)
                    excluded.append(zone.id)
                else:
                    included.append(zone.id)
                values.update(include_zones=included, exclude_zones=excluded)
            else:
                values.update(aim_target=None, aim_zone=zone.id)
        notes = self._positions.set_strip_assignment(
            effect_owner(track), strip.target, HeadAssignment.model_validate(values), 0.0,
            recall=pad < 24 or self._position_mode == "aim",
        )
        if notes:
            raise ValueError("; ".join(notes))

    def _open_effects(self, effect_type: EffectType) -> None:
        """SCENE LAUNCH: show this effect type on the pads; pressing the shown type closes it."""
        if self._effects is None:
            return
        if self._fx_open and self._fx_type is effect_type:
            self._fx_open = False
            return
        self._fx_open = True
        self._position_track = None
        self._fx_type = effect_type
        self._selected = None

    def _select_fx_track(self, track: int) -> None:
        """TRACK SELECT on the effects page: the strip the device knobs edit (again = none)."""
        if self._position_track is not None:
            self._open_positions(track)
            return
        if self._fx_track == track:
            self._fx_track = None
        elif self.fixtures(track):
            self._fx_track = track

    def _effect_pad(self, pad: int) -> None:
        """Start a preset of the selected type on the pad's track; the running one stops."""
        assert self._effects is not None
        row, column = divmod(pad, hw.PAD_COLUMNS)
        track = column + 1
        if not self.is_active(track):
            return
        fixtures = self.effect_fixtures(track, self._fx_type)
        if not fixtures:
            return
        self._fx_track = track  # the knobs follow the strip last played
        owner = effect_owner(track)
        component = self.fx_component(track, self._fx_type)
        if self._shift:
            # SHIFT + FX pad edits that preset slot; it never starts or stops FX.
            self._fx_dialog_id += 1
            self._fx_dialog = (track, self._fx_type.value, row, component)
            return
        running = self._effects.running(owner, self._fx_type, component=component)
        if running is not None and running.preset == row:
            self._effects.stop(owner, self._fx_type, component=component)
        else:
            self.start_effect(track, self._fx_type, row)

    def effect_definition(self, track: int, page: EffectType, row: int, *, component: Optional[Literal["beam", "ring"]] = None) -> FxDefinition:
        strip = self._strips.get(track)
        if strip is None or strip.target is None:
            raise ValueError("Choose a patched fixture/group strip.")
        if self.fx_service is not None:
            return self.fx_service.store.get(strip.target, page, row, component=component or self.fx_component(track, page))
        return family_presets(page)[row].model_copy(deep=True)

    def start_effect(self, track: int, page: EffectType, row: int, definition: Optional[FxDefinition] = None, *, component: Optional[Literal["beam", "ring"]] = None) -> None:
        with self._lock:
            if self._effects is None or not self.is_active(track):
                raise ValueError("Choose an active strip; switch the group/individual mode first.")
            strip = self._strips[track]
            assert strip.target is not None
            component = component or self.fx_component(track, page)
            if self.fx_service is not None:
                self.fx_service.start(effect_owner(track), strip.target, page, row, definition, component=component)
            else:
                definition = definition or self.effect_definition(track, page, row)
                fixtures = self.effect_fixtures(track, page)
                if definition.kind in MOVEMENT_TYPES:
                    if any(not self._positions.movement_allowed(f.patch.id) for f in fixtures):
                        raise ValueError("Movement FX need an included area reachable with measured/calibrated heads.")
                    self._positions.prepare_movement([f.patch.id for f in fixtures])
                participants = (
                    [segment_participant(f.patch.id, section) for f in fixtures for section in ring_segments(f)]
                    if component == "ring" else [f.patch.id for f in fixtures]
                )
                self._effects.start(effect_owner(track), page, row, participants, definition=definition, component=component)
            self._fx_track = track

    def _effect_knob(self, knob: int, level: float) -> None:
        assert self._effects is not None
        if self._fx_track is None:
            return
        running = self.running_effect(self._fx_track, self._fx_type)
        name = effect_knobs(self._fx_type, running).get(knob)
        if name is not None:
            self._effects.set_param(effect_owner(self._fx_track), self._fx_type, name, level,
                                    component=self.fx_component(self._fx_track, self._fx_type))

    def _move(self, fixtures: list[ResolvedFixture], axis: str, level: float) -> None:
        """Pan/tilt knobs: inside the include zone if one is assigned, else raw (guarded) moves."""
        for fixture in fixtures:
            head = fixture.patch.id
            if self._positions.has_include_zone(head):
                u, v = self._positions.spot(head)
                u, v = (level, v) if axis == "pan" else (u, level)
                try:
                    self._positions.set_spot(head, u, v, fade_seconds=0.0)
                except EXPECTED_ERRORS as error:
                    logger.warning("APC40 %s for '%s' ignored: %s", axis, head, error)
            else:
                self._controller.set_values(head, {axis: level})

    # --- LEDs -------------------------------------------------------------------------------

    def leds(self) -> LedState:
        """Every LED the APC40 should show right now (the feedback loop sends only changes)."""
        with self._lock:
            blink_on = int(self._clock() / BLINK_SECONDS) % 2 == 0
            state: LedState = {}
            for track in range(1, hw.TRACKS + 1):
                active = self.is_active(track)
                state[("note", track, hw.NOTE_ACTIVATOR)] = hw.LED_ON if active else hw.LED_OFF
                state[("note", track, hw.NOTE_CROSSFADER_AB)] = hw.LED_ON if self.ring_selected(track) else hw.LED_OFF
                state[("note", track, hw.NOTE_SOLO)] = (
                    hw.LED_ON if active and self.strobe_on(track) else hw.LED_OFF
                )
                state[("note", track, hw.NOTE_RECORD_ARM)] = (
                    hw.LED_ON if track in self._held else hw.LED_OFF
                )
                # Blinking = colour page open / strip on the effect knobs; on = colour set / effect running.
                if self._selected == track:
                    clip_stop = blink_on
                else:
                    clip_stop = self.colour_set(track) and any(has_rgb(f) for f in self.fixtures(track))
                state[("note", track, hw.NOTE_CLIP_STOP)] = hw.LED_ON if clip_stop else hw.LED_OFF
                editing = self._fx_open and self._fx_track == track
                position_editing = self._position_track == track
                fast_blink = int(self._clock() / (BLINK_SECONDS / 2)) % 2 == 0
                select = fast_blink if editing else (
                    blink_on if position_editing or self.effect_running(track) else False
                )
                state[("note", track, hw.NOTE_TRACK_SELECT)] = hw.LED_ON if select else hw.LED_OFF
                saturation = self.saturation(track) if active else None
                ring = hw.CC_TRACK_KNOB_FIRST + track - 1
                state[("cc", 1, hw.CC_TRACK_RING_TYPE_FIRST + track - 1)] = (
                    hw.RING_VOLUME if saturation is not None else hw.RING_OFF
                )
                state[("cc", 1, ring)] = round((saturation or 0.0) * 127)
            in_positions = self._position_track is not None
            state[("note", 1, hw.NOTE_DEVICE_ON_OFF)] = hw.LED_ON if in_positions and self._position_mode == "aim" else hw.LED_OFF
            state[("note", 1, hw.NOTE_DEVICE_LOCK)] = hw.LED_ON if in_positions and self._position_mode == "permissions" else hw.LED_OFF
            state[("note", 1, hw.NOTE_DEVICE_LEFT)] = hw.LED_ON if self._show_page == "scenes" else hw.LED_OFF
            state[("note", 1, hw.NOTE_DEVICE_RIGHT)] = hw.LED_ON if self._show_page == "sequences" else hw.LED_OFF
            state[("note", 1, hw.NOTE_MASTER)] = (
                hw.LED_ON if self._service.blackout_active() else hw.LED_OFF
            )
            state[("note", 1, hw.NOTE_SESSION_RECORD)] = (
                hw.LED_ON if self.preview_active() else hw.LED_OFF
            )
            phase = self.beat_phase()
            state[("note", 1, hw.NOTE_METRONOME)] = (
                hw.LED_ON if phase is not None and phase < 0.2 else hw.LED_OFF
            )
            for pad, velocity in self.pad_colours(blink_on).items():
                state[("note", 1, hw.pad_note(pad))] = velocity
            for index, velocity in enumerate(self.scene_launch_colours(blink_on)):
                state[("note", 1, hw.NOTE_SCENE_LAUNCH_FIRST + index)] = velocity
            knobs = self.device_knobs()
            for knob in range(1, 9):
                function, _, value = knobs.get(knob, (None, None, None))
                state[("cc", 1, hw.CC_DEVICE_RING_TYPE_FIRST + knob - 1)] = (
                    KNOB_RINGS.get(function, hw.RING_VOLUME)
                    if function and value is not None
                    else hw.RING_OFF
                )
                state[("cc", 1, hw.CC_DEVICE_KNOB_FIRST + knob - 1)] = round((value or 0.0) * 127)
            return state

    def scene_launch_colours(self, blink_on: bool = True) -> list[int]:
        """SCENE LAUNCH 1-5: shown type bright, others dim, blinking while one of its effects
        runs; dark when no strip can use the type."""
        with self._lock:
            colours = []
            for effect_type in EFFECT_TYPES:
                if not any(self.effect_fixtures(t, effect_type) for t in range(1, hw.TRACKS + 1)):
                    colours.append(0)
                    continue
                bright, dim = hw.EFFECT_COLOURS[effect_type.value]
                colour = bright if self._fx_open and effect_type is self._fx_type else dim
                if self.type_running(effect_type) and not blink_on:
                    colour = 0
                colours.append(colour)
            return colours + [0] * (hw.SCENE_LAUNCH_COUNT - len(colours))

    def pad_colours(self, blink_on: bool = True) -> dict[int, int]:
        """Palette velocity per pad (0 = dark)."""
        with self._lock:
            if self._position_track is not None:
                colours = {pad: 0 for pad in range(hw.PAD_COUNT)}
                document = self._positions.rig.room_file()
                assignment = self.position_assignment(self._position_track)
                for pad, (_, kind, active, excluded) in self.position_pads().items():
                    explicitly_excluded = (
                        kind == "zone" and document.zones[pad - 24].id in assignment.exclude_zones
                    )
                    colours[pad] = (
                        (21 if active else 22) if kind == "target"
                        else 6 if explicitly_excluded else 1 if excluded else 37 if active else 38
                    )
                return colours
            if self._selected is not None:
                if self.ring_selected(self._selected):
                    track = self._selected
                    colours = {pad: 0 for pad in range(hw.PAD_COUNT)}
                    selected_section = self._ring_sections.get(track, 0)
                    colours[0] = 3 if selected_section == 0 else 2
                    section_colours = self.ring_colours(track)
                    for pad in range(1, self.ring_section_count(track) + 1):
                        rgb = section_colours[pad - 1]
                        colours[pad] = min(RING_LED_COLOURS, key=lambda item: sum(
                            (a - b) ** 2 for a, b in zip(rgb, item[1])
                        ))[0]
                        if pad == selected_section and not blink_on:
                            colours[pad] = 0
                    for index, (_, velocity) in enumerate(RING_PALETTE):
                        colours[index + 8] = velocity
                    return colours
                current = self._settings[self._selected].colour_pad
                return {
                    pad: 0 if pad == current and not blink_on else hw.colour_pad_velocity(pad)
                    for pad in range(hw.PAD_COUNT)
                }
            if self._fx_open:
                return self._effect_pad_colours(blink_on)
            if self._show_page == "sequences":
                colours = {pad: 0 for pad in range(hw.PAD_COUNT)}
                sequences = self._scenes.sequences
                if sequences is not None:
                    known = {sequence.id for sequence in sequences.store.all()}
                    status = sequences.status()
                    for pad in range(hw.PAD_COUNT):
                        assignment = self._pads.get_sequence(pad)
                        if assignment is not None and assignment.sequence in known:
                            if status and status["id"] == assignment.sequence and status["status"] == "paused":
                                colours[pad] = 9 if blink_on else 1
                            elif status and status["id"] == assignment.sequence and status["status"] == "running":
                                colours[pad] = hw.SCENE_PAD_COLOURS[assignment.colour] if blink_on else 0
                            else:
                                colours[pad] = hw.SCENE_PAD_COLOURS[assignment.colour]
                return colours
            active = self._scenes.active_scene()
            scene_ids = {s.id for s in self._scenes.store.all()}
            colours = {pad: 0 for pad in range(hw.PAD_COUNT)}
            for assignment in self._pads.all():
                if assignment.scene not in scene_ids:
                    continue
                if assignment.scene == active and not blink_on:
                    continue
                colours[assignment.pad] = hw.SCENE_PAD_COLOURS[assignment.colour]
            return colours

    def _effect_pad_colours(self, blink_on: bool) -> dict[int, int]:
        """Effects page: each active track's column shows the 5 presets; the running one blinks."""
        bright, dim = hw.EFFECT_COLOURS[self._fx_type.value]
        colours = {pad: 0 for pad in range(hw.PAD_COUNT)}
        for column in range(hw.PAD_COLUMNS):
            track = column + 1
            if not self.is_active(track) or not self.effect_fixtures(track, self._fx_type):
                continue
            running = self.running_effect(track, self._fx_type)
            for row in range(hw.PAD_ROWS):
                pad = row * hw.PAD_COLUMNS + column
                if running is not None and running.preset == row:
                    colours[pad] = bright if blink_on else 0
                else:
                    colours[pad] = dim
        return colours
