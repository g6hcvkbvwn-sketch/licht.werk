"""HTTP routes for MIDI status, monitor, bindings and learn mode."""

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .midi_control import MidiControl
from .midi_mapping import MidiAction, MidiBinding, MidiMappingStore
from .scenes import PersistenceError


class MonitorEntry(BaseModel):
    kind: str
    channel: int
    number: int
    value: int


class MidiStatus(BaseModel):
    enabled: bool
    device_filter: Optional[str] = None
    connected: bool = False
    connected_device: Optional[str] = None
    available_devices: list[str] = []
    last_error: Optional[str] = None
    # APC40 MKII ports switched to Alternate Ableton Live mode, and the last setup error.
    apc40_alternate_mode: list[str] = []
    apc40_error: Optional[str] = None
    learning: Optional[MidiAction] = None
    bindings: list[MidiBinding] = []
    monitor: list[MonitorEntry] = []


class BindingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bindings: list[MidiBinding]


def build_router(
    mapping: MidiMappingStore,
    control: MidiControl,
    input_status: Optional[Callable[[], dict]],
) -> APIRouter:
    router = APIRouter(prefix="/api/midi")

    def status() -> MidiStatus:
        base = input_status() if input_status else {}
        return MidiStatus(
            enabled=input_status is not None,
            learning=control.learning(),
            bindings=mapping.bindings(),
            monitor=[MonitorEntry(**e) for e in control.monitor()],
            **base,
        )

    @router.get("", response_model=MidiStatus)
    def get_status() -> MidiStatus:
        return status()

    @router.put("/bindings", response_model=MidiStatus)
    def put_bindings(request: BindingsRequest) -> MidiStatus:
        try:
            mapping.replace(request.bindings)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PersistenceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return status()

    @router.post("/learn", response_model=MidiStatus)
    def start_learn(action: MidiAction) -> MidiStatus:
        if input_status is None:
            raise HTTPException(status_code=409, detail="MIDI is not enabled in config/app.json.")
        control.start_learn(action)
        return status()

    @router.delete("/learn", response_model=MidiStatus)
    def cancel_learn() -> MidiStatus:
        control.cancel_learn()
        return status()

    return router
