# LichtWerk

LichtWerk is a local-first lighting control prototype. Milestones 0–3 provide a localhost-only API, an in-memory DMX simulator, an OLA output adapter that is disabled by default (see the last section), and a fixture engine that turns logical values (intensity, color) into DMX through fixture definitions and a patch.

## Requirements

Manufacturer PDF manuals are kept locally in `docs/manuals/` and are not
redistributed in this repository. Fixture definitions retain their source/manual
references; obtain the referenced manuals from the manufacturers when needed.

- macOS (the current target machine reports macOS 26.6.2)
- Python 3.13 recommended; Python 3.11 or newer is required

The inspected Mac currently has Python 3.9.6, which is below the project's supported minimum. Install a supported Python version before following the commands below. OLA is intentionally not installed or required for this simulation milestone.

The tests pass with the inspected Mac's existing Python 3.9.6 environment, but a clean package installation under the supported Python version has not yet been verified.
Dependencies currently have compatible version bounds in `pyproject.toml`; a resolved lock file is deferred until the supported Python environment is available.

## Setup

From the project folder:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Run in simulation

```sh
python -m lichtwerk serve
```

The API binds to `127.0.0.1:8010` by default. With simulation configuration, status reports `output_mode: simulated` and `physical_output_enabled: false`; no physical adapter is used. **Check your configuration before running:** explicitly configured OLA output starts automatically, in lighting blackout with heads at rest and haze pump zero. Release blackout deliberately to light fixtures.

Change DMX address 3 (red on the configured Cameo Thunder Wash 100 RGB 6Ch profile; address 1 is its master dimmer) to 255 in simulated universe 5:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8010/api/commands/set-channel \
  -H 'Content-Type: application/json' \
  -d '{"universe":5,"channel":3,"value":255}'
```

Inspect the full 512-slot simulated output buffer:

```sh
curl --fail-with-body http://127.0.0.1:8010/api/universes/5
```

Activate and release simulated blackout:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8010/api/output/blackout \
  -H 'Content-Type: application/json' \
  -d '{"active":true}'
```

The blackout zeros simulated output without erasing the underlying channel state. Releasing blackout restores the previous simulated values.

## Visual interface (simulation)

Start the backend (above) in one terminal, then in a second terminal:

```bash
cd "/Users/olivermartin/Library/CloudStorage/OneDrive-Personal/Documents/My documents/Coding/LichtWerk/frontend" && npm install && npm run dev
```

Open http://127.0.0.1:5173. The header shows the licht.werk logo and a SIMULATED or OUTPUT DISABLED warning when applicable. Healthy enabled real output has no status badge; blackout, connection/output errors and safety override warnings remain visible. The interface has controller, fixture, positioning, setup, MIDI and diagnostics pages. Nothing reaches hardware with simulation configuration. Explicitly configured real output no longer requires ARM; every backend startup begins in lighting blackout.

The supplied original logo is preserved at `frontend/public/lichtwerk-logo.png`
(1024 × 1024). Header, favicon and Apple touch-icon derivatives live beside it.
Use that original as the icon source when a desktop app is packaged in future;
desktop packaging is not currently implemented.

## Fixtures and patch

Fixture setup is split into two kinds of JSON files, both checked at startup:

- `config/fixtures/*.json` — **fixture definitions**: one file per fixture model, listing its DMX modes and what each channel does (`intensity`, `red`, `green`, `blue`, `white`; a second channel with `"resolution": "fine"` makes an attribute 16-bit). Every definition needs a `source` note saying where the channel layout comes from. The included Cameo Thunder Wash 100 RGB has only mode `3Ch3` (red, green, blue), as confirmed by you; it has not been checked against the manual.
- `config/patch.json` — **patched fixtures**: which definition and mode each physical fixture uses, its universe (0–7) and start address. The LED bar is `led-bar-1` on universe 5, address 1.

Startup stops with an error naming the file and the problem if a definition is invalid or the patch has an unknown definition/mode, a duplicate id, an unconfigured universe, an address overflow past 512, or overlapping addresses (disabled fixtures still reserve their addresses). Fixtures without an intensity channel get a virtual dimmer: intensity scales the colors. All fixtures start dark. Editing the patch in the UI comes in Milestone 6; for now edit the files and restart the server.

Example: set the LED bar to full red at half intensity (logical values are 0.0–1.0):

```sh
curl --fail-with-body -X POST http://127.0.0.1:8010/api/fixtures/led-bar-1/state \
  -H 'Content-Type: application/json' \
  -d '{"values":{"intensity":0.5,"red":1.0}}'
```

Groups come from the optional `"groups": ["bars", "all"]` list of each fixture in `config/patch.json`; a group exists as soon as one fixture names it.

## Scenes, fades and master

Scenes are stored in `config/scenes.json` (created on the first save). How values combine is defined in `project.md`, section "State resolution and transitions". In short:

- A scene only changes the attributes it lists; everything else keeps its current value. Inside a scene, group entries apply in the listed order and fixture entries win last.
- Recalling a scene fades its selected attributes from current values. All controls remain manually adjustable without Release; manual changes never rewrite saved scenes. Re-pressing a scene recalls it again, never turns unrelated fixtures off.
- Master (0–100 %) scales intensity at output only; blackout is immediate. Neither changes live values or stored scenes, and fades continue underneath a blackout.
- At startup everything is dark and no scene is recalled.

Every save writes the file atomically and keeps the previous version as `config/scenes.json.bak`. If `scenes.json` is damaged or refers to fixtures/groups no longer in the patch, the server refuses to start and names the problem; fix the file or the patch, or copy `scenes.json.bak` back over `scenes.json`. Fades are calculated `fade_rate_hz` times per second (optional in `config/app.json`, default 40, range 1–100).

Example: save the current look as a 2-second scene, then recall it:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8010/api/scenes/capture \
  -H 'Content-Type: application/json' -d '{"name":"Red","fade_seconds":2}'
curl --fail-with-body -X POST http://127.0.0.1:8010/api/scenes/red/recall
```

## Moving heads (Milestone 4)

The patch (universe 5) is: LED bar in 6Ch mode at address 1-6, moving head 1 (Equinox Fusion Orbit MKII, 37ch mode) at address 7-43, moving head 2 at address 44-80, two Cameo Q-Spot 15 RGBW BK (`dj-spot-1`, `dj-spot-2`, 9CH mode) at address 81-89 and 90-98. **Set these modes and addresses on the fixtures' displays before using real output.** The heads' pan/tilt speed also depends on the HISP/LoSP menu setting (manual p. 10). Positioning needs data that must be measured at the club and entered by hand:

- `config/room.json`: room size, targets (`x`, `y`, `z` in metres), zones, legacy head assignments and independent `strip_assignments` with `include_zones`/`exclude_zones`. Existing legacy aims migrate without widening permissions. Convention: origin at the floor corner at the booth wall on the DJ's left, x right, y into the room, z up.
- `config/fixture-names.json`: fixture/group display names, saved immediately after renaming in the UI and restored at startup. When using `--patch`, this file lives alongside the selected patch file. Stable IDs and scene/MIDI references do not change.
- `config/heads.json`: per head `position` (x, y, z), `orientation` (`ceiling`), movement `limits`, a safe `rest` pan/tilt, and the `calibration` (filled in by the app).

Calibration in the UI ("Moving-head positioning"): deliberately enable the temporary calibration override, steer with Fine aim until the beam hits a measured target, record it, repeat for at least 3 well-spread targets (prefer 4-6), then fit and save. Disable the override afterwards. Missing geometry/calibration otherwise keeps the head dark. Check the universe and fixture limits before supervised real calibration; blackout still extinguishes light. Native strobe is available as `strobe` and `ring_strobe`; `max_strobe` in `config/app.json` caps it.

Zones: define measured surface rectangles in **Setup**, then choose the aim and multiple area permissions in **Positions** or the APC40 target/zone menu. The union of included zones permits light, explicit exclusions win, and everything undefined is dark. Zone aiming begins at the geometric centre; spot controls move within the selected rectangle. Dark travel is allowed. A selected static target is an exception, not permission for its travel path or nearby movement FX. Legacy zone `mode` remains in configuration for compatibility; permissions are now per strip, not a global property of a zone.

API: `GET/PUT /api/room`, `PUT/DELETE /api/zones/{id}`, `DELETE /api/targets/{id}`, `PUT /api/positions/{head or group}/assignment`, `POST /api/positions/{head or group}/spot`, `GET /api/heads`, `PUT /api/heads/{id}`, `POST /api/heads/{id}/aim` (`{"target": "disco-ball", "fade_seconds": 2}`), `POST /api/heads/{id}/rest`, `POST /api/heads/{id}/calibration/fit`.

## API

- `GET /api/status` — simulation/output state and configured universes
- `GET /api/fixture-definitions` — loaded fixture definitions
- `GET /api/fixtures` — patched fixtures with their attributes and current logical state
- `POST /api/fixtures/{id}/state` — change some attributes, e.g. `{"values": {"red": 1.0}}`; unknown attributes or values outside 0.0–1.0 are rejected, disabled fixtures return 409
- `GET /api/groups` — groups and their fixtures
- `POST /api/groups/{group}/state` — like the fixture command, for every enabled member that has the attribute
- `GET /api/scenes` — stored scenes
- `POST /api/scenes` — create a scene, e.g. `{"name": "Warm", "fade_seconds": 3, "groups": {"bars": {"intensity": 1.0, "red": 1.0}}, "fixtures": {}}`
- `POST /api/scenes/capture` — store the current values of all enabled fixtures, `{"name": "...", "fade_seconds": 0}`
- `PUT /api/scenes/{id}` — replace a scene's content; `POST /api/scenes/{id}/duplicate` (optional `{"name": "..."}`); `DELETE /api/scenes/{id}`
- `POST /api/scenes/{id}/recall` — recall with the stored fade, or override it once with `{"fade_seconds": 0}`
- `POST /api/output/master` — `{"level": 0.0–1.0}`
- `GET /api/universes/{universe}` — complete simulated 512-slot buffer
- `POST /api/commands/set-channel` — diagnostic: set one 1-based DMX address; accepts universe 0–7 (the Botex output labels) and value 0–255
- `POST /api/output/blackout` — set `{"active": true}` or `{"active": false}`
- `GET /api/apc40` — on-screen APC40 state (strips, pads, knobs, strobe rate, last hardware input)
- `POST /api/apc40/event` — act like the APC40, e.g. `{"kind": "note", "channel": 1, "number": 80, "value": 127}`
- `PUT /api/apc40/pads/{pad}` — give a scene pad (0–39, top-left = 0) a scene and colour, `{"scene": "test-scene", "colour": "orange"}`; `DELETE /api/apc40/pads/{pad}` clears it
- `GET /api/preview` — `{"active": true|false}`; `POST /api/preview` with `{"active": true}` starts preview, `{"active": false}` = Go (sends the preview live)
- `GET /api/map` — 2D map: room size and every fixture's map position, live colour and preview colour
- `PUT /api/map/fixtures/{id}` with `{"x": 1.5, "y": 0.4}` (metres, booth side = y 0) places a fixture; `DELETE` removes it
- `GET /docs` — local interactive API documentation

The app rejects invalid universe numbers, channel addresses, DMX values, and unknown configuration fields. The main configuration is `config/app.json`; `python -m lichtwerk serve` also accepts `--fixtures <folder>`, `--patch <file>`, `--scenes <file>`, `--room <file>`, `--heads <file>`, `--midi <file>`, `--apc40 <file>` and `--map <file>` (default `config/map.json`, created when you place the first fixture on the map). `max_strobe` (0.0–1.0, default 1.0) in `config/app.json` caps native strobe.

## Tests

```sh
python -m pytest
```

Tests use only the simulated adapter and do not require OLA, the Botex, Resolume, or fixtures.

## Logging

The server writes startup and unexpected-error diagnostics to the console and to the rotating local log `logs/lichtwerk.log`. Logs are local runtime data, ignored by Git, and are not uploaded or sent to Copilot.

## OLA and hardware status (Milestone 2)

OLA 0.10.9 is installed via Homebrew (native Apple silicon) and has been verified on this Mac:
- `olad` starts and the Art-Net plugin loads.
- `~/.ola/ola-artnet.conf` sets `ip = 2.0.0.1`, so Art-Net uses the Botex interface (before this it bound to 192.168.10.170). Net and subnet are 0.
- Art-Net output port 0 is patched to OLA universe 5 and reports "ArtNet Universe 0:0:5", i.e. Art-Net universe 5. A supervised sweep showed that the LED bar responds to Art-Net universe 5: the Botex labels its outputs 0–7, and the app uses the same numbers (universe = Botex label = Art-Net universe = OLA universe).
- The unused network plugins (ShowNet, ESP Net, SandNet, Pathport, sACN, OSC, KiNET) are disabled in `~/.ola/`.
- The adapter's `/set_dmx` call was tested against the real `olad` on a harmless Dummy-device universe; nothing was sent to the network. `olad` rejects universes without a patched port ("Universe doesn't exist"), which LichtWerk reports as an output error.

A supervised raw Art-Net sweep has reached the LED bar on universe 5. Automated tests use mocks and a local stub server only.

Start OLA when you want to use it: `/opt/homebrew/opt/ola/bin/olad --no-http-quit` (stop it with Ctrl+C).

Real output is disabled in the default configuration:
1. `config/app.json` must set `"output_mode": "ola"`, `"physical_output_enabled": true` and an `"ola": {"allowed_universes": [5]}` section. Only those universes can ever be sent.
2. With that explicit configuration, backend startup enables output automatically in blackout. Heads move to their saved rest positions; native speed defaults to fastest. Pump/fan start at zero. Browser reload and controller reconnect do not reset the show.
3. An OLA failure disables delivery until the operator uses **Retry output**. Inspect the error before retrying. Lighting blackout preserves head motion and haze values; it is not an output disconnect or hazer shutdown.

Note: OLA replaces a whole universe per update, including unused channels. Do not share an allowed universe with Resolume. LichtWerk's current patch uses universe 5: lights at 1-98 and the Look Unique 2.1 hazer at 99-100.

Supervised test preflight (you do these; I cannot):
1. Done: Homebrew and OLA installed.
2. Done: Art-Net bound to 2.0.0.1 and OLA universe 5 patched (see above); you can check it in the olad web UI at http://127.0.0.1:9090.
3. Confirm modes and addresses in `project.md`, including the hazer's pump/fan at 99/100. Keep Resolume on its existing other universes.
4. Start `olad`, then LichtWerk with the intended configuration. Verify blackout is active, heads are at rest and haze pump is zero before releasing lighting blackout.
5. BLACKOUT extinguishes lights, not haze. Set haze pump to zero separately when required.

### Hazer operation

Track 8 fader controls pump; its top knob controls fan. The HAZE/RECORD ARM button temporarily drives both to maximum **live**, including during preview, and restores the underlying live values on release. Ordinary pump/fan changes wait for Go during preview. Lighting master and blackout do not affect haze.

The UI uses normalised output levels; these are not a measured physical flow curve. Verify zero/off and endpoint behaviour on the actual Unique 2.1. **Pump zero is not machine shutdown.** Follow the manual's OFF/cooldown procedure and keep power available for run-on. OLA/Botex may keep transmitting DMX after LichtWerk exits, so exiting the app does not guarantee DMX-loss cooldown.

## Operator UI (Milestone 6)

Open the UI (`npm run dev` in `frontend/`, then http://127.0.0.1:5173). The top bar (simulated/real, page tabs, active scene, master, blackout) is always visible. Pages: **Controller** (on-screen Xone:K2, see below), **Fixtures** (scenes, per-fixture sliders), **Positions** (aim and calibrate moving heads), **Setup** (room size, target coordinates, head mounting positions in meters), **Diagnostics** (output, patch, setup warnings, recent errors). If the backend stops, the UI shows a warning and reconnects by itself.

## MIDI controller (Milestone 7)

LichtWerk listens to a MIDI device whose name contains `XONE:K2` (`midi` section in `config/app.json`) and sends LED colours back to its buttons (Latching Layers must be OFF on the K2). Open the **MIDI** page in the UI: it shows the connection, a live monitor of incoming messages, and a **Learn** button per action (master, blackout, each fixture/group intensity, each scene). Press Learn, then move the fader or press the button. Bindings are saved in `config/midi.json`. Unplugging the controller never stops the lights; it reconnects when plugged in again.

**AKAI APC40 MKII:** whenever an APC40 MKII is plugged in (or already connected when LichtWerk starts), the app automatically switches it to *Alternate Ableton Live mode* with the introduction SysEx from the AKAI protocol document. This is needed because the APC40 starts in Generic mode after every power-up. The MIDI page shows "APC40 set to Alternate mode" when this worked. Requires `midi.enabled: true`; it works regardless of which controller is set in `midi.device` (the APC40 port is found by `midi.apc40_device`, default `APC40 mkII`).

The APC40 has a fixed logic (see `project.md`, section 6.3): track 1 LED bar, 2 heads group, 3–4 single heads, 5 DJ spots group, 6–7 single spots. Faders = intensity, top knobs = saturation, numbered buttons = group/single mode, SOLO = strobe, RECORD ARM = flash (hold), CLIP STOP = select a strip for colour pads and device knobs, MASTER button = blackout, CUE LEVEL = strobe rate. Without a selected strip the 40 pads launch scenes; which scene and colour each pad has is stored in `config/apc40.json` (created when you assign the first pad).

### Controller page

The **Controller** page has two tabs, **APC40 MKII** and **Xone:K2**. Until you pick one, it shows the APC40, or the K2 when only the K2 is connected. The on-screen APC40 works like the real one (click buttons and pads, drag knobs and faders); **ASSIGN SCENE PADS** → click a pad → choose scene and colour → Save. "Last hardware input" shows the last message from the real APC40.

**Preview mode:** press **PREVIEW** (top bar, the on-screen button, K2 LAYER or APC40 SESSION RECORD). Changes now only go into the preview; the page gets an orange frame. Press **GO** to send everything at once and return to live. Master, blackout, flash and strobe-hold always act live (marked with a red LIVE badge). The **Room map** in the side panel shows each fixture as a dot (left half = live, right half = preview); use **EDIT POSITIONS** to place the fixtures.

**RFC controller workflow (29 October 2026):**
- **COLOR** selects colour pads. **SCENE LAUNCH 1/2/3** selects Dimmer/Colour/Move; buttons 4/5 are unused. Each page has five presets per strip. Press a pad to start/stop it; TRACK SELECT chooses the FX editing strip. **STOP FX / STOP ALL CLIPS** stops that strip's selected beam/ring FX context, not its scene sequence. One effect per family/component runs on a fixture; starting another replaces the conflicting effect as a whole.
- Track FX indication blinks while FX run, including outside the FX page. Faster blinking indicates the FX editing strip.
- Only the two left device-knob columns are used: physical knobs **1, 2, 5, 6**. FX functions are Rate, Depth/Size, Spread, Softness where applicable; rate is shown in beats or seconds per cycle. Default tempo is 128 BPM; TAP adjusts it and METRONOME displays the beat (the physical TAP button has no LED).
- **SHIFT + TRACK SELECT** on a head/group opens target/zone pads in AIM mode. Keyboard Shift also works. First 24 pads are targets; last 16 are zones. UP/AIM selects a destination without changing permissions. DOWN/PERMISSIONS cycles each zone: not included -> included -> explicitly excluded -> not included, without moving heads. SHIFT + an existing pad edits it. Several zones can be included together.
- **Store current position** in the target dialog fills estimated x/y/z from the calibrated head's commanded beam intersection with the first room wall, floor or ceiling. Choose a head when operating the group, wait for real movement to settle, review the coordinates and press Save. This cannot locate an object inside the room; enter measured coordinates for those targets. Missing geometry/calibration produces an error, never guessed coordinates.
- **Edit/delete target/zone** in the same controller menu edits names/geometry without changing IDs. Delete asks for confirmation and removes the item from all assignments. Finish preview before capturing or changing definitions.
- Empty target/zone pads on the APC40 or display open the matching Add dialog (keep the browser's controller page open for hardware requests). During Move FX, every included zone pad is fully illuminated; normal indication returns when Move FX stops. This is pad feedback, not simultaneous beam coverage of every area.
- Included areas persist across backend restarts; heads still start at rest, not at the saved zone aim. A zone is not shown as the reached aim until the commanded positions match it. Starting Move FX deliberately recalls a reachable permitted base aim, so a rest position outside the included areas does not itself block FX launch.

### Editing FX presets

Open an FX family, choose its strip with TRACK SELECT, then click **Edit FX presets**
on screen. Select one of the five slots. Factory choices retain the original
Chase, Rainbow, Two colours, Circle and Figure 8 looks, including the old
phase-offset Mirror presets.

- **Save** changes the next launch, not an already running FX. **Save & Apply now**
  saves and relaunches deliberately in the current Live/Preview context.
- **Store current FX** saves the selected running slot's knob tuning.
- **Reset to factory** removes its personal override; it does not stop or change
  the current run. Personal presets survive backend restarts, but FX do not start
  automatically. Names/scene definitions are not changed.
- Dimmer ranges are percentages of current base intensity: 20%-100% of an 80%
  fader means 16%-80% output before master. Zero base remains dark.
- Follow-base colour responds to scene/manual colours. Fixed palettes ignore
  base RGB while active and zero main white; intensity still works normally.
- More options expose fixture order, Forward/Reverse, Odd/Even/Alternating,
  Spread, Seconds timing and movement reflection. A reflected pattern is not the
  same as a half-cycle phase offset. Spread needs multiple participants.
- In Move, choose **Target Tour**, add/reorder named targets and set travel/hold
  durations in the selected units. Pattern None holds each exact target; Circle
  or Figure 8 runs only during holds, not while travelling. Tour and pattern are
  saved as one Move preset, so colour/dimmer FX or a scene sequence can run beside it.
  During a tour device knob 1 is labelled Travel and changes travel duration;
  pattern cycle timing remains the editor's separate Rate setting.
  A pattern needs permitted areas; selecting a target never permits its travel
  path. Forbidden beam output remains dark, even under FLASH.
- Stop reveals the latest underlying base state, not values captured at FX launch.
  For ordinary movement launch this is the deliberately prepared base aim.
- Fast cycles can have too few output frames to reproduce reliably; heed the
  timing warning and use the native strobe controls for strobing.

### Fusion Orbit ring controls

On compatible moving-head strips, **RING** (physical A/B) switches between beam
and ring editing without changing output. The track fader and FLASH always
remain beam controls. In ring context the top knob is **Ring intensity**;
returning to beam restores the separate saturation value/LED indication.
STROBE controls the ring; CUE changes the shared strobe rate.

Press COLOR with RING selected. The first pad row selects **ALL** or a numbered
section; the compact palette below applies to that selection. Colours and
intensity are independent. In 37ch mode the six sections can differ; 16ch mode
offers uniform ring control only. Section numbers follow the manual, not an
assumed clockwise physical order.

Dimmer and Colour FX use the sections as participants, allowing spread/chases
around and across rings. Ring presets and runs are independent of beam presets
and runs; the FX editor identifies the context. STOP FX stops the selected
context; Move remains fixture-level. Group/single switching remembers both
contexts. Scenes and sequence authoring offer Uniform ring / Individual sections
before showing the detailed controls.

Rings stay lit independently of beam targets/zones and dark movement transfers.
Global master/blackout still apply to both. Ring changes and FX stage in Preview
until Go. Both room monitors draw ring output around each moving-head dot,
including individual section colours, dimmer/colour FX and independent ring
strobe (using the existing display blink-rate cap). Dark sections retain a dim
outline. The layout is schematic, not physical section orientation.
Fixture-native ring macros are not used; verify their disabled state
and physical section order with the club checklist before relying on hardware.

### Scene/sequence authoring and live operation

- Empty scene/sequence pads open authoring; SHIFT + an existing pad edits its saved
  definition without recalling it. Detailed authoring requires the browser open.
  Scene capture saves selected logical base values, not master/blackout/FLASH/FX;
  including haze pump/fan is an explicit choice.
- LEFT selects Scenes, RIGHT selects Sequences. Both pages have independent 40-pad
  assignments. Repeated static scene presses recall; running sequence presses pause/resume.
  PLAY pauses/resumes, SHIFT + PLAY restarts, RECORD stops sequence playback.
  STOP ALL CLIPS keeps its selected-strip FX-stop behavior.
- Sequences arrange existing scenes with an incoming fade and a separate hold after
  that fade. Choose seconds or beats for the sequence, one-shot or loop. Stop freezes
  current values; it does not switch lights or haze off. Unrelated static scenes can
  coexist; recalling overlapping scene attributes stops the sequence.
- Configure **Live controls** inside a sequence to preserve selected fixture attributes
  (for example moving-head colour) through all its steps. They start with existing values
  and remain adjustable using normal controls. Other manual changes work too, but the
  next step may overwrite them. Direct scenes still recall their saved attributes:
  no sequence policy leaks into unrelated scene recalls. FX and safety overrides
  retain their normal priority.
- The scene editor follows controller track order. Choose group or individual control
  per parameter family; for example group colour with separate head intensities.
  Levels use percentages and invalid fields show feedback before Save. New fades and
  sequences default to beats; existing seconds settings remain unchanged until edited.
  A scene's aimed zone is permitted automatically; expand **Include other zones?**
  only for additional permissions. Existing hidden zone-spot values are preserved.
- Editing definitions affects future launches. Apply now is an explicit recall/restart,
  not an automatic side effect of Save. Referenced scenes and positioning definitions
  must be removed from their users before deletion.
  Stop a sequence before changing its referenced destination coordinates/zone geometry
  or room dimensions (including while paused or holding its final state). Unrelated
  geometry, display names and runtime safety permissions remain editable.
- Preview can run an animated sequence independently of live playback. Compare Live
  on the left with Preview on the right; Go adopts the current preview phase.
  Outside preview mode its room and neutral fixture markers remain visible without
  live output. Both monitors sit in the controller's right pane, which scrolls when
  needed. Gray haze bubbles grow as they travel and fade away: fan controls speed
  (zero pauses travel), pump controls brightness (100% is brightest). This illustrates
  commanded haze, not measured room fog; reduced-motion settings pause bubble travel.
- The target/zone menu always opens in **Aim** mode: zone presses deliberately aim
  without changing permissions. UP selects Aim; DOWN selects **Permissions**, where zone
  presses cycle not included -> included -> explicitly excluded -> not included without
  movement. Dim neutral/cyan/red pads distinguish these permission states. SHIFT + pad
  edits instead of excluding. Permission changes may darken output or change an active
  Move FX tour; they never issue a separate zone-centre recall.

For concrete club checks and cleanup steps, use [testing.rfc](testing.rfc).
- Undefined areas are dark. Static selected targets are exceptions, but not their travel paths or movement FX around forbidden targets. Group and individual strips retain independent settings. Missing calibration/geometry keeps heads dark; use the explicit setup override only for supervised calibration, then disable it.
- With multiple included zones and no selected static target, current-aim Move FX cycle through all included zones: a complete pattern in each rectangle, then a short dark move to the next. Size scales the pattern inside each zone; Rate is beats or seconds per pattern and transfer takes another quarter-pattern duration. The last illuminated zone pad is only the base aim, not a restriction on the tour. Named Target Tours instead use their configured Travel/Hold durations. Stopping FX restores the current underlying base aim. Preview uses its staged zone set until Go.
- The right pane puts PREVIEW/GO and the map above tempo. Beam triangles are illustrative top-view projections of commanded geometry, not physical measurements. Fixture/group names can be edited without changing IDs.

The Xone:K2 tab shows the Xone:K2 as it is assigned: column titles (Master, LED bar 1, Moving heads, DJ spots), what every knob, button and fader does, live values and the button LED colours. You can operate everything with the mouse: drag knobs and faders up/down (or scroll), hold buttons. The on-screen controller and the real K2 do exactly the same thing. **ASSIGN CONTROLS** → tap a control → choose action and fixtures/scene → Save. The MIDI channel used by the page is `midi.channel` in `config/app.json` (default 15). The full first assignment is listed in `project.md` (Milestone 7 section).

If `pip install` of `python-rtmidi` fails on macOS with a linker error, build with the older SDK: `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk .venv/bin/pip install -e ".[dev]"`.
