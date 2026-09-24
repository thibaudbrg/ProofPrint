"""Labs — isolate and debug one liveness check at a time, live.

Check 4 (gyroscope <-> video)
  laptop:  http://localhost:8000/lab            dashboard, polls every second
  phone:   https://<tunnel>/lab/phone           tilt burst ONLY (no ID / face / profile)
Check 5 (light pulse)
  laptop:  http://localhost:8000/lab/light      dashboard, polls every second
  phone:   https://<tunnel>/lab/light/phone     flash + record ONLY, same web/light.js as the app

Nothing here touches the production onboarding flow: own endpoints, own in-memory
stores, no `capture` changes. Each lab calls the very same check() as production for
the official verdict, then exposes the internals the dashboard needs (curves, sweeps,
per-slot responses, timing statistics) so thresholds can be calibrated on real phones.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import secrets
from typing import Any

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import face_match
import light_check
import motion_check as mc

router = APIRouter(prefix="/lab", tags=["lab"])
WEB = os.path.join(os.path.dirname(__file__), "..", "web")

RUNS: list[dict[str, Any]] = []          # check-4 runs, newest last
LIGHT_RUNS: list[dict[str, Any]] = []    # check-5 runs, newest last
LIGHT_CHALLENGES: dict[str, dict] = {}   # lab-minted light challenges by id (single use)
MAX_RUNS = 40
_ids = itertools.count(1)
_light_ids = itertools.count(1)


# ----------------------------------------------------------------------------- helpers
def _z(a) -> list[float]:
    a = np.asarray(a, float)
    s = a.std()
    return ((a - a.mean()) / s).tolist() if s > 1e-9 else [0.0] * len(a)


def _stats(x) -> dict[str, float | None]:
    x = np.asarray(x, float)
    if x.size == 0:
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    return {"n": int(x.size), "mean": round(float(x.mean()), 3), "median": round(float(np.median(x)), 3),
            "std": round(float(x.std()), 3), "min": round(float(x.min()), 3), "max": round(float(x.max()), 3)}


def _round(a, nd=2) -> list[float]:
    return [round(float(v), nd) for v in np.asarray(a, float)]


def _decimate(a, max_n=400):
    a = np.asarray(a, float)
    step = max(1, int(np.ceil(len(a) / max_n)))
    return a[::step]


# ---------------------------------------------------------------------------- analysis
def analyse(motion: list[dict], frames: list[tuple[float, np.ndarray]]) -> dict[str, Any]:
    """Official verdict + full internals. `frames` = [(t_ms, bgr)]."""
    official = mc.check(motion, frames)              # exactly what production decides
    detail: dict[str, Any] = {}

    frames = sorted(frames, key=lambda f: f[0])
    ms = sorted(motion or [], key=lambda m: m["t"])
    if len(frames) < 2 or len(ms) < 2:
        return {"official": official, "detail": detail}

    mt = np.array([float(m["t"]) for m in ms])
    rx = np.array([float(m.get("rx") or 0.0) for m in ms])      # beta  -> pitch -> vertical flow
    ry = np.array([float(m.get("ry") or 0.0) for m in ms])      # gamma -> yaw   -> horizontal flow
    rz = np.array([float(m.get("rz") or 0.0) for m in ms])
    acc = np.array([[float(m.get("ax") or 0.0), float(m.get("ay") or 0.0), float(m.get("az") or 0.0)] for m in ms])
    has_acc = any(m.get("ax") is not None for m in ms)

    face_box = mc._face_box(frames[0][1])
    t_mid, vx, vy, method = mc.global_flow(frames, face_box)
    if len(t_mid) < 3:
        return {"official": official, "detail": {"error": "too few usable frame intervals"}}

    # ---- lag sweep (same maths as motion_check.correlate, but we keep every point)
    e_x, e_y = float(np.var(rx)), float(np.var(ry))
    lags = np.arange(-mc.LAG_MAX_MS, mc.LAG_MAX_MS + 1, mc.LAG_STEP_MS)
    sweep_score, sweep_yaw, sweep_pitch = [], [], []
    best = None
    for lag in lags:
        g_rx = np.interp(t_mid + lag, mt, rx)
        g_ry = np.interp(t_mid + lag, mt, ry)
        r_yaw, r_pitch = mc._pearson_abs(vx, g_ry), mc._pearson_abs(vy, g_rx)
        score = (e_y * r_yaw + e_x * r_pitch) / (e_x + e_y) if (e_x + e_y) > 0 else 0.0
        sweep_score.append(score); sweep_yaw.append(r_yaw); sweep_pitch.append(r_pitch)
        if best is None or score > best[0]:
            best = (score, int(-lag), g_rx, g_ry, r_yaw, r_pitch)
    score, lag_ms, g_rx, g_ry, r_yaw, r_pitch = best

    # signed correlation tells us which way to flip the gyro so the overlay lines up
    def _sign(a, b):
        return -1.0 if (np.std(a) > 1e-9 and np.std(b) > 1e-9 and np.corrcoef(a, b)[0, 1] < 0) else 1.0
    sh, sv = _sign(vx, g_ry), _sign(vy, g_rx)
    z_vx, z_vy = np.array(_z(vx)), np.array(_z(vy))
    z_yaw, z_pitch = sh * np.array(_z(g_ry)), sv * np.array(_z(g_rx))

    # axis diagnostic: |r| of each image axis against EACH gyro axis at the best lag. If vx pairs
    # best with something other than yaw (ry), the client's axis mapping is wrong for this device.
    def _r(a, b): return round(mc._pearson_abs(a, b), 3)
    g_rz = np.interp(t_mid - lag_ms, mt, rz)
    diag = {"vx": f"{_r(vx, g_rx)} / {_r(vx, g_ry)} / {_r(vx, g_rz)}",
            "vy": f"{_r(vy, g_rx)} / {_r(vy, g_ry)} / {_r(vy, g_rz)}"}
    best_vx = max([("pitch", _r(vx, g_rx)), ("yaw", _r(vx, g_ry)), ("roll", _r(vx, g_rz))], key=lambda x: x[1])[0]
    diag["verdict"] = "ok (vx follows yaw)" if best_vx == "yaw" else f"MISMATCH: vx follows {best_vx}"

    fdt = np.diff([f[0] for f in frames])
    mdt = np.diff(mt)
    acc_j = float(np.sqrt(np.mean(np.sum((acc - acc.mean(axis=0)) ** 2, axis=1)))) if has_acc else None

    t0 = t_mid[0]
    detail = {
        "t_ms": _round(t_mid - t0, 1),
        "video": {"vx": _round(vx), "vy": _round(vy), "units": "px/s @160px wide"},
        "gyro_aligned": {"yaw": _round(g_ry), "pitch": _round(g_rx), "units": "deg/s", "lag_ms": lag_ms,
                         "sign_h": sh, "sign_v": sv},
        "z": {"vx": _round(z_vx, 3), "vy": _round(z_vy, 3), "yaw": _round(z_yaw, 3), "pitch": _round(z_pitch, 3)},
        "residual": {"h": _round(z_vx - z_yaw, 3), "v": _round(z_vy - z_pitch, 3),
                     "rms_h": round(float(np.sqrt(np.mean((z_vx - z_yaw) ** 2))), 3),
                     "rms_v": round(float(np.sqrt(np.mean((z_vy - z_pitch) ** 2))), 3)},
        "sweep": {"lag_ms": [int(-l) for l in lags], "score": _round(sweep_score, 3),
                  "r_yaw": _round(sweep_yaw, 3), "r_pitch": _round(sweep_pitch, 3), "best_lag_ms": lag_ms},
        "raw_motion": {"t_ms": _round(_decimate(mt - t0), 1), "yaw": _round(_decimate(ry)),
                       "pitch": _round(_decimate(rx)), "roll": _round(_decimate(rz)),
                       "acc": _round(_decimate(np.linalg.norm(acc - acc.mean(axis=0), axis=1)), 3) if has_acc else []},
        "axis_diag": diag,
        "medians": {"abs_vx_pxs": round(float(np.median(np.abs(vx))), 2), "abs_vy_pxs": round(float(np.median(np.abs(vy))), 2),
                    "abs_yaw_dps": round(float(np.median(np.abs(ry))), 2), "abs_pitch_dps": round(float(np.median(np.abs(rx))), 2)},
        "energy": {"var_yaw": round(e_y, 2), "var_pitch": round(e_x, 2),
                   "weight_yaw": round(e_y / (e_x + e_y), 3) if (e_x + e_y) > 0 else None},
        "stats": {"score": round(score, 3), "r_yaw": round(r_yaw, 3), "r_pitch": round(r_pitch, 3),
                  "gyro_rms_dps": round(float(np.sqrt(np.mean(rx ** 2 + ry ** 2))), 2),
                  "flow_rms_pxs": round(float(np.sqrt(np.mean(vx ** 2 + vy ** 2))), 2),
                  "acc_jitter_ms2": None if acc_j is None else round(acc_j, 3),
                  "flow_method": method, "face_masked": face_box is not None, "face_box": face_box},
        "timing": {"n_frames": len(frames), "n_motion": len(ms),
                   "frame_interval_ms": _stats(fdt), "frame_fps": round(1000.0 / float(np.mean(fdt)), 1) if len(fdt) else None,
                   "frame_jitter_pct": round(100.0 * float(np.std(fdt) / np.mean(fdt)), 1) if len(fdt) else None,
                   "motion_interval_ms": _stats(mdt), "motion_hz": round(1000.0 / float(np.mean(mdt)), 1) if len(mdt) else None,
                   "burst_ms": round(float(frames[-1][0] - frames[0][0]), 0)},
        "thresholds": {"SCORE_PASS": mc.SCORE_PASS, "SCORE_REVIEW": mc.SCORE_REVIEW,
                       "MIN_GYRO_RMS_DPS": mc.MIN_GYRO_RMS_DPS, "FLOW_STATIC_PXS": mc.FLOW_STATIC_PXS,
                       "STATIONARY_GYRO_DPS": mc.STATIONARY_GYRO_DPS, "STATIONARY_ACC_MS2": mc.STATIONARY_ACC_MS2,
                       "LAG_MAX_MS": mc.LAG_MAX_MS},
    }
    return {"official": official, "detail": detail}


def _summary(run: dict) -> dict:
    o, s = run["official"], run["detail"].get("stats", {})
    return {"id": run["id"], "at": run["at"], "label": run["label"], "verdict": o.get("verdict"),
            "score": o.get("score"), "lag_ms": o.get("lag_ms"), "r_yaw": s.get("r_yaw"), "r_pitch": s.get("r_pitch"),
            "gyro_rms": s.get("gyro_rms_dps"), "flow_rms": s.get("flow_rms_pxs"), "method": s.get("flow_method"),
            "flags": o.get("flags", []), "reason": o.get("reason")}


# --------------------------------------------------------------------------- endpoints
@router.get("")
def lab_page():
    return FileResponse(os.path.join(WEB, "lab.html"))


@router.get("/phone")
def lab_phone_page():
    return FileResponse(os.path.join(WEB, "lab_phone.html"))


@router.post("/motion")
async def lab_motion(frames: list[UploadFile] = File(default=[]), meta: str = Form(...)):
    """Tilt burst only: `frames` parts + meta{frames[], motion[], label?}. Returns the run."""
    try:
        m = json.loads(meta)
    except json.JSONDecodeError:
        raise HTTPException(400, "meta is not JSON")
    t_by_name = {f.get("file"): f.get("t") for f in (m.get("frames") or [])}
    burst = []
    for uf in frames:
        t = t_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            burst.append((float(t), img))
    res = analyse(m.get("motion") or [], burst)
    run = {"id": next(_ids), "at": dt.datetime.now().strftime("%H:%M:%S"),
           "label": (m.get("label") or "").strip()[:40] or "untitled",
           "device": {"ua_mobile": bool(m.get("claimsMobile")), "label": m.get("cameraLabel")},
           **res}
    RUNS.append(run)
    del RUNS[:-MAX_RUNS]
    o = run["official"]
    print(f"[lab] run #{run['id']} '{run['label']}' -> {o.get('verdict')} score={o.get('score')} lag={o.get('lag_ms')}")
    return {**_summary(run), "official": o, "detail": run["detail"]}


@router.get("/motion/latest")
def lab_latest():
    if not RUNS:
        return {"run": None, "runs": []}
    r = RUNS[-1]
    return {"run": {**_summary(r), "official": r["official"], "detail": r["detail"]},
            "runs": [_summary(x) for x in reversed(RUNS)]}


@router.get("/motion/{run_id}")
def lab_run(run_id: int):
    for r in RUNS:
        if r["id"] == run_id:
            return {**_summary(r), "official": r["official"], "detail": r["detail"]}
    raise HTTPException(404, "no such run")


@router.delete("/motion")
def lab_clear():
    RUNS.clear()
    return {"ok": True}


# ============================================================ check 5 — light pulse lab
def _light_timing(lm: dict, frames: list[tuple[float, np.ndarray]]) -> dict:
    """Capture-side statistics: frame rate, timestamp sources, painter accuracy."""
    t = np.array(sorted(f[0] for f in frames), float)
    gaps = np.diff(t) if t.size > 1 else np.array([])
    sw = [s for s in (lm.get("switches") or []) if s.get("name") != "end"]
    end = next((s for s in (lm.get("switches") or []) if s.get("name") == "end"), None)
    planned, painted = [], []
    for i, s in enumerate(sw):
        nxt = sw[i + 1] if i + 1 < len(sw) else end
        if nxt and s.get("t_painted") is not None and nxt.get("t_painted") is not None:
            planned.append(s.get("ms")); painted.append(nxt["t_painted"] - s["t_painted"])
    fmeta = lm.get("frames") or []
    return {"n_frames": int(t.size), "capture_ms": round(float(t[-1] - t[0]), 1) if t.size > 1 else 0,
            "fps": round(1000 / float(np.median(gaps)), 1) if gaps.size else None,
            "gap_ms": _stats(gaps), "gap_p90_ms": round(float(np.percentile(gaps, 90)), 1) if gaps.size else None,
            "t_cap_present": bool(fmeta) and all(f.get("t_cap") is not None for f in fmeta),
            "grab": lm.get("grab"), "raf": lm.get("raf"), "cam_lock": lm.get("cam_lock"), "display": lm.get("display"),
            "client_checks": lm.get("client_checks"), "hidden": lm.get("hidden"),
            "slot_planned_ms": planned, "slot_painted_ms": [round(float(v), 1) for v in painted],
            "slot_err_max_ms": round(float(np.max(np.abs(np.array(painted) - np.array(planned)))), 1) if painted else None}


def _light_summary(run: dict) -> dict:
    o, t = run["official"], run["timing"]
    return {"id": run["id"], "at": run["at"], "label": run["label"], "verdict": o.get("verdict"), "score": o.get("score"),
            "rho": o.get("rho"), "acc": o.get("acc"), "p": o.get("p"), "snr": o.get("snr"), "amp": o.get("amp"),
            "lag_ms": o.get("lag_ms"), "bg": o.get("bg_reference"), "fps": t.get("fps"), "mode": (t.get("grab") or {}).get("mode"),
            "flags": o.get("flags", [])}


@router.get("/light")
def lab_light_page():
    return FileResponse(os.path.join(WEB, "lab_light.html"))


@router.get("/light/phone")
def lab_light_phone_page():
    return FileResponse(os.path.join(WEB, "lab_light_phone.html"))


@router.post("/light/mint")
def lab_light_mint():
    """A fresh challenge for one lab run (same mint() as production, single use)."""
    ch = light_check.mint()
    cid = "lab_" + secrets.token_hex(3)
    LIGHT_CHALLENGES[cid] = ch
    for k in list(LIGHT_CHALLENGES)[:-20]:       # keep the dict small
        del LIGHT_CHALLENGES[k]
    return light_check.to_client(ch, cid)


@router.post("/light")
async def lab_light(light_frames: list[UploadFile] = File(default=[]), meta: str = Form(...)):
    """Light run only: `light_frames` parts + meta{light{...}, label?}. Returns the run."""
    try:
        m = json.loads(meta)
    except json.JSONDecodeError:
        raise HTTPException(400, "meta is not JSON")
    lm = m.get("light") or {}
    ch = LIGHT_CHALLENGES.pop(lm.get("challenge_id"), None)
    if ch is None:
        raise HTTPException(400, "unknown or already used challenge_id — mint one first")
    t_by_name = {f.get("file"): f.get("t") for f in (lm.get("frames") or [])}
    frames = []
    for uf in light_frames:
        t = t_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            frames.append((float(t), img))
    frames.sort(key=lambda x: x[0])
    official = light_check.check(frames, light_check.colour_log_from_meta(lm), ch)
    run = {"id": next(_light_ids), "at": dt.datetime.now().strftime("%H:%M:%S"),
           "label": (m.get("label") or "").strip()[:40] or "untitled",
           "device": {"ua_mobile": bool(m.get("claimsMobile")), "label": m.get("cameraLabel"), "ua": (m.get("ua") or "")[:120]},
           "challenge": ch, "official": official, "timing": _light_timing(lm, frames),
           "thresholds": {k: getattr(light_check, k) for k in
                          ("SCORE_PASS", "SCORE_REVIEW", "P_MAX", "SNR_MIN", "A_MIN", "LAG_OK", "POST_MS", "PRE_MS",
                           "MIN_FR_SLOT", "MIN_COLOUR_SLOTS", "MIN_FRAMES")}}
    LIGHT_RUNS.append(run)
    del LIGHT_RUNS[:-MAX_RUNS]
    print(f"[lab/light] run #{run['id']} '{run['label']}' -> {official.get('verdict')} score={official.get('score')} "
          f"rho={official.get('rho')} acc={official.get('acc')} p={official.get('p')} snr={official.get('snr')} lag={official.get('lag_ms')}")
    return {**_light_summary(run), **{k: run[k] for k in ("device", "challenge", "official", "timing", "thresholds")}}


@router.get("/light/latest")
def lab_light_latest():
    if not LIGHT_RUNS:
        return {"run": None, "runs": []}
    r = LIGHT_RUNS[-1]
    return {"run": {**_light_summary(r), **{k: r[k] for k in ("device", "challenge", "official", "timing", "thresholds")}},
            "runs": [_light_summary(x) for x in reversed(LIGHT_RUNS)]}


@router.get("/light/{run_id}")
def lab_light_run(run_id: int):
    for r in LIGHT_RUNS:
        if r["id"] == run_id:
            return {**_light_summary(r), **{k: r[k] for k in ("device", "challenge", "official", "timing", "thresholds")}}
    raise HTTPException(404, "no such run")


@router.delete("/light")
def lab_light_clear():
    LIGHT_RUNS.clear()
    return {"ok": True}
