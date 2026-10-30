"""HTTP routes for the 2D room map: room size, fixture positions and live/preview colours."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .fixture_service import FixtureController
from .room import RigStore
from .room_map import PLACEHOLDER_ROOM, RGB, MapPosition, MapStore, light_colour, ring_colours
from .scenes import PersistenceError
from .service import LightingService
from .strobe import strobe_hz


class MapFixture(BaseModel):
    id: str
    name: str
    enabled: bool
    x: Optional[float]
    y: Optional[float]
    live: Optional[list[float]]  # RGB 0-1 actually output (master, blackout, flash included)
    preview: Optional[list[float]]  # RGB 0-1 of the state being edited (= live outside preview)
    live_strobe_hz: Optional[float]  # approximate native strobe rate on the output; None = off
    preview_strobe_hz: Optional[float]  # same for the state being edited
    live_beam: Optional[list[float]] = None
    preview_beam: Optional[list[float]] = None
    shape: Optional[str] = None
    guard_reason: Optional[str] = None
    setup_override: bool = False
    live_haze: Optional[dict[str, float]] = None
    preview_haze: Optional[dict[str, float]] = None
    live_ring: list[RGB] = Field(default_factory=list)
    preview_ring: list[RGB] = Field(default_factory=list)
    live_ring_strobe_hz: Optional[float] = None
    preview_ring_strobe_hz: Optional[float] = None


class MapView(BaseModel):
    width: float  # room x size in metres
    depth: float  # room y size in metres
    measured: bool  # False = placeholder size, for drawing only
    preview_active: bool
    fixtures: list[MapFixture]


def build_router(
    controller: FixtureController, service: LightingService, rig: RigStore, store: MapStore
) -> APIRouter:
    router = APIRouter(prefix="/api/map")

    def room_size() -> tuple[float, float, bool]:
        room = rig.room_file().room
        if room.size_x is not None and room.size_y is not None:
            return room.size_x, room.size_y, True
        return PLACEHOLDER_ROOM[0], PLACEHOLDER_ROOM[1], False

    def view() -> MapView:
        width, depth, measured = room_size()
        master = controller.master()
        blackout = service.blackout_active()
        positions = store.all()
        fixtures = []
        for fixture in controller.fixtures():
            fixture_id = fixture.patch.id
            position = positions.get(fixture_id)
            enabled = fixture.patch.enabled
            output = controller.output_state(fixture_id)
            editing = controller.preview_output_state(fixture_id)
            live = light_colour(output, 0.0 if blackout else master)
            # The preview shows the editing state at the current master; blackout is ignored.
            preview = light_colour(editing, master)
            guard = controller.guard
            moving_head = "pan" in editing and "tilt" in editing
            live_hit = guard.hit(fixture_id, output["pan"], output["tilt"]) if guard and moving_head else None
            preview_hit = guard.hit(fixture_id, editing["pan"], editing["tilt"]) if guard and moving_head else None
            fixtures.append(MapFixture(
                id=fixture_id, name=fixture.patch.name, enabled=enabled,
                x=position.x if position else None, y=position.y if position else None,
                live=list(live) if live and enabled else None,
                preview=list(preview) if preview and enabled else None,
                live_strobe_hz=strobe_hz(fixture, output.get("strobe")) if enabled else None,
                preview_strobe_hz=strobe_hz(fixture, editing.get("strobe")) if enabled else None,
                live_beam=[live_hit.x, live_hit.y] if live_hit and enabled else None,
                preview_beam=[preview_hit.x, preview_hit.y] if preview_hit and enabled else None,
                shape={"cameo-thunder-wash-100-rgb": "rectangle", "cameo-q-spot-15-rgbw-bk": "spot"}.get(fixture.patch.definition),
                guard_reason=guard.reason(fixture_id, editing["pan"], editing["tilt"], live=False) if guard and moving_head else None,
                setup_override=guard.setup_override(fixture_id) if guard and moving_head else False,
                live_haze={name: output[name] for name in ("pump", "fan")}
                if enabled and {"pump", "fan"} <= output.keys() else None,
                preview_haze={name: editing[name] for name in ("pump", "fan")}
                if enabled and controller.preview_active() and {"pump", "fan"} <= editing.keys() else None,
                live_ring=ring_colours(fixture, output, 0.0 if blackout else master),
                preview_ring=ring_colours(fixture, editing, master),
                live_ring_strobe_hz=strobe_hz(fixture, output.get("ring_strobe"), "ring_strobe") if enabled else None,
                preview_ring_strobe_hz=strobe_hz(fixture, editing.get("ring_strobe"), "ring_strobe") if enabled else None,
            ))
        return MapView(
            width=width, depth=depth, measured=measured,
            preview_active=controller.preview_active(), fixtures=fixtures,
        )

    @router.get("", response_model=MapView)
    def get_map() -> MapView:
        return view()

    @router.put("/fixtures/{fixture_id}", response_model=MapView)
    def place(fixture_id: str, position: MapPosition) -> MapView:
        try:
            controller.patch.get(fixture_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"Unknown fixture '{fixture_id}'.") from error
        width, depth, _ = room_size()
        if position.x > width or position.y > depth:
            raise HTTPException(
                status_code=422,
                detail=f"Position must be inside the room (0-{width:g} m by 0-{depth:g} m).",
            )
        try:
            store.place(fixture_id, position)
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return view()

    @router.delete("/fixtures/{fixture_id}", response_model=MapView)
    def unplace(fixture_id: str) -> MapView:
        try:
            store.remove(fixture_id)
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return view()

    return router
