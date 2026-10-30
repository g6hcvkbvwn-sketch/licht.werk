# GitHub Copilot Instructions

## General

- Always read `project.md` before implementing or changing product behavior.
- Treat `project.md` as the primary source of truth for product requirements, architecture, scope, milestones, and technical constraints.
- If an implementation request conflicts with `project.md`, point out the conflict before making the change.
- Do not silently change architectural decisions defined in `project.md`.
- Keep solutions as simple as possible. Do not introduce unnecessary abstractions, frameworks, services, or dependencies.
- This project is primarily developed through GitHub Copilot by a user who is not an expert software developer. Explain important decisions in clear, practical language.
- When providing terminal commands, provide complete commands that can be copied and executed directly.
- The primary development environment is macOS.

## Working Style

- Before implementing a substantial feature, inspect the existing repository and relevant files first.
- Reuse existing architecture, models, services, utilities, and patterns where appropriate.
- Do not create duplicate implementations of functionality that already exists.
- For substantial changes, explain the intended implementation approach before changing many files.
- Break large tasks into small, testable steps.
- Prefer completing one working vertical slice over creating many unfinished components.
- Do not refactor unrelated code while implementing a feature unless necessary.
- If a refactor is necessary, explain why.
- Do not change working behavior unless the requested feature requires it.
- Do not rename or move files unnecessarily.

## Project Architecture

- Keep domain logic independent from UI and hardware integrations.
- Keep lighting logic outside React components and API routes.
- Keep OLA-specific code inside the OLA adapter/integration layer.
- Keep MIDI-specific code inside the MIDI adapter/integration layer.
- Keep OSC-specific code inside the OSC adapter/integration layer.
- Keep persistence logic separate from domain logic.
- FastAPI routes should delegate behavior to services/domain logic rather than implementing lighting behavior directly.
- React components must not communicate directly with OLA, MIDI, OSC, or DMX hardware.
- Hardware integrations must be replaceable with mock or simulation implementations.

## DMX and Lighting Safety

- Real DMX output must be disabled by default during development.
- Starting the application or running tests must never automatically activate physical fixtures.
- Never send real DMX output from automated tests.
- Always validate DMX universe numbers, channel addresses, fixture ranges, and generated DMX values.
- DMX channel values must always remain within valid DMX ranges.
- Detect fixture address collisions and universe overflow.
- Moving-head pan and tilt commands must respect configured limits.
- Never invent fixture channel layouts.
- Fixture definitions must be based on verified manufacturer documentation or information explicitly supplied by the user.
- Never assume the DMX mode of a fixture.
- Never assume the relationship between an OLA universe and the corresponding Art-Net universe.
- Before implementing or testing real hardware output, clearly identify the universe and channels that will be affected.
- Preserve a global blackout mechanism.
- Master intensity and blackout must not permanently modify stored scene values.

## OLA

- OLA is the primary DMX transport layer.
- Application domain logic must not depend directly on OLA.
- Communicate with OLA through a clearly defined DMX output interface/adapter.
- Maintain a mock DMX output implementation for development and automated testing.
- Do not introduce pyArtNet or another DMX transport library unless explicitly requested or technically justified.
- Do not reimplement Art-Net networking if OLA already provides the required capability.
- OLA configuration belongs to the infrastructure layer, not the fixture or scene domain.
- Verify macOS compatibility before proposing OLA installation or configuration commands.

## Fixture Engine

- Separate reusable fixture definitions from patched fixture instances.
- Fixture definitions describe DMX modes and channel functions.
- Fixture instances describe universe, address, name, groups, physical position, and calibration.
- Do not hard-code fixture-specific DMX channel numbers in scene, MIDI, UI, or effect code.
- Translate logical fixture attributes into DMX values through the fixture engine.

## Moving Heads

- Treat spatial positioning as a core feature of the application.
- Prefer named targets and logical positions over raw DMX pan/tilt values during normal operation.
- Keep geometric position calculations separate from fixture-specific DMX conversion.
- Support per-fixture calibration, inversion, offsets, pan/tilt range, and movement limits.
- Do not assume theoretical coordinate calculations alone will accurately point real fixtures.
- Preserve manual fine calibration.
- Keep raw pan/tilt control available for setup and diagnostics.

## Scenes and State
- The application owns the authoritative logical lighting state.
Scenes describe desired logical fixture states, not raw universe buffers whenever possible.
- Scene recall must not mutate the stored scene definition.
Live overrides must remain separate from stored scene data.
Master intensity is applied to runtime output and must not rewrite scenes.
- Blackout is a runtime state and must not destroy previously active scene values.
- Implement transitions centrally through the transition engine.
Avoid implementing separate fade logic in UI, MIDI, scene, or fixture components.

## Effects

- Effects must operate on or modulate logical lighting state.
Effects must not permanently modify stored scenes.
- Keep effect generation independent from DMX transport.
- Do not implement advanced effects until direct fixture control, positioning, and scenes are stable.

## MIDI

- Use MIDI as a control interface to application commands.
- MIDI mappings must remain configurable and must not be hard-coded throughout the application.
- Keep APC40-specific behavior isolated from generic MIDI behavior.
- Do not invent APC40 MIDI note, CC, SysEx, or LED mappings.
- Use verified mappings or user-provided hardware information.
- MIDI disconnection or malformed input must not crash the lighting engine.
- High-frequency MIDI input must not block DMX output.
- Leave architecture open for future MIDI output and APC40 LED feedback.

## Resolume and OSC

- Resolume remains responsible for the existing pixel bars and video/TV system.
- Do not alter or replace the existing Resolume workflow unless explicitly requested.
- Keep OSC integration optional.
- The lighting application must continue working if Resolume is unavailable.
- Do not invent Resolume OSC paths.
- Use verified Resolume OSC documentation when concrete mappings are implemented.

## Backend

- Use Python with type hints for new application code where practical.
- Prefer small modules and functions with clear responsibilities.
- Avoid large monolithic files.
- Avoid unnecessary global mutable state.
- Use Pydantic for configuration and API validation where appropriate.
- Handle expected errors explicitly.
- Do not silently catch unexpected exceptions.
- Log unexpected failures with sufficient debugging context.
- Do not generate excessive per-frame logging during normal DMX output.

## Frontend

- Use React with TypeScript.
- Keep components reasonably small and focused.
- Separate API communication from presentation components.
- Do not duplicate backend domain logic in the frontend.
- The frontend displays and manipulates application state but is not the authoritative lighting engine.
- Optimize the interface for fast operation in a dark club environment.
- Prefer large controls, clear state indication, high contrast, and minimal navigation.
- Clearly distinguish simulated output from real hardware output.
- Clearly indicate blackout and output-disabled states.

## Persistence

- Keep persistent project/configuration data human-readable where practical.
- Validate persisted data when loading it.
- Prefer atomic writes for important configuration/project data.
- Do not silently discard unknown or invalid configuration.
- Return actionable validation messages.
- Do not commit runtime logs, caches, virtual environments, generated build output, or local machine state.
- Never commit secrets, access tokens, passwords, or private keys.

## Testing

- Add or update tests whenever implementing important domain behavior.
- Unit tests must not depend on physical lighting hardware.
- Unit tests must not require the APC40, Botex NETcon, Resolume, or OLA daemon to be running.
- Mock external hardware/protocol adapters where appropriate.
- Important lighting calculations should be deterministic and independently testable.
- Add regression tests when fixing meaningful bugs.
- Before considering an implementation complete, run the relevant tests.
- Report failing tests instead of ignoring them.
- Never modify tests simply to make a failing implementation appear correct.

## Dependencies

- Prefer existing project dependencies.
- Before adding a new dependency, check whether existing dependencies or the Python standard library can solve the requirement.
- Do not add major frameworks without explaining the reason.
- Do not introduce Docker, databases, cloud services, QLC+, pyArtNet, or other major infrastructure unless explicitly requested or approved.
- When adding a dependency, update the project's dependency configuration and relevant documentation.
- Do not assume Linux package-management commands on macOS.

## Configuration and Hardware Information

- Never guess hardware configuration.

- If required information is unavailable, mark it clearly as requiring configuration or user input.

- Examples include:
  - fixture manufacturer and model
  - fixture DMX mode
  - fixture channel layout
  - fixture DMX address
  - universe assignment
  - Botex NETcon IP address
  - Botex Art-Net mapping
  - APC40 model
  - APC40 MIDI mapping
  - Moving Head mounting location
  - Moving Head orientation
  - room coordinates
  - Resolume OSC mappings

- Prefer configuration files over hard-coded values.

## Git and File Management

- Do not commit generated files, caches, logs, virtual environments, or machine-specific files.
- Maintain .gitignore when new generated artifacts are introduced.
- Do not delete user-created configuration or project data.
- Do not overwrite existing configuration files without preserving required user settings.
- Keep commits and changes logically focused.
- Before large structural changes, explain which files will be created, modified, moved, or deleted.

## Documentation

- Keep README.md focused on installation, setup, running, testing, and basic usage.
- Keep project.md focused on product requirements, architecture, scope, and milestones.
- Update documentation when setup steps, dependencies, configuration structures, or user-visible behavior change.
- Do not duplicate large sections of project.md inside other documentation.

## Development Workflow

- For each implementation task:
  - Read the relevant sections of project.md.
  - Inspect the existing implementation.
  - Identify the smallest coherent change required.
  - State the implementation plan for substantial changes.
  - Implement the change.
  - Add or update tests.
  - Run relevant tests.
  - Fix failures caused by the change.
  - Summarize what changed.
  - Mention any assumptions, TODOs, hardware dependencies, or remaining limitations.

## Definition of Done

- A feature is not complete until:
  - The requested behavior is implemented;
  - Relevant validation exists;
  - Relevant tests pass;
  - Existing functionality has not knowingly been broken;
  - Simulation mode still works where applicable;
  - Documentation is updated if setup or behavior changed;
  - No real hardware values were guessed;
  - No unnecessary dependency was introduced.

## Communication with the User

- Assume the user understands the product and lighting goals better than the internal software architecture.
- Explain software architecture and terminal steps clearly without assuming advanced programming knowledge.
- Give exact file paths when asking the user to inspect or edit something.
- Give copy-and-paste-ready terminal commands where useful.
- When multiple technical approaches are possible, recommend one and briefly explain why.
- Do not overwhelm the user with unnecessary implementation theory.
- Clearly tell the user when physical hardware information or manual testing is required before continuing.