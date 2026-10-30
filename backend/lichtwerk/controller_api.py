"""HTTP routes for the on-screen Xone:K2: layout with live assignments, and virtual control events.

On-screen controls send the same events as the hardware, so both behave identically. Assigning
controls uses the existing PUT /api/midi/bindings route.
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .fixture_service import FixtureController
from .midi_control import MidiControl, MidiEvent
from .midi_feedback import button_colour
from .midi_mapping import (
    ALL_TARGET,
    CONTINUOUS_ACTIONS,
    FIXTURE_ACTIONS,
    REQUIRED_ATTRIBUTES,
    MidiAction,
    MidiBinding,
    MidiKey,
    MidiMappingStore,
)
from .scene_service import SceneService
from .service import LightingService
from .xone_k2 import CONTROLS, SurfaceControl

ACTION_LABELS = {
    "master": "Master",
    "intensity": "Intensity",
    "hue": "Colour",
    "saturation": "Saturation",
    "white": "White",
    "strobe": "Strobe",
    "scene": "Scene",
    "blackout": "Blackout",
    "flash": "Flash",
    "strobe_hold": "Strobe hold",
    "preview": "Preview / Go",
}


def group_label(name: str) -> str:
    """'dj-spots' -> 'DJ spots', 'moving-heads' -> 'Moving heads'."""
    words = [w.upper() if len(w) <= 2 else w for w in name.split("-")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


class ControlView(BaseModel):
    id: str
    type: str
    kind: str
    number: int
    area: str
    row: str
    column: int
    label: str
    relative: bool
    action: Optional[MidiAction] = None
    action_label: str = ""
    target_label: str = ""
    value: Optional[float] = None
    led: str = "off"


class ColumnView(BaseModel):
    column: int
    title: str
    target: Optional[str] = None
    colour: Optional[tuple[float, float, float]] = None


class TargetView(BaseModel):
    id: str
    label: str
    actions: list[str]  # fixture actions this target supports


class ActionInfo(BaseModel):
    type: str
    label: str
    continuous: bool  # fader/pot/encoder (CC) rather than button (note)
    target: Literal["none", "scene", "fixture"]


def action_infos() -> list[ActionInfo]:
    return [
        ActionInfo(
            type=action, label=label, continuous=action in CONTINUOUS_ACTIONS,
            target="scene" if action == "scene" else "fixture" if action in FIXTURE_ACTIONS else "none",
        )
        for action, label in ACTION_LABELS.items()
    ]


class ControllerView(BaseModel):
    channel: int
    blackout: bool
    master: float
    active_scene: Optional[str]
    preview: bool = False
    columns: list[ColumnView]
    controls: list[ControlView]
    targets: list[TargetView]
    actions: list[ActionInfo]


class ControllerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["note", "cc"]
    number: int = Field(ge=0, le=127)
    value: int = Field(ge=0, le=127)


class EventResponse(BaseModel):
    accepted: bool = True


def build_router(
    channel: int,
    mapping: MidiMappingStore,
    control: MidiControl,
    controller: FixtureController,
    scenes: SceneService,
    service: LightingService,
) -> APIRouter:
    router = APIRouter(prefix="/api/controller")
    surface = {(c.kind, c.number): c for c in CONTROLS}

    def target_labels() -> dict[str, str]:
        labels = {ALL_TARGET: "All fixtures"}
        for name in controller.patch.groups():
            labels[name] = group_label(name)
        for fixture in controller.patch.fixtures:
            labels[fixture.patch.id] = fixture.patch.name
        for scene in scenes.store.all():
            labels[scene.id] = scene.name
        return labels

    def targets() -> list[TargetView]:
        patch = controller.patch
        entries = [(ALL_TARGET, "All fixtures", patch.fixtures)]
        entries += [(n, group_label(n), m) for n, m in patch.groups().items()]
        entries += [(f.patch.id, f.patch.name, (f,)) for f in patch.fixtures]
        def supported(members, action: str) -> bool:
            needed = set(REQUIRED_ATTRIBUTES[action])
            return any(needed <= set(f.mode.attributes()) for f in members)

        return [
            TargetView(id=target_id, label=label,
                       actions=[a for a in FIXTURE_ACTIONS if supported(members, a)])
            for target_id, label, members in entries
        ]

    def control_view(
        item: SurfaceControl, binding: Optional[MidiBinding], labels: dict[str, str],
        active: Optional[str], blackout: bool, held: set[MidiKey], preview: bool,
    ) -> ControlView:
        view = ControlView(
            id=item.id, type=item.type, kind=item.kind, number=item.number, area=item.area,
            row=item.row, column=item.column, label=item.label, relative=item.relative,
        )
        if binding is None:
            return view
        action = binding.action
        view.action = action
        view.action_label = ACTION_LABELS[action.type]
        view.target_label = labels.get(action.target or "", action.target or "")
        view.value = control.value(action)
        view.led = button_colour(binding, active, blackout, held, preview) or "off"
        return view

    def columns(views: list[ControlView]) -> list[ColumnView]:
        result = []
        for column in range(1, 5):
            strip = [v for v in views if v.area == "strip" and v.column == column and v.action]
            strip.sort(key=lambda v: v.type != "fader")  # the fader names the column
            title, target = f"Column {column}", None
            if strip:
                first = strip[0].action
                title = strip[0].target_label or strip[0].action_label
                fixture_targets = [v.action.target for v in strip if v.action.type in FIXTURE_ACTIONS]
                target = first.target if first.type in FIXTURE_ACTIONS else (
                    fixture_targets[0] if fixture_targets else None
                )
            colour = control.colour(target) if target else None
            result.append(ColumnView(column=column, title=title, target=target, colour=colour))
        return result

    @router.get("", response_model=ControllerView)
    def get_controller() -> ControllerView:
        by_key = {b.key: b for b in mapping.bindings()}
        labels = target_labels()
        active = scenes.active_scene()
        blackout = service.blackout_active()
        held = control.held()
        preview = controller.preview_active()
        views = []
        for item in CONTROLS:
            key = MidiKey(kind=item.kind, channel=channel, number=item.number)
            views.append(control_view(item, by_key.get(key), labels, active, blackout, held, preview))
        return ControllerView(
            channel=channel, blackout=blackout, master=controller.master(), active_scene=active,
            preview=preview, columns=columns(views), controls=views, targets=targets(), actions=action_infos(),
        )

    @router.post("/event", response_model=EventResponse)
    def send_event(event: ControllerEvent) -> EventResponse:
        if (event.kind, event.number) not in surface:
            raise HTTPException(status_code=404, detail="No such Xone:K2 control.")
        control.handle(MidiEvent(event.kind, channel, event.number, event.value), virtual=True)
        return EventResponse()

    return router
