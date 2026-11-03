"""Family FX slot authoring and safe launch commands."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .apc40_control import Apc40Control, effect_owner
from .effects import EFFECT_TYPES, ComboDefinition, EffectType, FxDefinition, factory_choices, family, fits
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
    combo_name: str | None = None


class TourTargetView(BaseModel):
    id: str
    name: str
    x: float | None
    y: float | None
    z: float | None
    reachable: bool
    pattern_ok: bool
    note: str


class TourZoneView(BaseModel):
    id: str
    name: str
    surface: str
    box: tuple[float, float, float, float] | None
    state: Literal["included", "excluded", "unreachable"]
    note: str


class CurrentAimView(BaseModel):
    head: str
    name: str
    target_id: str | None
    zone_id: str | None
    include_zones: list[str]


class FxPageView(BaseModel):
    slots: list[FxDefinition]
    factory: list[FxDefinition]
    fixtures: list[NamedReference]
    targets: list[NamedReference]
    running: FxRunningView | None
    warnings: list[str]
    preview: bool
    component: Literal["beam", "ring"] = "beam"
    tour_targets: list[TourTargetView] = []
    tour_zones: list[TourZoneView] = []
    current_aims: list[CurrentAimView] = []
    anchors: list[str] = []
    room: tuple[float, float, float] | None = None


class ComboRunningView(BaseModel):
    slot: int
    definition: ComboDefinition
    active: bool
    modified: bool


class ComboPageView(BaseModel):
    slots: list[ComboDefinition]
    families: dict[str, FxPageView]
    running: ComboRunningView | None
    preview: bool
    component: Literal["beam", "ring"]


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
        movement = page == "circle" and component == "beam"
        return FxPageView(
            slots=[service.store.get(target, page, row, component=component) for row in range(5)],
            factory=[item for item in factory_choices() if family(item.kind) == page],
            fixtures=references(track, page, component),
            targets=[NamedReference(id=t.id, name=t.name) for t in service.positions.rig.room_file().targets],
            tour_targets=[TourTargetView(**vars(t)) for t in service.tour_targets(target)] if movement else [],
            tour_zones=[TourZoneView(**vars(z)) for z in service.tour_zones(target)] if movement else [],
            current_aims=[
                CurrentAimView(**{**vars(a), "include_zones": list(a.include_zones)})
                for a in service.current_aims(target)
            ] if movement else [],
            anchors=service.movement_anchors(target) if movement else [],
            room=service.positions.rig.room_file().size if movement else None,
            running=FxRunningView(
                slot=running.preset,
                definition=FxDefinition(name=running.name, kind=running.kind, params=running.params),
                active=running.active,
                combo_name=running.combo_name,
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

    def combo_target(track: int) -> str:
        strip = next((s for s in control.strips if s.track == track), None)
        if strip is None or strip.target is None:
            raise ValueError("Choose a patched fixture/group strip.")
        return strip.target

    def combo_view(track: int, component: Literal["beam", "ring"]) -> ComboPageView:
        target = combo_target(track)
        bundled = service.effects.combo_effects(effect_owner(track), component=component)
        first = bundled[0] if bundled else None
        if first is not None:
            assert first.combo_name is not None and first.combo_slot is not None
        return ComboPageView(
            slots=[service.store.get_combo(target, row, component=component) for row in range(5)],
            families={page.value: view(track, page, component) for page in EFFECT_TYPES
                      if any(fits(page, fixture, component) for fixture in control.fixtures(track))},
            running=ComboRunningView(
                slot=first.combo_slot,
                definition=ComboDefinition(name=first.combo_name, effects=[
                    FxDefinition(name=effect.name, kind=effect.kind, params=effect.params)
                    for effect in bundled
                ]),
                active=any(effect.active for effect in bundled),
                modified={family(effect.kind) for effect in bundled} != set(first.combo_families),
            ) if first else None,
            preview=control.preview_active(), component=component,
        )

    def combo_command(track: int, component: Literal["beam", "ring"],
                      action: Callable[[], None]) -> ComboPageView:
        try:
            action()
            return combo_view(track, component)
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except (ValueError, KeyError, PositioningError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/combo/{track}/slots", response_model=ComboPageView)
    def get_combos(track: int, component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        return combo_command(track, component, lambda: None)

    @router.put("/combo/{track}/slots/{row}", response_model=ComboPageView)
    def save_combo(track: int, row: int, definition: ComboDefinition,
                   component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        return combo_command(track, component, lambda: service.store.save_combo(
            combo_target(track), row, definition, component=component,
        ))

    @router.delete("/combo/{track}/slots/{row}", response_model=ComboPageView)
    def reset_combo(track: int, row: int, component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        return combo_command(track, component, lambda: service.store.reset_combo(
            combo_target(track), row, component=component,
        ))

    @router.post("/combo/{track}/slots/{row}/start", response_model=ComboPageView)
    def start_combo(track: int, row: int, component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        return combo_command(track, component, lambda: control.start_combo(track, row, component=component))

    @router.post("/combo/{track}/slots/{row}/apply", response_model=ComboPageView)
    def apply_combo(track: int, row: int, definition: ComboDefinition,
                    component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        def apply() -> None:
            if not control.is_active(track):
                raise ValueError("Activate this strip before Apply now.")
            target = combo_target(track)
            service.validate_combo_start(target, row, definition, component=component)
            service.store.save_combo(target, row, definition, component=component)
            control.start_combo(track, row, definition, component=component)
        return combo_command(track, component, apply)

    @router.post("/combo/{track}/slots/{row}/store", response_model=ComboPageView)
    def store_combo(track: int, row: int, component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        def store() -> None:
            current = combo_view(track, component).running
            if current is None or current.slot != row:
                raise ValueError("Run this Combo slot before storing its current components.")
            service.store.save_combo(combo_target(track), row, current.definition, component=component)
        return combo_command(track, component, store)

    @router.post("/combo/{track}/stop", response_model=ComboPageView)
    def stop_combo(track: int, component: Literal["beam", "ring"] = "beam") -> ComboPageView:
        return combo_command(track, component, lambda: service.effects.stop_combo(
            effect_owner(track), component=component,
        ))

    @router.post("/combo/{track}/knobs/{page}")
    def combo_knobs(track: int, page: EffectType) -> dict[str, str]:
        combo_command(track, control.fx_component(track), lambda: control.select_combo_family(track, page))
        return {"page": page.value}

    @router.post("/{track}/circle/include-zone/{zone_id}", response_model=FxPageView)
    def include_zone(track: int, zone_id: str) -> FxPageView:
        """Add a zone to this strip's included areas (permission only; heads do not move)."""
        return command(track, EffectType.CIRCLE, lambda: control.include_zone(track, zone_id))

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
