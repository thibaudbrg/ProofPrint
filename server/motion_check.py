"""Check 4 — gyroscope <-> video motion consistency (the core check).

A deepfake pushed through a virtual camera copies a face, but the video does not
move when the *phone* moves. A real front camera on a real phone does: when the
phone rotates, the whole picture shifts the opposite way, and the shift is
proportional to the angular velocity the gyroscope reports at the same instant.

So the phone records, during a ~3 s "tilt your phone" burst:
  - low-res video frames with timestamps           (meta.frames + frame files)
  - devicemotion rotationRate samples, deg/s        (meta.motion)
and the server checks that the two agree.

Method (see notes: arXiv 2605.00218 for the IMU-as-auxiliary-signal idea):
  1. Global image shift between consecutive frames via phase correlation
     (cv2.phaseCorrelate on a windowed, downscaled grey frame) -> px/s.
  2. Gyro resampled to the frame-interval midpoints (+ a candidate lag).
  3. Pearson |r| between horizontal flow and yaw rate (rotation about the
     device y axis, DeviceMotion `gamma`) and between vertical flow and pitch
     rate (`beta`), each searched over lags in +-LAG_MAX_MS. Absolute value
     because axis sign depends on platform and camera mirroring.
  4. Score = energy-weighted mix of the two axes at the best lag, so the axis
     the user actually moved dominates.

Verdicts:
  pass          score >= SCORE_PASS, phone moved enough
  review        SCORE_REVIEW <= score < SCORE_PASS   -> step_up
  fail          score < SCORE_REVIEW, or video moves while gyro is flat -> block
  insufficient  too little data / phone never moved  -> step_up (ask to move)
  absent        client sent no burst at all          -> step_up

Units: DeviceMotionEvent.rotationRate is deg/s on iOS AND Android (W3C).
The rad/s trap is the Generic Sensor API `Gyroscope`, which we do not use.
"""
from __future__ import annotations
import numpy as np
import cv2

# --- thresholds (CALIBRATE on real phone bursts; these come from synthetic tests) ---
SCORE_PASS = 0.60
SCORE_REVIEW = 0.35
MIN_GYRO_RMS_DPS = 8.0       # below this the user did not really move the phone
MIN_FRAMES = 8
MIN_MOTION_SAMPLES = 10
LAG_MAX_MS = 300             # camera pipeline latency vs sensor timestamps
LAG_STEP_MS = 16
FLOW_WIDTH = 160             # downscale frames to this width before correlating
FLOW_STATIC_PXS = 4.0        # px/s below which the video is considered static


def _grey_small(bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    if w != FLOW_WIDTH:
        g = cv2.resize(g, (FLOW_WIDTH, max(1, int(h * FLOW_WIDTH / w))),
                       interpolation=cv2.INTER_AREA)
    return g.astype(np.float32)


def global_flow(frames: list[tuple[float, np.ndarray]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-interval global shift. Returns (t_mid ms, vx px/s, vy px/s), in the
    downscaled frame's pixel units."""
    greys = [_grey_small(f) for _, f in frames]
    win = cv2.createHanningWindow((greys[0].shape[1], greys[0].shape[0]), cv2.CV_32F)
    t_mid, vx, vy = [], [], []
    for i in range(1, len(frames)):
        dt = (frames[i][0] - frames[i - 1][0]) / 1000.0
        if dt <= 0:
            continue
        (dx, dy), _resp = cv2.phaseCorrelate(greys[i - 1], greys[i], win)
        t_mid.append((frames[i][0] + frames[i - 1][0]) / 2.0)
        vx.append(dx / dt)
        vy.append(dy / dt)
    return np.array(t_mid), np.array(vx), np.array(vy)


def _pearson_abs(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(abs(np.corrcoef(a, b)[0, 1]))


def correlate(t_mid, vx, vy, mt, rx, ry) -> dict:
    """Search the lag that best aligns gyro (rx=pitch/beta, ry=yaw/gamma) with
    flow (vx horizontal, vy vertical). Returns score, lag and the aligned series."""
    e_x = float(np.var(rx))   # pitch energy -> drives vertical flow
    e_y = float(np.var(ry))   # yaw energy   -> drives horizontal flow
    if e_x + e_y <= 0:
        return {"score": 0.0, "lag_ms": 0, "r_yaw": 0.0, "r_pitch": 0.0}
    best = None
    for lag in np.arange(-LAG_MAX_MS, LAG_MAX_MS + 1, LAG_STEP_MS):
        g_rx = np.interp(t_mid + lag, mt, rx)
        g_ry = np.interp(t_mid + lag, mt, ry)
        r_yaw = _pearson_abs(vx, g_ry)
        r_pitch = _pearson_abs(vy, g_rx)
        score = (e_y * r_yaw + e_x * r_pitch) / (e_x + e_y)
        if best is None or score > best["score"]:
            # lag_ms > 0 means the camera frames arrive LATER than the sensor
            # samples (the usual pipeline latency), so the gyro that explains a
            # frame is the one sampled `lag_ms` earlier.
            best = {"score": score, "lag_ms": int(-lag), "r_yaw": r_yaw, "r_pitch": r_pitch,
                    "gyro_yaw": g_ry, "gyro_pitch": g_rx}
    return best


def check(motion: list[dict] | None, frames: list[tuple[float, np.ndarray]] | None) -> dict:
    """motion: [{t, rx, ry, rz}] deg/s, t in ms. frames: [(t_ms, bgr)]. Returns a signal dict."""
    if not motion and not frames:
        return {"ok": False, "verdict": "absent", "score": 0.0, "reason": "no liveness burst"}
    motion = motion or []
    frames = sorted(frames or [], key=lambda f: f[0])
    if len(frames) < MIN_FRAMES or len(motion) < MIN_MOTION_SAMPLES:
        return {"ok": False, "verdict": "insufficient", "score": 0.0,
                "reason": f"frames={len(frames)} motion={len(motion)}"}

    ms = sorted(motion, key=lambda m: m["t"])
    mt = np.array([float(m["t"]) for m in ms])
    rx = np.array([float(m.get("rx") or 0.0) for m in ms])   # beta  (about x, pitch)
    ry = np.array([float(m.get("ry") or 0.0) for m in ms])   # gamma (about y, yaw)

    t_mid, vx, vy = global_flow(frames)
    if len(t_mid) < MIN_FRAMES - 1:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "reason": "bad frame timestamps"}

    gyro_rms = float(np.sqrt(np.mean(rx ** 2 + ry ** 2)))
    flow_rms = float(np.sqrt(np.mean(vx ** 2 + vy ** 2)))
    phone_moved = gyro_rms >= MIN_GYRO_RMS_DPS
    video_moved = flow_rms >= FLOW_STATIC_PXS

    if not phone_moved:
        # Video that moves on its own while the phone lies still is the injection
        # signature; a static phone AND a static video just means "please move".
        verdict = "fail" if video_moved else "insufficient"
        out = {"ok": False, "verdict": verdict, "score": 0.0, "lag_ms": 0,
               "gyro_rms_dps": round(gyro_rms, 1), "flow_rms_pxs": round(flow_rms, 1),
               "reason": "phone did not move" + (" but video did" if video_moved else "")}
        return out

    best = correlate(t_mid, vx, vy, mt, rx, ry)
    score = round(float(best["score"]), 3)
    if score >= SCORE_PASS:
        verdict = "pass"
    elif score >= SCORE_REVIEW:
        verdict = "review"
    else:
        verdict = "fail"

    # Aligned series for the analyst dashboard (two curves overlaid).
    keep = max(1, len(t_mid) // 60)
    series = {
        "t_ms": [round(float(t - t_mid[0])) for t in t_mid[::keep]],
        "flow_x": [round(float(v), 1) for v in vx[::keep]],
        "flow_y": [round(float(v), 1) for v in vy[::keep]],
        "gyro_yaw": [round(float(v), 1) for v in best["gyro_yaw"][::keep]],
        "gyro_pitch": [round(float(v), 1) for v in best["gyro_pitch"][::keep]],
    }
    print(f"[motion_check] score={score:.3f} verdict={verdict} lag={best['lag_ms']}ms "
          f"r_yaw={best['r_yaw']:.2f} r_pitch={best['r_pitch']:.2f} "
          f"gyro_rms={gyro_rms:.1f}dps flow_rms={flow_rms:.1f}px/s "
          f"frames={len(frames)} motion={len(motion)}")
    return {"ok": verdict == "pass", "verdict": verdict, "score": score,
            "lag_ms": best["lag_ms"], "r_yaw": round(best["r_yaw"], 3),
            "r_pitch": round(best["r_pitch"], 3),
            "gyro_rms_dps": round(gyro_rms, 1), "flow_rms_pxs": round(flow_rms, 1),
            "n_frames": len(frames), "n_motion": len(motion), "series": series}
