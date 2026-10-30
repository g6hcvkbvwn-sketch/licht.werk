"""Logical helpers for independently addressable fixture ring sections."""

from typing import Final

from .patch import ResolvedFixture

RING_COLOURS: Final[tuple[str, ...]] = ("red", "green", "blue")
RING_SEGMENTS: Final[int] = 6


def segment_attribute(index: int, colour: str) -> str:
    """Return a canonical ring section attribute using a one-based segment index."""
    if isinstance(index, bool) or not 1 <= index <= RING_SEGMENTS:
        raise ValueError(f"Ring segment index must be between 1 and {RING_SEGMENTS}.")
    if colour not in RING_COLOURS:
        raise ValueError(f"Ring colour must be one of {', '.join(RING_COLOURS)}.")
    return f"ring_{index}_{colour}"


def segment_participant(fixture_id: str, index: int) -> str:
    """Return the deterministic runtime participant ID for one ring section."""
    if isinstance(index, bool) or not 1 <= index <= RING_SEGMENTS:
        raise ValueError(f"Ring segment index must be between 1 and {RING_SEGMENTS}.")
    return f"{fixture_id}:ring:{index}"


def parse_segment_participant(participant_id: str) -> tuple[str, int] | None:
    """Resolve a pseudo-fixture ID to its physical fixture and one-based section."""
    fixture_id, separator, suffix = participant_id.rpartition(":ring:")
    if not separator or not fixture_id or not suffix.isdecimal():
        return None
    index = int(suffix)
    if not 1 <= index <= RING_SEGMENTS:
        return None
    return fixture_id, index


def ring_segments(fixture: ResolvedFixture) -> list[int]:
    """List addressable ring participants for a patched fixture.

    Six-section modes expose the canonical section attributes. Older modes with
    one uniform RGB ring use a single participant and retain their legacy channels.
    """
    attributes = set(fixture.mode.attributes())
    segments = [
        index for index in range(1, RING_SEGMENTS + 1)
        if all(segment_attribute(index, colour) in attributes for colour in RING_COLOURS)
    ]
    if segments:
        return segments
    if all(f"ring_{colour}" in attributes for colour in RING_COLOURS):
        return [1]
    return []


def segment_colour_attributes(fixture: ResolvedFixture, index: int) -> tuple[str, str, str]:
    """Return DMX-backed RGB attributes for a ring participant."""
    segments = ring_segments(fixture)
    if index not in segments:
        raise ValueError(f"Fixture '{fixture.patch.id}' has no ring segment {index}.")
    if len(segments) == 1 and not all(
        segment_attribute(index, colour) in fixture.mode.attributes()
        for colour in RING_COLOURS
    ):
        return tuple(f"ring_{colour}" for colour in RING_COLOURS)
    return tuple(segment_attribute(index, colour) for colour in RING_COLOURS)


def expand_legacy_ring_values(
    fixture: ResolvedFixture, values: dict[str, float]
) -> dict[str, float]:
    """Fan legacy uniform RGB writes out to every section, preserving explicit overrides."""
    out = dict(values)
    sections = [
        index for index in range(1, RING_SEGMENTS + 1)
        if all(segment_attribute(index, colour) in fixture.mode.attributes()
               for colour in RING_COLOURS)
    ]
    if not sections:
        return out
    for colour in RING_COLOURS:
        legacy = f"ring_{colour}"
        if legacy in values:
            for index in sections:
                out.setdefault(segment_attribute(index, colour), values[legacy])
    return out


def expand_legacy_ring_attributes(
    fixture: ResolvedFixture, attributes: set[str]
) -> set[str]:
    """Expand uniform RGB attribute names into the corresponding section attributes."""
    out = set(attributes)
    segments = [
        index for index in range(1, RING_SEGMENTS + 1)
        if all(segment_attribute(index, colour) in fixture.mode.attributes()
               for colour in RING_COLOURS)
    ]
    if not segments:
        return out
    for colour in RING_COLOURS:
        if f"ring_{colour}" in attributes:
            out.update(segment_attribute(index, colour) for index in segments)
    return out
