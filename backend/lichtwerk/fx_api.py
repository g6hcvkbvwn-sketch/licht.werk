"""Family FX slot authoring and safe launch commands."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .apc40_control import Apc40Control, effect_owner
from .effects import EFFECT_TYPES, EffectType, FxDefinition, factory_choices, family, fits
from .fx_service import FxService
from .positioning import PositioningError
from .scenes import PersistenceError
from .rings import ring_segments, segment_participant


class NamedReference(BaseModel):
    id: str
    name: str


class FxRunningView(BaseModel):
    slot: int
    definition: FxDefinition
    active: bool


class FxPageView(BaseModel):
    slots: list[FxDefinition]
    factory: list[FxDefinition]
    fixtures: list[NamedReference]
    targets: list[NamedReference]
    running: FxRunningView | None
    warnings: list[str]
    preview: bool
    component: Literal["beam", "ring"] = "beam"


def build_router(control: Apc40Control, service: FxService) -> APIRouter:
    router = APIRouter(prefix="/api/fx")

    def target_for(track: int, page: EffectType, component: Literal["beam", "ring"] = "beam") -> str:
        strip = next((s for s in control.strips if s.track == track), None)
        if strip is None or strip.target is None or page not in EFFECT_TYPES:
            raise HTTPException(status_code=404, detail="Choose a patched strip and one of the three FX families.")
        if not any(fits(page, f, component=component) for f in control.fixtures(track)):
            raise HTTPException(status_code=422, detail="This strip has no fixtures compatible with that FX family.")
        return strip.target

    def view(track: int, page: EffectType, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        target = target_for(track, page, component)
        running = service.effects.running(effect_owner(track), page, component=component)
        warnings = []
        notice = service.effects.notice
        if notice is not None:
            warnings.append(notice)
        if not control.is_active(track):
            warnings.append("This strip is in standby. Switch its group/individual mode to launch FX.")
        if running is not None:
            warnings.extend(service.warnings(FxDefinition(name=running.name, kind=running.kind, params=running.params)))
            if running.params.colour_mode == "palette":
                warnings.append(f"Fixed palette controls {component} colour; colour controls still edit the underlying base.")
        return FxPageView(
            slots=[service.store.get(target, page, row, component=component) for row in range(5)],
            factory=[item for item in factory_choices() if family(item.kind) == page],
            fixtures=references(track, page, component),
            targets=[NamedReference(id=t.id, name=t.name) for t in service.positions.rig.room_file().targets],
            running=FxRunningView(
                slot=running.preset,
                definition=FxDefinition(name=running.name, kind=running.kind, params=running.params),
                active=running.active,
            ) if running else None,
            warnings=warnings,
            preview=control.preview_active(),
            component=component,
        )

    def references(track: int, page: EffectType, component: Literal["beam", "ring"]) -> list[NamedReference]:
        fixtures = [f for f in control.fixtures(track) if fits(page, f, component=component)]
        if component == "ring":
            return [
                NamedReference(id=segment_participant(f.patch.id, section), name=f"{f.patch.name} · section {section}")
                for f in fixtures
                for section in ring_segments(f)
            ]
        return [NamedReference(id=f.patch.id, name=f.patch.name) for f in fixtures]

    def command(track: int, page: EffectType, action: Callable[[], None], component: Literal["beam", "ring"] = "beam") -> FxPageView:
        target_for(track, page, component)
        try:
            action()
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except (ValueError, KeyError, PositioningError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return view(track, page, component)

    @router.get("/{track}/{page}/slots", response_model=FxPageView)
    def get_slots(track: int, page: EffectType, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        return view(track, page, component)

    @router.put("/{track}/{page}/slots/{row}", response_model=FxPageView)
    def save_slot(track: int, page: EffectType, row: int, definition: FxDefinition, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        return command(track, page, lambda: service.store.save(target_for(track, page, component), page, row, definition, component=component), component)

    @router.delete("/{track}/{page}/slots/{row}", response_model=FxPageView)
    def reset_slot(track: int, page: EffectType, row: int, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        return command(track, page, lambda: service.store.reset(target_for(track, page, component), page, row, component=component), component)

    @router.post("/{track}/{page}/slots/{row}/start", response_model=FxPageView)
    def start_slot(track: int, page: EffectType, row: int, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        return command(track, page, lambda: control.start_effect(track, page, row, component=component), component)

    @router.post("/{track}/{page}/slots/{row}/apply", response_model=FxPageView)
    def apply_slot(track: int, page: EffectType, row: int, definition: FxDefinition, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        def apply() -> None:
            target = target_for(track, page, component)
            if not control.is_active(track):
                raise ValueError("Switch the group/individual mode to activate this strip before Apply now.")
            service.validate_start(target, page, row, definition, component=component)
            service.store.save(target, page, row, definition, component=component)
            control.start_effect(track, page, row, definition, component=component)
        return command(track, page, apply, component)

    @router.post("/{track}/{page}/slots/{row}/store", response_model=FxPageView)
    def store_current(track: int, page: EffectType, row: int, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        def store() -> None:
            running = service.effects.running(effect_owner(track), page, component=component)
            if running is None or running.preset != row:
                raise ValueError("Run this slot before storing its current FX parameters.")
            service.store.save(
                target_for(track, page, component), page, row,
                FxDefinition(name=running.name, kind=running.kind, params=running.params),
                component=component,
            )
        return command(track, page, store, component)

    @router.post("/{track}/{page}/stop", response_model=FxPageView)
    def stop_family(track: int, page: EffectType, component: Literal["beam", "ring"] = "beam") -> FxPageView:
        return command(track, page, lambda: service.effects.stop(effect_owner(track), page, component=component), component)

    return router
