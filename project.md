# LichtWerk – club lighting control for schall.werk

This file is the source of truth for **what** LichtWerk does and **how it is built**. Setup and usage commands are in [README.md](README.md). The previous long version of this file, including all question-and-answer rounds, is archived in [docs/archive/project-before-cleanup-2026-10-26.md](docs/archive/project-before-cleanup-2026-10-26.md).

## Contents

1. [Status at a glance](#1-status-at-a-glance)
2. [Purpose and principles](#2-purpose-and-principles)
3. [Installed hardware](#3-installed-hardware)
4. [Architecture](#4-architecture)
5. [Behaviour](#5-behaviour)
6. [Controllers](#6-controllers)
7. [User interface](#7-user-interface)
8. [Safety, persistence and testing](#8-safety-persistence-and-testing)
9. [Milestones](#9-milestones)
10. [Open items and club checklist](#10-open-items-and-club-checklist)
11. [Out of scope](#11-out-of-scope)
12. [Rules for GitHub Copilot](#12-rules-for-github-copilot)

---

## 1. Status at a glance

| # | Milestone | Status |
|---|---|---|
| 0 | Environment and foundation | Done |
| 1 | Simulated output | Done |
| 2 | OLA and single-channel hardware test | Done (LED bar reached on universe 5) |
| 3 | Fixture definitions and patch | Done; modes and addresses verified at the club |
| 4 | Moving-head positioning | Done; room measured, both heads calibrated at the club |
| 5 | Scenes, fades, master, blackout | Done in simulation |
| 6 | Operator UI | Done |
| 7 | Xone:K2 MIDI input, LED feedback, on-screen controller | Done; partly verified on the real K2 |
| 11 | Zones (include/exclude) and collapsible assign pane | Done; zones active since calibration |
| 12 | AKAI APC40 MKII support | Done in simulation; APC40 connection and Alternate mode confirmed, full check at the club |
| 13 | Preview mode and 2D map | Done in simulation; LEDs to check at the club |
| 8 | MVP commissioning at the club | Done (27 Oct 2026); K2 hardware check and strobe-rate measurements open (section 10) |
| 9 | OSC / Resolume (optional) | Later |
| 10 | Effects (chase, colour, movement, BPM) | Done in simulation; hardware test at the club open (section 10) |

Development runs on the show Mac (macOS 26 "Tahoe", Python 3.13 in `.venv`, OLA 0.10.9). macOS only.

## 2. Purpose and principles

LichtWerk replaces Wolfmix for the conventional DMX fixtures of the schall.werk club. Resolume stays in charge of the pixel bars and video and is not touched. The goal is precise, predictable control – especially *where* the moving heads point – without building a full lighting console.

Principles:

1. **Local-first.** Runs on one Mac, no cloud, API bound to localhost.
2. **The app owns the lighting state.** Controllers (MIDI) and the UI only send commands.
3. **Hardware behind adapters.** OLA, MIDI and OSC code lives in adapter modules; domain logic does not depend on them. Every adapter has a mock for tests.
4. **Simulation by default.** Tests never send real DMX. Explicitly configured real output starts in blackout, with heads at rest and haze pump at zero; there is no separate ARM step. Output failures require manual Retry.
5. **Never guess hardware.** Fixture channel layouts, DMX modes, addresses, MIDI numbers and OSC paths come from manuals or the owner. Unknown values stay `null` and are reported.
6. **Configuration over code.** Patch, room, scenes and mappings are human-readable JSON in `config/`.
7. **Fail safe.** Invalid configuration stops startup with a clear message; errors in MIDI/UI never stop DMX output.
8. **Small steps.** One working slice at a time, with tests.

## 3. Installed hardware

### Network and DMX

| Item | Value |
|---|---|
| Interface | Botex NETcon 8-3, IP 2.0.0.10, outputs labelled 0–7 |
| Mac adapter | AX88179B, IP 2.0.0.1, netmask 255.0.0.0 |
| Universe numbering | Botex label = Art-Net universe = OLA universe (verified by sweep) |
| Universe used by LichtWerk | 5 (only universe allowed in `config/app.json`) |
| Resolume | Uses other universes; must not be disturbed |

### Patch (universe 5)

| Fixture id | Model | Mode | Addresses | Group |
|---|---|---|---|---|
| `led-bar-1` | Cameo Thunder Wash 100 RGB | 6Ch | 1–6 | – |
| `moving-head-1` | Equinox Fusion Orbit MKII | 37ch | 7–43 | `moving-heads` |
| `moving-head-2` | Equinox Fusion Orbit MKII | 37ch | 44–80 | `moving-heads` |
| `dj-spot-1` | Cameo Q-Spot 15 RGBW BK | 9CH | 81–89 | `dj-spots` |
| `dj-spot-2` | Cameo Q-Spot 15 RGBW BK | 9CH | 90–98 | `dj-spots` |
| `hazer-1` | Look Unique 2.1 | 2ch | 99–100 | – |

Modes and addresses must be set on the fixtures' displays at the club. Manuals are in `docs/manuals/`; definitions in `config/fixtures/` name the manual page they come from.

### Room and moving heads

- Both heads hang from the ceiling above the dance floor, left and right of the DJ, on the booth side.
- Rest position: pointing at the ceiling (exact pan/tilt to be found at the club).
- Named targets: Absorber #1–#4, Bass trap #1–#2, Disco ball, Centre of dance floor, and the four dance-floor corners.
- Room size, target coordinates and head positions are **not measured yet** (`null` in `config/room.json` and `config/heads.json`).

### Controllers

| Controller | Facts |
|---|---|
| Allen & Heath Xone:K2 | MIDI channel 15, **Latching Layers OFF** (needed for amber/green LEDs). Manual `docs/manuals/manual-xone-k2.pdf`. |
| AKAI APC40 MKII | Port name `APC40 mkII` (seen on the Mac). Protocol in `docs/manuals/communication_protocol_akai_apc40_mkii.pdf`. |

## 4. Architecture

```
Web UI ──HTTP──► FastAPI routes ──► services / lighting engine ──► DMX output adapter ──► OLA ──Art-Net──► Botex ──► fixtures
MIDI controllers ──► MIDI adapter ──► controller logic ──► same services
                                    ◄── LED feedback ◄──
```

- **Backend:** Python 3.13, FastAPI, Pydantic, mido + python-rtmidi (MIDI). Code in `backend/lichtwerk/`, tests in `backend/tests/`.
- **Frontend:** React + TypeScript + Vite in `frontend/`. It only displays and sends commands; it polls the API (no WebSocket yet).
- **Output:** `output.py` has the simulated adapter and the OLA HTTP adapter. Only allowed universes can be armed.
- **Configuration files** (JSON, validated at startup, saved atomically with a `.bak`):

| File | Content |
|---|---|
| `config/app.json` | Output mode, allowed universes, MIDI settings, `max_strobe` |
| `config/fixtures/*.json` | Fixture definitions (modes, channels, ranges) |
| `config/patch.json` | Patched fixtures and groups |
| `config/room.json` | Room size, targets, zones |
| `config/heads.json` | Per-head mounting, calibration, limits, rest |
| `config/scenes.json` | Scenes |
| `config/midi.json` | Xone:K2 bindings |

Main backend modules: `fixtures.py`/`patch.py` (definitions and patch), `fixture_service.py` (logical state → DMX), `transitions.py` (fades), `scenes.py`/`scene_service.py`, `room.py`/`positioning.py`/`position_service.py` (room, aiming, calibration), `midi_*.py` + `xone_k2.py` + `colour.py` + `controller_api.py` (controller), `diagnostics.py`.

## 5. Behaviour

### 5.1 Fixtures and logical state

- A **fixture definition** describes a model and its DMX modes; a **patched fixture** says which definition/mode, universe, address and groups.
- The app stores logical values (0.0–1.0) per attribute: `intensity`, `red`/`green`/`blue`/`white`, `strobe`, `pan`/`tilt`, `speed`, ring attributes. The fixture engine converts them to DMX (8/16-bit, ranges from the definition). Fixtures without a dimmer get a virtual dimmer.
- The patch is validated: unknown definitions/modes, address collisions, universe overflow, unconfigured universes.

### 5.2 State, scenes and fades

1. **Startup:** blackout on, master 100 %, no scene recalled, heads at rest with fastest native speed, haze pump/fan zero. Browser reload and controller reconnect do not reset a running show.
2. **Latest takes precedence per attribute.** Scenes are partial one-time recalls: attributes a scene does not mention keep their value. All parameters remain manually adjustable without Release. A sequence may write its controlled parameters again at the next step; its configured Live controls are never overwritten by its scene steps.
3. **Inside a scene:** groups apply in listed order, then single fixtures.
4. **Fades** are linear on a monotonic clock, start from the current value, and only replace fades of the attributes they touch. Direct commands are immediate.
5. **Master** (0–100 %) scales lighting intensity at output time. **Blackout** zeroes lighting output, except pan, tilt and movement speed: heads go dark but stay where they point. Haze pump/fan are independent of both master and blackout. Neither changes stored scenes or live state.
6. **Stored scenes** only change by explicit save/delete. Recall never modifies them. Capture stores live values without master/blackout.
7. Pressing a scene again recalls it again; it never fades unrelated fixtures off or restores older values.
8. **Effects** (Milestone 10) modulate the output on top of the live state; they never change live state or stored scenes, and scenes do not store or stop effects. Output order: live state → effects → flash/holds → master → strobe cap → pan/tilt limits → beam guard → blackout.

**Approved FX revision (29 October 2026):**
- APC40 SCENE LAUNCH 1/2/3 select Dimmer/Colour/Move families; 4/5 are unused. Each family has exactly five preset slots per strip. All 25 original factory definitions remain available in the screen editor. Starting an FX replaces the active conflicting family as a whole; compatible dimmer, colour and movement combine. Inactive group/single effects remain remembered.
- Dimmer uses relative intensity factors, never overriding the base/fader ceiling. Named sine, triangle, square, saw-up/down and one-by-one generators extend legacy Pulse/Softness; maximum and depth control its range.
- Cycles use shared beats or optional seconds. Beat starts join the global grid; seconds starts are launch-relative and seconds-rate edits preserve phase. Spread, explicit fixture ordering, reverse, Odd/Even/Alternating selection and local movement reflection are configurable. No external clock receiver or software strobe fallback is introduced.
- Colour supports existing base-relative Rainbow/Two colours or an explicit 2-8-colour RGB palette with Step/Smooth transitions. Fixed palettes control main RGB and zero main white while active; base intensity and ring channels remain independent. Stop restores current underlying state.
- Move supports current aim/included-zone touring or ordered named Target Tour, optionally Circle/Figure 8 during holds. No pattern runs during target transfers. Pure target holds may use the exact stationary-target exception; patterns require permitted surrounding areas, and all travel respects normal beam blanking. Tour coordinates and room size cannot change while referenced by a running/suspended tour. Saved preset target references prevent deletion.
- Personal slots persist atomically in `config/fx-presets.json` (beside the selected patch with CLI `--patch`). Save changes the next run; Apply now explicitly saves/relaunches in Live or Preview. Store current FX saves knob tuning; Reset removes the override, not a running snapshot. Startup does not restart FX.
- Output remains on the existing central tick. Rates with fewer than four configured output frames per cycle receive a fidelity warning; no new 1/8-beat choice is exposed. Calibration, limits, exclusions, dark transfers under FLASH, master/blackout and Preview/Go remain authoritative.

**Approved Fusion Orbit ring workflow (30 October 2026):**
- The six RGB ring sections in verified 37ch mode are independently controllable logical emitters, not additional patched fixtures or controller tracks. Existing uniform ring colours remain compatible. The 16ch mode remains uniform-only. Native colour/program macros remain disabled; the assumed program-off value still requires club verification.
- A/B becomes RING on compatible APC40 strips. It selects a non-destructive beam/ring editing context per strip, including group/single operation; toggling does not change output or stop FX. Track fader and FLASH remain beam-only.
- In ring context the top knob controls shared ring intensity, restoring its independent remembered value and LED indication when switching back and forth with beam saturation. STROBE addresses the ring only; CUE rate remains shared.
- Ring COLOR offers ALL or an individual section before a compact colour palette. Default is ALL. Colour selection never changes intensity. Physical section numbering/orientation must be verified, not assumed.
- Dimmer/Colour FX operate on ordered ring sections (head 1 sections 1-6, then head 2 sections 1-6 by default), with independent beam/ring family ownership and five persistent slots per context/strip. Segment dimming scales RGB under the shared ring-intensity ceiling. Move remains fixture-level. STOP FX stops the selected ring context or existing beam context without cross-stopping.
- Ring output is independent of beam target/zone restrictions, missing beam calibration and mandatory dark movement transfers. This explicitly supersedes earlier shared beam/ring blanking. Master and global blackout still affect both; no beam protection is weakened.
- Scene/sequence authoring asks Uniform ring or Individual sections before displaying section controls. Merely choosing an editor view does not convert or overwrite saved colours; edits/conversion are explicit. Ring edits/FX retain existing Preview/Go and non-destructive scene semantics.
- Compact controller layout omits persistent explanatory paragraphs and the haze simulation legend, while preserving safety/status indications and control labels.
- Live/Preview monitors draw independent ring output around each moving-head beam dot: six coloured sections in 37ch mode, a uniform ring in 16ch mode. Ring intensity, FX, master and ring strobe are reflected separately from beam output; Live obeys blackout and Preview follows the existing rehearsal rules. Section layout is schematic, not a claim about physical orientation.

**Approved scene/sequence extension (29 October 2026):**
- Scene authoring uses columns in controller track order with explicit participation. Each parameter family uses the group or its individual fixtures, never both in newly authored settings; mixed group colour/individual intensity is supported. Existing overlapping entries are preserved until explicitly resolved. Unselected fields remain unchanged. Editing opens saved values without recall; explicit capture fills selected fields from logical base state. Haze pump/fan is opt-in. Master, blackout, held overrides and FX modulation are never captured.
- Per-head positioning is unchanged, named target, named zone/spot, or raw pan/tilt, never competing modes. Saved named references follow calibrated geometry. A scene's saved zone selections replace prior permissions (multiple includes/excludes supported); target recalls retain only the stationary endpoint exception. Active strip feedback must match the effective assignment. No calibration, limit or beam-safety bypass.
- Live controls are stored per sequence as fixture/attribute selections, with group choices expanded to members. They preserve current values at launch and across steps, and use normal manual controls; no initial value or separate application-wide mask is required. They do not suppress unrelated direct scenes. FX and holds retain their existing priority. Preview playback uses its own live-control policy. Edits affect subsequent launches; Apply now explicitly recalls/restarts.
- Sequences reference ordered existing scenes, allow repeats, incoming fade inheritance/override and hold-after-fade per step, and one-shot/loop. One sequence runs at a time; unrelated static scenes may coexist, but a manual scene overlapping any of its controlled attributes stops it before recall.
- Sequence timing mode is seconds or beats for the entire sequence. Beat choices: 1/2, 1, 2, 4, 8, 16, 32. Inherited scene fades convert upward at launch at current BPM (clamped to 32; zero remains a cut). BPM changes preserve progress in beats and affect duration immediately.
- New scene fades and sequences default to beats, with Cut (zero) available for fades. Scene fades can explicitly use seconds; legacy scenes/sequences keep their stored seconds semantics until edited. Standalone beat fades follow BPM without resetting progress. Scene fields show operator units (percentages for normalized levels), and invalid input receives immediate field feedback before Save.
- Pause freezes sequence timing/fades; Resume continues; Stop freezes commanded values and releases sequence control, without blackout; Restart begins step 1. One-shot holds final values. Blackout does not pause playback, movement or FX. Output delivery failure pauses playback until deliberate resume.
- Playback uses a launch snapshot, so editing definitions never silently changes a running show. Referenced scenes/targets/zones cannot be deleted without removing/repairing references. Definitions are human-readable, validated, atomically saved; playback never resumes automatically at backend startup.
- Stop a positioned sequence before editing its destination geometry or room dimensions; this also applies while paused or holding final values. This preserves named-destination snapshot semantics without freezing runtime safety masks. Unrelated geometry and display-name edits remain available.
- Preview rehearses continuously beside the live show (Live left / Preview right). Go promotes its current state, fade and timeline phase, not step 1. Master/blackout/holds still act live.
- Preview's room and neutral fixture markers remain visible outside preview mode, never mirroring live output. Staged output is displayed only during Preview. Monitor headers and canvases align horizontally; both monitors sit in the APC40 right pane so pads and tracks use the full controller height. The right pane scrolls when necessary.
- Haze monitors show an explicitly labelled commanded-haze illustration, not a physical fog prediction: small gray bubbles travel from the hazer toward the room centre, grow and fade away at the end. Fan controls travel speed (zero pauses travel); pump controls brightness, reaching maximum at 100%. Live and preview use their respective values, unaffected by lighting master/blackout; reduced-motion settings pause bubble travel.
- Scene zone authoring automatically permits the aimed zone. Additional included/excluded zones are optional under "Include other zones?". Exclusion overlap is warned about, not bypassed. Named zone spot controls are hidden for now; existing saved spot values are preserved.

### 5.3 Strobe

Strobe uses the fixtures' **own strobe channel**, never software flashing (DMX/network timing is too uneven for a steady frequency).

- Logical 0 = off; above 0 maps into the fixture's documented strobe range (`active_min`–`active_max`).
- Not scaled by master; zeroed by blackout; capped by `max_strobe`.
- Known rates: LED bar 0–30 Hz, Q-Spot <1–20 Hz. Equinox: no Hz in the manual. All scales are treated as linear until measured.

### 5.4 Moving-head positioning

**Coordinates:** meters. Origin = floor corner at the DJ booth wall on the DJ's left. x = to the DJ's right, y = away from the booth across the dance floor, z = up. Confirmed at the club; room 5.60 × 5.55 × 2.30 m.

**Calibration:** on the Positions page, steer a head with **Fine aim** (pan/tilt nudges of 10°, 1°, 0.2° or arrow keys) until the beam centre is on a known target, press “rec”, repeat for ≥ 3 targets (4–6 spread around the head is better), fit. Targets almost straight below a head say little about pan; the fit weights each target by how far it is off the head's axis. The fit is accepted when the worst error is under 3°. Per-head limits clamp pan/tilt. **Rest position:** steer the head with Fine aim to a safe spot (e.g. beam to the ceiling), press **Save as rest**; **Rest** moves it back there, and heads start there at startup.

**Targets vs. zones** – two different things:

| | Target | Zone |
|---|---|---|
| What | A named **point** (x, y, z) | A named **rectangle** on the floor, ceiling or a wall |
| Effect | Head points exactly there (static exception to area rules) | Per-strip included areas permit light; all other areas are dark. |

**Zone definition** (Milestone 11):

- Each zone has a name, a surface, one corner and a size. Legacy `mode` is retained for configuration compatibility; operational inclusion/exclusion is per strip.
- Surfaces and their axes:

| Surface | Plane | "Length" runs along | "Width" runs along |
|---|---|---|---|
| Floor | z = 0 | x (left → right) | y (away from booth) |
| Ceiling | z = room height | x | y |
| Booth wall | y = 0 | x | z (upward) |
| Far wall | y = room depth | x | z |
| Left wall | x = 0 | y | z |
| Right wall | x = room width | y | z |

- The corner is the one closest to the origin, entered as room coordinates; length and width are positive.

**Assignment and permissions** (RFCs 8/11, 29 October 2026):

- Each strip keeps its own aim and multiple permitted/excluded zones; group/single switching activates that strip's settings without rewriting the other strips. A group menu shows its own settings, not the individuals' previous settings.
- Light is permitted in the union of included zones minus explicit exclusions. Undefined areas are dark; explicit exclusion wins on overlaps. The DJ booth has no hard-coded special status.
- A selected static target is an exception, even outside permitted areas. This does not permit the travel path or nearby angles. Movement FX around forbidden targets are unavailable; FX, scenes, manual moves and FLASH otherwise obey the area mask.
- Position pads have Aim and Permissions modes; every menu entry defaults to Aim. Aim recalls a zone's centre without changing permissions (excluded destinations remain dark). Permissions cycles not included -> included -> explicitly excluded -> not included, without a movement command. SHIFT + an existing pad opens its edit/delete dialog. Target selection does not erase zone permissions. Manual positioning or competing scene/aim commands clear a stale target exception; modulation around a base aim does not rewrite it.
- The controller marks every included area with a check mark and inset border, and lists all included areas. The arrow/outer highlight denotes only the current aiming point, not the complete permitted-area selection. Physical pads are dim cyan for included areas, bright cyan for a permitted current zone aim, dim red for explicit exclusions, dim neutral for not-included areas, and dark for empty slots.
- While Move FX runs on the displayed strip, all included, non-excluded zone pads are fully lit on both APC40 and display. Stopping Move FX restores the normal dim-included/bright-aim indication; this changes feedback only, not beam permission or coverage.
- **Multi-zone Move FX:** when two or more areas are included and no static target is selected, Circle/Figure 8 visits all included, non-excluded zones in room-list order, not just the illuminated aiming zone. Each zone gets one complete surface-relative pattern, scaled by Size within its rectangle, followed by a dark reposition lasting one quarter of that pattern's duration. Speed remains beats per pattern; Spread offsets fixture timing. Exclusions/undefined areas and mechanical limits remain enforced, including during FLASH. Single-zone and selected-target FX keep their existing aim-centred angular patterns. Runtime touring does not rewrite base aims, scenes or permissions; preview tours its staged areas until Go.
- Up to 24 targets and 16 zones, in stable list/pad order. Target coordinates remain x/y/z: calibration determines a beam ray, not distance to an object.
- **Controller target capture:** "Store current position" can fill x/y/z using the calibrated commanded beam's first intersection with the measured room box (wall, floor or ceiling). This explicitly assumes a room-surface target and supersedes the earlier manual-coordinate-only workflow; it does not infer distances to interior objects. A group requires choosing one head. Capture includes current rendered movement/limits, does not move fixtures or enable light, and requires valid measurements/calibration. Review the estimate and Save to persist it in the existing room JSON store (no database introduced).
- **Controller edit/delete:** the target/zone menu has an Edit/delete dialog. Editing preserves IDs, ordering and references; delete requires confirmation and cleans references from all head/strip assignments. Definition changes and current-position capture are blocked during preview. Calibration/command lag can make a captured point inaccurate; wait for the real fixture to settle and retain manual coordinate correction.
- Pressing an empty pad in the target/zone menu, on hardware or on screen, opens the add dialog in the browser: target for the top three rows, zone for the bottom two. A hardware request is delivered through controller-state polling and requires the controller page open. Preview blocks creation with an explicit instruction to finish preview first; no empty-pad press changes existing assignments.
- Committed settings persist immediately. Preview edits are staged until Go, never saved over the live startup configuration; room-definition edits that affect protection are blocked during preview.
- On backend restart, saved area permissions are restored but heads remain at their rest positions. Included-pad indication represents these saved permissions; the normal bright aim indicator is shown only when the commanded head positions actually match that aim. Launching Move FX validates a reachable permitted zone rather than requiring the rest position to lie inside one, then recalls a permitted base aim as part of the explicit launch. Missing calibration/geometry and forbidden selected-target exceptions still prevent launch.

**Beam guard**:

- At output time the app predicts the first room-surface beam intersection and blanks light outside permitted areas, after FX and holds/FLASH. Dark travel is allowed; mechanical limits always apply.
- With no permissions or missing room measurements/head calibration, the affected moving head stays dark with an explanation. Calibration requires an explicit, visible, temporary setup override, never an automatic bypass.
- This is a commanded-position model, not measured physical feedback. Motor lag, finite beam width and calibration error remain hardware-test limitations; it is not a certified physical safety boundary.

### 5.5 Preview mode (Milestones 12/13)

- **Live mode** (default): every change goes to the fixtures immediately.
- **Preview mode:** changes go into a preview state and are shown on screen and on the 2D map, but not sent.
- **Exceptions that always act live:** Master, Blackout, Flash. They are marked "LIVE" on screen while preview is on.
- **Go:** pressing the preview button again promotes the whole preview state and current animated sequence phase, preserving an in-flight sequence fade rather than restarting it, and returns to live mode.
- Toggle: APC40 SESSION RECORD (lit = preview), K2 LAYER (amber = preview, green = live), and a button on screen.
- *Implementation notes:* there is no "discard"; Go is the only way back to live. Scenes recalled in preview land in the preview; sequence playback and its fades advance independently of live playback. Flash (full intensity in the fixture's current colour, so a fixture with no colour stays dark) and strobe-hold act only on the output while held and never change the stored state; the on-screen faders keep showing the physical fader position, so they behave the same in live and preview. Master and blackout are always live. Compare Live and Preview side by side before Go.

## 6. Controllers

### 6.1 General

- Device names are configured in `config/app.json`; the adapter re-scans every 2 s, so unplug/replug recovers. Disconnects never stop DMX output; MIDI input is queued so it never blocks output.
- Both controllers can be connected. The UI shows the screen for the connected controller; APC40 if both or none are connected, with a tab to switch.
- On-screen controllers send exactly the same events as the hardware.

### 6.2 Xone:K2 (done)

- Freely assignable: bindings in `config/midi.json`, edited on the Controller page with "Assign controls" or with MIDI learn on the MIDI page.
- Actions: `master`, `intensity`, `hue`, `saturation`, `white`, `strobe`, `scene`, `blackout`, `flash` (hold), `strobe_hold` (hold). Targets: fixture, group or all.
- Top encoders send relative values (1 = +1, 127 = −1). Step = 7.5° hue or 1/64 level.
- LEDs (notes from manual pp. 17–18; red = note, amber = +36, green = +72): assigned button amber, active scene green, blackout red while on, flash/strobe-hold red while held.
- Default assignment (columns: 1 Master/all, 2 LED bar, 3 moving heads, 4 DJ spots):

| Control | Action |
|---|---|
| Top encoder | Colour (hue) |
| Encoder push | Flash (hold) |
| Pot 1 / Pot 2 / Pot 3 | Saturation / White (none for LED bar) / Strobe |
| Switch under pot 3 | Strobe hold |
| Faders | Master, LED bar, moving heads, DJ spots intensity |
| Matrix A, B | Test scenes; C–P free |
| EXIT SETUP | Blackout |
| LAYER | Preview on/off (Milestone 13) |

### 6.3 AKAI APC40 MKII (Milestone 12)

The APC40 has a **fixed operating logic** (below); only the scene pads are freely assignable. All numbers are from the protocol document (v1.2); MIDI channels below are 0-based as in the document.

**Startup:** on connect, send the introduction SysEx for *Alternate Ableton Live mode* (`F0 47 7F 29 60 00 04 42 <ver> <ver> <bugfix> F7`): all buttons momentary, the app controls every LED. *Implemented:* sent automatically on every connect (app start, re-plug), confirmed on the real APC40 (it answers with its fader positions); see `backend/lichtwerk/apc40.py`.

**Channel strips:**

The on-screen 2x2 strip buttons are numbered / A/B on the top row and STROBE / FLASH (HAZE on track 8) on the bottom row.

| Track | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | Master |
|---|---|---|---|---|---|---|---|---|---|
| Controls | LED bar | Heads group | Head 1 | Head 2 | DJ spots group | Spot 1 | Spot 2 | Hazer | Master |

| Control | Message | Function |
|---|---|---|
| Track fader | CC 7, channel = track | Intensity; track 8 = haze pump |
| Master fader | CC 14 | Master |
| Track knob (top row) | CC 48–55 | Saturation; track 8 = haze fan (master has none) |
| Track activator (numbered button) | note 50, channel = track | Group/single mode (see below) |
| Solo | note 49 | Strobe on/off (toggle) |
| Record arm | note 48 | Flash (hold); track 8 = live pump/fan maximum while held |
| Clip stop | note 52 | Select this channel for pads and device knobs |
| MASTER button | note 80 | Blackout (toggle, lit while on) |
| CUE LEVEL | CC 47, relative | Global strobe rate 1–20 Hz |
| SESSION RECORD | note 102 | Preview on/off (lit = preview) |
| Clip launch pads | notes 0–39 | Scenes, colours or effects (see below) |
| Device knobs 1, 2, 5, 6 | CC 16, 17, 20, 21 | Two left columns only: selected channel / effect functions |
| Scene launch 1–5 | notes 82–86 | Open/close the effects page of one effect type (Milestone 10) |
| Track select | note 51, channel = track | Choose FX editing strip; SHIFT + head strip opens target/zone menu |
| STOP ALL CLIPS | note 81 | Stop all FX owned by the selected strip, including suspended FX; preview-only while previewing |
| SHIFT | note 98 | Held modifier; keyboard Shift also supported on screen |
| METRONOME | note 90 | Beat-phase LED; TAP TEMPO itself has no LED |
| TAP TEMPO | note 99 | Tap the BPM |
| TEMPO knob | CC 13, relative | BPM ±0.1 per step |

**Group vs. single mode** (heads: tracks 2–4, spots: tracks 5–7):
- Group mode is the start state: the group's numbered button is lit, the single buttons are dark, the group strip controls both fixtures, single strips are ignored.
- Pressing a dark single button switches to single mode: single buttons lit, group button dark, each fixture follows its own strip. Pressing the group button switches back.
- The mode covers the whole strip (fader, saturation, flash, strobe, colour). After a switch, the current fader position of the newly active strip applies immediately (faders are not motorised, so the light can jump).
- LED bar (track 1) and hazer (track 8) are always active when patched.
- Every strip keeps its **own** set points (fader, saturation, colour, white, strobe, device knobs). Moving a group strip does not change the single strips' values and vice versa, so the on-screen values always match the physical (non-motorised) controls. On a mode switch, the newly active strips' stored set points are applied.

**Clip stop selection:** only one clip stop can be on (lit). Pressing the lit one again deselects. When a channel is selected:
- **Pads = colours** for that channel: 8 columns of hues (red, orange, yellow, green, cyan, blue, purple, magenta) × 5 rows from saturated to pastel; one pad is "white" (uses the W LED where present). Each pad lights in its colour. Moving-head rings are excluded.
- **Device knobs:** heads – 1 pan, 2 tilt, 5 native movement speed, 6 white; Q-Spots – 1 white, 2 fine hue; LED bar – 1 fine hue. Only the two left hardware columns are used; unused knobs have no effect.

**No channel selected:** LEFT/RIGHT select independent Scenes/Sequences 40-pad pages. Scene and sequence assignments/colours persist in `config/apc40.json`; existing scene pads retain their positions. Empty pads open authoring, SHIFT + existing pads opens Edit/Delete. Static scene pads always recall; running sequence pads pause/resume. PLAY pauses/resumes the sequence, SHIFT+PLAY restarts, RECORD stops. The most recently recalled scene pad blinks; playback status remains visible independently of the current pad page.

**Strobe rate:** CUE LEVEL sets one shared rate (1–20 Hz, 0.5 Hz per step, start 10 Hz). Each fixture converts Hz to its strobe range with a linear table (`min_hz`/`max_hz` on the strobe channel in the fixture definition: LED bar 0–30 Hz, Q-Spot 0.5–20 Hz; Equinox has no values yet and uses 1–20 Hz as a rough guess, to be measured).

**Clip stop LED:** blinks while its colour page is open; otherwise lit when the strip has a colour set.

**Effects page (Milestone 10, revised):** **Scene launch 1–3** select Dimmer, Colour and Move; 4/5 are unused. Pressing one opens that family on the pads (closes the colour page); another switches family; the shown family again, or any clip stop, closes the page.
- **Scene launch LEDs:** dim in the family colour, bright for the family shown on the pads, and **blinking while any strip runs that family**. Dark if no fixture can use it.
- **Pads:** each column is one strip (track 1–8), rows 1–5 are five persistent editable presets. Press to start, again to stop; the running preset blinks, the others are dim. Incompatible strips stay dark. Dimmer, Colour and Move combine; a conflicting family is replaced as a whole. The editor retains all 25 original factory looks.
- **Device knobs** change the running family of one strip: the strip whose pad was pressed last, or the one picked with **track select** (press again = none). 1 Rate (32…¼ beats per cycle or 120…0.25 seconds), 2 Depth/Size, 5 Spread, 6 Softness where applicable. A Target Tour uses knob 1 as Travel duration; its optional hold pattern retains a separate editor Rate. Meaningless controls are disabled.
- **Track select LED:** blinks whenever the strip runs FX, even outside the FX page; faster blink identifies the FX editing strip. In the positioning menu the selected strip blinks. STOP ALL CLIPS stops only that selected strip's FX, not every strip.
- Group/single mode applies: standby FX are remembered but do not render; their clock continues so returning to the strip does not freeze their phase.

**Target/zone pads:** SHIFT + TRACK SELECT on an active head/group strip opens its positioning menu in Aim mode. Top three rows are targets; bottom two rows are zones. UP selects Aim; DOWN selects Permissions. SHIFT + existing pad edits; empty pad adds. Physical bindings are from AKAI's communication protocol (UP 0x5E, DOWN 0x5F, LEFT 0x61, RIGHT 0x60, PLAY 0x5B, RECORD 0x5D). A/B remains an inactive placeholder. Keyboard Shift is ignored in form fields and cleared on focus loss.

**Free for later:** A/B functionality, transport and navigation buttons.

**Assumptions in the implementation (verify on the hardware):**
- Pad note layout: the **bottom** pad row sends notes 0–7, the top-left pad note 32 (*confirmed on the real APC40*).
- Device-knob MIDI channel in Alternate mode (the app accepts any channel).
- Knob rings are sent on channel 1. Pan/tilt knobs are absolute 0–127 (coarse, about 4° pan per step).
- For 1 s after the APC40 connects, its fader/knob positions are only remembered, not applied, so plugging it in does not make the lights jump.

**Implemented in:** `backend/lichtwerk/apc40.py` (protocol numbers, palette), `apc40_control.py` (fixed logic), `apc40_feedback.py` (LEDs), `apc40_pads.py` (scene pads), `apc40_api.py` (`/api/apc40`), `frontend/src/Apc40View.tsx` (on-screen APC40).

## 7. User interface

- High contrast, large controls, no scrolling on the owner's MacBook with a maximized browser (about 1512 × 820 px).
- **Top bar (always visible):** licht.werk logo, page tabs, Retry on output failure, PREVIEW/GO (plus orange preview indication). SIMULATED and OUTPUT DISABLED remain explicit warnings; healthy enabled real output has no output-status chip. Blackout, output failure, backend loss and safety override warnings remain visible. On pages other than Controller it also shows active scene, master and BLACKOUT. No ARM control.
- **Branding:** the supplied original 1024px PNG is retained in `frontend/public/lichtwerk-logo.png` as the source for future desktop packaging. The header uses a smaller derivative; browser favicon and Apple touch icon use the same artwork. No desktop application/package is introduced by this change.
- **On-screen APC40 colour and mode feedback:** between the FLASH buttons and the faders, each strip shows a colour bar with its fixtures' current colour (one segment per fixture, e.g. Head 1 | Head 2; "no colour" when black, so flash would stay dark). Strips not in control (group vs. single) are greyed out and their bar reads STANDBY. On screen only: on the APC40 only the clip pads and Scene Launch buttons have RGB LEDs (protocol v1.2, p. 15); track select, clip stop, activator, solo and record arm are single-colour.
- **On-screen APC40:** aligned Live/Preview monitors at the top of the right pane; SCENE LAUNCH next to pads, BLACKOUT beside COLOR, STOP FX beside TRACK SELECT, numbered/FLASH/STROBE/A-B in per-strip 2x2 blocks, master fader below. Below the monitors, the right pane contains page/transport and PREVIEW/GO, then beat/TAP/tempo/BPM and four device knobs in the physical left-column layout, SHIFT and Target/Zone hint. Scroll the right pane when needed; pads and tracks retain the full controller height.
- **Pages:**
  - **Controller** (default): picture of the connected controller with every control's function, value and LED. Side panel: scene capture, collapsible "Assign controls" pane (K2), 2D map.
  - **Fixtures:** scenes and per-fixture sliders.
  - **Positions:** aim at targets/zones, calibration, head controls, zone/target assignment.
  - **Setup:** room size, targets, zones, head mounting.
  - **MIDI:** connection, monitor, learn.
  - **Diagnostics:** output state, universes, patch, warnings, recent errors (never stack traces).
- **2D map** (Milestone 13): top view (ceiling perspective) of the room. Every fixture is a dot drawn facing the viewer; each dot shows half live colour and half preview colour. A strobing fixture blinks on the map (capped at 8 Hz on screen for comfort) and is labelled with its approximate native rate, e.g. "⚡ off → 10 Hz" (live → preview). Dot brightness is boosted for visibility only (logarithmic curve, so the club's usual 5–20 % range is clearly visible and distinguishable while 100 % stays brightest; anything above 0 is never drawn as dark); the real output level is printed under the name, e.g. "8 %" or "5 % → 15 %" in preview. Fixture map positions are stored in `config/map.json` and placed by the owner on screen ("EDIT POSITIONS": pick a fixture, click in the room, drag to adjust); the booth is at the bottom, x to the DJ's right; until the room is measured the map uses a placeholder room of 5 m × 4 m × 2.5 m (50 m³), which is **only** used for drawing, never for aiming.
  - RFC update: the LED bar uses a rectangular marker. Moving heads have schematic triangular beam projections from commanded geometry; while previewing, live is a subdued reference and preview is highlighted. Nearly vertical projections use an endpoint marker. These are not physical position feedback. PREVIEW/GO and the map are at the top of the right controller pane, above tempo.

## 8. Safety, persistence and testing

**Safety**
- Real output: off by default; needs explicit `output_mode` and `physical_output_enabled` in `config/app.json`, only allowed universes. Configured output starts automatically in blackout; failures require manual Retry.
- Blackout always available; master and blackout never change stored data.
- All DMX values clamped; pan/tilt clamped to per-head limits; per-strip area allowlist enforced by the beam guard, with explicit static-target exceptions.
- Before any real-hardware test, state which universe and channels are affected.
- Software blackout is not a replacement for electrical safety.

**Persistence**
- JSON files validated at load; invalid files stop startup with a message naming the file and problem; atomic saves with `.bak`.
- Never commit logs, caches, `.venv`, `frontend/dist` or secrets.

**Testing**
- `pytest` for all domain rules (conversion, validation, state resolution, fades, positioning, zones, MIDI, LEDs). Tests never need OLA, the Botex, fixtures or a controller; adapters are mocked.
- Add a regression test for every meaningful bug fix.

**Logging:** standard `logging`; no per-frame logs; recent errors visible on the Diagnostics page.

## 9. Milestones

Each milestone ends with passing tests and an owner review. Hardware steps are supervised by the owner.

### Done (0–7)

| # | Key results |
|---|---|
| 0–1 | Project setup, simulated 512-channel universes, blackout, localhost API |
| 2 | OLA HTTP adapter, ARM in UI, supervised sweep reached the LED bar on universe 5 |
| 3 | Fixture definitions and patch with validation; generated fixture controls |
| 4 | Room/targets/heads files, aiming, calibration fit, limits, rest, native strobe |
| 5 | Partial scenes, LTP, central fades, master, blackout, capture, scene toggle |
| 6 | Operator UI with top bar, pages, diagnostics, backend-loss banner |
| 7 | Xone:K2 input, LED feedback, MIDI learn, on-screen K2 controller with assign mode and default assignment |

### Milestone 11 – zones and collapsible assign pane (done)

Delivered:
- Zone model separate from targets (section 5.4), stored in `config/room.json`; the old 3D-box zones are replaced.
- Setup page: create, edit and delete zones (surface, mode, corner, length, width) and targets.
- Positions page: per head or for the group, choose the aim (none / target / include zone) and the exclude zones.
- Include-zone aiming: pan/tilt controls move the beam spot within the rectangle.
- Beam guard for exclude zones, including stopping manual moves at the edge.
- "Assign controls" pane on the Controller page can be collapsed (collapsed by default).

The original single-zone/exclusion workflow above is superseded by the RFC multi-zone, default-excluded permission model in section 5.4. Dark travel is now allowed; the guard blanks output rather than promising to stop the physical head at an area boundary.

Exit gate: tests cover zone geometry, surface hit points, include-zone aiming, the beam guard on moves that cross an exclude zone, and validation. Works in simulation; real aiming after the club measurement.

### Milestone 12 – AKAI APC40 MKII

Deliver the behaviour in section 6.3: adapter support for both controllers, introduction SysEx, fixed APC40 logic, LED feedback (RGB pads, button LEDs), on-screen APC40 controller, scene-pad assignment with colour, and switching the controller screen by connected device.

Exit gate: tests for message decoding, group/single mode, clip-stop selection, colour pads, strobe rate table and LEDs; supervised check on the real APC40.

Delivered (in simulation): everything in section 6.3 (SESSION RECORD preview toggle added with Milestone 13). The Controller page has APC40 MKII / Xone:K2 tabs; until one is picked it follows the connected controller. The on-screen APC40 has an "Assign scene pads" mode (pick pad, scene and colour). Confirmed on the real APC40: connection and Alternate mode. Still to check at the club: LEDs, pads, knobs and faders against real fixtures.

### Milestone 13 – preview mode and 2D map

Deliver preview mode (section 5.5) in the core, preview toggles on both controllers and on screen, "LIVE" marks, and the 2D map (section 7).

Exit gate: tests prove that preview changes send nothing until Go, that Master/Blackout/Flash stay live, and that Go applies everything at once.

Delivered (in simulation): preview core (`preview.py`, `/api/preview`), K2 LAYER and APC40 SESSION RECORD toggles, PREVIEW/GO in the top bar and on the on-screen controllers, red "LIVE" badges on controls that stay live, and the 2D map (`room_map.py`, `/api/map`, `frontend/src/RoomMap.tsx`) in the Controller side panel. Tests: `backend/tests/test_preview.py`.

### Milestone 10 – Effects

Deliver chases, colour and movement effects synced to one BPM clock; effects modulate the output and never change scenes or live state.

Delivered (in simulation):
- **Tempo:** one shared BPM (20–300, start 128): TAP TEMPO (averages up to 8 taps, resets after 2 s pause), TEMPO knob, typed value on screen. Effects stay in phase when the BPM changes. METRONOME and screen beat indication follow backend phase.
- **FX families:** Dimmer offers relative depth/maximum and legacy, sine, triangle, square, saw and one-by-one generators. Colour groups Rainbow/Two colours, following base hue or using an ordered fixed palette. Move groups Circle/Figure 8, permitted-area movement and named Target Tours, optionally patterned during holds. One family per fixture; three families can combine.
- **Presets:** five editable persistent slots per family/strip, with all original factory looks available in the editor. Save affects the next launch; Apply now deliberately relaunches; Store current captures live tuning. Beat/seconds timing, ordering, Odd/Even/Alternating selection and movement mirror/reverse are configured in the editor (section 6.3).
- **Rules:** fixture intensity, master and blackout still apply; Flash overrides a chase but not area blanking; movement follows the current aim and per-head limits. Area permissions apply to FX too; heads continue moving dark during blackout. Native speed defaults to fastest, independently of musical cycle timing.
- **Preview:** while preview is on, starting/stopping/changing effects goes to the preview and is applied on Go; tempo always stays live.
- **APC40:** effects page on SCENE LAUNCH (section 6.3); on-screen APC40 updated. The Xone:K2 has no effect controls yet.

Code: `tempo.py`, `effects.py`, `effect_engine.py`, `fx_presets.py`, `fx_service.py`, `fx_api.py`, hooks in `fixture_service.py` and `preview.py`, APC40 logic in `apc40_control.py`, `/api/fx`, `PUT /api/apc40/tempo`. Tests: `backend/tests/test_effects.py`, `test_fx_runtime_v2.py`, `test_fx_api.py`, `test_fx_commands.py`.

### Milestone 8 – MVP commissioning at the club

- Measure room, targets and head positions; set fixture modes and addresses; calibrate heads; find rest positions.
- Run the club checklist (section 10) with real output on universe 5.
- Document remaining limitations.

Done at the club (27 Oct 2026):
- OLA → Art-Net → Botex universe 5 with real output after ARM; all five fixtures patched and controllable (modes and addresses as in section 3).
- Room measured (5.60 × 5.55 × 2.30 m, origin and axes confirmed), targets, zones and head positions entered.
- Both heads calibrated with Fine aim (Positions page); targets and zones are hit well.
- Fixes found on site: LED bar strobe range (6Ch: plain strobe is 128–250), Orbit speed channel inverted (knob right = fast), finer pan/tilt for calibration (Fine aim, 0.2° steps), calibration fit no longer spoiled by targets almost below a head, zone Apply starts in the zone centre, Save as rest button, blackout keeps head position (pan/tilt/speed).
- Verified on hardware: rest positions saved, scene recall and fades, beam off in the DJ-booth exclude zone, APC40 unplug/replug while lights run (it resumes with the last values and menu), preview sends nothing until Go, all APC40 controls.

MVP accepted. Deferred (section 10): Xone:K2 hardware check, strobe-rate measurements, Orbit ring macro channel.

Original MVP acceptance: app starts in simulation; real output only after ARM; LED bar, heads and spots controllable; two calibrated targets recalled repeatably; two scenes with fades; master/blackout non-destructive; controller works and survives unplugging; tests pass without hardware. The RFC startup policy now replaces ARM with automatic configured output in blackout.

### Approved RFC updates - 29 October 2026

The consolidated RFC decisions (all slices A-D accomplished; `rfc.md` has been emptied) update the existing milestones rather than introducing a new transport or lighting engine:
- Automatic configured output in startup blackout, manual Retry, no ARM step.
- APC40 layout/labels, per-strip FX stop, running/editing indications, 128 BPM beat feedback and left-column device knobs.
- Multi-zone permissions with undefined areas excluded, static-target exceptions, independent group/single settings and preview-safe persistence.
- Controller target/zone creation, schematic beam projections, stable-ID fixture/group names.
- Look Unique 2.1 pump/fan on universe 5, addresses 99-100, independent of lighting master/blackout with a live held haze override.

Validation: 314 backend tests pass, frontend TypeScript/production build passes. Browser checks used an isolated simulated backend with copied configuration, including target creation, multi-zone selection, renaming, independent group/single menus, calibration override visibility and controller layout at 1512 x 820. No real output was activated for these checks. Existing club commissioning does not substitute for the following checks of the changed behaviour.

Supervised acceptance after restarting the backend:
1. Confirm real-output startup remains dark, heads go to saved rest, haze pump/fan start at zero. Browser reload/controller reconnect must not reset a running show.
2. Verify TRACK SELECT blink rates, SCENE LAUNCH FX entry, selected-strip STOP ALL CLIPS, SHIFT + head target/zone menu and METRONOME feedback.
3. Include two zones; verify light in either and darkness elsewhere, including during FLASH and movement FX. Explicit exclusion wins on overlaps. Test an excluded static target without permitting its travel or movement FX around it.
4. Switch group/single modes and confirm each menu restores its own selections. In preview, stage aim/permissions/FX/haze; verify no live change until Go (except marked live controls).
5. Test temporary calibration override, then turn it off; missing geometry must remain fail-closed. Beam previews are illustrative, not certified safety feedback.
6. With the hazer addressed to 99, verify pump/fan and held maximum restoration under lighting master zero/blackout, then follow the manufacturer's cooldown procedure. Do not equate exiting the app or pump zero with machine shutdown.
7. Interrupt OLA only under controlled conditions; verify delivery stays disabled until deliberate Retry. Retry republishes current effective state and does not reset blackout, so check the intended state first.

### Later

- **9 – OSC / Resolume (optional):** configurable OSC, no dependency on Resolume being up.
- Effects follow-ups: effect controls on the Xone:K2, user-editable presets (config file).
- Automated animations between named targets/zones.
- **Aim accuracy analysis (backlog):** at the club the beam lands with a small offset that seems to depend on where the head points (noticed when aiming at zone centres). Analyse before correcting: record the hit error on several targets spread over the room for both heads; then check possible causes – beam origin not at the measured head position (tilt-axis height), yoke not level (tilted mounting), non-linear pan/tilt DMX response, measurement errors in target/head coordinates. Possible corrections: extra calibration parameters (mount tilt, beam origin offset) or a per-head correction table. Keep the current model until the analysis shows which one is needed.
- WebSocket push, patch editing in the UI, presets, scene editing.

## 10. Open items and club checklist

**Measure / set at the club**
- [x] Fixture modes and addresses on the displays (section 3)
- [x] Room size; confirm coordinate origin and axes (section 5.4)
- [x] Target coordinates and head mounting positions
- [x] Head calibration (both heads, Fine aim, 27 Oct 2026)
- [x] Rest pan/tilt per head (Positions page, Save as rest)
- [x] Equinox: speed channel behaviour – fastest at DMX 0 (manual says slow→fast), so the channel is inverted in the fixture definition (`"invert": true`); knob right = fast
- [ ] Equinox: ring macro channel (CH16 held at 0)
- [ ] Strobe rates (phone slow-motion video) for all fixture types
- [ ] LED bar strobe after the range fix (6Ch: plain strobe is DMX 128–250 only; 11–127 are random/ramp/break effects). Re-measure 1, 10 and 20 Hz.

**Verify on controllers**
- [ ] K2: encoder direction and step size, flash/strobe-hold release, blackout red LED, held LEDs
- [x] APC40: device-knob channel, all LEDs (activators, solo, record arm, clip stop, MASTER, pads, knob rings), group/single switching, colour pads, CUE strobe rate
- [x] APC40: unplug/replug while lights are running (K2 not yet tested)
- [x] Preview: Go sends everything at once; APC40 SESSION RECORD LED
- [ ] Preview: K2 LAYER LED (green live / amber preview)

**Verify on output**
- [x] LED bar, heads and spots via fixture controls with real output
- [x] Scene recall and fades on real fixtures
- [x] Blackout keeps head position (fix of 27 Oct 2026): heads go dark without moving, and come back on the same spot

**Effects (Milestone 10)**
- [ ] Each type and preset on real fixtures; presets tuned to taste
- [ ] Device knobs (Speed, Size, Spread, Softness) on a running effect
- [ ] TAP TEMPO, TEMPO knob and typed BPM; effects stay on beat
- [ ] Circle/Figure 8: smooth enough with the heads' speed channel; beam off in the DJ-booth exclude zone
- [ ] Flash over a chase; master; blackout during a movement effect
- [ ] Group/single switch pauses/resumes effects; scene recall while effects run
- [ ] Preview + Go with effects
- [ ] APC40 LEDs: Scene launch 1–5 (bright/dim/blinking), Track select, effect pads
- [x] Beam guard with real head movement: beam is off inside the DJ-booth exclude zone (manual pan/tilt instead stops at the zone edge)

## 11. Out of scope

QLC+, Wolfmix integration, pyArtNet, console cue stacks, timecode, 3D visualiser, cloud, mobile apps, user accounts, remote internet control, AI or automatic audio-reactive lighting, a generic fixture library, changes to the Resolume setup.

## 12. Rules for GitHub Copilot

The full working rules are in `.github/copilot-instructions.md`. In short:

1. Read this file before changing behaviour; point out conflicts instead of silently deviating.
2. Keep domain logic separate from FastAPI, React, OLA, MIDI and OSC.
3. Never invent fixture layouts, DMX modes, addresses, controller messages, Botex settings or OSC paths.
4. Simulation by default; tests never send real DMX. Explicitly configured hardware startup uses blackout with heads at rest and haze pump zero.
5. Add tests with every meaningful behaviour; run them before calling work done.
6. No new dependencies or frameworks without a concrete reason.
7. Update this file and README.md when behaviour, configuration or setup changes.
8. Explain decisions in plain language for a non-expert owner.
