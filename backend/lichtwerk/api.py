"""Local HTTP API for lighting control, safe startup and diagnostics."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated, Optional

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import (
    DEFAULT_APC40_PATH,
    DEFAULT_MAP_PATH,
    DEFAULT_FIXTURES_DIR,
    DEFAULT_PATCH_PATH,
    DEFAULT_HEADS_PATH,
    DEFAULT_MIDI_PATH,
    DEFAULT_ROOM_PATH,
    DEFAULT_SCENES_PATH,
    AppConfig,
    load_config,
)
from .diagnostics import DiagnosticsResponse, RecentErrors, build_diagnostics
from .dmx import DMX_SLOT_COUNT
from .effect_engine import EffectEngine
from .fx_presets import FxPresetStore
from .fx_service import FxService
from .fx_api import build_router as build_fx_router
from .fixture_service import FixtureController, FixtureDisabledError
from .fixtures import FixtureDefinition, load_fixture_library
from .midi_adapter import MidiInput, MidiPorts, MidoPorts
from .midi_api import build_router as build_midi_router
from .apc40 import introduction
from .apc40_api import build_router as build_apc40_router
from .apc40_control import Apc40Control
from .live_modifiers import LiveModifiers
from .looks import LookPlayer
from .apc40_feedback import Apc40Feedback
from .apc40_pads import PadStore
from .midi_control import MidiControl
from .xone_k2 import is_relative_cc
from .controller_api import build_router as build_controller_router
from .midi_feedback import LedFeedback
from .midi_mapping import MidiMappingStore
from .output import DmxOutput, GatedDmxOutput, OlaHttpDmxOutput, SimulatedDmxOutput
from .patch import Patch, load_patch
from .position_service import PositionService
from .map_api import build_router as build_map_router
from .preview import PreviewService
from .preview_api import build_router as build_preview_router
from .rig_api import build_router as build_rig_router
from .room_map import MapStore
from .room import RigStore
from .scene_api import GroupInfo, build_router
from .scene_service import SceneService
from .scenes import SceneStore
from .sequences import SequenceStore
from . import ola_runtime
from .sequence_service import SequenceService
from .show_api import build_router as build_show_router
from .service import LightingService
from .tempo import TempoClock
from .transitions import TransitionRunner

logger = logging.getLogger(__name__)


class SetChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    universe: int = Field(ge=0, le=7)
    channel: int = Field(ge=1, le=DMX_SLOT_COUNT)
    value: int = Field(ge=0, le=255)


class BlackoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    active: bool


class FixtureInfo(BaseModel):
    id: str
    name: str
    manufacturer: str
    model: str
    fixture_type: str
    mode: str
    universe: int
    start_address: int
    channel_count: int
    enabled: bool
    groups: list[str]
    attributes: list[str]
    state: dict[str, float]


class FixtureStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Logical attribute values, each 0.0-1.0. Only listed attributes change.
    values: dict[str, float] = Field(min_length=1)


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=80)


class FixtureStateResponse(BaseModel):
    output_mode: str
    physical_output_enabled: bool
    blackout_active: bool
    fixture: FixtureInfo


class StatusResponse(BaseModel):
    output_mode: str
    physical_output_enabled: bool
    blackout_active: bool
    universes: list[int]
    real_output_available: bool = False
    real_output_universes: list[int] = Field(default_factory=list)
    output_error: Optional[str] = None
    master: float = 1.0
    active_scene: Optional[str] = None
    transition_active: bool = False
    preview_active: bool = False
    setup_overrides: list[str] = Field(default_factory=list)


class ArmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    armed: bool
    # Must list exactly the universes that real output will drive.
    confirm_universes: list[int] = Field(default_factory=list)


class UniverseResponse(BaseModel):
    universe: int
    slot_count: int
    channels: list[int]
    blackout_active: bool


class CommandResponse(BaseModel):
    output_mode: str
    physical_output_enabled: bool
    universe: int
    channel: int
    value: int
    blackout_active: bool


class BlackoutResponse(BaseModel):
    output_mode: str
    physical_output_enabled: bool
    blackout_active: bool


def create_app(
    config: Optional[AppConfig] = None,
    real_output: Optional[DmxOutput] = None,
    patch: Optional[Patch] = None,
    fixture_library: Optional[dict[str, FixtureDefinition]] = None,
    scene_store: Optional[SceneStore] = None,
    rig_store: Optional[RigStore] = None,
    midi_store: Optional[MidiMappingStore] = None,
    midi_ports: Optional[MidiPorts] = None,
    apc40_pads: Optional[PadStore] = None,
    map_store: Optional[MapStore] = None,
    name_path: Optional[FilePath] = None,
    sequence_store: Optional[SequenceStore] = None,
    fx_path: Optional[FilePath] = None,
) -> FastAPI:
    """Build the app. Configured real output starts only after blackout/rest initialization.

    `real_output` lets tests inject a mock instead of contacting olad. Without an explicit
    config, the default config, fixture library, patch and scenes are loaded from config/.
    With an explicit config, a missing patch is empty and a missing scene store keeps
    scenes in memory only.
    """
    if config is None:
        if name_path is None:
            name_path = DEFAULT_PATCH_PATH.with_name("fixture-names.json")
        if fx_path is None:
            fx_path = DEFAULT_PATCH_PATH.with_name("fx-presets.json")
        settings = load_config()
        library = (
            fixture_library
            if fixture_library is not None
            else load_fixture_library(DEFAULT_FIXTURES_DIR)
        )
        if patch is None:
            patch = load_patch(DEFAULT_PATCH_PATH, library, settings.universes)
        if scene_store is None:
            scene_store = SceneStore.load(DEFAULT_SCENES_PATH, patch)
        if sequence_store is None:
            sequence_store = SequenceStore.load(DEFAULT_SCENES_PATH.with_name("sequences.json"), scene_store)
        if rig_store is None:
            rig_store = RigStore.load(DEFAULT_ROOM_PATH, DEFAULT_HEADS_PATH, patch)
        if midi_store is None:
            midi_store = MidiMappingStore.load(DEFAULT_MIDI_PATH, patch)
        if apc40_pads is None:
            apc40_pads = PadStore.load(DEFAULT_APC40_PATH)
        if map_store is None:
            map_store = MapStore.load(DEFAULT_MAP_PATH)
    else:
        settings = config
        library = fixture_library or {}
    patch = patch or Patch()
    patch.configure_names(name_path)
    scene_store = scene_store or SceneStore(patch)
    rig_store = rig_store or RigStore(patch)
    midi_store = midi_store or MidiMappingStore(patch)
    apc40_pads = apc40_pads or PadStore()
    map_store = map_store or MapStore()
    gate: Optional[GatedDmxOutput] = None
    output: DmxOutput
    if settings.output_mode == "ola" and settings.ola is not None:
        transport = real_output or OlaHttpDmxOutput(settings.ola.host, settings.ola.port)
        gate = GatedDmxOutput(transport, settings.ola.allowed_universes)
        output = gate
    else:
        output = SimulatedDmxOutput()
    service = LightingService(output, tuple(settings.universes), blackout_active=True)
    service.set_blackout_keep(patch.blackout_keep_channels())
    controller = FixtureController(patch, service, rig=rig_store, max_strobe=settings.max_strobe)
    scenes = SceneService(controller, scene_store)
    positions = PositionService(controller, rig_store)
    scenes.set_positions(positions)
    effects = EffectEngine(patch, TempoClock(), rig=rig_store)
    fx_store = FxPresetStore(patch, rig_store, fx_path)
    fx_service = FxService(controller, positions, effects, fx_store, settings.fade_rate_hz)

    def validate_room(document):
        scenes.validate_room(document)
        fx_service.validate_room(document)

    rig_store.set_definition_validator(validate_room)
    controller.set_effects(effects)
    sequences = SequenceService(
        scenes, sequence_store or SequenceStore(scene_store), effects.tempo,
        output_error=lambda: gate.last_error if gate is not None else None,
    )
    preview = PreviewService(controller, scenes, effects, positions=positions)
    runner = TransitionRunner(controller.tick, settings.fade_rate_hz)
    recent_errors = RecentErrors()
    midi_control = MidiControl(
        midi_store, controller, scenes, service,
        is_relative=lambda key: key.kind == "cc" and is_relative_cc(key.number),
        preview=preview,
    )
    apc40_control = Apc40Control(
        controller, scenes, service, positions, apc40_pads, preview=preview, effects=effects,
        fx_service=fx_service,
    )
    scenes.looks = LookPlayer(apc40_control, scenes)
    modifiers = LiveModifiers(controller, effects, {
        strip.track: (apc40_control.label(strip.track), [
            fixture.patch.id for fixture in apc40_control.fixtures(strip.track)
            if fixture.patch.enabled and {"red", "green", "blue"} <= set(fixture.mode.attributes())
        ])
        for strip in apc40_control.strips
        if any(fixture.patch.enabled and {"red", "green", "blue"} <= set(fixture.mode.attributes())
               for fixture in apc40_control.fixtures(strip.track))
    }, sequences)
    scenes.snapshot_listeners.append(apc40_control.scene_applied)
    midi_channel = settings.midi.channel if settings.midi is not None else 15
    midi_input: Optional[MidiInput] = None
    apc40_input: Optional[MidiInput] = None
    apc40_feedback: Optional[Apc40Feedback] = None
    if settings.midi is not None and settings.midi.enabled:
        ports = midi_ports or MidoPorts()
        midi_input = MidiInput(ports, midi_control, settings.midi.device)

        def apc40_connected() -> None:
            # Ignore the fader report the introduction triggers, then switch the mode.
            assert apc40_input is not None
            apc40_control.connected()
            if apc40_input.send_sysex(introduction()):
                logger.info("APC40 set to Alternate Ableton Live mode.")
            if apc40_feedback is not None:
                apc40_feedback.invalidate()

        apc40_input = MidiInput(
            ports, apc40_control, settings.midi.apc40_device, on_connect=apc40_connected
        )
        apc40_feedback = Apc40Feedback(
            apc40_control.leds, apc40_input.send_note, apc40_input.send_cc,
            apc40_input.connection_id,
        )

    def midi_status() -> dict:
        assert midi_input is not None and apc40_input is not None
        apc40 = apc40_input.status()
        return {
            **midi_input.status(),
            "apc40_alternate_mode": [apc40["connected_device"]] if apc40["connected"] else [],
            "apc40_error": apc40["last_error"],
        }

    def controller_status() -> dict:
        if midi_input is None or apc40_input is None:
            return {"connected": False, "device": None, "other_connected": False}
        apc40 = apc40_input.status()
        return {
            "connected": apc40["connected"],
            "device": apc40["connected_device"],
            "other_connected": midi_input.status()["connected"],
        }
    led_feedback = (
        LedFeedback(
            midi_store, scenes, service, midi_control.held,
            midi_input.send_note, midi_input.connection_id, preview.active,
        )
        if midi_input is not None
        else None
    )

    def armed() -> bool:
        return gate is not None and gate.armed

    def mode_fields() -> dict:
        return {"output_mode": settings.output_mode, "physical_output_enabled": armed()}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if gate is not None and settings.physical_output_enabled:
            gate.arm()
            service.republish_all()
        logger.info(
            "LichtWerk started (output mode %s, real output %s, startup blackout active).",
            settings.output_mode, "enabled" if armed() else "disabled",
        )
        package_logger = logging.getLogger("lichtwerk")
        package_logger.addHandler(recent_errors)
        runner.start()
        if midi_input is not None:
            midi_input.start()
        if apc40_input is not None:
            apc40_input.start()
        if led_feedback is not None:
            led_feedback.start()
        if apc40_feedback is not None:
            apc40_feedback.start()
        yield
        if apc40_feedback is not None:
            apc40_feedback.stop()
        if led_feedback is not None:
            led_feedback.stop()
        if apc40_input is not None:
            apc40_input.stop()
        if midi_input is not None:
            midi_input.stop()
        runner.stop()
        package_logger.removeHandler(recent_errors)
        if gate is not None:
            gate.disarm()
        logger.info("LichtWerk stopped.")

    app = FastAPI(
        title="LichtWerk",
        version="0.1.0",
        description="Local lighting control. Simulation by default; configured hardware starts in blackout.",
        lifespan=lifespan,
    )
    app.state.lighting_service = service
    app.state.dmx_output = output
    app.state.fixture_controller = controller
    app.state.scene_service = scenes
    app.state.sequence_service = sequences
    app.include_router(build_router(controller, scenes))
    app.include_router(build_show_router(scenes, sequences, apc40_control.quantizer, modifiers))
    app.include_router(ola_runtime.build_router())
    app.include_router(build_rig_router(positions, rig_store, controller))
    app.include_router(build_apc40_router(apc40_control, service, controller_status))
    app.include_router(build_fx_router(apc40_control, fx_service))
    app.include_router(build_preview_router(preview))
    app.include_router(build_map_router(controller, service, rig_store, map_store))
    app.include_router(
        build_controller_router(midi_channel, midi_store, midi_control, controller, scenes, service)
    )
    app.include_router(
        build_midi_router(midi_store, midi_control, midi_status if midi_input else None)
    )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_, error: Exception):
        logger.error(
            "Unexpected API failure: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Unexpected application error. See the local LichtWerk log for details."
            },
        )

    @app.get("/api/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        return StatusResponse(
            **mode_fields(),
            blackout_active=service.blackout_active(),
            universes=list(service.universes()),
            real_output_available=settings.physical_output_enabled and gate is not None,
            real_output_universes=list(gate.allowed_universes) if gate else [],
            output_error=gate.last_error if gate else None,
            master=controller.master(),
            active_scene=scenes.active_scene(),
            transition_active=controller.transition_active(),
            preview_active=preview.active(),
            setup_overrides=[
                head_id for head_id in rig_store.head_ids()
                if controller.guard is not None and controller.guard.setup_override(head_id)
            ],
        )

    @app.get("/api/diagnostics", response_model=DiagnosticsResponse)
    def diagnostics() -> DiagnosticsResponse:
        return build_diagnostics(
            patch,
            rig_store,
            settings.output_mode,
            list(service.universes()),
            list(gate.allowed_universes) if armed() and gate else [],
            recent_errors,
        )

    def fixture_info(fixture_id: str) -> FixtureInfo:
        fixture = patch.get(fixture_id)
        return FixtureInfo(
            id=fixture.patch.id,
            name=fixture.patch.name,
            manufacturer=fixture.definition.manufacturer,
            model=fixture.definition.model,
            fixture_type=fixture.definition.fixture_type,
            mode=fixture.mode.name,
            universe=fixture.patch.universe,
            start_address=fixture.patch.start_address,
            channel_count=fixture.mode.channel_count,
            enabled=fixture.patch.enabled,
            groups=list(fixture.patch.groups),
            attributes=fixture.mode.attributes(),
            state=controller.state(fixture_id),
        )

    @app.get("/api/fixture-definitions", response_model=list[FixtureDefinition])
    def list_fixture_definitions() -> list[FixtureDefinition]:
        return [library[key] for key in sorted(library)]

    @app.get("/api/fixtures", response_model=list[FixtureInfo])
    def list_fixtures() -> list[FixtureInfo]:
        return [fixture_info(fixture.patch.id) for fixture in controller.fixtures()]

    @app.put("/api/fixtures/{fixture_id}/name", response_model=FixtureInfo)
    def rename_fixture(fixture_id: str, command: RenameRequest) -> FixtureInfo:
        try:
            patch.rename_fixture(fixture_id, command.name)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=error.args[0]) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return fixture_info(fixture_id)

    def group_info(group_id: str) -> GroupInfo:
        return GroupInfo(
            id=group_id, name=patch.group_name(group_id),
            fixtures=[fixture.patch.id for fixture in patch.group(group_id)],
        )

    @app.put("/api/groups/{group_id}/name", response_model=GroupInfo)
    def rename_group(group_id: str, command: RenameRequest) -> GroupInfo:
        try:
            patch.rename_group(group_id, command.name)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=error.args[0]) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return group_info(group_id)

    @app.post("/api/fixtures/{fixture_id}/state", response_model=FixtureStateResponse)
    def set_fixture_state(fixture_id: str, command: FixtureStateRequest) -> FixtureStateResponse:
        try:
            controller.set_values(fixture_id, command.values)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=error.args[0]) from error
        except FixtureDisabledError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return FixtureStateResponse(
            **mode_fields(),
            blackout_active=service.blackout_active(),
            fixture=fixture_info(fixture_id),
        )

    @app.get("/api/universes/{universe}", response_model=UniverseResponse)
    def inspect_universe(
        universe: Annotated[int, Path(ge=0, le=7)],
    ) -> UniverseResponse:
        try:
            channels = service.read_output(universe)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return UniverseResponse(
            universe=universe,
            slot_count=DMX_SLOT_COUNT,
            channels=list(channels),
            blackout_active=service.blackout_active(),
        )

    @app.post("/api/commands/set-channel", response_model=CommandResponse)
    def set_channel(command: SetChannelRequest) -> CommandResponse:
        try:
            service.set_channel(command.universe, command.channel, command.value)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return CommandResponse(
            **mode_fields(),
            universe=command.universe,
            channel=command.channel,
            value=command.value,
            blackout_active=service.blackout_active(),
        )

    @app.post("/api/output/blackout", response_model=BlackoutResponse)
    def set_blackout(command: BlackoutRequest) -> BlackoutResponse:
        service.set_blackout(command.active)
        logger.info("Blackout %s.", "activated" if command.active else "released")
        return BlackoutResponse(
            **mode_fields(),
            blackout_active=service.blackout_active(),
        )

    @app.post("/api/output/retry", response_model=BlackoutResponse)
    def retry_output() -> BlackoutResponse:
        if gate is None or not settings.physical_output_enabled:
            raise HTTPException(
                status_code=409,
                detail="Real output is not enabled in config/app.json; staying in simulation.",
            )
        gate.arm()
        service.republish_all()
        return BlackoutResponse(
            **mode_fields(), blackout_active=service.blackout_active(),
        )

    @app.post("/api/output/arm", response_model=BlackoutResponse, deprecated=True)
    def arm_output(command: ArmRequest) -> BlackoutResponse:
        if gate is None or not settings.physical_output_enabled:
            raise HTTPException(
                status_code=409,
                detail="Real output is not enabled in config/app.json; staying in simulation.",
            )
        if command.armed:
            if sorted(command.confirm_universes) != list(gate.allowed_universes):
                raise HTTPException(
                    status_code=422,
                    detail="confirm_universes must list exactly the universes real output "
                    f"will drive: {list(gate.allowed_universes)}.",
                )
            gate.arm()
            logger.warning("Real DMX output ARMED for universes %s.", list(gate.allowed_universes))
            service.republish_all()
        else:
            gate.disarm()
            logger.warning("Real DMX output disarmed.")
        return BlackoutResponse(
            **mode_fields(),
            blackout_active=service.blackout_active(),
        )

    return app
