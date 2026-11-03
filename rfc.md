# RFC: Performance mode (draft v2, 2 November 2026)

Status: milestones 1–5 (looks, wait for NEXT, accents, quantize and live modifiers)
and two-state zone permissions are implemented; club test pending.
The original long draft is kept outside the repo as a reference.

## 1. Problem

Scenes and sequences already work well live. What is slow:

- **A scene recall only sets static values.** Its FX must be launched separately per strip
  (SCENE LAUNCH → column pad, maybe Combo, maybe COLOR palette, maybe Aim/route).
  A "drop" today means 6–10 presses in 3–4 menus, during the musical moment.
- **A sequence only plays scenes** (no FX) and runs strictly on its timer. It cannot
  "build, then wait until the DJ drops".
- **No short accents.** Apart from FLASH (full intensity per strip) there is no
  "hit something for a moment, then go back to what was running".
- **Launches happen when the button is pressed**, not on the beat.

Goal: **fewer presses at the musical moment**, not more features. Prepare looks calmly,
fire them with one press, on the beat.

## 2. Review of the original draft

| Original idea | Decision | Why |
|---|---|---|
| New Performance Cue engine (steps, actions, ownership, return modes) | **Replace** by extending **Sequences** | Sequences already have steps, beat/seconds timing, start/stop/overlap rules. A second engine would duplicate them. |
| Free action lists (set parameter, set position, blackout …) per step | **Replace** by **capturing a live look** | Building action lists in an editor is slow and error-prone. Capturing what you see on the rig is fast and matches how you already work. |
| HOLD / wait for GO | **Keep** (as a sequence step "wait for NEXT") | This is the core value for DJ sets. |
| Name "GO" | **Rename** to **NEXT** | GO already means "send the preview to live". Two GOs would be dangerous. |
| PREVIOUS_STATE / temporary overrides / RELEASE | **Simplify** to **accent pads** | Same idea as FLASH: temporary, render-time, never changes stored data. |
| Quantization | **Keep**, small: off / next beat | Real benefit, built on the existing tempo clock. |
| Separate Performance UI / programming mode | **Drop** | The APC is the performance surface. The scenes pad page becomes the "looks" page; the screen only shows status. |
| Shows / set lists, NEXT/BACK through cues | **Later** | DJ sets aren't predictable; the pad grid already acts as the set list. |
| Parameterised macros, OSC/Resolume, timelines, scripting | **Later / never** | Outside the "fewer presses" goal. |
| 24 starter presets | **Drop** | They would be guesses about your rig. You capture your own. |
All rows agreed (2 November 2026).

## 3. What we build

### A. Looks = scenes that can include FX
- A scene can optionally carry an **FX set**: per strip, the running Dimmer / Colour / Move FX
  or Combo, with their live tweaks (knobs, palette, Move route).
- Scenes without an FX set behave exactly as today (FX untouched).
- **One list, one page:** for the user everything is a **look** (the UI no longer says
  "scene"; internally `scenes.json` and the API keep their names). The pad page is called
  LOOKS; there is no separate page.
  - On screen, a pad with an FX set shows a small **FX** badge.
  - The APC pad keeps its scene colour; its LED cannot show a symbol.
- **Store live look:** SHIFT + an empty pad opens the look editor and runs **Capture live**.
  Every capture stores static values plus running FX **and** the group/single mode of the
  strips involved (also when no FX runs). "Values only" turns a look back into one that
  leaves FX and modes alone (like old scenes). Blackout, master, FLASH,
  strobe-hold and preview-only changes are never captured.
- Recall = one pad press, as today. It applies values **and** starts/stops FX on the
  strips whose fixtures are in the look. Strips not in the look keep running (e.g. hazer,
  DJ spots).

### B. Sequences that can wait for you
- Each step is **either** timed (fade + hold, as today) **or** "wait for NEXT", never
  both. One sequence can mix both kinds.
- Steps can use looks, so a sequence can now change FX.
- Example "Build → Drop": Build A (8 beats) → Build B (wait for NEXT) → Drop (4 beats)
  → Peak (wait for NEXT) → ….
- **NEXT = SCENE LAUNCH 5.** It blinks white while a step waits and is dim while a
  sequence runs. The user wins (Q11): NEXT also skips a timed step, and during a fade it
  starts the next step from the current level. NEXT on the last step ends a one-shot
  sequence (a loop wraps); NEXT on a paused sequence resumes it.
- Pressing another sequence pad replaces the running sequence (on the next beat while
  quantize is on).

### C. Accent pads
- A pad can be marked **accent**, with a per-pad choice:
  - **while held** (default, like FLASH);
  - **for N beats**.
- Afterwards the previous state returns exactly, including running FX.
- Temporary and render-time: never changes scenes, FX presets or fader values.
- *Implemented:* set per pad in the look dialog (SHIFT + pad → "Pad action"). An accent
  plays the complete look — values, moving-head positions and its FX — on top of the
  output; strip modes, zone permissions and running FX underneath stay untouched. Move FX
  follow the current permissions; blocked parts are reported. It acts live, also in
  preview (like FLASH). Retriggering restarts the beats. The pad is white while active.

### D. Quantize
- Global **Off / next beat**, toggled with **METRONOME** (its LED shows it is on).
- Applies to look pads, accent pads, sequence pads and NEXT.
- While queued, the pad (or NEXT) blinks fast. The screen shows "queued".
- *Already done:* while a step waits, NEXT (APC and screen) and WAITING FOR NEXT flash
  on the beat, so the press can be timed by eye.
- *Implemented:* METRONOME LED flashes on the beat while quantize is on and is dark while
  off (the on-screen BEAT always shows the beat). Also switchable on screen (QUANT, status
  strip). A press within 0.1 beat after a beat fires at once. One queued action per kind:
  a later look press replaces a queued one. A while-held accent let go before its beat
  plays for one beat. Turning quantize off fires what is queued. Starts off at launch.

### E. Status on screen
The top bar or Controller page shows:
- the active look;
- the running sequence and its step;
- a large **WAITING FOR NEXT** indicator;
- beats left until the next step;
- queued actions;
- **what was blocked** (see rule 4).

No new page or app mode.

### F. Global live modifiers
- On-screen controls only, in the existing status strip: **FX speed** and **Sequence speed**
  each offer **¼× / ½× / 1× / 2× / 4×**,
  **colour tint**, and **Reset modifiers**. No new APC shortcuts.
- Speed scales all Dimmer / Colour / Move FX, including rings, Combo and accent FX,
  without restarting their phase. BPM, sequence steps, quantize and accent durations
  retain their original timing.
- Sequence speed independently scales timed holds only (beats or seconds), not fades
  or WAIT FOR NEXT. ¼× is slower, 4× faster; a live change preserves hold progress.
  No minimum hold duration is imposed. The beat countdown uses whole beats for integer
  effective fade/hold timings, otherwise half-beat ticks rounded up, never quarter-beat
  display updates. Seconds retain one decimal. The actual countdown includes unchanged fades.
- Tint replaces output colour on all colour-capable lighting fixtures by default,
  including RGB rings; colour brightness, dimmers and protection remain effective.
  Explicitly exclude fixtures/groups (e.g. Booth Spots) instead of selecting participants.
  Exclusions follow physical fixtures across group/single mode and look/sequence changes;
  excluding a group excludes all its fixtures. Exclusions affect tint only, not global FX
  speed. Haze is never tinted. Choose a colour, then Apply tint; Tint off reveals the current
  underlying colours/FX, not a frozen snapshot.
- These are global **live** controls, including while preview is open. They also
  appear in the preview monitor. Changes take effect immediately, not quantized.
- Modifiers and tint exclusions survive look recalls, sequence steps and accents.
  Reset returns both speeds to 1× and removes tint, keeping exclusions.
  They are runtime-only: browser reload keeps them; backend restart starts neutral.
  Capture live and Store current FX never bake them into saved data.

## 4. Rules

1. Looks, sequences and accents call existing services (scene recall, FX start/stop,
   combo, positions). No new lighting engine and no second timer.
2. Recalling a look changes FX only on strips with fixtures in the look; it stops their
   other FX. Other strips keep running.
3. Last input wins: manual faders, knobs or FX after a recall override it until the next
   recall. Faders use **soft takeover** (section 5).
4. **The show never stops.** If part of a look cannot run (e.g. a Move FX is not allowed
   with the current permissions), the rest is applied and the sequence continues.
   - The blocked part is shown on the APC (error) and on screen ("blocked: Move FX on MH
     Group – zone not included").
   - Safety rules (permissions, beam blanking) are never bypassed.
5. Stopping a sequence cancels all its pending steps; nothing fires afterwards.
6. Blackout, master, STOP FX, Preview/GO and FLASH work unchanged. In preview, looks
   and NEXT are staged like everything else.
7. Data stays in the existing human-readable files (`scenes.json`, sequences, APC pad
   assignments). Missing FX presets or targets give an actionable error on load,
   never silent removal.
8. No real DMX in tests; all behaviour is tested with the simulator.

## 5. Soft takeover for faders (moved here from "Other points")

The APC faders are not motorised. Once looks and sequences change intensities by
themselves, the physical fader will often be in the wrong place, and touching it would
make the light jump.

- After a recall, sequence step or FX changes a strip's level, the fader is
  **detached**. Moving it does nothing until it passes the current level. From then on
  it controls the level again (last input wins).
- The on-screen fader shows a ghost marker at the real level and "PICKUP" until it is
  caught.
- The master fader is excluded: nothing automates it.
- This belongs with milestone 1, because looks are what create the jumps.

## 6. Milestones (each is testable on its own)

1. **Looks:** FX set in scenes, Capture live, FX badge, recall with strip rules,
   blocked-part feedback, **soft takeover**, tests. *(Done, club test pending.)*
2. **Wait for NEXT** steps, NEXT on SCENE LAUNCH 5, status display. *(Done, club test
   pending. Looks used as sequence steps also switch modes and start their FX.)*
3. **Accent pads.** *(Done, club test pending. NEXT flashes on the beat.)*
4. **Quantize** (next beat). *(Done, club test pending.)*
5. **Global live modifiers and two-state zone permissions.** *(Done in simulation;
   brought forward by request before the club test.)*

**Done when:** from "Deep Blue", one sequence pad runs Build → waits → NEXT fires Drop
on the next beat (scene + strobe + Move FX) → Peak waits. An accent pad gives a
2-beat white hit and returns to Peak with its FX intact. Unrelated strips are untouched,
faders don't jump, and DMX output stays smooth.

Shows and set lists are dropped for now.

## 7. Last open questions

- **Q11 – NEXT on a timed step.** NEXT should only advance a waiting step, but what
  should it do on a timed step?
  - (a) Ignore it and show "step is timed".
  - (b) Skip to the next step anyway.

  **Recommendation: (a).** Your rule "never both for one step" stays clear, and NEXT
  can't fire a drop too early by mistake.
  > Answer: no, I want the user to win. If the user decided to move earlier to the next phase, so be it.
  > (Implemented this way in milestone 2.)

- **Q12 – Group vs single mode in a look.** FX are stored per strip, and the head and bar
  strips can be in group or single mode.
  - (a) Recall switches the strip to the mode the look was captured in.
  - (b) A look only captures FX of the strips active in the current mode and plays them
    only when that mode is active.

  **Recommendation: (a).** The look then always looks the same, and the ACTIVATOR LEDs
  show the switch.
  > Answer: follow recommendation.

- **Q13 – Existing scenes.** Should scenes keep today's behaviour (FX untouched) until
  you re-capture them as looks? **Recommendation: yes.** Nothing changes until you
  decide.
  > Answer: follow recommendation.

---

## Other points

- **Done, club test pending — zone permissions:** included/excluded only, per strip/head.
  Permissions pads toggle cyan/red without moving heads; look and Move editors use the
  same states. Move's quick include requires confirmation. New zones and legacy neutral
  zones are excluded; exclusions win on overlaps, including overlaps with newly added
  zones. Static named-target exceptions remain unchanged. The misleading room-level
  permission selector is removed; legacy `mode` metadata is preserved for file compatibility.

## Backlog

- Club test (hardware, no party ongoing): re-test the features added since v1.0 on the real rig: Combo FX, Colour FX pad picker, the Move FX editor (three modes, map, zone quick-include) and APC pad picking of Move routes.
- Moving heads miss precise targets after startup: investigate at the club, together with the club test.
- OSC implementation and MIDI clock sync from other Mac via Ethernet
