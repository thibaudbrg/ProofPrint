"""Check 4 - gyroscope <-> video motion consistency (the core check).

A deepfake pushed through a virtual camera copies a face, but the video does not
move when the *phone* moves. A real front camera on a real phone does: when the
phone rotates, the BACKGROUND shifts the opposite way, proportional to the
angular velocity the gyroscope reports at the same instant.

The phone records, during a short "tilt your phone" burst:
  - low-res video frames with timestamps                  (meta.frames + frame files)
  - devicemotion rotationRate (deg/s) and acceleration    (meta.motion)
and the server checks that the two agree.

Method (arXiv 2605.00218 "Selfie-capture dynamics": IMU as an auxiliary signal;
raw acceleration is the most informative channel and *stationary proxies* -
phone on a stand, emulator - are rejected outright):
  1. Sparse optical flow (Shi-Tomasi + Lucas-Kanade) on BACKGROUND points only.
     The face box (YuNet, first frame) is masked out so a user moving their head
     cannot look like camera motion. Median displacement per frame pair -> px/s.
     Falls back to whole-frame phase correlation if the background is featureless.
  2. Gyro resampled to the frame-interval midpoints (+ a candidate lag).
  3. Pearson |r| between horizontal flow and yaw rate (`gamma`) and between
     vertical flow and pitch rate (`beta`), searched over lags +-LAG_MAX_MS.
  4. Score = energy-weighted mix of the two axes at the best lag.
  5. Stationary-device test: a hand-held phone always jitters. Gyro AND
     accelerometer both flat for the whole burst = stand / emulator / injected
     sensor stream -> strong attack signal.

Verdicts:
  pass          score >= SCORE_PASS, phone moved enough
  review        SCORE_REVIEW <= score < SCORE_PASS                  -> step_up
  fail          score < SCORE_REVIEW, or background pans while the phone is
                still, or the device is perfectly stationary         -> block
  insufficient  phone did not move (head may have) / too little data -> step_up
  absent        client sent no burst at all                          -> step_up

Units: DeviceMotionEvent.rotationRate is deg/s on iOS AND Android (W3C);
acceleration is m/s^2. The rad/s trap is the Generic Sensor API `Gyroscope`,
which we do not use.
"""
from __future__ import annotations
import numpy as np
import cv2

import face_match

# --- thresholds (first real-phone run 2026-09-24 informed these; keep calibrating) ---
SCORE_PASS = 0.60
SCORE_REVIEW = 0.35
MIN_GYRO_RMS_DPS = 8.0       # below this the user did not really move the phone
STATIONARY_GYRO_DPS = 1.0    # hand-held phones never sit below BOTH of these
STATIONARY_ACC_MS2 = 0.08
MIN_FRAMES = 8
MIN_MOTION_SAMPLES = 10
LAG_MAX_MS = 300             # camera pipeline latency vs sensor timestamps
LAG_STEP_MS = 16
FLOW_WIDTH = 160             # downscale frames to this width before flow
FLOW_STATIC_PXS = 4.0        # px/s below which the background is considered static
MIN_BG_FEATURES = 12         # fewer tracked background corners -> phase-correlation fallback
FACE_MASK_GROW = 0.35        # grow the face box by this fraction before masking


def _grey_small(bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    if w != FLOW_WIDTH:
        g = cv2.resize(g, (FLOW_WIDTH, max(1, int(h * FLOW_WIDTH / w))),
                       interpolation=cv2.INTER_AREA)
    return g


def _face_box(bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Face box in the ORIGINAL frame's pixels, or None."""
    best = face_match._best_face(bgr)
    if best is None:
        return None
    f, img = best[0], best[1]
    s = bgr.shape[1] / img.shape[1]           # _prep may have downscaled
    return (int(f[0] * s), int(f[1] * s), int(f[2] * s), int(f[3] * s))


def _bg_mask(shape: tuple[int, int], box: tuple[int, int, int, int] | None, scale: float) -> np.ndarray:
    m = np.full(shape, 255, np.uint8)
    if box is not None:
        x, y, w, h = [v * scale for v in box]
        gx, gy = w * FACE_MASK_GROW, h * FACE_MASK_GROW
        x0, y0 = int(max(0, x - gx)), int(max(0, y - gy))
        x1, y1 = int(min(shape[1], x + w + gx)), int(min(shape[0], y + h + gy))
        m[y0:y1, x0:x1] = 0
    return m


def global_flow(frames: list[tuple[float, np.ndarray]],
                face_box: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """Per-interval BACKGROUND shift. Returns (t_mid ms, vx px/s, vy px/s, method)
    in the downscaled frame's pixel units."""
    greys = [_grey_small(f) for _, f in frames]
    scale = greys[0].shape[1] / frames[0][1].shape[1]
    mask = _bg_mask(greys[0].shape, face_box, scale)
    win = cv2.createHanningWindow((greys[0].shape[1], greys[0].shape[0]), cv2.CV_32F)
    t_mid, vx, vy = [], [], []
    used_lk = 0
    for i in range(1, len(frames)):
        dt = (frames[i][0] - frames[i - 1][0]) / 1000.0
        if dt <= 0:
            continue
        dx = dy = None
        p0 = cv2.goodFeaturesToTrack(greys[i - 1], maxCorners=120, qualityLevel=0.01,
                                     minDistance=5, mask=mask)
        if p0 is not None and len(p0) >= MIN_BG_FEATURES:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(greys[i - 1], greys[i], p0, None,
                                                 winSize=(15, 15), maxLevel=2)
            ok = st.reshape(-1) == 1
            if ok.sum() >= MIN_BG_FEATURES:
                d = (p1 - p0).reshape(-1, 2)[ok]
                dx, dy = float(np.median(d[:, 0])), float(np.median(d[:, 1]))
                used_lk += 1
        if dx is None:                          # featureless background: whole-frame fallback
            a = greys[i - 1].astype(np.float32)
            b = greys[i].astype(np.float32)
            if face_box is not None:            # still keep the face out of it
                m = (mask / 255.0).astype(np.float32)   # float32 like `win`, or phaseCorrelate asserts
                a = a * m; b = b * m
            (dx, dy), _ = cv2.phaseCorrelate(a, b, win)
        t_mid.append((frames[i][0] + frames[i - 1][0]) / 2.0)
        vx.append(dx / dt)
        vy.append(dy / dt)
    method = "lk_background" if used_lk >= len(t_mid) / 2 else "phase_corr"
    return np.array(t_mid), np.array(vx), np.array(vy), method


def _pearson_abs(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(abs(np.corrcoef(a, b)[0, 1]))


def correlate(t_mid, vx, vy, mt, rx, ry) -> dict:
    """Search the lag that best aligns gyro (rx=pitch-role, ry=yaw-role - see the
    `check()` docstring for which raw DeviceMotion field the client currently
    maps to each) with flow (vx horizontal, vy vertical). Returns score, lag
    and the aligned series."""
    e_x = float(np.var(rx))   # pitch energy -> drives vertical flow
    e_y = float(np.var(ry))   # yaw energy   -> drives horizontal flow
    if e_x + e_y <= 0:
        return {"score": 0.0, "lag_ms": 0, "r_yaw": 0.0, "r_pitch": 0.0,
                "gyro_yaw": np.zeros_like(t_mid), "gyro_pitch": np.zeros_like(t_mid)}
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


def _log(out: dict, extra: str = "") -> dict:
    print(f"[motion_check] verdict={out['verdict']} score={out.get('score')} "
          f"lag={out.get('lag_ms')}ms gyro_rms={out.get('gyro_rms_dps')}dps "
          f"acc_rms={out.get('acc_rms_ms2')} flow_rms={out.get('flow_rms_pxs')}px/s "
          f"method={out.get('flow_method')} n_frames={out.get('n_frames')} "
          f"n_motion={out.get('n_motion')} {extra}")
    return out


def check(motion: list[dict] | None, frames: list[tuple[float, np.ndarray]] | None) -> dict:
    """motion: [{t, rx, ry, rz, ax?, ay?, az?}] deg/s + m/s^2, t in ms.
    frames: [(t_ms, bgr)]. Returns a signal dict.

    rx/ry are a FIELD-NAME CONTRACT with the client, not literal DeviceMotion
    property names: rx correlates against VERTICAL flow (the "pitch-role"
    axis), ry against HORIZONTAL flow (the "yaw-role" axis) - see `correlate()`.
    Which raw DeviceMotionEvent.rotationRate property (alpha/beta/gamma) the
    client puts in each field was corrected 2026-09-24 after a real-phone test
    (web/index.html's onMotion(): verified on an iPhone that a yaw "door-turn"
    shows up in `beta`, not `gamma` as first guessed) - this function doesn't
    care which raw property it was, only that the contract above holds.
    """
    if not motion and not frames:
        return {"ok": False, "verdict": "absent", "score": 0.0, "reason": "no liveness burst"}
    motion = motion or []
    frames = sorted(frames or [], key=lambda f: f[0])
    base = {"n_frames": len(frames), "n_motion": len(motion)}
    if len(frames) < MIN_FRAMES or len(motion) < MIN_MOTION_SAMPLES:
        return _log({"ok": False, "verdict": "insufficient", "score": 0.0,
                     "reason": f"frames={len(frames)} motion={len(motion)}", **base})

    ms = sorted(motion, key=lambda m: m["t"])
    mt = np.array([float(m["t"]) for m in ms])
    rx = np.array([float(m.get("rx") or 0.0) for m in ms])   # pitch-role -> correlated vs vertical flow
    ry = np.array([float(m.get("ry") or 0.0) for m in ms])   # yaw-role   -> correlated vs horizontal flow
    has_acc = any(m.get("ax") is not None for m in ms)
    acc = np.array([[float(m.get("ax") or 0.0), float(m.get("ay") or 0.0), float(m.get("az") or 0.0)]
                    for m in ms]) if has_acc else None

    face_box = _face_box(frames[0][1])
    t_mid, vx, vy, method = global_flow(frames, face_box)
    base.update({"flow_method": method, "face_masked": face_box is not None})
    if len(t_mid) < MIN_FRAMES - 1:
        return _log({"ok": False, "verdict": "insufficient", "score": 0.0,
                     "reason": "bad frame timestamps", **base})

    gyro_rms = float(np.sqrt(np.mean(rx ** 2 + ry ** 2)))
    flow_rms = float(np.sqrt(np.mean(vx ** 2 + vy ** 2)))
    # Accelerometer jitter with gravity removed per axis (mean-subtracted).
    acc_rms = float(np.sqrt(np.mean(np.sum((acc - acc.mean(axis=0)) ** 2, axis=1)))) if acc is not None else None
    base.update({"gyro_rms_dps": round(gyro_rms, 1), "flow_rms_pxs": round(flow_rms, 1),
                 "acc_rms_ms2": None if acc_rms is None else round(acc_rms, 3)})

    phone_moved = gyro_rms >= MIN_GYRO_RMS_DPS
    bg_moved = flow_rms >= FLOW_STATIC_PXS
    stationary = gyro_rms < STATIONARY_GYRO_DPS and (acc_rms is not None and acc_rms < STATIONARY_ACC_MS2)

    if stationary:
        # arXiv 2605.00218: stationary proxies are rejected outright - a hand
        # never holds a phone this still. Stand, emulator, or synthetic sensors.
        return _log({"ok": False, "verdict": "fail", "score": 0.0, "lag_ms": 0,
                     "reason": "stationary_device", "flags": ["stationary_device"], **base})

    if not phone_moved:
        # Background that pans on its own while the phone is still is the
        # injection signature. Face-only motion (user moved their head, not the
        # phone) is masked out, so it lands here as "please move the phone".
        if bg_moved:
            return _log({"ok": False, "verdict": "fail", "score": 0.0, "lag_ms": 0,
                         "reason": "phone still but background moved",
                         "flags": ["video_moves_phone_still"], **base})
        return _log({"ok": False, "verdict": "insufficient", "score": 0.0, "lag_ms": 0,
                     "reason": "phone did not move (tilt the phone, not your head)", **base})

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
    return _log({"ok": verdict == "pass", "verdict": verdict, "score": score,
                 "lag_ms": best["lag_ms"], "r_yaw": round(best["r_yaw"], 3),
                 "r_pitch": round(best["r_pitch"], 3), "flags": [], "series": series, **base},
                f"r_yaw={best['r_yaw']:.2f} r_pitch={best['r_pitch']:.2f}")
