"""Geometry tests: a synthetic head is simulated with the forward model, then recovered."""

import pytest

from lichtwerk.fixtures import MotionRange
from lichtwerk.positioning import (
    AimError,
    HeadCalibration,
    HeadLimits,
    HeadSetup,
    NotConfiguredError,
    Observation,
    Point,
    aim_at,
    beam_direction,
    beam_floor_point,
    fit_calibration,
)

MOTION = MotionRange(pan_degrees=540, tilt_degrees=210)
POSITION = Point(x=2.0, y=3.0, z=4.0)
FLOOR_CORNERS = [
    Point(x=0.0, y=0.0, z=0.0),
    Point(x=6.0, y=0.0, z=0.0),
    Point(x=0.0, y=8.0, z=0.0),
    Point(x=6.0, y=8.0, z=0.0),
]


def setup(pan_sign: int = 1, tilt_sign: int = 1, **kwargs) -> HeadSetup:
    return HeadSetup(
        position=POSITION,
        calibration=HeadCalibration(
            pan_sign=pan_sign, tilt_sign=tilt_sign, pan_zero_azimuth_deg=-40.0, tilt_axis_deg=100.0
        ),
        **kwargs,
    )


def test_aim_then_forward_model_hits_the_target() -> None:
    head = setup()
    for target in FLOOR_CORNERS + [Point(x=3.0, y=4.0, z=1.0)]:
        aim = aim_at(head, MOTION, target)
        hit = beam_direction(head, MOTION, aim.pan, aim.tilt)
        dx, dy, dz = target.x - 2.0, target.y - 3.0, target.z - 4.0
        norm = (dx * dx + dy * dy + dz * dz) ** 0.5
        assert hit == pytest.approx((dx / norm, dy / norm, dz / norm), abs=1e-6)


def test_floor_point_matches_aimed_target() -> None:
    head = setup()
    aim = aim_at(head, MOTION, FLOOR_CORNERS[1])

    hit = beam_floor_point(head, MOTION, aim.pan, aim.tilt)

    assert (hit.x, hit.y, hit.z) == pytest.approx((6.0, 0.0, 0.0), abs=1e-6)


def test_aim_takes_the_short_pan_way() -> None:
    head = setup()
    target = FLOOR_CORNERS[2]
    near_low = aim_at(head, MOTION, target, prefer_pan=0.0)
    near_high = aim_at(head, MOTION, target, prefer_pan=1.0)

    assert abs(near_low.pan - 0.0) <= abs(near_high.pan - 0.0)
    assert abs(near_high.pan - 1.0) <= abs(near_low.pan - 1.0)


def test_limits_make_targets_unreachable() -> None:
    head = setup(limits=HeadLimits(tilt_min=0.0, tilt_max=0.1))

    with pytest.raises(AimError, match="cannot be reached"):
        aim_at(head, MOTION, FLOOR_CORNERS[0])


def test_uncalibrated_head_refuses() -> None:
    with pytest.raises(NotConfiguredError):
        aim_at(HeadSetup(position=POSITION), MOTION, FLOOR_CORNERS[0])
    with pytest.raises(NotConfiguredError):
        aim_at(HeadSetup(), MOTION, FLOOR_CORNERS[0])


@pytest.mark.parametrize("orientation", ["ceiling", "floor"])
@pytest.mark.parametrize("pan_sign", [1, -1])
@pytest.mark.parametrize("tilt_sign", [1, -1])
def test_fit_recovers_synthetic_calibration(orientation, pan_sign, tilt_sign) -> None:
    truth = HeadSetup(
        orientation=orientation,
        position=POSITION if orientation == "ceiling" else Point(x=2.0, y=3.0, z=0.2),
        calibration=HeadCalibration(
            pan_sign=pan_sign, tilt_sign=tilt_sign, pan_zero_azimuth_deg=25.0, tilt_axis_deg=95.0
        ),
    )
    targets = FLOOR_CORNERS if orientation == "ceiling" else [Point(x=x, y=y, z=3.0) for x, y in ((0, 0), (6, 0), (0, 8), (6, 8))]
    observations = []
    for target in targets:
        aim = aim_at(truth, MOTION, target)
        observations.append(Observation(pan=aim.pan, tilt=aim.tilt, target=target))

    fit = fit_calibration(orientation, truth.position, MOTION, observations)

    assert fit.acceptable
    assert fit.max_deg < 0.01
    # Tilt sign and a 180 degree pan shift describe the same physical head, so compare
    # what matters: the beam direction for arbitrary pan/tilt.
    recovered = HeadSetup(orientation=orientation, position=truth.position, calibration=fit.calibration)
    for pan, tilt in ((0.1, 0.3), (0.5, 0.5), (0.9, 0.7), (0.3, 0.2)):
        assert beam_direction(recovered, MOTION, pan, tilt) == pytest.approx(
            beam_direction(truth, MOTION, pan, tilt), abs=1e-6
        )


def test_fit_flags_inconsistent_observations() -> None:
    truth = setup()
    observations = []
    for index, target in enumerate(FLOOR_CORNERS):
        aim = aim_at(truth, MOTION, target)
        pan = aim.pan + (0.15 if index == 0 else 0.0)  # one wrongly recorded corner
        observations.append(Observation(pan=min(pan, 1.0), tilt=aim.tilt, target=target))

    fit = fit_calibration("ceiling", POSITION, MOTION, observations)

    assert not fit.acceptable
    assert max(fit.residuals_deg) > 3.0


def test_fit_is_not_spoiled_by_a_target_almost_below_the_head() -> None:
    # Club layout: a floor corner lies ~5 cm beside the point straight under the head, where
    # pan barely matters; the operator's pan there is essentially arbitrary.
    position = Point(x=1.77, y=2.5, z=2.13)
    truth = HeadSetup(
        position=position,
        calibration=HeadCalibration(pan_zero_azimuth_deg=-80.0, tilt_axis_deg=105.0),
    )
    corners = [Point(x=x, y=y, z=0.0) for x, y in ((1.75, 2.45), (1.75, 4.2), (4.4, 2.45), (4.4, 4.2))]
    observations = []
    for index, target in enumerate(corners):
        aim = aim_at(truth, MOTION, target)
        pan = aim.pan + (35.0 / MOTION.pan_degrees if index == 0 else 0.0)
        observations.append(Observation(pan=pan, tilt=aim.tilt, target=target))

    fit = fit_calibration("ceiling", position, MOTION, observations)

    assert fit.acceptable


def test_fit_needs_enough_observations() -> None:
    with pytest.raises(NotConfiguredError, match="at least 3"):
        fit_calibration("ceiling", POSITION, MOTION, [])
