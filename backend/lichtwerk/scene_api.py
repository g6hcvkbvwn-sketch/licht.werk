"""HTTP routes for groups, scenes and master intensity. Behavior lives in the services."""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .fixture_service import FixtureController, FixtureDisabledError
from .scene_service import SceneService
from .scenes import BEAT_VALUES, PersistenceError, Scene, SceneContent
from .positioning import PositioningError
from .transitions import MAX_FADE_SECONDS


class GroupInfo(BaseModel):
    id: str
    name: str
    fixtures: list[str]


class ValuesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    values: dict[str, float] = Field(min_length=1)


class GroupStateResponse(BaseModel):
    group: str
    changed_fixtures: list[str]


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=80)
    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)
    fade_timing: Literal["seconds", "beats"] = "seconds"
    fade_beats: float = 1.0
    fixture_ids: Optional[list[str]] = None
    group_ids: Optional[list[str]] = None
    attributes: Optional[list[str]] = None
    include_haze: bool = False
    include_position: bool = False

    @field_validator("fade_beats")
    @classmethod
    def valid_fade_beats(cls, value: float) -> float:
        if value != 0 and value not in BEAT_VALUES:
            raise ValueError("Beat fades must be a cut (0) or .5, 1, 2, 4, 8, 16, 32.")
        return value


class DuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)


class RecallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Overrides the scene's stored fade time for this recall only.
    fade_seconds: Optional[float] = Field(default=None, ge=0.0, le=MAX_FADE_SECONDS)


class MasterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    level: float = Field(ge=0.0, le=1.0)


class MasterResponse(BaseModel):
    master: float


class DeletedResponse(BaseModel):
    deleted: str


def build_router(controller: FixtureController, scenes: SceneService) -> APIRouter:
    router = APIRouter()
    store = scenes.store

    def not_found(error: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=error.args[0])

    def save_failed(error: PersistenceError) -> HTTPException:
        return HTTPException(status_code=503, detail=str(error))

    @router.get("/api/groups", response_model=list[GroupInfo])
    def list_groups() -> list[GroupInfo]:
        return [
            GroupInfo(
                id=name, name=controller.patch.group_name(name),
                fixtures=[m.patch.id for m in members],
            )
            for name, members in controller.patch.groups().items()
        ]

    @router.post("/api/groups/{group}/state", response_model=GroupStateResponse)
    def set_group_state(group: str, command: ValuesRequest) -> GroupStateResponse:
        try:
            changed = controller.set_group_values(group, command.values)
        except KeyError as error:
            raise not_found(error) from error
        except FixtureDisabledError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return GroupStateResponse(group=group, changed_fixtures=changed)

    @router.post("/api/output/master", response_model=MasterResponse)
    def set_master(command: MasterRequest) -> MasterResponse:
        controller.set_master(command.level)
        return MasterResponse(master=controller.master())

    @router.get("/api/scenes", response_model=list[Scene])
    def list_scenes() -> list[Scene]:
        return store.all()

    @router.post("/api/scenes", response_model=Scene, status_code=201)
    def create_scene(content: SceneContent) -> Scene:
        try:
            return store.create(content)
        except PersistenceError as error:
            raise save_failed(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/api/scenes/capture", response_model=Scene, status_code=201)
    def capture_scene(command: CaptureRequest) -> Scene:
        try:
            return scenes.capture(**command.model_dump())
        except KeyError as error:
            raise not_found(error) from error
        except PersistenceError as error:
            raise save_failed(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.put("/api/scenes/{scene_id}", response_model=Scene)
    def update_scene(scene_id: str, content: SceneContent) -> Scene:
        try:
            return store.update(scene_id, content)
        except KeyError as error:
            raise not_found(error) from error
        except PersistenceError as error:
            raise save_failed(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/api/scenes/{scene_id}/duplicate", response_model=Scene, status_code=201)
    def duplicate_scene(scene_id: str, command: Optional[DuplicateRequest] = None) -> Scene:
        try:
            return store.duplicate(scene_id, command.name if command else None)
        except KeyError as error:
            raise not_found(error) from error
        except PersistenceError as error:
            raise save_failed(error) from error

    @router.delete("/api/scenes/{scene_id}", response_model=DeletedResponse)
    def delete_scene(scene_id: str) -> DeletedResponse:
        try:
            scenes.delete(scene_id)
        except KeyError as error:
            raise not_found(error) from error
        except PersistenceError as error:
            raise save_failed(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return DeletedResponse(deleted=scene_id)

    @router.post("/api/scenes/{scene_id}/apply", response_model=Scene)
    @router.post("/api/scenes/{scene_id}/recall", response_model=Scene)
    def recall_scene(scene_id: str, command: Optional[RecallRequest] = None) -> Scene:
        try:
            return scenes.recall(scene_id, command.fade_seconds if command else None)
        except KeyError as error:
            raise not_found(error) from error
        except PositioningError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
