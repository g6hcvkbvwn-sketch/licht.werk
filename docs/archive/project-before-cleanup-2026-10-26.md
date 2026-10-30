# LichtWerk Club Lighting Control System

## Document purpose and status

This document is the product and architecture baseline. It describes intended behavior and constraints; it is not evidence that hardware, fixture profiles, or network settings have been verified.

The Milestone 0–2 scope now has a localhost-only API, an in-memory DMX simulator, and an OLA HTTP output adapter behind the generic output boundary. Real output is off by default, restricted to configured universes, and requires explicit runtime arming. OLA 0.10.9 is installed and verified on this Mac (olad runs, Art-Net bound to 2.0.0.1, OLA universe 5 patched to Art-Net universe 5), and a supervised sweep confirmed the LED bar on Botex output/universe 5. Milestone 3 is implemented: JSON fixture definitions in `config/fixtures/`, a validated patch in `config/patch.json` (address collisions, overflow, unknown definitions/modes, unconfigured universes), logical 8/16-bit conversion with a virtual dimmer for fixtures without an intensity channel, a fixture state API and generated UI controls; its supervised hardware check (controlling the LED bar through the fixture controls) is pending. Patch editing through the UI is deferred to Milestone 6. Milestone 5 is implemented and tested in simulation: fixture groups from the patch, partial scenes stored atomically in `config/scenes.json` (with `.bak` recovery), central monotonic fades, master intensity and the precedence rules in "State resolution and transitions"; scene recall on real hardware is still to be checked. Milestone 4 is implemented and tested in simulation (moving-head fixture definition, room/targets/zones in `config/room.json`, head setup in `config/heads.json`, aim and calibration fit, limits, rest position, native strobe); it still needs the measurements and the supervised calibration at the club (see "Milestone 4 implementation decisions"). The broader Milestone 0 exit gate remains pending a resolved dependency lock. The inspected target Mac reports macOS 26.6.2 and Python 3.9.6; use Python 3.13 for development because the installed 3.9 runtime is no longer supported by the project. Treat hardware details not explicitly confirmed below as unresolved.

## Contents

- [Project purpose](#project-purpose)
- [Guiding principles](#guiding-principles)
- [Environment and architecture](#existing-and-planned-environment)
- [Technology and repository structure](#technology-stack)
- [Domain and product requirements](#core-domain-model)
- [Safety, persistence, testing, and operations](#safety-requirements)
- [Open decisions and information gaps](#open-decisions-and-information-gaps)
- [MVP acceptance criteria](#mvp-acceptance-criteria)
- [Milestone plan and exit gates](#implementation-milestones)
- [Out of scope and future information](#explicit-out-of-scope-for-initial-development)

## Project purpose

Build a custom, local-first lighting-control application for the fixed schall.werk club installation. The application replaces Wolfmix for conventional DMX fixtures while Resolume remains responsible for the existing pixel bars and television/video content.

The core design goal is precise, deterministic control rather than reproducing a generic lighting console. In particular, the system must make it easy to control exactly where each moving head points, how fixtures are grouped, how scenes look, and how physical MIDI controls affect the show.

The application will be developed primarily with GitHub Copilot. Architecture, code structure, automated tests, diagnostics, and documentation therefore need to be explicit, modular, and easy for an AI coding agent to understand and modify safely.

## Guiding principles

1. Local-first. The core lighting system must work on the local computer and local lighting network without cloud services.
2. Deterministic. A saved scene or position should reproduce the same intended fixture state each time.
3. Hardware-independent domain logic. Scene, fixture, position, effect, MIDI, and UI logic must not depend directly on a specific Art-Net gateway.
4. OLA as the lighting I/O abstraction. The application sends universe data to Open Lighting Architecture (OLA); OLA handles the configured lighting-network output.
5. Resolume remains separate. Do not reimplement pixel/video playback. Integrate with Resolume later through OSC where useful.
6. MIDI is a control surface, not the source of truth. Xone:K2 mappings call application commands. Lighting state belongs to the application.
7. Fail safe. On application errors, invalid fixture data, or lost output connectivity, behavior must be predictable. No uncontrolled movement or unexpected full-intensity output.
8. Build incrementally. Get one fixture and one universe working end-to-end before adding scenes, MIDI, effects, and advanced features.
9. Configuration over hard-coding. Fixture definitions, patch, groups, positions, scenes, and controller mappings must be stored in editable configuration/project data.
10. No unnecessary console complexity. Do not build a GrandMA, Wolfmix, or QLC+ clone.

## Existing and planned environment

Existing components:
- Botex NETcon 8-3 network-to-DMX interface with unused universes available.
- Resolume for pixel bars and television/video output. This shall remain in place.
- Conventional DMX fixtures including spots, wash lights, and moving heads.
- Allen & Heath Xone:K2 controller (manual: `docs/manuals/manual_xone_K2.pdf`) intended for later MIDI input and LED/status feedback where feasible. It replaces the originally planned AKAI APC40.

Target application:
- Python backend and lighting engine.
- OLA daemon as intermediate DMX/network output layer.
- Art-Net from OLA to the Botex NETcon 8-3 after manual network/output configuration.
- Local web-based user interface.
- MIDI input/output via the Xone:K2.
- OSC integration with Resolume as a later milestone.

IMPORTANT: Do not assume exact Botex IP addresses, Art-Net universe numbers, fixture addresses, fixture channel modes, Xone:K2 layer/MIDI-channel settings, Resolume OSC addresses, or room coordinates. These must be configurable and confirmed against the actual hardware/manual before use.

## System architecture

Target signal path:

Web UI -> Application API / WebSocket -> Lighting Engine -> OLA Adapter -> OLA daemon -> Art-Net -> Botex NETcon 8-3 -> DMX -> Fixtures

Xone:K2 -> MIDI Adapter -> Command Bus / Lighting Engine

Lighting Engine <-> OSC Adapter <-> Resolume

The application owns the logical lighting state. OLA is the transport/output abstraction, not the scene engine. Resolume owns video/pixel content.

## Technology stack

### Required runtime

- Python 3, use a currently supported stable version compatible with all selected dependencies.
- Python virtual environment.
- OLA / olad installed as a system-level dependency appropriate to the host OS.

### Python packages

Candidate packages (not a blanket install list; add each only when its milestone needs it and verify compatibility):
- FastAPI: local HTTP API and WebSocket endpoints.
- Uvicorn: ASGI development/runtime server.
- Pydantic: validation of configuration, API payloads, fixtures, scenes, and mappings.
- PyYAML: YAML configuration/project files if YAML is used.
- mido and python-rtmidi: MIDI message abstraction and local hardware backend; defer to the MIDI milestone.
- python-osc: OSC communication; defer to the optional Resolume milestone.
- pytest: automated tests.
- pytest-asyncio: asynchronous tests when required.
- httpx: API/integration testing where useful.

Use Python's standard logging module unless there is a concrete requirement for an additional logging package.

### OLA integration

Use Open Lighting Architecture as the DMX output layer. OLA provides a daemon and client APIs and supports DMX-over-IP protocols including Art-Net. Implement an OLA adapter behind a generic DMX output interface so that application/domain code has no direct dependency on OLA-specific calls.

The project must include setup documentation for installing and configuring OLA, but must not silently change system or network configuration without explicit user action.

Do not add pyArtNet to the primary implementation unless a later architecture decision explicitly introduces it as an optional fallback output adapter. Avoid maintaining two DMX output paths in the MVP.

### Frontend

Use a lightweight local web frontend. Preferred approach:
- React
- TypeScript
- Vite

The UI is a control and configuration surface. Lighting timing and state must not depend on the browser being open.

### Dependency management

Maintain explicit dependency files suitable for reproducible setup, such as pyproject.toml plus a lock strategy supported by the chosen environment, and package.json/package-lock.json for the frontend.

Copilot may propose exact versions after checking current package compatibility. Do not invent or blindly pin versions without verifying compatibility.

### Suggested repository structure

project-root/
  project.md
  README.md
  .gitignore
  pyproject.toml
  backend/
    app/
      main.py
      api/
      core/
      domain/
        fixtures/
        patch/
        scenes/
        positions/
        effects/
        commands/
      adapters/
        ola/
        midi/
        osc/
      services/
      persistence/
      diagnostics/
    tests/
  frontend/
    src/
      components/
      pages/
      services/
      state/
  config/
    app.yaml
    fixtures/
    patch.yaml
    groups.yaml
    positions.yaml
    scenes.yaml
    midi-mappings.yaml
    osc.yaml
  saved_projects/
  docs/

Copilot may refine the structure if there is a clear architectural reason, but must preserve separation between domain logic, hardware/protocol adapters, persistence, API, and UI.

Implemented deviation: configuration files use JSON (`config/app.json`, `config/fixtures/*.json`, `config/patch.json`) via the standard library, so PyYAML is not needed.

## Core domain model

### Fixture definition

A fixture definition describes a fixture model and a specific DMX mode.

Required information should support:
- manufacturer
- model
- fixture type
- DMX mode name
- channel count
- channel definitions
- channel function/type
- value range
- default value
- 8-bit or 16-bit/coarse+fine relationship where applicable
- pan/tilt properties where applicable
- color system
- dimmer
- shutter/strobe
- gobos or other supported attributes

Fixture definitions must be reusable between multiple patched instances of the same fixture model.

MVP does not require a universal fixture library. Initially create definitions only for actual schall.werk fixtures.

### Fixture instance / patch

A patched fixture references a fixture definition and includes:
- unique fixture ID
- user-visible name
- fixture definition
- DMX universe
- DMX start address
- enabled/disabled state
- optional group memberships
- optional physical mounting coordinates
- moving-head orientation/calibration data where applicable

Validation must prevent overlapping addresses within one universe unless explicitly supported for a justified reason.

The application must validate that a fixture fits inside the 512-channel DMX universe.

### Fixture groups

Allow logical groups such as:
- all moving heads
- left moving heads
- right moving heads
- washes
- spots
- dancefloor fixtures

A fixture may belong to multiple groups.

### Lighting state

Maintain an internal desired state independent of DMX byte representation.

Examples of logical attributes:
- intensity 0.0 to 1.0
- RGB/RGBW color as normalized values
- pan/tilt as logical values or target positions
- shutter state
- strobe rate
- gobo selection
- fixture enabled state

A translation layer converts logical fixture state into final DMX channel values according to the fixture definition.

## DMX and OLA output requirements

1. Support multiple DMX universes from the architecture from the beginning, even if only one is initially tested.
2. Maintain one 512-slot output buffer per active universe.
3. Build the buffer from the application's desired fixture state.
4. Send/stream universe data through the OLA adapter.
5. Keep transport concerns outside fixture/scene logic.
6. Provide connection/output status visible to diagnostics and UI where technically available.
7. Provide a configurable output enable/disable switch for development.
8. Provide a global blackout command.
9. Provide a master intensity multiplier without destroying underlying scene values.
10. Avoid excessive uncontrolled output loops. Output timing/rate must be explicit and configurable.
11. Do not assume OLA universe IDs are identical to external Art-Net universe numbering. Treat mapping/configuration as an explicit infrastructure concern.
12. Include an output simulation/mock adapter for tests so automated tests never require real lighting hardware.

## Moving-head position system

This is a defining feature of the project.

The user must be able to work with named targets instead of only raw pan/tilt DMX values.

Examples:
- DJ
- center dancefloor
- bar
- entrance
- wall
- ceiling

### Position target

A target may contain 3D coordinates:
- x
- y
- z

Coordinate units and origin must be defined in project settings, for example meters relative to a documented room origin.

### Moving-head mounting data

Each moving head requires configurable:
- x, y, z mounting position
- orientation/reference direction
- pan range
- tilt range
- pan inversion
- tilt inversion
- pan offset/calibration
- tilt offset/calibration
- optional movement limits

### Position calculation

Implement a Position Engine that can calculate a desired pan/tilt direction from moving-head location and target coordinates and then map that result to fixture-specific output values.

Because real moving heads can have different mechanical ranges, orientations, zero points, and inversion behavior, calculated positions must support per-fixture calibration.

Do not assume theoretical geometry is sufficient. Provide manual fine adjustment and calibration per fixture/target.

### Milestone 4 implementation decisions

- **Coordinate convention (proposed, confirm at the club):** meters; origin at the floor corner of the dance floor that the owner picks; x to the right, y into the room, z up. Room size, target coordinates and head positions are `null` in the files until measured; nothing is guessed. Targets are all-or-none (x, y, z together).
- **Files:** `config/room.json` (room size, named targets, zones) and `config/heads.json` (per moving head: `orientation` ceiling/floor, `position`, `calibration`, `limits`, `rest`). Both are validated against the patch at startup and written atomically with a `.bak`. Missing files mean "nothing configured yet".
- **Model:** pan degrees = logical pan × the fixture's pan range, tilt likewise (from the fixture definition's `motion`). Beam azimuth = `pan_zero_azimuth_deg + pan_sign × pan degrees`; beam angle from the mounting axis = `tilt_sign × (tilt degrees − tilt_axis_deg)`. Aiming picks, among the reachable pan/tilt solutions (normal or tilted past vertical with pan turned 180°), the one closest to the current pan.
- **Calibration:** point the head by hand at 3 or more targets with known coordinates (ideally the four dance-floor corners), record pan/tilt for each, and fit. The fit reports the error per observation in degrees and is only saved by explicit user action; it is "acceptable" when the worst error is under 3°. Tilt sign and a 180° pan shift are physically indistinguishable, so the fit does not claim to identify them separately.
- **Zones:** boxes in room coordinates, each switchable between `include` (aim only inside) and `exclude` (never aim inside). Applied to every aim command.
- **Limits and rest:** per head logical pan/tilt windows clamp the output (stored state is untouched). A rest position (pan/tilt) is used at startup and by "Rest"; it is unset until a safe value is found at the club, and heads start at pan/tilt 0 until then.
- **Modes and addresses (decided by the owner):** heads use the 37ch mode (16-bit pan/tilt; the six ring segments all get the same colour; macro/show/sound channels held at 0), the LED bar uses the 6Ch mode (master dimmer, native strobe 11-250 = 0-30 Hz per its manual, RGB, sound control held at 0). Universe 5 patch: LED bar 1-6, head 1 at 7-43, head 2 at 44-80, two Cameo Q-Spot 15 RGBW BK (DJ booth, group `dj-spots`) in 9CH mode at 81-89 and 90-98 (dimmer, dimmer fine held at 0, strobe 128-250 = <1-20 Hz, RGBW; colour macro and colour temperature held at 0; per its manual p. 37). These must be set on the fixtures at the club. The 16ch and 3Ch3 definitions remain available.
- **Open, requires the club:** room size and target coordinates, head mounting positions, rest pan/tilt, the real behavior of the speed channel (CH3, default fast), the ring macro-program channel (CH16 in 37ch, held at 0, assumed to mean no function).
- **Not in this milestone:** scenes referencing named targets (scenes can store pan/tilt directly), presets, a UI for editing room/head data (edit the JSON files until Milestone 6).

### Raw mode

For setup and troubleshooting, allow authorized local control of raw logical pan/tilt and DMX channel values. Raw mode must be clearly distinguished from normal show operation.

## Scene system

A scene is a named snapshot/definition of desired lighting attributes for fixtures and/or groups.

A scene may include:
- fixture/group intensities
- colors
- named target positions
- direct pan/tilt where intentionally used
- shutter/strobe state
- gobos if supported
- transition/fade duration

Examples:
- Warmup
- Peak
- Afterhour
- DJ Focus
- Floor Wash
- Blackout

Scenes must be editable, saveable, recallable, duplicateable, and deletable.

Changing from one scene to another should support fades for attributes that can be interpolated safely.

Do not overwrite stored source scenes when applying master intensity, blackout, or temporary live overrides.

### Presets

Support reusable presets independent of full scenes.

Initial preset categories:
- color presets
- position presets
- intensity presets
- movement/effect presets later

Scenes should reference presets where practical without creating fragile hidden dependencies.

### Transition and fade engine

Implement time-based interpolation centrally rather than separately in each UI or MIDI handler.

Requirements:
- configurable fade duration
- non-blocking execution
- new commands may replace/intervene in a running transition according to documented rules
- pan/tilt movement transition support
- intensity and color interpolation
- immediate commands where required, for example blackout

Use monotonic time for transition timing.

### State resolution and transitions

Implemented in Milestone 5, following common lighting-console practice (latest-takes-precedence and tracking, as in grandMA, Chamsys or QLC+). Adjust if live use shows a need.

1. **Startup:** all logical values are 0, master is 100 %, and no scene is recalled. A scene is never recalled automatically.
2. **Latest takes precedence (LTP), per attribute:** scene recalls, group commands and fixture commands all write the same live state. Attributes a command does not mention keep their current value, so scenes are partial (tracking).
3. **Inside one scene:** group entries apply in their listed order, then fixture entries (fixture beats group, later group beats earlier group). A group entry applies only to members that have the attribute. Disabled fixtures are skipped.
4. **Fades:** linear and timed with a monotonic clock. Each fade starts from the attribute's current, possibly mid-fade, value. A new scene recall replaces the fades of only the attributes it touches; other fades keep running. Direct fixture/group commands are immediate and cancel the running fades of the attributes they set. A recall may override the stored fade time once.
5. **Master:** a 0–100 % multiplier on intensity, applied at output time and immediately. For fixtures without a dimmer channel it scales the colors (virtual dimmer). It is never stored in live state or scenes.
6. **Blackout:** immediate, at output level. Fades continue underneath, and releasing blackout shows the current state.
7. **Stored scenes** change only through explicit scene editing (create, capture, update, duplicate, delete). Recall, master, blackout and direct commands never modify them.
8. **Capture** stores the current live values (mid-fade values if a fade runs) of all enabled fixtures, without master or blackout.
9. **Persistence:** `config/scenes.json` is validated at startup. A missing file means no scenes. An invalid file, or references to fixtures/groups/attributes not in the patch, stops startup with a message naming the file and problem; recovery is fixing the file or patch or restoring `scenes.json.bak`. Every save is atomic (temporary file and rename) and keeps the previous version as `.bak`. A failed save is reported and changes nothing.

Pan/tilt fades use the same engine once Milestone 4 adds position attributes. Presets are deferred.

## Strobe and other timing-critical effects

Strobe is a fixture-native attribute (`strobe`, `ring_strobe`), not an effect generated by the software. DMX refreshes at roughly 44 Hz at most and network/software jitter would make software-driven flashes uneven, so fast strobing uses the strobe channel built into the fixture.

- Logical 0 = off (DMX 0); above 0 maps onto the fixture's documented strobe range (`active_min`..`active_max` in the definition, so the "no function" range is never entered).
- Strobe is not scaled by master intensity; blackout zeroes it; it is off at startup; `max_strobe` in `config/app.json` (0.0-1.0, default 1.0) caps it.
- The manual gives no strobe rates in Hz. Rates must be measured at the club before strobe is tied to tempo; until then beat-sync is approximate.
- The LED bar in 6Ch mode has a native strobe (DMX 11-250 = 0-30 Hz per its manual; 0-5 = open, 6-10 = closed, so logical 0 sends 0). In 3Ch3 mode it has none; a software flash is acceptable only for single hits, not as a frequency source.
- Strobe from the Xone:K2 is a Milestone 7 mapping to these attributes.

## Effects engine

Effects are not required for the first hardware milestone but architecture must leave room for them.

Potential later effects:
- dimmer pulse
- chase across fixture group
- color chase
- pan sine
- tilt sine
- circle movement
- random movement within defined safe limits

Effects should modulate a base state rather than destroying saved scene values.

Do not implement sophisticated effect generation before stable direct fixture and scene control exists.

## MIDI / Xone:K2 requirements

MIDI integration is a planned core feature.

### MIDI discovery

The application must:
- enumerate available MIDI input/output ports
- allow selection of devices
- show connection state
- reconnect gracefully where practical
- provide a MIDI monitor for development that displays received message type and relevant values

### MIDI mappings

Mappings must be configurable and persistent, not hard-coded into business logic.

Supported mapping concepts:
- MIDI Note -> command
- MIDI CC -> parameter
- button -> scene trigger
- button -> position preset
- button -> blackout
- fader -> master intensity or group intensity
- encoder -> parameter adjustment

Where the connected Xone:K2 supports suitable output messages, the architecture should allow later LED/status feedback. Device-specific MIDI output behavior must be implemented from the verified Xone:K2 manual or messages observed in the MIDI monitor, not guessed.

Xone:K2 facts from its manual (to be verified with the MIDI monitor before use): it is USB class-compliant (no driver on macOS); the default MIDI channel is 15; faders, pots and encoders send CC, buttons send notes; an optional "Latching Layers" mode provides three layers (red, amber, green) with different note/CC numbers per layer; the buttons have three-colour LEDs. The layer mode and MIDI channel used at the club are configuration items, and mapping stays in a configuration file with MIDI learn rather than hard-coded numbers.

### Learn mode

Later milestone: a MIDI learn workflow where the user selects a software action, moves/presses a physical control, and saves the mapping.

## OSC and Resolume integration

Resolume remains responsible for pixel bars and video/TV content.

The lighting application must not interfere with Resolume's existing output universes.

Add an OSC adapter as a later milestone.

Possible use cases:
- send commands from the lighting system to Resolume
- receive state/control messages from Resolume
- trigger a lighting scene based on a defined OSC message
- trigger a Resolume action from a MIDI control routed through the application

All OSC addresses and host/port settings must be configurable. Use verified Resolume OSC documentation when implementing concrete mappings.

Do not make the lighting engine dependent on Resolume being available.

## Local web UI requirements

The UI should be optimized for operation in a club environment: high contrast, low clutter, large hit areas, immediate status visibility, and minimal navigation during live operation.

### Dashboard

Display:
- application status
- OLA/output status
- active universes
- MIDI connection status
- OSC status when enabled
- master intensity
- blackout state
- current/last active scene
- warnings/errors

### Fixtures page

Functions:
- list fixtures
- add/edit/delete fixture instances
- select fixture definition/mode
- set universe/address
- identify/test fixture
- direct parameter control for setup
- group assignment

### Moving-head setup page

Functions:
- select moving head
- raw pan/tilt control
- define mounting coordinates
- configure inversion/offsets/ranges
- select target position
- fine-tune target calibration
- save calibration

### Positions page

Functions:
- create/delete/rename targets
- edit x/y/z coordinates
- command selected fixture/group to a target
- store per-fixture correction where required

### Scenes page

Functions:
- create scene from current state
- recall scene
- edit scene
- duplicate scene
- delete scene
- configure fade time

### MIDI page

Functions:
- choose MIDI device
- view MIDI monitor
- view/edit mappings
- later: MIDI learn

### Diagnostics page

Display:
- output enabled/disabled
- active OLA universe mapping
- last output/update status where available
- fixture patch conflicts
- configuration validation warnings
- recent application errors

## API requirements

Expose a local API for frontend control and future integration.

Suggested endpoint groups:
- /api/status
- /api/fixtures
- /api/groups
- /api/positions
- /api/scenes
- /api/output
- /api/midi
- /api/osc
- /api/config

Use WebSocket or another appropriate push mechanism for live status/state changes so the UI does not need aggressive polling.

API models must use validation and return useful error messages.

## Persistence and project files

The system should be able to preserve and reload a lighting project locally.

MVP may use YAML/JSON files for human-readable configuration.

Persist:
- fixture definitions
- patch
- groups
- room/target positions
- fixture calibration
- scenes/presets
- MIDI mappings
- OSC settings
- application settings

Writes must be atomic where practical to reduce corruption on unexpected shutdown.

Keep runtime logs, caches, virtual environments, generated frontend output, and machine-specific state out of Git.

Do not commit credentials, tokens, private keys, or secrets.

## Safety requirements

Lighting control affects physical devices, so safety is part of the MVP.

1. Blackout must be available independent of the currently displayed UI page.
2. Provide an output-disable mode for development.
3. Validate DMX addresses and channel ranges before output.
4. Clamp all generated DMX values to valid ranges.
5. Validate pan/tilt limits before sending movement commands.
6. Support configurable safe movement limits for moving heads.
7. On malformed configuration, refuse unsafe output and clearly report the problem.
8. Application restart must have explicitly documented startup behavior. Do not unexpectedly restore high-intensity output or movement without a deliberate configuration decision.
9. Loss of the UI must not crash the lighting engine.
10. MIDI floods or repeated commands must not block the output loop.
11. Never let an exception in MIDI, OSC, persistence, or WebSocket handling terminate the DMX output service silently.
12. Physical emergency/safety procedures remain independent of this software. Software blackout is not a substitute for electrical safety controls.

## Logging and diagnostics

Log at minimum:
- application startup/shutdown
- configuration load/validation failures
- OLA connection/output errors
- MIDI connect/disconnect/errors
- OSC errors
- scene changes
- blackout changes
- unexpected exceptions

Do not emit one log line per DMX frame in normal operation.

Provide verbose/debug logging as a selectable development mode.

## Testing requirements

### Unit tests

Cover at minimum:
- DMX address validation
- fixture logical-value to DMX conversion
- 8/16-bit conversion
- scene state resolution
- master dimmer behavior
- blackout behavior
- position calculations
- fixture calibration/inversion logic
- transition interpolation
- MIDI mapping resolution
- configuration validation

### Integration tests

Use mock adapters for OLA, MIDI, and OSC wherever possible.

Tests must be runnable without physical fixtures, Botex hardware, Resolume, or the Xone:K2.

Provide an optional hardware integration/test mode separately.

### Regression principle

Whenever a fixture mapping, position algorithm, scene resolution rule, or MIDI mapping bug is fixed, add a regression test where feasible.

## Development / simulation mode

Provide a development mode that can run without OLA or physical lighting hardware.

Minimum simulation features:
- mock DMX output adapter
- show current 512-channel buffer per universe in diagnostics or a developer endpoint
- simulate fixture commands
- allow frontend development with synthetic device status clearly labeled as simulated

Never silently treat simulated output as real output.

## Configuration validation

At startup and after configuration changes validate:
- unknown fixture definition references
- duplicate fixture IDs
- invalid universe/address
- channel overflow beyond 512
- address overlaps
- malformed scene references
- unknown position/preset references
- invalid MIDI mapping definitions
- invalid numeric ranges
- impossible/missing moving-head calibration fields where required

Display actionable error messages rather than raw stack traces in the normal UI.

## Non-functional requirements

### Reliability

The system is intended for live club use. Long-running event operation must not depend on browser refreshes or manual background restarts.

### Performance

UI actions should feel immediate. MIDI actions should be translated to application commands without perceptible intentional delay. DMX output scheduling must not be tied to frontend rendering.

Do not invent a numeric latency or frame-rate requirement until measured against actual hardware.

### Maintainability

- typed Python where practical
- small modules with single responsibilities
- no giant main.py
- docstrings for non-obvious domain behavior
- clear naming
- minimal global mutable state
- dependency inversion at hardware boundaries
- tests for important domain rules

## Platform

The primary development/operation environment is macOS unless later specified otherwise. Copilot must verify OLA installation and Python binding compatibility for the actual macOS/Python environment before generating system-specific installation commands.

Do not assume Linux-only package-manager commands are valid on the target machine.

## Security / deployment assumptions

MVP is a trusted local-network/local-computer tool, not a public internet service.

- Bind services to localhost by default unless LAN access is explicitly enabled.
- If the UI/API is exposed to the LAN later, define access-control requirements before doing so.
- Do not implement cloud authentication for the MVP.
- Do not open router/firewall ports automatically.

## Open decisions and information gaps

These questions affect implementation or safe acceptance. Do not silently choose behavior where the answer changes live-show behavior, hardware compatibility, or project scope. The owner should answer or provide the site-specific facts below before the milestone shown; routine technical choices (such as schema versioning or test organization) remain implementation work and should follow the requirements elsewhere in this document. Record approved answers in this document or the relevant configuration/documentation.

| Priority | Owner decision or verified information needed | Why it matters | Resolve by |
|---|---|---|---|
| Discovery | Which Mac/macOS version and Python version will be used for development and operation? | OLA availability, Python bindings, MIDI dependencies, and installation instructions may differ by environment. Copilot can inspect the current development machine, but the owner should confirm whether it is also the intended show computer. | Milestone 0 |
> Use this Mac version as target (Tahoe) as this will be used to run the program. Recommend the Python version that best fits the project requirements and Mac environment. For the moment, I plan this tool to be Mac-compatible only.
| Blocking | What exact fixtures, verified DMX modes/channel tables, patch addresses, and universe assignments are in scope for the first live test? | No fixture profile or channel mapping can safely be invented; this defines the first real-fixture acceptance test. | Before Milestone 3; a channel-only OLA test may precede it |
> Start with a Cameo Thunder Wash 100 RGB LED bar on Botex universe/output label 5 (the sixth output; corrected after a supervised hardware sweep), starting address 1, in 3Ch3 mode. The user confirms that 3Ch3 is RGB: DMX address 1 = red, address 2 = green, and address 3 = blue.
| Technical verification | What OLA version/client integration and network-interface binding work on the target macOS installation? | OLA packaging and interface binding must be verified on the actual Mac; this is an implementation/environment check, not a missing Botex universe decision. | Milestone 0 / before Milestone 2 hardware test |
**Correction (verified by a supervised sweep on 2026-10-19):** the Botex labels its outputs/universes 0 through 7, and the LED bar responds to Art-Net universe 5. The app therefore uses universe numbers 0–7, identical to the Botex labels, the Art-Net universe and the OLA universe. The earlier 1–8 statements below are superseded. The user confirms that physical Botex DMX output labelled 5 is configured for Art-Net universe 5, and that the device provides outputs/universes 0 through 7. Resolume also sends Art-Net to pixel fixtures, but the user confirms Resolume uses different universes/outputs. OLA maps its universe IDs to Art-Net Port-Addresses; per the [OLA Art-Net plugin documentation](https://github.com/OpenLightingProject/ola/blob/master/plugins/artnet/README.md), with Art-Net Net/Sub-Net set to 0, OLA universe 5 maps to Port-Address 5. OLA's Art-Net plugin is bound to the AX88179B interface (2.0.0.1) and verified on the target Mac. The Botex IP is 2.0.0.10, the Mac adapter is AX88179B at 2.0.0.1 with netmask 255.0.0.0.

Superseded original text:
> The Botex NETcon 8-3 has IP address 2.0.0.10. The Mac connects through an AX88179B Ethernet adapter, configured with IP address 2.0.0.1 and subnet mask 255.0.0.0. The user confirms that physical Botex DMX output 6 is configured for Art-Net universe 6, and that the device provides outputs/universes 1 through 8. The user wants the UI to use those same 1–8 universe labels. Resolume also sends Art-Net to pixel fixtures, but the user confirms Resolume uses different universes/outputs, not universe/output 6. Configure the user-facing universe numbers 1–8 to match Botex output/universe labels. OLA uses its own universe IDs and maps them to Art-Net Port-Addresses; per the [OLA Art-Net plugin documentation](https://github.com/OpenLightingProject/ola/blob/master/plugins/artnet/README.md), with Art-Net Net/Sub-Net set to 0, OLA universe 6 maps to Port-Address 6. Bind OLA's Art-Net plugin to the AX88179B network interface and verify on the target Mac during setup.
| Blocking | What isolated test fixture or other safe test setup is available, and what is the approved physical test procedure and safe test state? Who authorizes a real-output test? | A channel-only DMX change can affect an attached fixture even if the fixture profile is unknown. Test setup must ensure the selected universe/channel cannot cause unintended movement, strobe, or full intensity. | Before any real-output test |
> This is not a problem. Even if the tool sends a DMX signal to the wrong universe or channel this won't result in a hardware failure. Therefore, the concept would also be to start with a simple LED bar and then look into the moving heads. For the development phase, let's implement a user input either via the Copilot dialog or when there's a GUI a message or dialog to be confiremd by the user that the command will be sent to the physical device.
| Blocking | What action should arm real output at runtime (for example, explicit operator action after status/preflight confirmation), and should there be any additional interlock? | “Output deliberately enabled” and output-disabled startup are policies, but the actual activation sequence and safeguards are not defined. | Before Milestone 2 live test |
> See comment before. Aim for the user to send explicitly the actions to the real devices. Safeguard could be that upon closing the tool, all DMX values of the assigned deives are set to zero.
| Blocking | What exact MIDI controller and profile are available, and what control is required for MVP? | Device messages and LED feedback vary by model/profile; “one button or fader” does not identify the minimum usable mapping. | Before Milestone 7 |
> Let's keep that for the backlog until we reach the milestone. It's the MK II model of APC40.
>> Superseded: the controller is now the Allen & Heath Xone:K2 (manual in `docs/manuals/`). Update: start with MIDI channel 15 and the Latching Layers function (owner's decision; verify with a MIDI monitor in Milestone 7). Minimum controls are still decided at Milestone 7.
| Blocking for positioning | What are the room coordinate units/origin, target locations, fixture mounting coordinates/orientations, and calibration procedure? | A target-to-pan/tilt calculation is not uniquely usable without a coordinate convention and per-fixture calibration data. | Before Milestone 4 hardware acceptance |
> I was thinking of two steps: a room definition procedure and a calibration procedure. The room definition pocedure shall define the xyz lengths in meters and the positions of the fixtures (this will be relevant particularly for the moving heads). With each moving head it shall be then possible to freely adjust pan and tilt to define the four room corners at the ground (when moving head is moounted to the ceiling). With that information it should be later possible for the user to exactly tell the tool: point moving head to this or that position and the tool will automatically adjust pan and tilt.
- Model. The exact manufacturer and model name of the moving heads, and how many you have. If you give me the model, I'll look up the official manual myself. Otherwise put the manual PDF in docs/manuals/, a new folder in the project.
> There are two moving heads called Fusion Orbit MKII by Equinox. I added the manual and also the one for the LED Bar under docs/manuals. We have two moving heads.
- DMX mode set on the heads, for example "16ch" or "Extended". It's shown on the fixture's display menu. I won't assume it: the same model has different channel layouts per mode.
> We use the "16ch" mode.
>> Updated later: use the 37ch mode; the LED bar moves to 6Ch mode. New addresses: LED bar 1-6, head 1 at 7, head 2 at 44 (universe 5).
- Universe and start address of each head, e.g. "head 1: universe 4, address 1; head 2: universe 4, address 17".
> head 1: universe 5, address 4; head 2: universe 5, address 20"
- Mounting: hanging from the ceiling (upside down) or standing. Also roughly where they hang, e.g. "both above the dance floor, left and right of the DJ".
> both above the dance floor left and right of the DJ and at the side which connects to the DJ booth.
- Safe resting position: where should the heads point when LichtWerk starts or shuts down? For example straight down, or at the DJ.
> The moving heads shall point towards the ceiling which means the beam won't shoot anywhere through the room.
- No-go zones (optional): directions the beams must never point, e.g. "not lower than head height towards the audience", "never into the DJ booth".
> There should be general a concept of defining zones (e.g., back wall, dance floor, DJ booth, etc). Those zones shall be redefined as either "include" or "exclude" of animation. So it's easy for the VJ to quickly change the behavior of the fixture.
- Named targets you want, e.g. DJ, centre of the dance floor, bar, entrance, mirror ball.
> Absorber #1, Absorber #2, Absorber #3, Absorber #4, Bass trap #1, Bass trap #2, Disco ball, Center of dance floor, Left lower corner dance floor, Left upper corner dance floor, Right lower corner dance floor, Right upper corner dance floor

| Required behavior | After a process crash, OLA disconnect, network loss, or restart, should the application hold last output, request blackout, or take another action? What can the hardware/gateway guarantee? | “Fail safe” and “no unexpected output” are not enough to determine behavior; abrupt blackout can itself cause unsafe movement or a disruptive show change. | Before Milestone 2 live test |
> See above. Best would be blackout and moving set to zero. This could be customizable in a later version of the tool.
| Required behavior | Are scenes complete snapshots or partial updates? If fixture and group entries overlap or omit attributes, what takes precedence and what happens to unspecified attributes? How do direct controls and live overrides interact with scene recall? | The current “snapshot/definition” wording allows different implementations. State resolution must be consistent across UI, MIDI, and API. | Before Milestone 5 |
> Come up with a proposal for the handling. Are there best practices from other software tools of this kind? It's better to start with something and then adjust if needed.
>> Proposal implemented (Milestone 5): partial scenes with latest-takes-precedence per attribute; see "State resolution and transitions".
| Required behavior | What happens to an active transition when a new scene, manual/group command, blackout, or master change arrives? Should transitions be cancellable/replaced, and do safety commands always take effect immediately? | The document currently says a new command may intervene but leaves arbitration and interruption behavior undefined. | Before Milestone 5 |
> Also here I would follow common practices from other tools.
>> Implemented (Milestone 5): new commands replace fades only for the attributes they touch; direct commands are immediate; blackout and master act immediately at output level and never change stored scenes. See "State resolution and transitions".
| Required behavior | Is direct raw DMX control required, or is raw pan/tilt setup sufficient? If raw DMX is required, what practical restriction makes it safe in an application with no user accounts? | Raw bytes bypass fixture semantics and may cause unexpected movement or intensity; “authorized local control” is undefined when all local users share access. | Before implementing raw mode |
> Raw DMX is not needed.
| Scope | Which reusable presets, if any, are required for MVP? The product requirements list color, position, and intensity presets, but milestones and MVP acceptance do not say whether they are included. | Presets affect persistence, scene references, UI scope, and acceptance. Decide whether to include them in MVP or defer them explicitly. | Before Milestone 5 |
> Presets are not needed for the MVP. Defer.
| Scope | What is the minimum first-live-session workflow in the UI, and which controls/pages must be operator-ready versus setup-only? | The requirements list several screens, while MVP acceptance does not identify the exact essential workflow beyond status, master, and blackout. | Before Milestone 6 |
> For the MVP, it's helpful to have additionally a fader and color picker for the LED bar mentioned above to use for first testing purposes. Then, for the Moving Heads two potty knobs shall be displayed for pan and tilt.
| Scope | Is the application ever expected to be controlled from another device on the LAN for MVP? | Localhost is the safe default; remote LAN access requires explicit exposure and access-control decisions. If not needed, keep it out of scope. | Before Milestone 6 |
> It can be local. If shared, my friend would use simply his own Mac with the software running there. there's no need for remote access.
| Quality gate | What supervised run duration and observable pass/fail criteria should demonstrate acceptable repeatability, responsiveness, and long-run reliability for the club? | The document correctly avoids inventing latency/frame-rate numbers, but “live club use” needs owner-approved commissioning criteria. These can be set after the simulation and hardware tests provide measurements. | Before MVP sign-off |
> In a club environment, it is expected that the tool can run for hours without interruption. However, it will be impossible to test that after each implementation step. So, what would be helpful to already think about is implementing an automatic error log which tries to fetch the problem which then can be analyzed by copilot for bug fixing.

### Planning clarifications

- The MVP acceptance criteria below define the full first usable, hardware-validated product, not the completion of Milestones 0 or 1. Simulation and single-channel hardware proofs are intermediate gates.
- Resolume/OSC and effects remain explicitly outside MVP. The Python OSC dependency should therefore be deferred until the OSC milestone unless an earlier concrete need is approved.
- “One physical fixture” is gated on verified fixture documentation and patch data. “One selected DMX channel” in an OLA proof of concept is not a substitute for fixture-engine acceptance.
- Real-output verification is a manually supervised commissioning activity, never an automated test. The exact output-arm/confirmation UX remains an open decision.
- Keep operation local and bind to localhost by default. Any LAN access needs a separate, explicit scope and access-control decision.
- The first implementation path is simulation first, then a separately gated single-channel OLA proof of concept, then verified fixture control, and finally the full MVP; this resolves the earlier ambiguity about the first deliverable.

## MVP acceptance criteria

MVP is complete when all of the following are demonstrated:

1. The application starts locally using documented commands and runs in simulation with physical output disabled by default.
2. Configuration defines at least one verified schall.werk fixture/mode and a validated universe/address patch.
3. The simulation produces expected fixture channel values; automated tests cover relevant conversions and safety rules without physical hardware.
4. After documented manual preflight and explicit operator enablement, OLA transmits only the selected, configured universe through the verified lighting interface to the Botex NETcon 8-3.
5. One verified physical fixture can be controlled for intensity and its documented primary parameters.
6. A moving head supports raw/manual pan/tilt within configured limits.
7. At least two calibrated named targets can be stored and repeatably recalled by a moving head.
8. At least two scenes can be stored and recalled with transitions; interruption behavior follows a documented rule.
9. Master intensity and blackout do not modify stored scene values; blackout remains available independent of the displayed UI page.
10. The verified Xone:K2 can trigger/control at least one explicitly configured application function, with connection loss handled without stopping lighting output.
11. The operator UI exposes output, MIDI, blackout/master, and actionable error status required for the first live-use workflow.
12. Automated tests pass without physical hardware connected, and no test or ordinary application startup can enable real output.

OSC/Resolume integration is explicitly not required for MVP completion.

## Implementation milestones

Milestones are sequential unless explicitly approved otherwise. Each milestone ends with its listed evidence and owner review; do not advance to real output or live fixture control just because code exists. Keep each milestone’s configuration, tests, documentation, and acceptance evidence in the repository. Hardware gates require a human-supervised test with the affected universe, channels, fixture, and expected action stated beforehand.

### Milestone 0: scope, environment, and development foundation

Deliver:
- inspect and document the actual repository, host OS/macOS version, Python version, and available hardware information
- resolve the simulation-versus-hardware proof-of-concept scope
- verify OLA installation/client compatibility before selecting its Python integration method; document unresolved compatibility if verification is not yet possible
- choose and document only dependencies needed for this milestone; defer OSC and other later-milestone dependencies
- establish dependency manifests, test/lint commands, and a minimal README setup path
- define initial configuration shape and validation/error-reporting conventions
- build a simulation-only startup path; do not connect to real DMX hardware

Exit gate: a clean local setup starts in simulation, runs its automated tests without hardware, and clearly reports that output is simulated/disabled. No real-output path is enabled by default.

### Milestone 1: simulated control vertical slice

Deliver:
- generic DMX output boundary and mock implementation
- one 512-slot simulated universe buffer with validated channel/value updates
- minimal application command, API, and diagnostic surface to inspect simulated output
- output status and global blackout represented safely in simulation

Exit gate: a documented command changes one selected simulated channel, invalid values are rejected, blackout is observable, and tests prove no physical adapter is instantiated by default.

### Milestone 2: OLA and single-channel hardware proof of concept

Deliver:
- OLA adapter behind the generic DMX output boundary, only after host compatibility is verified
- explicit output enable/disable and clear diagnostics for selected OLA universe/output status
- operator-controlled selection of the test universe/channel/value; no fixture profile is assumed for this test
- manual OLA/Botex configuration and preflight documentation, with exact settings left configurable until verified

Exit gate: after the open hardware/network/safety decisions are resolved, a human-supervised test deliberately changes exactly the documented channel on the selected universe. Automated tests continue to use mocks only. If macOS/OLA compatibility cannot be verified, stop at simulation and present the evidence and alternatives for owner decision; do not silently switch transport.

### Milestone 3: fixture definitions and patch

Deliver:
- reusable verified fixture definition/mode model and patched-instance model
- channel/value translation and DMX address/range validation
- fixture test controls through logical attributes, initially for only the verified fixtures in scope

Exit gate: tests prove the selected real fixture profiles map logical values correctly and reject address collisions/overflow; a supervised hardware test controls one verified fixture without editing universe bytes manually.

### Milestone 4: moving-head positioning

Deliver:
- documented room coordinate convention and measured targets/mounting data for selected moving heads
- target calculation, per-fixture calibration, movement limits, and setup diagnostics
- raw setup controls only with explicit scope/guardrails decided

Exit gate: calibrated targets meet an owner-approved repeatability test on selected hardware; out-of-range/unreachable targets are reported and do not produce unsafe movement.

### Milestone 5: state, scenes, and transitions

Deliver:
- local persistence for scenes and required project configuration, including documented validation/recovery behavior
- scene recall, central non-blocking transitions, master intensity, and blackout
- defined precedence among scenes, groups, direct controls, transitions, and safety commands

Exit gate: tests verify state resolution, persistence, master/blackout non-destructiveness, and documented transition interruption behavior; scene recall is repeatable in simulation.

### Milestone 6: minimum operator UI

Deliver:
- prioritized operational workflow for dashboard/status, fixture setup, positions, scenes, and diagnostics
- high-contrast operator controls for master intensity and blackout that remain available across pages
- actionable status/errors; UI disconnect does not stop the lighting engine
- LAN exposure disabled unless separately approved and specified

Exit gate: owner-approved first-live-session workflow can be completed through the UI and simulated failures/status are visible without raw stack traces.

**Milestone 6 implementation decisions (simulation):**
- Always-visible top bar on every page: SIMULATED/REAL chip, master slider, large BLACKOUT button. The arm/disarm button (when real output is configured) and error banners sit directly below it.
- Pages: Controller (on-screen Xone:K2, default; see below Milestone 7), Fixtures (formerly "Live": scenes and all fixture controls), Positions (aim/calibrate plus moving-head controls), Setup (room size, target coordinates, head mounting positions), Diagnostics (output mode, armed vs simulated universes, patch table with address ranges, setup warnings, recent application errors). The top bar is one compact row (chip, page tabs, active scene, master, arm, blackout).
- `GET /api/diagnostics` is read-only. Recent errors are log messages only, never stack traces.
- Backend loss is shown as a banner and the UI recovers by itself (2 s status heartbeat). The lighting engine does not depend on the browser.
- **Deferred:** WebSocket push (the UI polls status every 2 s, and every 250 ms during a fade), UI editing of fixtures/patch, head calibration limits/rest, zone and target creation (use `config/*.json`), scene editing/duplication. MIDI and OSC status appear with Milestone 7.


### Milestone 7: MIDI / Xone:K2

Deliver:
- verified device discovery/selection and MIDI monitor
- persistent mappings from supported MIDI messages to application commands
- tested connect/disconnect handling and command processing that does not block output
- LED feedback or MIDI learn only if verified and separately prioritized

**Milestone 7 implementation decisions (simulation / no guessed messages):**
- Input, plus LED feedback for scene buttons (the only thing ever sent to the controller): a button bound to a scene lights amber, the active scene's button lights green, others stay dark. LED notes are from the K2 manual pp. 17-18 (matrix buttons: red notes 24-51, amber = +36, green = +72) and are sent on the button's own MIDI channel. Each button has one LED: only the note of the wanted colour is sent (velocity 127); a velocity-0 message on any colour note switches the LED off, so it is sent only when a button should go dark. Verified on the real K2 with Latching Layers off (green on activation). Pressing the active scene's button again fades all intensities to 0 (scene's fade time) and clears the active scene; stored scenes are never modified. Faders and the UI: the UI polls and follows MIDI-driven changes.
- With Latching Layers on, the K2 ignored amber/green notes in a direct test (only red lit); LED feedback therefore needs Latching Layers off.
- Code: `midi_adapter.py` (the only module using mido/python-rtmidi; finds the device by name part, default `XONE:K2`, re-scans every 2 s so unplug/replug recovers, queues events so MIDI can never block DMX output), `midi_control.py` (events to commands, monitor, learn; no MIDI library), `midi_mapping.py` (validated bindings in `config/midi.json`, atomic saves), `midi_api.py` (`/api/midi`).
- Enabled with `"midi": {"enabled": true, "device": "XONE:K2"}` in `config/app.json`; disabled in tests.
- Actions: `master`, `intensity` (fixture id or group), `scene` (scene id), `blackout` (toggle). Faders/pots (CC) drive continuous actions; buttons (note-on) trigger. A binding includes the MIDI channel (the K2 manual default is 15).
- **No mapping is shipped.** The manual's note-name to note-number convention and which CC rows are faders could not be verified from the PDF text, so mappings are taught with "Learn" on the UI's MIDI page from the real controller. The owner-selected plan: 4 faders to master, LED bar, head 1, head 2 intensity; four buttons to scenes 1-4; one button to blackout.
- Latching Layers: each layer sends different numbers, so bind controls on the layer you will use at the club.
- Python MIDI packages need a compiler on this Mac: install with `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk .venv/bin/pip install -e ".[dev]"` if the default SDK fails.
- **Open:** Latching Layers on/off state and channel 15 on the unit itself, and LED feedback.

**Controller page (on-screen Xone:K2), added after Milestone 7:**
- The default UI page ("Controller") is a picture of the Xone:K2: four strips (encoder with push, three pots each with the switch below it, fader), the 4×4 matrix A–P and the bottom row (LAYER, two encoders, EXIT SETUP). Every on-screen control shows its assignment, live value and LED colour, and sends the same MIDI-style event as the hardware (`POST /api/controller/event`, handled on the configured channel), so screen and hardware always do the same thing. Layout and numbers: `xone_k2.py` (from the K2 manual pp. 9, 14–18). Data: `GET /api/controller`. It fits a maximized browser on the owner's MacBook (about 1512×820 px) without scrolling.
- "ASSIGN CONTROLS" lets the owner pick a control and choose its action and target; the backend lists which actions each target supports. One action per control; assigning an action that is already used moves it. The old "Fixtures" page keeps the per-fixture sliders.
- Actions: `master`, `intensity`, `hue` (colour), `saturation`, `white`, `strobe` (native fixture strobe, capped by `max_strobe`), `scene`, `blackout`, `flash` (intensity full while held, previous value restored on release) and `strobe_hold` (strobe full while held, restored on release). Fixture actions target a fixture id, a group or `all`, and only reach fixtures that have the needed attributes.
- Colour: hue/saturation are converted to RGB at full value (the dimmer sets brightness); hue is remembered per fixture so saturation 0 (white) and back keeps the colour. Moving-head ring LEDs are not part of the column colour.
- Top encoders send two's complement relative steps (1 = one step clockwise, 127 = one step anticlockwise; manual p. 9). One step = 7.5° hue or 1/64 of a level. Encoders are marked `relative` in `config/midi.json`.
- `config/app.json` → `midi.channel` (default 15) sets the channel the on-screen controller uses.
- LEDs: assigned scene/blackout/flash/strobe-hold buttons amber; active scene green; blackout button red while blackout is on; flash/strobe-hold buttons red while held.
- First assignment (columns: 1 Master/all fixtures, 2 LED bar, 3 moving heads, 4 DJ spots):

| Control | Action |
|---|---|
| Top encoder (CC 0–3) | Colour (hue) of the column |
| Encoder push (notes 52–55) | Flash (hold) |
| Pot 1 (CC 4–7) | Saturation |
| Pot 2 (CC 8–11) | White (column 2 free: the LED bar has no white) |
| Pot 3 (CC 12–15) | Strobe (native) |
| Switch under pot 3 (notes 40–43) | Strobe hold |
| Faders (CC 16–19) | Master, LED bar, moving heads (group), DJ spots (group) intensity |
| Matrix A, B | Test scenes (unchanged); C–P free for scenes |
| EXIT SETUP (note 15) | Blackout |
| Switches under pots 1 and 2, LAYER, bottom encoders | Free (candidates: position targets, pan/tilt) |

- **To verify on hardware:** encoder direction and step size, flash/strobe-hold release, blackout red LED, and flash/strobe-hold LEDs on the pot switches and encoder push windows.

Exit gate: the Xone:K2 with its configured layer mode and MIDI channel performs the owner-selected mapping in simulation and, after safety review, on the configured system. No guessed controller messages are accepted.

### Milestone 8: MVP commissioning and sign-off

Deliver:
- complete the MVP acceptance criteria as a traced checklist with test results and supervised hardware evidence
- document the verified fixture, patch, OLA/gateway mapping, output enable procedure, startup/restart behavior, and emergency fallback procedure
- record any remaining limitations and explicitly defer them rather than implying they are complete

Exit gate: owner approves all hardware-dependent evidence; tests do not use physical hardware and ordinary startup remains output-disabled.

### Milestone 9: optional OSC / Resolume integration

Deliver:
- verified OSC configuration and send/receive behavior
- explicit configured mappings between lighting actions and Resolume
- failure isolation if Resolume is unavailable

Exit gate: verified Resolume OSC documentation/configuration and tests for unavailable peer; does not change the core lighting engine's ability to run alone.

### Milestone 10: effects and live-performance enhancements

Only after the core system is stable and MVP is accepted:
- chases
- movement effects
- color effects
- beat/BPM concepts if required
- richer Xone:K2 LED feedback
- strobe as a fixture-native attribute in scenes and MIDI (see "Strobe and other timing-critical effects")

Exit gate: each effect has bounded/safe output, simulation tests, clear interaction with base state, and owner-approved live test criteria.

### Milestone 11:

The tool allows the definition of zones which can be later assigned as zones for the moving heads to let the beam only point within this area - as opposed to targets which are fixed points. For the sake of simplicity we consider only zones which are located either at the ground, the walls, or the ceiling. All zones are rectangles and are rectangularly aligned with the room coordinates. So, as input it requires the room coordinates of one of the corners plus length and width of the rectangle.

Make the assign controls pane collapsable. It is not used all the time, but consumes a lot of space.

Collapsible assign pane.
Separate zone model.
Zone input on the Setup page.
Assigning one target or zone per head.
Zone aiming is built and tested in simulation, but works on real heads only after tomorrow's measurement.
Preview mode in the core: the safe base for both the APC40 and the K2.

### Milestone 12:

Implement AKAI APC40 MKII support. Change the UI interface depending on the MIDI controller connected. Default if no controller is connected is the APC40 GUI.

The APC40 behaviour is a fixed operating logic (group/single mode, Clip Stop selection, colour pads), not a free MIDI-learn list. Only the scene pads are freely assignable, as you asked.
The K2 keeps its current, freely assignable setup.
Milestone 13: the 2D map on the Controller page.

First starting point for mapping and return values:

	• Fader:
		○ For intensity
		○ One Master
		○ 1 LED Bar 
		○ 3x Moving Heads: 1x group, 2x individual
		○ 3x DJ Spots: 1x group, 2x individual
	• Numbered button above fader: to activate/deactivate groups (toggle behavior: group activated - group button illuminated and individual fixture button turned off; and vice versa); pressing a deactivated single-fixture button will deactivate group mode
	• Master button above CUE Level: Master Blackout
	• Record Button for each channel: Flash function
	• Solo-Button for Strobe function (but as a steady toggle, not just hold); the CUE Level knob shall be used to adjust a global strobe frequency (may require a translation table for fixtures which doesn’t provide the information of frequency)
	• The upper row of the rotary knobs on top of the channels shall regulate the saturation (it‘s OK that master has no saturation)
	• Stop all Clips button toggles between: Preview mode and Active mode (preview mode allows for changing fixture parameters before they are sent to the fixtures and active mode sends all currently set parameters to the fixtures)
	• The Clip Stop Buttons enable selection of a fixture or fixture group for which Clip Launch Buttons and Device Control Knobs become exclusively active. When a Clip Stop Button is turned on:
		○ All Clip Launch Buttons are used to allow for the fixture/group color selection (we exclude the rings of the Moving Heads for now)
		○ The device control knobs allow for other functions of the fixture/group such as pan/tilt.
		○ There can be only one Clip Stop Button turned on at the same time.
		○ When no channel is selected, the Clip Launch Buttons serve as launch tool for scenes. User can assign them freely and choose the color to be displayed. If a scene is activated the light shall flash.

Check out the two manuals of AKAI APC40 in the docs/manual folder to understand how the implementation is to be done.

Like for the Xone K2 implementation, the GUI shall mimic the layout of the controller to make it easy for the VJ.

### Milestone 13:

Simple preview monitor 2D and preview mode (some setting/scene/FX is changed/prelaunched) before it is actually sent to the fixtures (by pushing a button). All fixtures shall be displayed on a simple 2D map with showing the preview color settings of all fixtures.


## Explicit out of scope for initial development

Do not implement unless later requested:
- QLC+ dependency
- Wolfmix integration
- pyArtNet as parallel primary output path
- full lighting-console tracking/cue-stack semantics
- timecode
- 3D photorealistic visualizer
- cloud service dependency
- mobile apps
- user accounts/roles
- remote internet control
- AI-generated live lighting decisions
- automatic audio-reactive lighting
- generic fixture library for the entire market

## Installation tasks for Copilot

Copilot should prepare, not blindly execute, the environment in clear steps.

Add only dependencies required by the active milestone; verify supported versions and the host environment before installation.

Core MVP candidates, as needed:
- fastapi
- uvicorn
- pydantic
- pyyaml
- pytest
- pytest-asyncio
- httpx

Add at the MIDI milestone if selected and verified:
- mido
- python-rtmidi

Add only for the optional OSC milestone:
- python-osc

Frontend dependencies:
- React
- TypeScript
- Vite

System dependency:
- Open Lighting Architecture (OLA), including olad and a compatible way for the Python application to communicate with it.

Before giving OLA installation commands, detect/document the host OS and verify the current official/recommended installation method and Python API compatibility. Do not assume `apt install ola` because the target environment is macOS.

Do not install QLC+, pyArtNet, a database, Docker, or additional frameworks unless the requirement becomes concrete and the architectural reason is documented.

## Instructions for GitHub Copilot

Read this entire project.md before proposing architecture or implementing product behavior.

When asked to implement a feature:
1. Check whether the relevant domain model already exists.
2. Keep domain logic independent of FastAPI, React, OLA, MIDI, and OSC wherever practical.
3. Put hardware/protocol integrations behind adapter interfaces.
4. Add or update tests with each meaningful domain behavior.
5. Update documentation when configuration or setup changes.
6. Do not introduce new frameworks/dependencies unless they solve a concrete requirement.
7. Do not make up fixture channel maps, Xone:K2 MIDI mappings, Botex settings, or Resolume OSC addresses. Ask for the manual/model/profile or mark the item as configuration/TODO.
8. Do not modify the existing Resolume/pixel-bar system unless explicitly requested.
9. Default to simulation/output-disabled behavior during development.
10. Never send real DMX output merely because tests or the development server start.
11. Before a real-hardware test, clearly state what universe/channels will be affected.
12. Prefer small milestone-sized implementations over generating the entire application at once.
13. Explain important architecture choices in plain language suitable for a non-expert developer using GitHub Copilot.
14. If requirements conflict, stop and identify the conflict rather than silently choosing a behavior.
15. Treat this document as the product/architecture source of truth until it is deliberately updated.

### First Copilot task after adding this file

Do not build the complete product in the first prompt.

Recommended first task:

"Read project.md completely. Do not implement the application yet. Produce a concrete implementation plan for Milestones 0 and 1 only. Inspect the current repository first. Identify the proposed folder structure, Python and frontend dependencies, OLA integration boundary, configuration models, test strategy, and macOS setup requirements. Flag any assumption that depends on the exact Botex NETcon 8-3 configuration, fixture profiles, MIDI controller model, or local network. Then propose the smallest end-to-end proof of concept that runs in simulation by default and can later send one deliberately selected DMX channel through OLA after output is explicitly enabled. Do not alter existing Resolume configuration. Wait for approval of the plan before implementing."

## Future information to collect

Before actual fixture implementation, add the following verified data to the repository:
- exact fixture manufacturer/model names
- DMX mode/channel table for each fixture
- current DMX start addresses
- current universe assignments
- Botex NETcon 8-3 IP/network configuration
- Botex Art-Net/DMX port and universe mapping
- Xone:K2 layer mode and MIDI channel in use (manual is in `docs/manuals/`)
- Xone:K2 observed MIDI messages for the controls to be mapped
- room dimensions and coordinate origin
- physical mounting position/orientation of each moving head
- Resolume OSC configuration only when the OSC milestone begins

## Success vision

The finished system should make schall.werk lighting feel spatial rather than channel-based. During normal operation, the user should think in commands such as "Moving Heads -> DJ", "Washes -> blue", "activate Peak scene", "master 70%", or "blackout", while the application handles fixture-specific DMX conversion and OLA handles lighting-network transport.

The product is successful if it gives schall.werk substantially more precise and predictable fixture control than Wolfmix without introducing the complexity of a general-purpose professional lighting console.

### Resposen to last conversation before implementing Milestones 11, 12, and 13

G1 ★ Which controller do you use? project.md currently says the Xone:K2 replaced the APC40, and Milestone 12 brings the APC40 back. My proposal:

Support both.
Show the screen for the controller that is plugged in.
Show the APC40 screen when nothing is plugged in.
If both are plugged in, the APC40 is in charge and a tab switches the screen to the K2.
Do you agree?
> Yes, agreed on your proposal.

G2 ★ Timing and order. All three milestones before tomorrow is ambitious; the APC40 part is the largest by far. Will you have the APC40 at the club tomorrow? My proposed order is Milestone 11, then preview mode (Milestones 12 and 13 both need it), then Milestone 13, then the rest of Milestone 12. If you bring the APC40, I'd do Milestone 12 before Milestone 13.
> I have the APC40 connected right now. I'd prefer it's integration before the test at the club to be ready.


Milestone 11: zones and collapsible assign pane
The collapsible "Assign controls" pane needs no answers.

Z1 ★ What does assigning a zone to a moving head do?

(a) Recommended: the beam can never leave the zone. The pan/tilt knobs then move the beam spot left/right and forward/back inside the rectangle (0–100%) instead of raw pan/tilt.
(b) The head only refuses targets that lie outside its zone.
> For now one can either let a moving head point to a specific target which makes it static with respect to its position or one can assign exclusion/inclusion zones. The user shall define which zones are excluded (beam must never enter there) or included (beam must never leave the zone - however, multiple zones might be included which extends the allowable range; if the zones are not overlapping the beam must turn off when going from one included zone to another when it's passing an excluded zone; same goes for connecting multiple targets within one automated animation). Each zone must be either of it. 

Z2 Existing zones. The app already has whole-room 3D boxes that are "include" or "exclude" for all heads. Proposal: keep the "exclude" boxes as a safety rule, for example "never into the DJ booth", and add the new zones as surface rectangles that you assign to a head.
> Everything defined so far is actually just a target, not a zone according to my understanding. We need to clearly separate those two items.

Z3 How you enter a zone. Proposal:

Choose the surface: floor, ceiling, left wall, right wall, far wall, or booth wall.
The corner you enter is the one closest to the room origin.
On the floor and ceiling, length runs left→right and width runs away from the booth.
On a wall, length runs along the wall and width runs upward.
Also note: room size and head mounting positions in config/room.json and config/heads.json are still empty. Zones can be created now, but aiming into them only works once you measure the room at the club.
> Measurement will happen tomorrow at the club.

Z4 Assigning a zone to a head. Proposal: one zone per head at a time. You pick it on the Positions page, and it is saved as part of a scene.
> As opposed to my statement above we can start with one zone/target at the time. But I definitely want to address it later that multiple zones/targets can be selected for one moving head.

Milestone 12: APC40 MKII
The protocol document gives all the numbers I need: faders, buttons, knobs, the 40 colour pads and the LED colours.

Two facts you should know:

The APC40 needs a mode switch. The app has to switch it into its "Alternate Ableton Live mode" each time it connects. Otherwise the APC40 switches its own button lights and flash-on-hold does not work.
> OK. OK. I'll take care that this will be done during you're busy implementing this milestone.

Stop All Clips has no light. The protocol document lists no LED for that button.
> OK. Maybe you can propose a different button then that provides visual feedback.

A1 ★ Showing preview/active state. Because Stop All Clips has no light, the preview/active state can only be shown on screen. Is that OK, or should a button that has a light (for example PLAY) take over this job?
> See above.

A2 ★ Which fader does what. Proposal:

Master fader: Master.
Track faders 1–7: LED bar, Moving-head group, Head 1, Head 2, DJ-spot group, Spot 1, Spot 2.
Track 8: free.
> Yes.

A3 ★ Group mode versus single mode. Proposal:

Group mode (the start state): the group fader controls both fixtures. The single-fixture faders are ignored and their buttons are dark.
Single mode: each fixture follows its own fader, and the group fader is ignored.
The mode covers the whole channel strip, not just the fader: saturation, flash, strobe and colour all follow it.
After switching, the current position of the fader that just became active applies right away. The light can jump, because the faders aren't motorised.
What should the numbered buttons on the LED bar channel and on track 8 do? Proposal: nothing.
> Nothing for Track 8, but for LED Bar the numbered button should at least be illuminated to follow the same logic indicating it's active.

A4 ★ Strobe frequency.

The LED bar manual gives 0–30 Hz and the Q-Spot manual gives under 1 Hz up to 20 Hz.
The Equinox manual gives no Hz at all.
Neither manual says whether the scale is even (linear).
Proposal: the CUE LEVEL knob sets one shared rate from 1 to 20 Hz, and I treat the LED bar and Q-Spot scales as linear. The heads use a rough guess, marked "to measure at the club". You could count flashes with phone slow-motion video. OK?
> Yes, let's keep this easy for this step. We can calibrate the frequencies later if needed.

A5 Selecting a channel with Clip Stop. Proposal: pressing a lit Clip Stop button again deselects the channel, and the pads go back to scenes. The master channel has no Clip Stop button, so it can't be selected.
> Agreed.

A6 The 40 colour pads. Proposal: 8 columns of hues (red, orange, yellow, green, cyan, blue, purple, magenta) and 5 rows from saturated to pastel. One pad is "white", which uses the W LED on the Q-Spots and heads. Each pad lights in its own colour. Or do you want your own list?
> Your proposal sounds good to me.

A7 ★ The 8 device knobs when a channel is selected. Proposal:

Heads: 1 pan, 2 tilt, 3 movement speed, 4 white. If the head has a zone, knobs 1 and 2 move the beam spot inside the zone.
Q-Spots: 1 white, 2 fine hue.
LED bar: 1 fine hue.
All other knobs are free.
> OK.

A8 Active scene "flashes". The pad blinks. The APC40 times its blinking from a tempo the computer sends, so if it doesn't blink without that, I'll let the app make it blink instead. This needs a check on the hardware. Scene Launch 1–5, the track-select buttons and the rest stay free for now.
> OK.

Milestone 13: preview mode and 2D map
P1 ★ What always goes out live, even in preview mode? Proposal: Blackout, Master and Flash, because they're for emergencies and quick live hits. Everything else waits for the button: intensity, colour, saturation, strobe on/off, pan/tilt, zones and scenes.
> OK. Sounds good. It requires some indication of the values/controllers which are still send out during preview mode.

P2 ★ What happens when you send the preview to the fixtures? Should it switch over instantly, or fade over a time you set? Proposal: instant.
> Yes, instant.

P3 Preview on the K2. Proposal: the K2's LAYER button toggles preview mode, lit amber during preview and green when live.
> Sounds good.

P4 ★ Where the fixtures sit on the map. I have no positions for the LED bar or the two DJ spots, and the head positions aren't measured yet. Proposal: a simple sketch of the room (booth at the bottom, dance floor at the top) where you drag each fixture into place once. The positions are saved in a config file. Beam spots are drawn later, once the heads are measured.
> Let's assume for now we take the ceiling perspective. You can assume some room dimensions for a 50 m3 room. I will add this tomorrow to be more precise. Just position all fixtures so they don't overlap. Of course, in the 2D map the outlet of the fixtures is always pointing out of plane no matter the actual orientation of the fixture so the user can actually see what's going on.

P5 ★ Where the map goes on screen. The APC40 screen is dense. Proposal: a compact map on the right of the Controller page, using the space the collapsed assign pane frees up. Each fixture is drawn as a dot, half live colour and half preview colour. Does that work for you, or would you prefer a separate tab?
> Yes, this sounds good for a first implementation.

1. ★ Exclude zones: for every head, or only the selected head? Your plan is one target or one zone per head at a time. An exclude zone such as "never into the DJ booth" only protects anything if it's always active.

Proposal: exclude zones always apply to all moving heads. For each head you choose one target or one include zone.
If you'd rather assign an exclude zone to a single head, say so.
> It shall be possible to either assign those zones/targets to individual moving heads or the entire group.

2. ★ What happens when a beam would enter an exclude zone?

During manual moves (knobs, pan/tilt): proposal: the head stops at the edge and doesn't move into the zone.
> That's OK.
When switching to another target or zone (your "beam off while passing" rule): proposal: if the path crosses an exclude zone, the beam is turned off for the whole move and comes back on at arrival.
> Yes.
The app can't know exactly how long a real head takes to travel. So the beam-off time is a setting per head (default 1.5 s) that you tune at the club.
> The pan/tilt to room coordinates calibration shall actually define where one zone starts and where another ends. So I hope this doesn't need to be a matter of timing.

3. The preview/live button on the APC40.

Proposal: SESSION RECORD (bottom right, has its own light). Light on means preview mode; press it to go live, which sends everything at once.
STOP ALL CLIPS stays free.
On the K2, LAYER does the same job: amber in preview, green when live.
The controls that still act live during preview (Master, Blackout, Flash) get a "LIVE" mark on screen.
> OK.

4. Room size for the map.

Proposal: I use 5 m × 4 m × 2.5 m (= 50 m³) only for the 2D map. I store it as a placeholder with the map layout and don't write it into config/room.json.
This way no beam is aimed using made-up measurements. Tomorrow you enter the real values in Setup and the map uses them.
Already settled from your answers:

What exists today are targets, not zones. Zones become separate rectangles on the floor, ceiling or walls.
Each zone is either include or exclude.
I'll enter zones the way I proposed (choose a surface, enter a corner, length and width), since you didn't change that part.
Several zones or targets per head comes later. I'll note it in project.md.
> OK.
