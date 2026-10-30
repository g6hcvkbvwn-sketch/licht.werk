"""Conversion between a strobe rate in Hz and a fixture's logical strobe level (0-1).

Uses the Hz range documented in the fixture definition (`min_hz`/`max_hz` on the strobe
channel), treated as linear until measured. Fixtures without a documented range use a rough
fallback.
"""

from typing import Optional

from .patch import ResolvedFixture

# Used for fixtures whose definition gives no Hz range (Equinox): a rough linear guess, to be
# measured at the club.
FALLBACK_STROBE_HZ = (1.0, 20.0)
# Smallest logical level that still means "strobe on" (maps to the fixture's slowest rate).
MIN_STROBE_LEVEL = 1 / 255


def _hz_range(fixture: ResolvedFixture, attribute: str = "strobe") -> Optional[tuple[float, float]]:
    channel = next((c for c in fixture.mode.channels if c.attribute == attribute), None)
    if channel is None:
        return None
    if channel.min_hz is not None and channel.max_hz is not None:
        return channel.min_hz, channel.max_hz
    return FALLBACK_STROBE_HZ


def strobe_level(fixture: ResolvedFixture, hz: float, attribute: str = "strobe") -> Optional[float]:
    """Logical strobe level for a flash rate; None if the fixture has no strobe channel."""
    hz_range = _hz_range(fixture, attribute)
    if hz_range is None:
        return None
    low, high = hz_range
    return min(max((hz - low) / (high - low), MIN_STROBE_LEVEL), 1.0)


def strobe_hz(
    fixture: ResolvedFixture, level: Optional[float], attribute: str = "strobe",
) -> Optional[float]:
    """Approximate flash rate for a logical strobe level; None when strobe is off or absent."""
    hz_range = _hz_range(fixture, attribute)
    if hz_range is None or not level or level <= 0:
        return None
    low, high = hz_range
    return max(low + min(level, 1.0) * (high - low), 0.1)
