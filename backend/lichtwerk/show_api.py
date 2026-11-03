"""Scene ownership/global masks and sequence authoring/playback HTTP boundary."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .scene_service import SceneService
from .scenes import PersistenceError
from .sequence_service import SequenceService
from .sequences import Sequence, SequenceContent
from .positioning import PositioningError
from .quantize import Quantizer
from .live_modifiers import LiveModifiers, ModifierSettings


class GlobalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixtures: dict[str, list[str]]


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture_ids: Optional[list[str]] = None
    attributes: Optional[list[str]] = None


class QuantizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class PlaybackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview: Optional[bool] = None


def build_router(scenes: SceneService, sequences: SequenceService,
                 quantizer: Optional[Quantizer] = None,
                 modifiers: Optional[LiveModifiers] = None) -> APIRouter:
    router = APIRouter()

    def translate(error: Exception) -> HTTPException:
        if isinstance(error, KeyError):
            return HTTPException(404, detail=str(error.args[0]))
        if isinstance(error, PersistenceError):
            return HTTPException(503, detail=str(error))
        if isinstance(error, PositioningError):
            return HTTPException(409, detail=str(error))
        return HTTPException(422, detail=str(error))

    def show_state() -> dict:
        return {
            "globals": scenes.globals(), "locks": scenes.locks(),
            "sequence": sequences.status(), "live_sequence": sequences.status(live=True),
            "look_notice": scenes.look_notice(),
            "beat": sequences.beat(),
            "quantize": quantizer.state() if quantizer is not None else None,
            "modifiers": modifiers.state() if modifiers is not None else None,
        }

    @router.get("/api/show")
    def get_show() -> dict:
        return show_state()

    @router.put("/api/show/globals")
    def set_globals(command: GlobalsRequest) -> dict:
        try:
            scenes.set_globals(command.fixtures)
        except (KeyError, ValueError) as error:
            raise translate(error) from error
        return show_state()

    @router.put("/api/show/quantize")
    def set_quantize(command: QuantizeRequest) -> dict:
        if quantizer is None:
            raise HTTPException(409, detail="Quantize is not available.")
        try:
            quantizer.set_enabled(command.enabled)
        except ValueError as error:
            raise translate(error) from error
        return show_state()

    @router.put("/api/show/modifiers")
    def set_modifiers(command: ModifierSettings) -> dict:
        if modifiers is None:
            raise HTTPException(409, detail="Live modifiers are not available.")
        try:
            modifiers.set(command)
        except ValueError as error:
            raise translate(error) from error
        return show_state()

    @router.post("/api/show/release")
    def release(command: Optional[ReleaseRequest] = None) -> dict:
        command = command or ReleaseRequest()
        scenes.release_control(command.fixture_ids, command.attributes)
        return show_state()

    @router.get("/api/sequences", response_model=list[Sequence])
    def list_sequences() -> list[Sequence]:
        return sequences.store.all()

    @router.get("/api/sequences/{sequence_id}", response_model=Sequence)
    def get_sequence(sequence_id: str) -> Sequence:
        try:
            return sequences.store.get(sequence_id)
        except KeyError as error:
            raise translate(error) from error

    @router.post("/api/sequences", response_model=Sequence, status_code=201)
    def create_sequence(content: SequenceContent) -> Sequence:
        try:
            return sequences.store.create(content)
        except (ValueError, KeyError, PersistenceError) as error:
            raise translate(error) from error

    @router.put("/api/sequences/{sequence_id}", response_model=Sequence)
    def update_sequence(sequence_id: str, content: SequenceContent) -> Sequence:
        try:
            return sequences.store.update(sequence_id, content)
        except (ValueError, KeyError, PersistenceError) as error:
            raise translate(error) from error

    @router.delete("/api/sequences/{sequence_id}")
    def delete_sequence(sequence_id: str) -> dict:
        try:
            sequences.store.delete(sequence_id)
        except (ValueError, KeyError, PersistenceError) as error:
            raise translate(error) from error
        return {"deleted": sequence_id}

    @router.post("/api/sequences/{sequence_id}/{action}")
    def playback(sequence_id: str, action: str, command: Optional[PlaybackRequest] = None):
        actions = {
            "start": lambda: sequences.start(sequence_id), "pause": sequences.pause,
            "resume": sequences.resume, "stop": sequences.stop, "restart": sequences.restart,
            "apply": sequences.apply_now, "next": sequences.next,
        }
        if action not in actions:
            raise HTTPException(404, detail="Unknown sequence action.")
        preview = command.preview if command else None
        if preview is True and not scenes._controller.preview_active():
            raise HTTPException(409, detail="Start preview mode before previewing a sequence.")
        with scenes._controller.editing_context(
            scenes._controller.preview_active() if preview is None else preview
        ):
            try:
                sequences.store.get(sequence_id)
                current = sequences.status()
                if action != "start" and (current is None or current["id"] != sequence_id):
                    raise HTTPException(409, detail="This sequence is not the selected runtime sequence.")
                if (action == "next" and quantizer is not None and current["status"] in ("running", "paused")
                        and quantizer.submit("next", "NEXT", lambda held: sequences.next(),
                                             preview=scenes._controller.editing_preview())):
                    return sequences.status()
                return actions[action]()
            except (ValueError, KeyError, PositioningError) as error:
                raise translate(error) from error

    return router
