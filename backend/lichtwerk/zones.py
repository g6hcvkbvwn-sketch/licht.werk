"""Zones: named rectangles on a room surface, and where a beam hits the room.

Pure geometry in meters; nothing here knows about DMX, files or fixtures.

The room is the box from (0, 0, 0) to (size_x, size_y, size_z), see project.md section 5.4.
A zone lies on one of the six surfaces. Its `corner` holds the two in-plane room
coordinates of the corner closest to the origin; `length` and `width` run along the
surface's two axes (table below).
"""

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fixtures import ID_PATTERN
from .positioning import Point

Surface = Literal["floor", "ceiling", "booth_wall", "far_wall", "left_wall", "right_wall"]
RoomSize = tuple[float, float, float]
AXES = ("x", "y", "z")

# surface -> (axis fixed by the plane, plane at the far side of the room?, length axis, width axis)
SURFACES: dict[str, tuple[str, bool, str, str]] = {
    "floor": ("z", False, "x", "y"),
    "ceiling": ("z", True, "x", "y"),
    "booth_wall": ("y", False, "x", "z"),
    "far_wall": ("y", True, "x", "z"),
    "left_wall": ("x", False, "y", "z"),
    "right_wall": ("x", True, "y", "z"),
}

# Points this close to a zone border or plane count as on it (meters).
TOLERANCE = 1e-6


class Zone(BaseModel):
    """`include`: a head's beam spot is kept inside. `exclude`: the beam must never enter."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    surface: Surface
    mode: Literal["include", "exclude"] = "exclude"
    corner: dict[str, float]
    length: float = Field(gt=0, allow_inf_nan=False)
    width: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _corner_axes(self) -> "Zone":
        _, _, length_axis, width_axis = SURFACES[self.surface]
        if set(self.corner) != {length_axis, width_axis}:
            raise ValueError(
                f"Zone '{self.id}' on the {self.surface.replace('_', ' ')}: the corner needs "
                f"exactly '{length_axis}' and '{width_axis}' (meters)."
            )
        for axis, value in self.corner.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Zone '{self.id}': corner {axis} must be a number of 0 m or more.")
        return self

    @property
    def axes(self) -> tuple[str, str]:
        _, _, length_axis, width_axis = SURFACES[self.surface]
        return length_axis, width_axis

    def plane(self, size: RoomSize) -> tuple[str, float]:
        axis, far, _, _ = SURFACES[self.surface]
        return axis, size[AXES.index(axis)] if far else 0.0

    def fit_problem(self, size: tuple[Optional[float], Optional[float], Optional[float]]) -> Optional[str]:
        """Why the zone does not fit on its surface, or None (unknown room sizes are skipped)."""
        length_axis, width_axis = self.axes
        for axis, extent in ((length_axis, self.length), (width_axis, self.width)):
            limit = size[AXES.index(axis)]
            end = self.corner[axis] + extent
            if limit is not None and end > limit + TOLERANCE:
                return (
                    f"zone '{self.id}' reaches {axis} = {end:g} m, beyond the room "
                    f"({limit:g} m along {axis})"
                )
        return None

    def contains(self, point: Point, size: RoomSize) -> bool:
        """True if `point` lies on this zone's surface, inside the rectangle (borders included)."""
        axis, plane = self.plane(size)
        if abs(getattr(point, axis) - plane) > TOLERANCE:
            return False
        length_axis, width_axis = self.axes
        u = getattr(point, length_axis) - self.corner[length_axis]
        v = getattr(point, width_axis) - self.corner[width_axis]
        return -TOLERANCE <= u <= self.length + TOLERANCE and -TOLERANCE <= v <= self.width + TOLERANCE

    def point_at(self, u: float, v: float, size: RoomSize) -> Point:
        """Room point at fraction `u` along the length and `v` along the width (0.0-1.0)."""
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            raise ValueError("Spot position must be between 0.0 and 1.0 in both directions.")
        axis, plane = self.plane(size)
        length_axis, width_axis = self.axes
        values = {
            axis: plane,
            length_axis: self.corner[length_axis] + u * self.length,
            width_axis: self.corner[width_axis] + v * self.width,
        }
        return Point(x=values["x"], y=values["y"], z=values["z"])


def inside_room(point: Point, size: RoomSize) -> bool:
    return all(
        -TOLERANCE <= getattr(point, axis) <= limit + TOLERANCE for axis, limit in zip(AXES, size)
    )


def room_hit(origin: Point, direction: tuple[float, float, float], size: RoomSize) -> Optional[Point]:
    """Where a ray from `origin` (inside the room) first hits a wall, floor or ceiling.

    Returns None if the origin lies outside the room box.
    """
    if not inside_room(origin, size):
        return None
    start = (origin.x, origin.y, origin.z)
    best: Optional[float] = None
    for index, step in enumerate(direction):
        if abs(step) < 1e-12:
            continue
        plane = size[index] if step > 0 else 0.0
        distance = max((plane - start[index]) / step, 0.0)
        if best is None or distance < best:
            best = distance
    if best is None:
        return None
    coords = [min(max(s + best * d, 0.0), limit) for s, d, limit in zip(start, direction, size)]
    return Point(x=coords[0], y=coords[1], z=coords[2])
