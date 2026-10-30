"""Moving-head geometry: aim at a point, predict where a head points, fit calibration.

Pure calculations in degrees and meters; nothing here knows about DMX or I/O.

Convention (see project.md, "Room coordinates and moving-head model"):
- Room coordinates are meters. z points up.
- A head is mounted on the ceiling (hanging, beam axis points down) or on the floor
  (standing, beam axis points up). Its `position` is where it sits.
- Logical pan/tilt 0.0-1.0 span the mechanical range from the fixture definition.
- Beam azimuth (direction in the floor plane, counter-clockwise from +x) is
  `pan_zero_azimuth_deg + pan_sign * pan_degrees`.
- The angle between the beam and the mounting axis is
  `tilt_sign * (tilt_degrees - tilt_axis_deg)`.
- `pan_zero_azimuth_deg` and `tilt_axis_deg` are measured per head by calibration; signs
  describe inversion. Theory alone does not point real heads, so nothing is assumed.
"""

import math
from dataclasses import dataclass
from itertools import product
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fixtures import MotionRange

# Calibration is called acceptable when no observation is further off than this.
ACCEPTABLE_RESIDUAL_DEG = 3.0
MIN_OBSERVATIONS = 3
MAX_OBSERVATIONS = 8


class PositioningError(Exception):
    """Base class for expected positioning problems."""


class NotConfiguredError(PositioningError):
    """Mounting, calibration or target data is missing; the user must supply it."""


class AimError(PositioningError):
    """The requested direction cannot be reached within range and limits."""


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    z: float = Field(allow_inf_nan=False)


class HeadLimits(BaseModel):
    """Logical pan/tilt window the head may be driven into (0.0-1.0 = full mechanical range)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pan_min: float = Field(default=0.0, ge=0.0, le=1.0)
    pan_max: float = Field(default=1.0, ge=0.0, le=1.0)
    tilt_min: float = Field(default=0.0, ge=0.0, le=1.0)
    tilt_max: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> "HeadLimits":
        if self.pan_min >= self.pan_max or self.tilt_min >= self.tilt_max:
            raise ValueError("Limits need min < max for pan and for tilt.")
        return self

    def clamp_pan(self, value: float) -> float:
        return min(max(value, self.pan_min), self.pan_max)

    def clamp_tilt(self, value: float) -> float:
        return min(max(value, self.tilt_min), self.tilt_max)


class HeadCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pan_sign: Literal[1, -1] = 1
    tilt_sign: Literal[1, -1] = 1
    # None means "not calibrated yet".
    pan_zero_azimuth_deg: Optional[float] = Field(default=None, allow_inf_nan=False)
    tilt_axis_deg: Optional[float] = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _both_or_none(self) -> "HeadCalibration":
        if (self.pan_zero_azimuth_deg is None) != (self.tilt_axis_deg is None):
            raise ValueError("pan_zero_azimuth_deg and tilt_axis_deg are set together or not at all.")
        return self


class HeadRest(BaseModel):
    """Safe resting pan/tilt (logical), e.g. beam toward the ceiling. Found at the club."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pan: float = Field(ge=0.0, le=1.0)
    tilt: float = Field(ge=0.0, le=1.0)


class HeadSetup(BaseModel):
    """Everything LichtWerk knows about where one moving head is and how it is calibrated."""

    model_config = ConfigDict(extra="forbid", strict=True)

    orientation: Literal["ceiling", "floor"] = "ceiling"
    position: Optional[Point] = None
    calibration: HeadCalibration = Field(default_factory=HeadCalibration)
    limits: HeadLimits = Field(default_factory=HeadLimits)
    rest: Optional[HeadRest] = None

    @property
    def calibrated(self) -> bool:
        return self.position is not None and self.calibration.pan_zero_azimuth_deg is not None


@dataclass(frozen=True)
class Aim:
    pan: float
    tilt: float


@dataclass(frozen=True)
class Observation:
    """The head was driven to this pan/tilt and visibly pointed at `target`."""

    pan: float
    tilt: float
    target: Point


@dataclass(frozen=True)
class CalibrationFit:
    calibration: HeadCalibration
    residuals_deg: tuple[float, ...]
    rms_deg: float
    max_deg: float

    @property
    def acceptable(self) -> bool:
        return self.max_deg <= ACCEPTABLE_RESIDUAL_DEG


def _axis_sign(orientation: str) -> float:
    return -1.0 if orientation == "ceiling" else 1.0


def _direction(
    orientation: str,
    motion: MotionRange,
    pan_zero: float,
    tilt_axis: float,
    pan_sign: int,
    tilt_sign: int,
    pan: float,
    tilt: float,
) -> tuple[float, float, float]:
    azimuth = math.radians(pan_zero + pan_sign * pan * motion.pan_degrees)
    alpha = math.radians(tilt_sign * (tilt * motion.tilt_degrees - tilt_axis))
    return (
        math.sin(alpha) * math.cos(azimuth),
        math.sin(alpha) * math.sin(azimuth),
        _axis_sign(orientation) * math.cos(alpha),
    )


def _require_calibrated(setup: HeadSetup) -> tuple[Point, float, float]:
    cal = setup.calibration
    if setup.position is None:
        raise NotConfiguredError("The head's mounting position is not configured.")
    if cal.pan_zero_azimuth_deg is None or cal.tilt_axis_deg is None:
        raise NotConfiguredError("The head is not calibrated yet.")
    return setup.position, cal.pan_zero_azimuth_deg, cal.tilt_axis_deg


def beam_direction(
    setup: HeadSetup, motion: MotionRange, pan: float, tilt: float
) -> tuple[float, float, float]:
    """Unit vector the beam points along for a logical pan/tilt (calibrated heads only)."""
    _, pan_zero, tilt_axis = _require_calibrated(setup)
    cal = setup.calibration
    return _direction(
        setup.orientation, motion, pan_zero, tilt_axis, cal.pan_sign, cal.tilt_sign, pan, tilt
    )


def beam_floor_point(
    setup: HeadSetup, motion: MotionRange, pan: float, tilt: float
) -> Optional[Point]:
    """Where the beam would hit the floor (z = 0), or None if it points level or upward."""
    position, _, _ = _require_calibrated(setup)
    dx, dy, dz = beam_direction(setup, motion, pan, tilt)
    if dz >= -1e-9 or position.z <= 0:
        return None
    t = -position.z / dz
    return Point(x=position.x + t * dx, y=position.y + t * dy, z=0.0)


def aim_at(
    setup: HeadSetup, motion: MotionRange, target: Point, prefer_pan: float = 0.5
) -> Aim:
    """Pan/tilt that point a calibrated head at `target`, inside range and limits.

    A direction can often be reached two ways (tilt past vertical with the pan turned by
    180 degrees); the pan is also ambiguous by 360 degrees. Among reachable options the one
    closest to `prefer_pan` is used, so the head takes the short way.
    """
    position, pan_zero, tilt_axis = _require_calibrated(setup)
    cal = setup.calibration
    dx, dy, dz = target.x - position.x, target.y - position.y, target.z - position.z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance < 1e-6:
        raise AimError("The target is at the head's own position.")
    alpha = math.degrees(math.acos(max(-1.0, min(1.0, _axis_sign(setup.orientation) * dz / distance))))
    if math.hypot(dx, dy) / distance < 1e-9:
        azimuth = pan_zero + cal.pan_sign * prefer_pan * motion.pan_degrees
    else:
        azimuth = math.degrees(math.atan2(dy, dx))

    best: Optional[Aim] = None
    for branch_alpha, branch_azimuth in ((alpha, azimuth), (-alpha, azimuth + 180.0)):
        tilt_deg = tilt_axis + cal.tilt_sign * branch_alpha
        if not -1e-9 <= tilt_deg <= motion.tilt_degrees + 1e-9:
            continue
        tilt = min(max(tilt_deg / motion.tilt_degrees, 0.0), 1.0)
        if not setup.limits.tilt_min - 1e-9 <= tilt <= setup.limits.tilt_max + 1e-9:
            continue
        base = (cal.pan_sign * (branch_azimuth - pan_zero)) % 360.0
        for turns in range(-2, 3):
            pan_deg = base + 360.0 * turns
            if not -1e-9 <= pan_deg <= motion.pan_degrees + 1e-9:
                continue
            pan = min(max(pan_deg / motion.pan_degrees, 0.0), 1.0)
            if not setup.limits.pan_min - 1e-9 <= pan <= setup.limits.pan_max + 1e-9:
                continue
            if best is None or abs(pan - prefer_pan) < abs(best.pan - prefer_pan):
                best = Aim(pan=pan, tilt=tilt)
    if best is None:
        raise AimError(
            "That target cannot be reached within this head's pan/tilt range and movement "
            f"limits (it would need a beam angle of {alpha:.0f} degrees from the mounting axis)."
        )
    return best


def _angle_between(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = sum(p * q for p, q in zip(a, b))
    norm = math.sqrt(sum(p * p for p in a)) * math.sqrt(sum(q * q for q in b))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norm))))


def fit_calibration(
    orientation: str,
    position: Point,
    motion: MotionRange,
    observations: list[Observation],
) -> CalibrationFit:
    """Find pan zero, tilt axis and inversion that best explain where the head was pointed.

    Needs at least three observations (four room corners is ideal). All four sign
    combinations are tried and the one with the smallest error is kept; the per-observation
    error in degrees is returned so a wrong setup is visible instead of silently used.
    """
    if len(observations) > MAX_OBSERVATIONS:
        raise NotConfiguredError(f"Use at most {MAX_OBSERVATIONS} observations for calibration.")
    if len(observations) < MIN_OBSERVATIONS:
        raise NotConfiguredError(
            f"Calibration needs at least {MIN_OBSERVATIONS} observations; got {len(observations)}."
        )
    axis = _axis_sign(orientation)
    geometry = []
    for obs in observations:
        dx, dy, dz = obs.target.x - position.x, obs.target.y - position.y, obs.target.z - position.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-6:
            raise AimError("An observation target is at the head's own position.")
        alpha = math.degrees(math.acos(max(-1.0, min(1.0, axis * dz / distance))))
        geometry.append((alpha, math.degrees(math.atan2(dy, dx)), (dx / distance, dy / distance, dz / distance)))

    best: Optional[CalibrationFit] = None
    # A head can reach a point either "straight" or tilted past vertical with the pan turned
    # 180 degrees, and the operator may have used either for each target. Try every mix.
    for pan_sign, tilt_sign, flips in product(
        (1, -1), (1, -1), product((False, True), repeat=len(observations))
    ):
        branches = [
            (-alpha, azimuth + 180.0) if flip else (alpha, azimuth)
            for flip, (alpha, azimuth, _) in zip(flips, geometry)
        ]
        tilt_axes = [
            obs.tilt * motion.tilt_degrees - tilt_sign * alpha
            for obs, (alpha, _) in zip(observations, branches)
        ]
        tilt_axis = sum(tilt_axes) / len(tilt_axes)
        sines = cosines = 0.0
        for obs, (alpha, azimuth), (true_alpha, _, _) in zip(observations, branches, geometry):
            # Near the mounting axis (e.g. a target straight below the head) the pan hardly
            # changes where the beam lands, so its pan reading says little: weight by how far
            # the target is off-axis.
            weight = math.sin(math.radians(true_alpha))
            if weight < 0.02:
                continue
            estimate = math.radians(azimuth - pan_sign * obs.pan * motion.pan_degrees)
            sines += weight * math.sin(estimate)
            cosines += weight * math.cos(estimate)
        if math.hypot(sines, cosines) < 1e-9:
            continue
        pan_zero = math.degrees(math.atan2(sines, cosines))
        residuals = tuple(
            _angle_between(
                _direction(
                    orientation, motion, pan_zero, tilt_axis, pan_sign, tilt_sign, obs.pan, obs.tilt
                ),
                unit,
            )
            for obs, (_, _, unit) in zip(observations, geometry)
        )
        rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
        # Prefer the plain branch on ties so results are stable.
        if best is not None and rms >= best.rms_deg - 1e-9:
            continue
        best = CalibrationFit(
            calibration=HeadCalibration(
                pan_sign=pan_sign,
                tilt_sign=tilt_sign,
                pan_zero_azimuth_deg=pan_zero,
                tilt_axis_deg=tilt_axis,
            ),
            residuals_deg=residuals,
            rms_deg=rms,
            max_deg=max(residuals),
        )
    if best is None:
        raise AimError(
            "The observations do not determine the pan direction (all targets lie along the "
            "mounting axis). Use targets spread around the head."
        )
    return best
