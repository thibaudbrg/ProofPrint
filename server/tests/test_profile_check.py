"""Tests for check 6 (profile-turn challenge, simplified). Geometry is driven
with synthetic yaw tracks (no consented faces in the repo).
Run from server/:  ./.venv/bin/python -m pytest tests -q
"""
import numpy as np
import profile_check as pc


def track(yaws):
    return [None if y is None else {"yaw": y} for y in yaws]


def ramp(peak, n=24, sign=+1.0):
    return list(sign * peak * np.sin(np.linspace(0, np.pi, n)))


def test_correct_side_full_turn_passes():
    r = pc.analyse(track(ramp(0.25)), "left")
    assert r["verdict"] == "pass", r
    assert r["peak_ok"] >= pc.TURN_FULL


def test_wrong_side_is_hard_fail():
    r = pc.analyse(track(ramp(0.25, sign=-1)), "left")
    assert r["verdict"] == "fail" and r["reason"] == "wrong_side", r


def test_no_turn_is_insufficient_not_fail():
    r = pc.analyse(track([0.01, -0.02, 0.0, 0.02, 0.01, 0.0, -0.01, 0.0, 0.01, 0.0]), "right")
    assert r["verdict"] == "insufficient" and r["reason"] == "no_turn", r


def test_face_lost_after_trending_counts_as_full_profile():
    yaws = list(0.14 * np.linspace(0, 1, 10)) + [None] * 6      # turning left, then YuNet loses it
    r = pc.analyse(track(yaws), "left")
    assert r["lost_at_profile"] is True
    assert r["verdict"] == "pass", r


def test_snap_back_is_penalised():
    yaws = ramp(0.25)
    yaws[12] = -0.05                                             # face-swap glitch mid-turn
    r = pc.analyse(track(yaws), "left")
    assert "yaw_snap" in r["flags"]
    clean = pc.analyse(track(ramp(0.25)), "left")
    assert r["score"] < clean["score"]


def test_too_few_frames_is_insufficient():
    assert pc.analyse(track(ramp(0.25, n=5)), "left")["verdict"] == "insufficient"


def test_disabled_and_missing():
    assert pc.check(None, "left", enabled=False)["verdict"] == "absent"
    assert pc.check([], "left", enabled=True)["verdict"] == "absent"
    assert pc.check([], "left", enabled=True)["ok"] is False


def test_modest_real_turn_passes_review_band():
    # The first real-phone run measured peak_ok=0.171 on a turn the user
    # considered "modest" (not a full 90 deg). That should land at least in
    # review, not be discarded as insufficient.
    yaws = ramp(0.171)
    r = pc.analyse(track(yaws), "left")
    assert r["verdict"] in ("pass", "review"), r


def test_blank_frame_has_no_face():
    assert pc._landmarks(np.zeros((240, 320, 3), np.uint8)) is None
