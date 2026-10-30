"""HTTP routes for the room, zones and moving-head positioning."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .fixture_service import FixtureDisabledError
from .position_service import PositionService
from .positioning import HeadCalibration, HeadSetup, Point, PositioningError, NotConfiguredError
from .room import HeadAssignment, RigStore, RoomFile
from .scenes import PersistenceError
from .transitions import MAX_FADE_SECONDS
from .zones import Zone


class HeadInfo(BaseModel):
    id: str
    name: str
    setup: HeadSetup
    calibrated: bool
    pan: float
    tilt: float
    # Mechanical range from the fixture definition, so the UI can show pan/tilt in degrees.
    pan_degrees: float
    tilt_degrees: float
    floor_point: Optional[Point]
    assignment: HeadAssignment
    # Beam spot inside the include zone (0-1 along length and width).
    spot_u: float
    spot_v: float
    guard_active: bool
    guard_reason: Optional[str]
    beam_hit: Optional[Point]
    beam_blocked_by: Optional[str]
    setup_override: bool = False


class AimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target: Optional[str] = None
    point: Optional[Point] = None
    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)


class RestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    aim_target: Optional[str] = None
    aim_zone: Optional[str] = None
    include_zones: list[str] = Field(default_factory=list)
    exclude_zones: list[str] = Field(default_factory=list)
    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)


class SpotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    u: float = Field(ge=0.0, le=1.0)
    v: float = Field(ge=0.0, le=1.0)
    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)


class StripAssignmentRequest(AssignmentRequest):
    target: str = Field(min_length=1)


class StripAssignmentResult(BaseModel):
    owner: str
    target: str
    assignment: HeadAssignment
    active: bool
    heads: list[HeadInfo]
    notes: list[str]


class HeadsResult(BaseModel):
    heads: list["HeadInfo"]
    # Heads that could not be moved and why (the assignment itself is saved).
    notes: list[str]


class FitRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pan: float = Field(ge=0.0, le=1.0)
    tilt: float = Field(ge=0.0, le=1.0)
    target: str


class SetupOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class FitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observations: list[FitRecord]


class FitResponse(BaseModel):
    calibration: HeadCalibration
    residuals_deg: list[float]
    rms_deg: float
    max_deg: float
    acceptable: bool


def build_router(positions: PositionService, rig: RigStore, controller) -> APIRouter:
    router = APIRouter(prefix="/api")

    def head_info(head_id: str) -> HeadInfo:
        fixture = controller.patch.get(head_id)
        state = controller.state(head_id)
        guard = controller.guard
        status = guard.status(head_id) if guard else None
        exception = controller.static_target_active(head_id, state, preview=controller.preview_active())
        blocked = guard.blocking_zone(head_id, state["pan"], state["tilt"], live=False) if guard else None
        reason = guard.reason(head_id, state["pan"], state["tilt"], live=False) if guard else "no room data"
        if exception and guard and not guard.setup_override(head_id):
            reason = "Selected static target exception (stationary aim only)"
        u, v = positions.spot(head_id)
        return HeadInfo(
            id=head_id,
            name=fixture.patch.name,
            setup=rig.head(head_id),
            calibrated=rig.head(head_id).calibrated,
            pan=state["pan"],
            tilt=state["tilt"],
            pan_degrees=fixture.definition.motion.pan_degrees,
            tilt_degrees=fixture.definition.motion.tilt_degrees,
            floor_point=positions.floor_point(head_id),
            assignment=rig.assignment(head_id),
            spot_u=u,
            spot_v=v,
            guard_active=bool(status and status.active),
            guard_reason=reason,
            beam_hit=guard.hit(head_id, state["pan"], state["tilt"]) if guard else None,
            beam_blocked_by=blocked.name if blocked and not exception else None,
            setup_override=guard.setup_override(head_id) if guard else False,
        )

    def heads_result(target: str, notes: list[str]) -> HeadsResult:
        return HeadsResult(heads=[head_info(h) for h in positions.heads_of(target)], notes=notes)

    def changed_room(call):
        """Run a room change, then re-render so the beam guard sees the new zones at once."""
        result = guard(call)
        controller.clear_static_aims(rig.head_ids())
        controller.refresh_all()
        return result

    def guard(call):
        try:
            return call()
        except KeyError as error:
            raise HTTPException(404, str(error.args[0])) from error
        except FixtureDisabledError as error:
            raise HTTPException(409, str(error)) from error
        except NotConfiguredError as error:
            raise HTTPException(409, str(error)) from error
        except PositioningError as error:
            raise HTTPException(422, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except PersistenceError as error:
            raise HTTPException(500, str(error)) from error

    @router.get("/room", response_model=RoomFile)
    def get_room() -> RoomFile:
        return rig.room_file()

    @router.put("/room", response_model=RoomFile)
    def put_room(document: RoomFile) -> RoomFile:
        return changed_room(lambda: rig.set_room_file(document))

    @router.put("/zones/{zone_id}", response_model=Zone)
    def put_zone(zone_id: str, zone: Zone) -> Zone:
        if zone.id != zone_id:
            raise HTTPException(422, "The zone id in the URL and in the body must match.")
        return changed_room(lambda: rig.save_zone(zone))

    @router.delete("/zones/{zone_id}", status_code=204)
    def delete_zone(zone_id: str) -> None:
        changed_room(lambda: rig.delete_zone(zone_id))

    @router.delete("/targets/{target_id}", status_code=204)
    def delete_target(target_id: str) -> None:
        changed_room(lambda: rig.delete_target(target_id))

    @router.get("/heads/{head_id}/surface-point", response_model=Point)
    def current_surface_point(head_id: str) -> Point:
        return guard(lambda: positions.current_surface_point(head_id))

    @router.put("/positions/{target}/assignment", response_model=HeadsResult)
    def put_assignment(target: str, request: AssignmentRequest) -> HeadsResult:
        def run() -> HeadsResult:
            assignment = HeadAssignment(
                aim_target=request.aim_target,
                aim_zone=request.aim_zone,
                include_zones=(
                    request.include_zones if "include_zones" in request.model_fields_set
                    else ([request.aim_zone] if request.aim_zone else [])
                ),
                exclude_zones=request.exclude_zones,
            )
            notes = positions.assign(target, assignment, request.fade_seconds)
            return heads_result(target, notes)

        return guard(run)

    def strip_result(owner: str, target: str, notes: list[str]) -> StripAssignmentResult:
        return StripAssignmentResult(
            owner=owner, target=target, assignment=positions.strip_assignment(owner),
            active=positions.strip_active(owner),
            heads=[head_info(head) for head in positions.heads_of(target)], notes=notes,
        )

    @router.get("/positions/strips/{owner}", response_model=StripAssignmentResult)
    def get_strip_assignment(owner: str, target: str) -> StripAssignmentResult:
        return guard(lambda: strip_result(owner, target, []))

    @router.put("/positions/strips/{owner}", response_model=StripAssignmentResult)
    def put_strip_assignment(owner: str, request: StripAssignmentRequest) -> StripAssignmentResult:
        def run() -> StripAssignmentResult:
            values = request.model_dump(exclude={"target", "fade_seconds"})
            if "include_zones" not in request.model_fields_set:
                values.pop("include_zones")
            assignment = HeadAssignment.model_validate(values)
            notes = positions.set_strip_assignment(
                owner, request.target, assignment, request.fade_seconds,
            )
            return strip_result(owner, request.target, notes)

        return guard(run)

    @router.post("/positions/{target}/spot", response_model=HeadsResult)
    def post_spot(target: str, request: SpotRequest) -> HeadsResult:
        def run() -> HeadsResult:
            heads = positions.heads_of(target)
            if len(heads) == 1:
                positions.set_spot(heads[0], request.u, request.v, request.fade_seconds)
                return heads_result(target, [])
            notes = positions.set_group_spot(target, request.u, request.v, request.fade_seconds)
            return heads_result(target, notes)

        return guard(run)

    @router.get("/heads", response_model=list[HeadInfo])
    def list_heads() -> list[HeadInfo]:
        return [head_info(head_id) for head_id in rig.head_ids()]

    @router.put("/heads/{head_id}/setup-override", response_model=HeadInfo)
    def setup_override(head_id: str, request: SetupOverrideRequest) -> HeadInfo:
        def run() -> HeadInfo:
            if request.enabled:
                rig.require_live_setup()
            if controller.guard is None:
                raise NotConfiguredError("No beam guard is configured.")
            controller.guard.set_setup_override(head_id, request.enabled)
            controller.refresh(head_id)
            return head_info(head_id)

        return guard(run)

    @router.get("/heads/{head_id}", response_model=HeadInfo)
    def get_head(head_id: str) -> HeadInfo:
        return guard(lambda: head_info(head_id))

    @router.put("/heads/{head_id}", response_model=HeadInfo)
    def put_head(head_id: str, setup: HeadSetup) -> HeadInfo:
        def run() -> HeadInfo:
            positions.save_head(head_id, setup)
            return head_info(head_id)

        return guard(run)

    @router.post("/heads/{head_id}/aim", response_model=HeadInfo)
    def aim(head_id: str, request: AimRequest) -> HeadInfo:
        if (request.target is None) == (request.point is None):
            raise HTTPException(422, "Give exactly one of 'target' or 'point'.")

        def run() -> HeadInfo:
            if request.target is not None:
                positions.aim_target(head_id, request.target, request.fade_seconds)
            else:
                positions.aim_point(head_id, request.point, request.fade_seconds)
            return head_info(head_id)

        return guard(run)

    @router.post("/heads/{head_id}/rest", response_model=HeadInfo)
    def rest(head_id: str, request: RestRequest) -> HeadInfo:
        def run() -> HeadInfo:
            positions.go_rest(head_id, request.fade_seconds)
            return head_info(head_id)

        return guard(run)

    @router.post("/heads/{head_id}/calibration/fit", response_model=FitResponse)
    def fit(head_id: str, request: FitRequest) -> FitResponse:
        def run() -> FitResponse:
            result = positions.fit(
                head_id, [(r.pan, r.tilt, r.target) for r in request.observations]
            )
            return FitResponse(
                calibration=result.calibration,
                residuals_deg=list(result.residuals_deg),
                rms_deg=result.rms_deg,
                max_deg=result.max_deg,
                acceptable=result.acceptable,
            )

        return guard(run)

    return router
