"""Synthetic tests for check 4 (gyro <-> video).

We render a textured scene and pan a 320x240 window across it exactly as a
camera would if the phone rotated with the synthetic gyro trace. That gives a
ground-truth "genuine" burst, and every attack is a broken version of it.
Run from server/:  ./.venv/bin/python -m pytest tests -q
"""
import numpy as np
import cv2
import motion_check as mc

RNG = np.random.default_rng(7)
SCENE = cv2.resize(RNG.integers(0, 255, (60, 80, 3), dtype=np.uint8), (1600, 1200),
                   interpolation=cv2.INTER_CUBIC)     # smooth, textured background
F_PX = 400.0            # focal length in px for the 320-wide window
W, H = 320, 240
FPS, HZ, DUR = 12, 60, 3.0


def gyro_trace(yaw_amp=60.0, pitch_amp=20.0, hz=HZ, dur=DUR, seed=1):
    t = np.arange(0, dur, 1.0 / hz)
    ry = yaw_amp * np.sin(2 * np.pi * 0.7 * t + seed)          # deg/s, yaw
    rx = pitch_amp * np.sin(2 * np.pi * 0.45 * t + 2 * seed)   # deg/s, pitch
    return [{"t": float(ti * 1000), "rx": float(a), "ry": float(b), "rz": 0.0}
            for ti, a, b in zip(t, rx, ry)]


def render_frames(motion, fps=FPS, lag_ms=0.0, dur=DUR, static=False, scale=1.0):
    """Integrate the gyro to an angle and pan the window accordingly."""
    mt = np.array([m["t"] for m in motion]) / 1000.0
    ry = np.array([m["ry"] for m in motion]); rx = np.array([m["rx"] for m in motion])
    ft = np.arange(0, dur, 1.0 / fps)
    frames = []
    cx0, cy0 = SCENE.shape[1] // 2 - W // 2, SCENE.shape[0] // 2 - H // 2
    for tf in ft:
        yaw = np.trapezoid(np.interp(np.linspace(0, tf, 200), mt, ry), dx=tf / 199) if tf > 0 else 0.0
        pitch = np.trapezoid(np.interp(np.linspace(0, tf, 200), mt, rx), dx=tf / 199) if tf > 0 else 0.0
        if static:
            yaw = pitch = 0.0
        dx = int(round(scale * F_PX * np.tan(np.radians(yaw))))
        dy = int(round(scale * F_PX * np.tan(np.radians(pitch))))
        x, y = cx0 + dx, cy0 + dy
        crop = SCENE[y:y + H, x:x + W].copy()
        crop = np.clip(crop.astype(np.int16) + RNG.integers(-4, 5, crop.shape), 0, 255).astype(np.uint8)
        frames.append((float((tf * 1000) + lag_ms), crop))
    return frames


def test_genuine_burst_passes():
    m = gyro_trace()
    r = mc.check(m, render_frames(m))
    assert r["verdict"] == "pass", r
    assert r["score"] >= mc.SCORE_PASS
    assert abs(r["lag_ms"]) <= 40


def test_camera_lag_is_recovered():
    m = gyro_trace(seed=3)
    r = mc.check(m, render_frames(m, lag_ms=96))
    assert r["verdict"] == "pass", r
    assert 60 <= r["lag_ms"] <= 130, r["lag_ms"]


def test_injected_static_video_with_moving_phone_fails():
    # Attacker replays a still deepfake while waving the phone around.
    m = gyro_trace(seed=5)
    r = mc.check(m, render_frames(m, static=True))
    assert r["verdict"] == "fail", r


def test_injected_moving_video_with_still_phone_fails():
    # Phone flat on the desk (gyro ~0) but the injected video pans.
    m_real = gyro_trace(seed=2)
    frames = render_frames(m_real)
    flat = [{**s, "rx": RNG.normal(0, 0.5), "ry": RNG.normal(0, 0.5)} for s in m_real]
    r = mc.check(flat, frames)
    assert r["verdict"] == "fail", r


def test_unrelated_motion_fails():
    # Video from one movement, gyro from a different one (pre-recorded attack).
    m_video = gyro_trace(seed=1)
    m_gyro = gyro_trace(yaw_amp=50, pitch_amp=50, seed=9)
    m_gyro = [{**s, "ry": 50 * np.sin(2 * np.pi * 1.9 * s["t"] / 1000 + 1.0)} for s in m_gyro]
    r = mc.check(m_gyro, render_frames(m_video))
    assert r["verdict"] in ("fail", "review"), r
    assert r["score"] < mc.SCORE_PASS


def test_phone_never_moved_is_insufficient_not_fail():
    m = [{"t": i * 1000 / HZ, "rx": 0.2, "ry": -0.1, "rz": 0.0} for i in range(int(HZ * DUR))]
    r = mc.check(m, render_frames(m, static=True))
    assert r["verdict"] == "insufficient", r


def test_missing_burst_is_absent():
    assert mc.check(None, None)["verdict"] == "absent"
    assert mc.check([], [])["verdict"] == "absent"


def test_too_few_frames_is_insufficient():
    m = gyro_trace()
    r = mc.check(m, render_frames(m)[:4])
    assert r["verdict"] == "insufficient"
