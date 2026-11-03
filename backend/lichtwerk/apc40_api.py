"""HTTP routes for the on-screen APC40 MKII: full view, virtual events, scene-pad assignment
and the typed effect tempo (BPM)."""

from typing import Callable, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import apc40 as hw
from .apc40_control import Apc40Control
from .apc40_pads import PadAssignment, SequencePadAssignment
from .effects import EFFECT_LABELS, EFFECT_TYPES
from .midi_control import MidiEvent
from .room_map import light_colour
from .scenes import PersistenceError
from .service import LightingService
from .tempo import validate_bpm


class StripView(BaseModel):
    track: int
    target: Optional[str]
    label: str
    available: bool  # has a fixture/group in the patch
    group: bool
    family: Optional[str]
    active: bool
    intensity: Optional[float]
    pickup: bool = False  # soft takeover: the fader waits to pass `level`
    level: Optional[float] = None  # the strip's current level (ghost marker while picking up)
    saturation: Optional[float]
    strobe_on: bool
    flash_held: bool
    selected: bool
    selectable: bool
    # Current colour of each of the strip's fixtures (RGB 0-1, the state being edited, intensity
    # ignored); None = fixture has no colour. Empty for strips without fixtures.
    colours: list[Optional[list[float]]]
    fixture_names: list[str]
    colour_set: bool  # CLIP STOP LED on: a colour was chosen with the pads/knobs
    fx_knobs: bool  # the device knobs edit this track's effect (TRACK SELECT blinks)
    fx_running: bool  # an effect of this track is running (TRACK SELECT on)
    position_selected: bool
    hazer: bool
    ring_available: bool
    ring_selected: bool
    ring_colours: list[list[float]]


class PadView(BaseModel):
    pad: int
    note: int
    colour: Optional[str]  # hex RGB of the pad LED, None = dark
    label: Optional[str] = None
    scene: Optional[str] = None
    sequence: Optional[str] = None
    sequence_loop: Optional[bool] = None
    scene_colour: Optional[str] = None
    missing: bool = False  # assigned scene no longer exists
    queued: bool = False  # waits for the next beat (quantize)
    active: bool = False
    kind: Optional[str] = None
    included: bool = False
    excluded: bool = False
    explicitly_excluded: bool = False
    route_order: Optional[int] = None  # position in the running Move FX route (pad blinks)
    look: bool = False  # the assigned scene carries an FX set (screen badge "FX")
    accent: Optional[str] = None  # "hold" / "beats" for accent pads
    accent_beats: Optional[float] = None
    fixture_badges: list[str] = Field(default_factory=list)


class KnobView(BaseModel):
    knob: int
    function: Optional[str]
    label: Optional[str]
    value: Optional[float]
    display: Optional[str] = None


class EffectTypeView(BaseModel):
    index: int  # SCENE LAUNCH 1-5
    type: str
    label: str
    colour: str  # hex of the bright pad colour
    selected: bool  # shown on the pads (effects page open)
    available: bool  # some strip can use this type
    running: bool  # some strip runs an effect of this type (SCENE LAUNCH blinks)


class EventView(BaseModel):
    kind: str
    channel: int
    number: int
    value: int


class ShowDialogView(BaseModel):
    id: int
    kind: Optional[Literal["scene", "sequence"]]
    item: Optional[str]
    pad: int
    capture_live: bool = False  # SHIFT + empty pad: open the editor with Capture live ticked


class FxDialogView(BaseModel):
    id: int
    track: Optional[int]
    page: Optional[str]
    slot: int
    component: Optional[Literal["beam", "ring"]]


class Apc40View(BaseModel):
    connected: bool
    device: Optional[str]
    other_connected: bool  # the Xone:K2 is connected (for choosing the screen)
    strips: list[StripView]
    master: float
    blackout: bool
    preview: bool
    strobe_hz: float
    selected: Optional[int]
    pad_mode: Literal["scenes", "sequences", "colours", "effects", "positions"]
    fx_track: Optional[int]
    combo_open: bool
    combo_knob_family: str
    combo_knob_families: list[str]
    combo_status: Optional[str]
    fx_palette: bool
    colour_fx_palette: list[str]
    effect_types: list[EffectTypeView]
    bpm: Optional[float]  # None = effects not available
    beat_phase: Optional[float]
    position_track: Optional[int]
    position_mode: Literal["aim", "permissions"]
    shift: bool
    error: Optional[str]
    fx_notice: Optional[str]
    look_notice: Optional[str] = None  # what the last look recall could not run
    sequence: Optional[dict] = None  # running sequence status (step, WAITING FOR NEXT, …)
    next_available: bool = False  # SCENE LAUNCH 5 = NEXT is lit
    next_waiting: bool = False
    next_queued: bool = False  # NEXT waits for the next beat (quantize)
    quantize: bool = False  # METRONOME: performance pads fire on the next beat
    position_dialog_id: int
    position_dialog_kind: Optional[Literal["target", "zone"]]
    position_dialog_item: Optional[str]
    show_dialog: ShowDialogView
    fx_dialog: FxDialogView
    pads: list[PadView]
    knobs: list[KnobView]
    scene_colours: dict[str, str]
    last_event: Optional[EventView]


class Apc40Event(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["note", "cc"]
    channel: int = Field(ge=1, le=16)
    number: int = Field(ge=0, le=127)
    value: int = Field(ge=0, le=127)


class TempoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bpm: float


class PadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scene: str = Field(min_length=1)
    colour: str = hw.DEFAULT_SCENE_PAD_COLOUR
    accent: Literal["off", "hold", "beats"] = "off"
    accent_beats: float = 2.0


class SequencePadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: str = Field(min_length=1)
    colour: str = hw.DEFAULT_SCENE_PAD_COLOUR


def build_router(
    control: Apc40Control,
    service: LightingService,
    status: Callable[[], dict],
) -> APIRouter:
    """`status` returns {"connected", "device", "other_connected"} for the two controllers."""
    router = APIRouter(prefix="/api/apc40")

    def fixture_colour(fixture_id: str) -> Optional[list[float]]:
        values = {k: v for k, v in control.controller.state(fixture_id).items() if k != "intensity"}
        colour = light_colour(values) if {"red", "green", "blue"} <= values.keys() else None
        return list(colour) if colour else None

    def view() -> Apc40View:
        selected = control.selected()
        fx_open = control.fx_open()
        palette_editing = control.palette_editing()
        palette_pads = control.palette_pads() if palette_editing else {}
        fx_track = control.fx_track()
        fx_type = control.fx_type()
        position_track = control.position_track()
        scenes = {s.id: s.name for s in control.scenes.store.all()}
        active_scene = control.scenes.active_scene()
        strips = []
        for strip in control.strips:
            fixtures = control.fixtures(strip.track)
            active = control.is_active(strip.track)
            strips.append(StripView(
                track=strip.track, target=strip.target, label=control.label(strip.track), available=bool(fixtures),
                group=strip.group, family=strip.family, active=active,
                intensity=control.fader(strip.track),
                pickup=control.pickup(strip.track),
                level=control.level(strip.track, "pump" if strip.track == 8 else "intensity"),
                saturation=control.saturation(strip.track),
                strobe_on=control.strobe_on(strip.track),
                flash_held=control.flash_held(strip.track),
                selected=selected == strip.track,
                selectable=active and (control.ring_selected(strip.track) or bool(control.knob_functions(strip.track))),
                colours=[fixture_colour(f.patch.id) for f in fixtures],
                fixture_names=[f.patch.name for f in fixtures],
                colour_set=control.colour_set(strip.track),
                fx_knobs=(fx_open or palette_editing) and fx_track == strip.track,
                fx_running=control.effect_running(strip.track),
                position_selected=position_track == strip.track,
                hazer=strip.track == 8,
                ring_available=control.ring_available(strip.track),
                ring_selected=control.ring_selected(strip.track),
                ring_colours=control.ring_colours(strip.track) if control.ring_available(strip.track) else [],
            ))
        colours = control.pad_colours(blink_on=True, steady_queued=True)
        route = control.route_pads() if position_track is not None else None
        pads = []
        queued = control.queued_pads() if position_track is None and selected is None and not fx_open else set()
        for pad in range(hw.PAD_COUNT):
            velocity = colours.get(pad, 0)
            entry = PadView(
                pad=pad, note=hw.pad_note(pad),
                colour=hw.PALETTE[velocity] if velocity else None,
                queued=pad in queued,
            )
            if position_track is not None:
                position = control.position_pads().get(pad)
                entry.route_order = (route or {}).get(pad)
                if position is not None:
                    entry.label, entry.kind, entry.active, entry.excluded = position
                    entry.included = entry.kind == "zone" and not entry.excluded
                    if pad >= 24 and entry.kind == "zone":
                        zone = control.positions.rig.room_file().zones[pad - 24]
                        entry.explicitly_excluded = zone.id in control.position_assignment(position_track).exclude_zones
            elif fx_open and selected is None:
                row, column = divmod(pad, hw.PAD_COLUMNS)
                if velocity:
                    if control.combo_open():
                        entry.label = control.combo_definition(column + 1, row).name
                        bundled = control.combo_effects(column + 1)
                        entry.active = bool(bundled and bundled[0].combo_slot == row)
                        if entry.active and len(bundled) != len(bundled[0].combo_families):
                            entry.label += " · modified"
                    else:
                        entry.label = control.effect_definition(column + 1, fx_type, row).name
                        running = control.running_effect(column + 1, fx_type)
                        entry.active = running is not None and running.preset == row
            elif selected is None and control.show_page() == "sequences":
                assignment = control.pads.get_sequence(pad)
                if assignment is not None:
                    entry.sequence = assignment.sequence
                    entry.scene_colour = assignment.colour
                    sequences = control.scenes.sequences
                    known = {s.id: s for s in sequences.store.all()} if sequences is not None else {}
                    entry.missing = assignment.sequence not in known
                    entry.label = known[assignment.sequence].name if not entry.missing else f"{assignment.sequence} (missing)"
                    if not entry.missing:
                        entry.sequence_loop = known[assignment.sequence].loop
                    playback = sequences.status() if sequences is not None else None
                    entry.active = bool(
                        playback and playback["id"] == assignment.sequence
                        and playback["status"] in ("running", "paused")
                    )
            elif selected is None:
                assignment = control.pads.get(pad)
                if assignment is not None:
                    entry.scene = assignment.scene
                    entry.scene_colour = assignment.colour
                    entry.missing = assignment.scene not in scenes
                    entry.label = scenes.get(assignment.scene, f"{assignment.scene} (missing)")
                    if assignment.accent != "off":
                        entry.accent = assignment.accent
                        entry.accent_beats = assignment.accent_beats
                        entry.active = pad in control.accents.active()
                    else:
                        entry.active = assignment.scene == active_scene
                    if not entry.missing:
                        scene = control.scenes.store.get(assignment.scene)
                        entry.look = scene.fx is not None
                        entry.fixture_badges = [
                            f"{control.controller.patch.group_name(group)} G" for group in scene.groups
                        ] + [
                            control.controller.patch.get(fixture).patch.name
                            for fixture in dict.fromkeys([*scene.fixtures, *scene.positions])
                        ]
            elif palette_editing:
                entry.kind = "fx_colour"
                indices = palette_pads.get(pad, [])
                entry.active = bool(indices)
                entry.label = ", ".join(str(index) for index in indices) if indices else None
                if pad == hw.WHITE_PAD:
                    entry.label = f"White {entry.label or ''}".strip()
            elif selected is not None and control.ring_selected(selected):
                entry.label, entry.active = control.ring_pad(selected, pad)
                if pad < 8 and entry.label is not None:
                    entry.kind = "ring_section"
                    if pad > 0:
                        section_colours = control.ring_colours(selected)
                        rgb = section_colours[pad - 1]
                        entry.colour = "".join(f"{round(c * 255):02X}" for c in rgb)
            else:
                entry.kind = "fixture_colour"
                entry.active = pad == control.selected_colour_pad()
                if pad == hw.WHITE_PAD:
                    entry.label = "White"
            pads.append(entry)
        device = control.device_knobs()
        knobs = []
        for knob in (1, 2, 3, 5, 6, 7):
            function, label, value = device.get(knob, (None, None, None))
            display = None
            if (fx_open or palette_editing) and function in ("shape", "maximum") and fx_track is not None:
                running = control.running_effect(fx_track, fx_type)
                if running is not None:
                    display = (
                        running.params.shape.replace("_", " ").title()
                        if function == "shape" else f"{running.params.maximum:.0%}"
                    )
            if (fx_open or palette_editing) and function == "speed" and fx_track is not None:
                running = control.running_effect(fx_track, fx_type)
                if running is not None:
                    duration = (running.params.travel if running.params.touring
                                else running.params.seconds if running.params.timing == "seconds"
                                else running.params.beats)
                    suffix = "travel" if running.params.touring else "cycle"
                    if running.params.timing == "seconds":
                        display = f"{duration:g} seconds/{suffix}"
                    else:
                        display = f"{duration:g} beats/{suffix}" if duration >= 1 else f"1/{1 / duration:g} beat/{suffix}"
            knobs.append(KnobView(knob=knob, function=function, label=label, value=value, display=display))
        launch = control.scene_launch_colours()
        playback = control.scenes.sequences.status() if control.scenes.sequences is not None else None
        effect_types = [
            EffectTypeView(
                index=index + 1, type=effect_type.value, label=EFFECT_LABELS[effect_type],
                colour=hw.PALETTE[hw.EFFECT_COLOURS[effect_type.value][0]],
                selected=fx_open and not control.combo_open() and effect_type is fx_type, available=launch[index] > 0,
                running=control.type_running(effect_type),
            )
            for index, effect_type in enumerate(EFFECT_TYPES)
        ]
        effect_types.append(EffectTypeView(
            index=4, type="combo", label="Combo",
            colour=hw.PALETTE[hw.EFFECT_COLOURS["combo"][0]],
            selected=control.combo_open(), available=launch[3] > 0,
            running=any(any(effect.active for effect in control.combo_effects(track)) for track in range(1, 9)),
        ))
        bundled = control.combo_effects(fx_track) if fx_track is not None else []
        event = control.last_event()
        state = status()
        return Apc40View(
            fx_palette=palette_editing,
            colour_fx_palette=control.colour_fx_palette(),
            connected=state.get("connected", False),
            device=state.get("device"),
            other_connected=state.get("other_connected", False),
            strips=strips,
            master=control.master(),
            blackout=service.blackout_active(),
            preview=control.preview_active(),
            strobe_hz=control.strobe_hz(),
            selected=selected,
            pad_mode=(
                "positions" if position_track is not None
                else "colours" if selected is not None
                else "effects" if fx_open
                else control.show_page()
            ),
            fx_track=fx_track,
            combo_open=control.combo_open(),
            combo_knob_family=fx_type.value,
            combo_knob_families=[page.value for page in EFFECT_TYPES
                                if fx_track is not None and control.effect_fixtures(fx_track, page)],
            combo_status=(bundled[0].combo_name + (
                " · modified" if len(bundled) != len(bundled[0].combo_families) else ""
            )) if bundled else None,
            effect_types=effect_types,
            bpm=control.bpm(),
            beat_phase=control.beat_phase(),
            position_track=position_track,
            position_mode=control.position_mode(),
            shift=control.shift_active(),
            error=control.last_error(),
            fx_notice=control.effects.notice if control.effects is not None else None,
            look_notice=control.scenes.look_notice(),
            sequence=playback,
            next_available=launch[hw.NEXT_SCENE_LAUNCH_INDEX] > 0 or bool(playback and playback["waiting"]),
            next_waiting=bool(playback and playback["waiting"] and playback["status"] in ("running", "paused")),
            next_queued=control.quantizer.is_queued("next"),
            quantize=control.quantizer.enabled(),
            position_dialog_id=control.position_dialog_request()[0],
            position_dialog_kind=control.position_dialog_request()[1],
            position_dialog_item=control.position_dialog_item(),
            show_dialog=ShowDialogView(**dict(zip(
                ("id", "kind", "item", "pad", "capture_live"), control.show_dialog_request(),
            ))),
            fx_dialog=_fx_dialog_view(control),
            pads=pads,
            knobs=knobs,
            scene_colours={name: hw.PALETTE[v] for name, v in hw.SCENE_PAD_COLOURS.items()},
            last_event=EventView(**event.__dict__) if event else None,
        )

    @router.get("", response_model=Apc40View)
    def get_view() -> Apc40View:
        return view()

    @router.post("/event", response_model=Apc40View)
    def send_event(event: Apc40Event) -> Apc40View:
        control.handle(MidiEvent(event.kind, event.channel, event.number, event.value), virtual=True)
        return view()

    @router.put("/tempo", response_model=Apc40View)
    def set_tempo(request: TempoRequest) -> Apc40View:
        if control.effects is None:
            raise HTTPException(status_code=409, detail="Effects are not available.")
        try:
            control.effects.tempo.set_bpm(validate_bpm(request.bpm))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return view()

    @router.put("/pads/{pad}", response_model=Apc40View)
    def assign_pad(pad: int, request: PadRequest) -> Apc40View:
        if not 0 <= pad < hw.PAD_COUNT:
            raise HTTPException(status_code=404, detail=f"Pad {pad} does not exist.")
        try:
            control.scenes.store.get(request.scene)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Look '{request.scene}' does not exist.")
        try:
            assignment = PadAssignment(
                pad=pad, scene=request.scene, colour=request.colour,
                accent=request.accent, accent_beats=request.accent_beats,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            control.pads.assign(assignment)
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return view()

    @router.delete("/pads/{pad}", response_model=Apc40View)
    def clear_pad(pad: int) -> Apc40View:
        if not 0 <= pad < hw.PAD_COUNT:
            raise HTTPException(status_code=404, detail=f"Pad {pad} does not exist.")
        try:
            control.pads.clear(pad)
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return view()

    @router.put("/sequence-pads/{pad}", response_model=Apc40View)
    def assign_sequence_pad(pad: int, request: SequencePadRequest) -> Apc40View:
        if not 0 <= pad < hw.PAD_COUNT:
            raise HTTPException(404, f"Pad {pad} does not exist.")
        if control.scenes.sequences is None:
            raise HTTPException(409, "Sequences are not available.")
        try:
            control.scenes.sequences.store.get(request.sequence)
            control.pads.assign_sequence(SequencePadAssignment(
                pad=pad, sequence=request.sequence, colour=request.colour,
            ))
        except KeyError as error:
            raise HTTPException(404, str(error.args[0])) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except PersistenceError as error:
            raise HTTPException(503, str(error)) from error
        return view()

    @router.delete("/sequence-pads/{pad}", response_model=Apc40View)
    def clear_sequence_pad(pad: int) -> Apc40View:
        if not 0 <= pad < hw.PAD_COUNT:
            raise HTTPException(404, f"Pad {pad} does not exist.")
        try:
            control.pads.clear_sequence(pad)
        except PersistenceError as error:
            raise HTTPException(503, str(error)) from error
        return view()

    return router


def _fx_dialog_view(control: Apc40Control) -> FxDialogView:
    request_id, request = control.fx_dialog_request()
    if request is None:
        return FxDialogView(id=request_id, track=None, page=None, slot=0, component=None)
    track, page, slot, component = request
    return FxDialogView(id=request_id, track=track, page=page, slot=slot, component=component)
