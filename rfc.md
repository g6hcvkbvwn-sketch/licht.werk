### UX/UI/Workflow refinements

#### Review result - 30 October 2026

**Recommendation:** refine the existing interface, not replace it with a different
console. The underlying live workflow is already coherent: tracks own fixture
control, pads change purpose, compatible FX combine, and Preview rehearses beside
Live. The main friction is discovering the current context, finding actions in
long editors, and distinguishing preparing a definition from changing output.

This section contains **proposals, not implemented workflow changes**. Only the
branding/header work under "Logo implement" below is implemented in this round.

**Review basis:** inspected the controller, K2 surface, all editor components,
secondary pages and their styles; exercised the APC40 page, target dialog,
scene/sequence/FX editors, Show, Fixtures, Positions, Setup and Diagnostics in an
isolated simulated backend. MIDI was disabled in that simulation, so enabled
MIDI learning/reconnection and K2 hardware operation were reviewed from code,
not verified on hardware. Browser layout checks used 1440 x 900 CSS pixels.
This is not a claim of club usability acceptance.

Concrete observations after the dialogs finished loading:
- The scene dialog already scrolls before selecting any fixtures. Save is below
  the initial viewport, and all fixture/group columns and parameter families
  remain visible even when inactive.
- The sequence editor exposes the full live-controls selection beneath the
  steps, even for an empty new sequence. That pushes Save much further down.
- The default Dimmer FX editor also needs vertical scrolling to reach its six
  footer actions, despite already placing some options behind "More".
- The Fixtures page showed 67 sliders, including the global master, for the
  five-fixture simulated patch.
  The new segment attributes make this generic view particularly long.
- Positions combines normal aiming, permissions, calibration and full fixture
  controls in one long page. These are different operator tasks.

#### Non-negotiable design rules

1. Preserve the APC40 silhouette and muscle memory: eight track columns, five
   pad rows, Scene Launch beside the pads, numbered activators, RING, STROBE,
   FLASH, faders, master, BLACKOUT and STOP FX stay in their hardware-related
   locations. Do not turn the surface into rearrangeable cards.
2. Keep the two monitors at the top of the right pane. Keep device knobs in
   their existing two-left-column arrangement: 1/2 above 5/6. Do not compact them
   into an unrelated horizontal toolbar or remap MIDI to solve layout problems.
3. Keep five presets per active track/family/component. Do not merge beam/ring
   FX ownership or invent extra FX pages.
4. Compact means less repetition and better grouping, not tiny controls.
   Preserve readable contrast and comfortable pointer targets. Colour is a
   secondary cue, never the only indication of selection, exclusion or danger.
5. No behavioural redesign hidden inside a visual tidy-up: retain group/single
   memory, partial scenes, Preview/Go phase, explicit Apply now, beam safety,
   independent ring output, master/blackout and existing physical shortcuts.

#### Priority 1 - reduce mistakes and interruptions

**1. One predictable dialog frame**

Current problem: Close is at the top and Save/Apply is at the bottom of the same
scrolling document. Scene, sequence, FX and target editors use different widths
and spacing. Scene/sequence dialogs close immediately on backdrop click or
Close without checking unsaved changes; the FX editor already checks for edits,
supports Escape and traps keyboard focus.

Proposal:
- A stable header with title, edited item, LIVE/PREVIEW context and Close.
- A scrolling content area, with a visible action footer outside that scroll.
  Include status/error feedback near those actions, not only above a long form.
- Bound the complete dialog, including padding, to the viewport. Use a compact
  width for target/zone forms, medium width for FX/sequences, and wide width only
  for scene comparisons. Do not make every dialog full-screen.
- Reuse the FX editor's dirty-draft/focus principles across editors: opening
  focus, focus containment, focus return, Escape with discard confirmation.
  Ignore backdrop clicks while editing; explicit Close is safer on a show Mac.
- Do not close or discard while a save is in progress. Destructive actions go
  into a separate "More" area, not alongside the primary Save button.

UX effect: actions are always reachable; an accidental click outside the form
does not lose preparation. No change to what Save or Apply actually does.

**2. Make editing context explicit in every editor**

Current problem: FX clearly states LIVE/PREVIEW and beam/ring, but scene and
sequence actions rely more on global context and explanatory paragraphs.
The global header and both monitors are partly obscured by a modal.

Proposal:
- A compact editor context line, e.g. "PREVIEW | Moving Heads | Ring | Slot 2".
- Use "Save for next launch" and "Save & apply to Preview" / "Save & apply Live"
  where applicable. Shorter button labels can accompany a persistent context
  label if the footer would otherwise wrap.
- For sequences, keep the current restriction on Apply now, but explain the
  disabled state next to the button: "Save, then launch from a pad" or
  "Only the running sequence can be refreshed here".
- Explain once, near Save: **Preview stages output, not file edits. Saved
  definitions change immediately for later launches.** Do not imply Preview
  is a sandbox for persistence.
- Keep blackout, master, FLASH and live haze visibly identified as live controls
  during Preview. Go is deliberate; no auto-apply or auto-Go on closing a dialog.

UX effect: fewer unintended live launches and less uncertainty about which
version of a look will be used. No new confirmation on every normal pad press.

**3. Make scene authoring compact without abandoning track order**

Current problem: the current all-columns/all-families matrix is good for
comparison but expensive for creating a simple look. Selecting a scope, then
selecting a family, then individual channels requires scanning a lot of empty
controls. Group and individual columns compete for space.

Proposal:
- Keep a slim scope-selection row in APC40 track order at the top.
- Initially show a concise prompt to select participating scopes; then show only
  selected columns in the main editor. Keep an explicit "Compare all" option for
  operators who prefer the existing matrix. Never hide saved participation.
- Use collapsible parameter-family rows. Show compact summaries when collapsed:
  "Colour: blue", "Intensity: 40%", "Ring: individual sections", "Position: Ball".
- Keep group/individual conflicts local to the relevant family and retain the
  explicit "Use group/individuals instead" resolution. Do not silently flatten.
- Put name, incoming fade and optional pad colour into one compact metadata
  block; put Capture beside the field-selection summary.
- Rename Capture to "Capture selected controls". Show the source and selected
  scope/family count. Offer an explicit shortcut to select common look controls,
  but never silently add movement, haze or previously unselected fields.
- Retain the ring Uniform/Individual prompt. Uniform edits remain deliberate;
  changing the view must not convert individual colours.

UX effect: a basic colour/intensity scene becomes a short task, while detailed
partial scenes and group-intensity/individual-position combinations remain
possible. Scope selection is a draft operation, never a lighting command.

**4. Put the sequence itself ahead of optional overrides**

Current problem: live-control checklists dominate the editor even when no
steps have been added. Repeated timing explanations distract from the order,
fade and hold of the actual show.

Proposal:
- Make the ordered step list the primary area: step number, scene, incoming fade
  or "inherit", hold, and compact Up/Down/Remove actions.
- Collapse "Live controls" by default with a meaningful summary, e.g.
  "Manual during playback: head colour + ring intensity". Reopen automatically
  when saved selections are invalid; never hide a validation problem.
- Keep referenced fixtures/attributes under a secondary details section.
- Explain fade + hold once using a small step diagram rather than repeated
  paragraphs. Show beat/second units next to every timing value.
- When changing units, display a clear warning before committing if numbers are
  being reinterpreted, e.g. "4 seconds becomes 4 beats, not a conversion".
  Recommend retaining the existing numeric semantics for now; automatic
  conversion needs separate approval and a defined rounding policy.
- In an empty editor, offer a clear route to create a scene first. Preserve the
  sequence draft on return rather than leaving only a disabled Add button.

UX effect: preparing and reordering a chase is faster; live-control flexibility
is retained without becoming mandatory configuration.

**5. Give FX actions a hierarchy**

Current problem: six footer actions plus factory loading and explanatory text
all compete at similar importance. "Start saved slot" deliberately ignores the
draft, which is correct but easy to misunderstand.

Proposal:
- Keep five slot selectors visible, with separate Selected and Running labels;
  a different selected slot must not look like the currently running look.
- Primary footer: Save for next launch, Save & Apply, Stop selected family.
- Put Store current tuning, Load factory and Reset saved slot in a secondary
  preset-tools section; keep Start saved as a clearly separate launch action,
  labelled as using saved values when a draft differs.
- Basic controls stay in view: Rate, Depth/Size, Spread, plus shape/palette or
  tour essentials. Order/selection/reflection stay under More.
- Replace the permanent mathematical example with a concise relative range
  summary; keep the formula/example available in expandable help.
- For ring FX, label the roster "Sections" and count "12 sections / 2 heads",
  not just fixtures. Keep the existing default order, with head grouping in
  reorder lists. Do not add a separate ring FX engine or a new track.
- Keep admission/fidelity warnings visible and actionable; never hide movement
  permission failures as mere help text.

UX effect: quicker tuning and fewer launches of the wrong version. The
five-slot model, existing factories and independent component runs stay intact.

#### Priority 2 - smooth live operation on the controller

**6. Clarify page, strip and beam/ring without adding a control centre**

The right pane changes meaning between colour, FX and positioning, but its
generic "Device knobs" heading can remain visible even on the target/zone page.
Scene selection, FX editing selection, group/single activity and RING are
different states.

Proposal: one short context line immediately above the contextual controls:
"COLOUR | Head 1 | RING | Section 2", "FX | Heads | BEAM | Dimmer", or
"POSITIONS | Heads | PERMISSIONS". Keep physical button feedback where it is.
Give standby a text cue; do not make it look like a connection fault. Use full
names in accessible labels/tooltips where track text is truncated.

For group/single changes, show a brief non-modal notice identifying the newly
active strips and that their remembered values apply. Do not add a disruptive
confirmation or invent pickup behaviour; whether to change the current
immediate-recall behaviour is a separate product decision.

**7. Make right-pane space predictable**

Keep its hardware-related order: monitors, navigation/transport and Preview,
tempo, context, the 1/2/5/6 device-knob block, SHIFT. Reserve steady space for the
contextual block rather than repeatedly inserting paragraphs above it.

- Replace repeated "not on map" lists in both monitors with a compact count and
  one expandable list. Put last hardware input and connection detail in a small
  expandable status area; keep disconnection/failure warnings visible.
- Keep important transient errors near the affected control and reserve room
  so status updates do not repeatedly push knobs down.
- Retain the existing right-pane overflow as a fallback. Prioritize the
  contextual knobs in the initial viewport at the owner's approximately
  1512 x 820 maximized window; verify at 1280 x 800 as well. Do not shrink the
  main pad/fader surface to make room for optional assignment/help content.
- Give a scroll affordance when content is below the fold. Do not solve this by
  moving monitors away from the top or filling the unused hardware knob columns.

UX effect: less hunting during a mix; physical locations and mappings survive.

**8. Keep Aim and Permissions visibly different**

The existing separation is right: including a zone must not aim there.
The side pane already has AIM/PERMISSIONS buttons; the missing piece is strong
feedback on the pads and clear tri-state instructions.

Proposal:
- Show the current mode next to the pad area, not only deep in the side pane.
- In Permissions mode, use consistent text/symbols for Not included, Included
  and Explicitly excluded. Provide a one-line cycle hint on demand.
- In Aim mode, distinguish the destination from the set of permitted areas.
  Selecting a target does not permit the route or nearby movement FX.
- Keep add/edit available through the existing empty-pad/SHIFT shortcuts, with
  the on-screen Add/Edit route retained for discoverability.
- In the target dialog, collapse the long surface-estimate explanation under
  help, but keep a short "Calculated estimate, not measured position" warning
  after Store current position. Show valid coordinates before Save.

UX effect: permission preparation remains non-moving; the operator can tell
whether the next pad press moves a beam or only changes admission.

**9. Keep Beam and Ring readable independently**

RING is an editor context, not an output toggle. The monitors now correctly
draw rings independently, including while a beam is safety-blanked.

Proposal: retain that model, show small Beam/Ring context text next to the
selected strip, and show group mixed colours explicitly instead of suggesting
the first head represents every section. In ring colour mode, keep ALL and
sections 1-6 in their current pad positions and use actual colour feedback.
Add a small non-modal monitor enlargement for checking individual sections;
keep the paired compact monitors visible in their existing pane.

Do not imply physical sensor feedback: the monitor is commanded output and its
section arrangement is schematic. Avoid permanently restoring the large
explanatory legends that were just removed.

**10. Consistent visual language and keyboard behaviour**

- Selected page, selected editing strip, running FX, suspended FX and live
  warnings need distinct text/border cues; do not rely on different blinking
  speeds or colour alone.
- Add visible keyboard focus and names to icon-only controls and field groups.
  Keep the existing Shift functions; do not introduce show-launch shortcuts
  while typing in an editor.
- Use tabular numerals for timing/levels and a consistent percent/Hz/beat
  notation. Label stop actions by scope: "Stop sequence", "Stop FX: Ring", not
  a new ambiguous global Stop.
- Add a compact, optional shortcut legend for the current page; keep it closed
  during performance. Do not make tooltip-only help essential.
- A backend failure should preserve the last visible values but clearly mark
  them stale; avoid several competing banners for the same disconnect.

#### Secondary pages

| Page | Finding | Concrete recommendation | Workflow effect / boundary |
|---|---|---|---|
| Show | Cards mix immediate recall and editing; long scope IDs become visual noise. No search/filter or quick pad-location overview. | Keep an unmistakable launch surface plus a separate Edit action; add name search, concise scope summaries using display names, and assigned pad locations. Show running/paused sequence state on its card. | Easier to find and repair a look without recalling it accidentally. Do not add arbitrary folders/tags before the library needs them. |
| Fixtures | Generic sliders expose every ring RGB channel and legacy alias, making even a small rig very long. It is also unclear at a glance which values are base state versus effective FX output. | Make this a direct-control/diagnostic page: fixture selector, Beam/Ring/Movement groups, normal colour picker and ring Uniform/Individual view, with raw attributes under Advanced. Show "Base controls" separately from a read-only effective-output summary. | Retains precise diagnostic access without eighteen section sliders dominating normal use. Never duplicate lighting calculations in React. |
| Positions | Normal aiming, fine adjustment, rest setup, calibration observations, safety override and full fixture sliders share one long page. | Default to Aim + Permissions and clear beam-off reason. Put Calibration/limits/rest in an explicit setup section; keep any active safety override unconditionally visible. Retain fine aim near the selected head and show which head/group owns the command. | Normal aiming becomes short; calibration remains available but harder to activate casually. No change to beam limits or permissions. |
| Setup | Coordinates depend on a distant "x/y/z" legend; inputs lack individual accessible names. Room, targets, zones and mounting each have separate save scopes. Zone "Include/Exclude" terminology can be mistaken for strip permission. | Label every coordinate with axis/unit and row name; show a small axis diagram. Show per-section dirty/saved state and warn before navigating away from edits. Clearly distinguish saved zone defaults from per-strip Include/Exclude permission, with a route to Positions. | Less coordinate confusion and fewer edits lost when another section is saved/reloaded. Do not change geometric conventions. |
| MIDI | Enabled UI combines connection, generic Learn bindings and an event monitor; APC40 has a separate fixed command surface. | Separate device health (APC40/K2) from configurable generic/K2 mapping. Clearly say APC40 fixed mappings are not remapped by Learn. Add explicit Learn waiting/cancel feedback, and pause/filter for the monitor. When disabled, give an actionable configuration path without offering a misleading Connect button. | Faster fault-finding without suggesting controls are freely remappable. Connected-device/real Learn tests still require hardware. |
| Diagnostics | Useful read-only patch/output/error information, but "armed" language is left over from the old workflow and warning lists do not offer recovery paths. | Use current Enabled/Disabled/Failed wording; group blocking versus informational issues, identify affected fixtures, and link to the relevant Setup/Positions page. Offer a local copy-diagnostics action and output Retry where applicable. | Recoverable problems become actionable. Keep full technical detail available; do not claim a healthy transport proves fixtures physically responded. |
| Xone:K2 surface | Its compact Save current look flow and assign pane differ from APC40/Show's selective scene editor. | Preserve the K2 geometry and bindings. Clarify exactly what quick capture includes; offer the same detailed scene editor as an optional route, with consistent Live/Preview and save terminology. | Familiar quick operation stays available. Do not silently replace capture semantics or remap the K2. |

#### Suggested implementation slices and decisions

**Slice A - consistent editors first (recommended next).** Shared dialog frame,
sticky actions/context, draft-loss protection and consistent spacing. Reuse
existing components and CSS; no new UI framework. Validate with long scenes,
many sequence steps, expanded FX order lists, errors and small laptop viewports.
This offers the biggest improvement without touching show resolution.

**Slice B - scene/sequence progressive disclosure.** Selected-scope scene view
with Compare all; collapsible family controls and sequence live-controls panel.
Retain every saved value and explicit group/individual resolution.

**Slice C - controller clarity.** Context strip, tighter optional status/help,
monitor enlargement and Aim/Permissions feedback. Keep hardware control
positions, five slots and monitor location fixed.

**Slice D - secondary pages.** Prioritize Fixtures ring grouping and Positions
setup separation, then library search and configuration/diagnostic clarity.

Recommended choices for approval:
- Use compact selected-scope authoring by default, with Compare all retained,
  rather than replacing the scene matrix with a multi-step wizard.
- Treat live actions as explicit actions, not "auto audition while editing".
  Keep Save separate from Apply and Go.
- Improve context before adding a new global Performance/Edit mode. A global
  lock mode would change established workflows and needs a separate RFC.
- Retain numeric timing semantics and current group/single recall behaviour.
  Automatic conversions or fader pickup are behaviour changes, not layout fixes.

**Not proposed:** draggable controller layouts, a new timeline/console
framework, new dependencies, a general undo engine, transport changes, automatic
hardware detection assumptions, or hiding safety warnings for aesthetic reasons.
Those would add complexity disproportionate to the current rig.

### Logo implement

**Implemented in this RFC round.**

- Previously, the SIMULATED/OUTPUT DISABLED chip changed to **REAL OUTPUT** when
  physical output was enabled; it did not disappear. It now disappears for
  enabled real output. SIMULATED and OUTPUT DISABLED remain visible in their
  respective states; blackout, backend/output failures, Retry and safety
  override warnings are unchanged. This is not a new output mode or ARM step.
- Copied `~/Downloads/lichtwerk-logo.png` into
  [frontend/public/lichtwerk-logo.png](frontend/public/lichtwerk-logo.png).
  The original 1024 x 1024 image is retained, unmodified.
- Added a 40px header logo to the left of the navigation/status area, using a
  96px derivative for sharp rendering. It appears on all pages without moving
  any APC40 controls. Added the matching 32px browser favicon and 180px Apple
  touch icon. The artwork is not stretched or recoloured.
- Future desktop packaging should use the original 1024px asset as its app-icon
  source. There is no desktop wrapper in this project today, so no fictitious
  desktop app, installer or unconsumed macOS icon configuration was added.
- Related product/setup documentation was updated. Per this round's instruction,
  no new club-testing procedure was added to `testing.rfc`.
- Validation: frontend build/type-check passed; browser checks confirmed that
  all four image assets load at the intended sizes. UI-only mocked OLA status
  verified no chip for healthy real output, visible OUTPUT DISABLED + Retry
  when disabled, and the existing failure banner when output fails. Those
  status checks did not enable a hardware adapter; the backend stayed simulated.

### Testing

Ignore for this RFC round:

After implementation, write a concrete testing procedure for me into testing.rfc next time when I'm back at the club to ensure all functionalities work as expected. Describe the testing steps as concrete as possible (what's needed to do on the APC40 and what on the screen) so I don't need to guess what's meant. Add every new test for each implemented feature to the already existing tests in this testig file, but streamline all tests in a way to avoid I'm testing duplicate stuff.
