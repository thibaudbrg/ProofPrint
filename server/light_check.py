"""Check 5 - light pulse: does the skin reflect the colours the SCREEN just showed?

The phone paints a server-minted random colour sequence full-screen (grey lead-in,
R/G/B ×2 + 2 grey guards in random order, random 380–560 ms slots, grey tail) while
the front camera records every frame. A recorded video or a face-swap that does not
relight cannot follow a sequence minted seconds ago.

Per frame we measure the face's log-chromaticity minus the background's:
    z = [log F − mean_c log F] − [log B − mean_c log B]
Face = forehead + two cheek quads built from YuNet's 5 landmarks; background = the top
of the frame outside an enlarged face box. The subtraction cancels auto-exposure, auto
white balance (global per-channel gains), albedo and face-only brightness changes; the
grey guard slots give a baseline that removes slow drift. Under a red flash
z − baseline ≈ (+⅔, −⅓, −⅓)·log(1 + S_R/A_R), and likewise for G and B.

Scoring, over a camera-lag search of −40…+420 ms:
    ρ   = mean per-channel Pearson(expected one-hot − ⅓, measured z − baseline)
    acc = share of colour slots whose dominant channel is the right one
    p   = permutation test: 1000 random sequences with the same timing (max over lags)
    score = 0.5·clip((acc−⅓)/⅔) + 0.5·clip(ρ/0.8), capped at 0.45 when p > 0.05
Verdicts: pass ≥ 0.75 & p ≤ 0.01 (→ review if the fitted lag is implausible),
insufficient when the response is flat (SNR < 3 or amplitude < 0.012 → step-up, never a
pass: daylight and an injected video look the same), review ≥ 0.50, else fail (a strong
response to the WRONG colours = a replay of another session).

Thresholds were set on synthetic captures (see tests/test_light_check.py) - calibrate
on real phones with the light lab (/lab/light).
References: Face Flashing (NDSS 2018), FaceRevelio (MobiCom 2020), Gerstner & Farid
(CVPRW 2022). Positioned as a FUSION signal (iProov Flashmark patents cover the primitive).
"""
from __future__ import annotations

import secrets

import cv2
import numpy as np

import face_match

# ---- colours ---------------------------------------------------------------
CH = {"red": 0, "green": 1, "blue": 2}          # RGB channel index; anything else = guard (K)
GUARD = "grey"                                  # #808080 - keeps the face lit, same chroma contrast as black
RGB = {"grey": [128, 128, 128], "black": [0, 0, 0], "red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 0, 255]}

# ---- timing ----------------------------------------------------------------
LEAD_MS, TAIL_MS = 700, 400                     # grey lead-in (AE settles, baseline) / grey tail
SLOT_MIN_MS, SLOT_MAX_MS = 380, 560             # ≤ 2.6 transitions/s → ≤ 1.3 flashes/s (WCAG 2.3.1 limit 3)
POST_MS, PRE_MS = 120, 40                       # ignore frames this close after / before a switch
LAGS = np.arange(-40, 421, 20)                  # camera + pipeline lag search (ms)
LAG_OK = (0, 300)                               # plausible genuine lag; calibrate per device
CHALLENGE_TTL_S = 45                            # a challenge must be answered within total + this

# ---- decision ---------------------------------------------------------------
MIN_FR_SLOT, MIN_COLOUR_SLOTS, MIN_FRAMES = 2, 5, 20
SNR_MIN, A_MIN = 3.0, 0.012
SCORE_PASS, SCORE_REVIEW, P_MAX = 0.75, 0.50, 0.01
N_NULL = 1000
MIN_IOD_PX, MIN_SKIN_PX, MIN_BG_PX = 40, 600, 2000

_x = np.arange(256) / 255.0
LUT = np.where(_x <= 0.04045, _x / 12.92, ((_x + 0.055) / 1.055) ** 2.4).astype(np.float32)   # sRGB → linear


# ============================================================================ challenge
def mint(rng=None) -> dict:
    """Server-minted challenge: {"slots": [names], "dur_ms": [ms]} - CSPRNG, no two equal neighbours."""
    r = secrets.SystemRandom() if rng is None else rng
    while True:
        body = ["red", "red", "green", "green", "blue", "blue", GUARD, GUARD]
        r.shuffle(body)
        seq = [GUARD] + body + [GUARD]
        if all(a != b for a, b in zip(seq, seq[1:])):
            break
    dur = [LEAD_MS] + [r.randint(SLOT_MIN_MS, SLOT_MAX_MS) for _ in body] + [TAIL_MS]
    return {"slots": seq, "dur_ms": dur}


def to_client(challenge: dict, challenge_id: str) -> dict:
    """What the phone receives: the slots with their RGB values and the duration budget."""
    slots = [{"name": n, "rgb": RGB[n], "ms": d} for n, d in zip(challenge["slots"], challenge["dur_ms"])]
    total = int(sum(challenge["dur_ms"]))
    return {"challenge_id": challenge_id, "slots": slots, "total_ms": total,
            "expires_in_ms": (total // 1000 + CHALLENGE_TTL_S) * 1000}


# ============================================================================ landmarks
def landmarks(bgr: np.ndarray) -> dict | None:
    """Best YuNet face -> {box:(x,y,w,h), pts:5x2 [eye_r, eye_l, nose, mouth_r, mouth_l]} or None."""
    det, _ = face_match._load()
    ih, iw = bgr.shape[:2]
    det.setInputSize((iw, ih))
    _, faces = det.detect(bgr)
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda r: float(r[14]))
    return {"box": tuple(float(v) for v in f[0:4]), "pts": f[4:14].reshape(5, 2).tolist(),
            "score": float(f[14])}


# ============================================================================ per-frame feature
def _quad(shape, c, u, v, d, u0, u1, v0, v1):
    """Mask of the rotated rectangle u ∈ [u0,u1]·d, v ∈ [v0,v1] (px) around c."""
    pts = [c + a * d * u + b * v for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, np.round(pts).astype(np.int32), 1)
    return m.astype(bool)


def rois(shape, lm):
    """Skin patches (forehead + cheeks) in an eye-line frame, and the background mask."""
    e1, e2, n, m1, m2 = np.asarray(lm["pts"], float)
    if e1[0] > e2[0]:
        e1, e2, m1, m2 = e2, e1, m2, m1                     # e1 = image-left eye
    c = (e1 + e2) / 2
    d = float(np.linalg.norm(e2 - e1))                      # inter-ocular distance
    u = (e2 - e1) / max(d, 1e-6)
    v = np.array([-u[1], u[0]])
    v = v if (n - c) @ v > 0 else -v                        # v points down the face
    vm = max(((m1 + m2) / 2 - c) @ v, 0.8 * d)              # eye line → mouth line
    fore = _quad(shape, c, u, v, d, -0.40, 0.40, -1.00 * d, -0.55 * d)      # above the brows
    chk_l = _quad(shape, c, u, v, d, -0.72, -0.30, 0.33 * vm, 0.72 * vm)    # between eye, nose and mouth
    chk_r = _quad(shape, c, u, v, d, 0.30, 0.72, 0.33 * vm, 0.72 * vm)
    x, y, w, h = lm["box"]
    H, W = shape[:2]
    cx, cy = x + w / 2, y + h / 2
    bg = np.zeros((H, W), bool)
    bg[int(0.03 * H):int(0.55 * H), int(0.03 * W):int(0.97 * W)] = True     # top half only (no shoulders)
    bg[max(0, int(cy - 0.95 * h)):int(cy + 0.95 * h) + 1,
       max(0, int(cx - 0.95 * w)):int(cx + 0.95 * w) + 1] = False          # 1.9× face box removed
    return [fore, chk_l, chk_r], bg, d


def _agg(lin, enc, mask, min_px):
    """Linear-space mean of the valid pixels in `mask` (no clipped / dark pixels, 10–90 % luminance trim)."""
    mx = enc.max(2)
    ok = mask & (mx < 250) & (mx > 20)
    n = int(ok.sum())
    if n < min_px:
        return None, n
    px = lin[ok]
    lum = px.sum(1)
    lo, hi = np.percentile(lum, [10, 90])
    px = px[(lum >= lo) & (lum <= hi)]                      # drops specular highlights, hair, brows
    return px.mean(0), n


def frame_feature(bgr, lm):
    """(face log-chroma, background log-chroma | None, n_skin, n_bg) or None if the face is unusable."""
    enc = bgr[..., ::-1]                                    # RGB order
    lin = LUT[enc]
    skins, bg, d = rois(bgr.shape, lm)
    if d < MIN_IOD_PX:
        return None
    F, nF = _agg(lin, enc, np.logical_or.reduce(skins), MIN_SKIN_PX)
    B, nB = _agg(lin, enc, bg, MIN_BG_PX)
    if F is None:
        return None
    zf = np.log(F) - np.log(F).mean()
    zb = None if B is None else np.log(B) - np.log(B).mean()
    return zf, zb, nF, nB


# ============================================================================ scoring
def check(frames, colour_log, challenge, landmarks_fn=landmarks) -> dict:
    """frames [(t_ms, bgr)], colour_log [{rgb, t_switch}] (one per slot, in order), challenge {slots, dur_ms}."""
    flags: list[str] = []
    names = [c["rgb"] for c in colour_log]
    if names != list(challenge["slots"]):
        return {"ok": False, "verdict": "fail", "score": 0.0, "flags": ["colour_log_mismatch"]}
    ts = np.array([c["t_switch"] for c in colour_log], float)
    plan = np.asarray(challenge["dur_ms"], float)
    edges = np.r_[ts, ts[-1] + plan[-1]]
    if np.abs(np.diff(edges) - plan).max() > 100:
        flags.append("slot_timing_jitter")

    # --- per-frame features; landmarks carried forward ≤ 250 ms when a detection drops ---
    t, ZF, ZB, nskin, nbg, last = [], [], [], [], [], None
    for tf, img in frames:
        img = face_match._prep(img)
        lm = landmarks_fn(img)
        if lm is not None:
            last = (tf, lm)
        elif last is None or tf - last[0] > 250:
            continue
        f = frame_feature(img, last[1])
        if f is None:
            continue
        t.append(tf); ZF.append(f[0]); ZB.append(f[1]); nskin.append(f[2]); nbg.append(f[3])
    n_face = len(t)
    if n_face < MIN_FRAMES:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "n_frames": len(frames),
                "n_face_frames": n_face, "flags": flags + ["few_face_frames"]}
    t = np.array(t); ZF = np.array(ZF)
    hasb = np.array([z is not None for z in ZB])
    if hasb.mean() >= 0.9:                                  # primary: face chroma − background chroma
        t, Z = t[hasb], ZF[hasb] - np.array([z for z in ZB if z is not None])
        bg_ok = True
    else:                                                   # fallback: face chroma only (still detrended)
        Z = ZF; bg_ok = False; flags.append("no_bg_reference")

    slots = list(challenge["slots"]); ns = len(slots)
    lab = np.array([CH.get(s, -1) for s in slots])          # -1 = guard
    col_slots = np.where(lab >= 0)[0]
    rng = np.random.default_rng(0)
    null_lab = rng.integers(0, 3, (N_NULL, len(col_slots)))   # random challenges, same timing
    mid = (edges[:-1] + edges[1:]) / 2

    def align(L, guard=True):
        tau = t - L
        k = np.clip(np.searchsorted(edges, tau, "right") - 1, 0, ns - 1)
        ok = (tau >= edges[0]) & (tau < edges[-1])
        if guard:
            ok &= (tau - edges[k] >= POST_MS) & (edges[k + 1] - tau >= PRE_MS)
        med = {s: np.median(Z[ok & (k == s)], 0) for s in range(ns) if (ok & (k == s)).sum() >= 1}
        kc = [s for s in range(ns) if lab[s] < 0 and s in med]
        if not kc:
            return None
        base = np.stack([np.interp(tau, mid[kc], [med[s][c] for s in kc]) for c in range(3)], 1)
        return k, ok, Z - base, med, base

    def corr(T, M):                                         # mean per-channel Pearson
        T = T - T.mean(-2, keepdims=True); M = M - M.mean(0)
        num = (T * M).sum(-2)
        den = np.sqrt((T ** 2).sum(-2) * (M ** 2).sum(0)) + 1e-12
        return (num / den).mean(-1)

    def template(lb):                                       # one-hot − ⅓ for colours, 0 for guards
        return np.eye(3)[np.maximum(lb, 0)] * (lb >= 0)[..., None] - (lb >= 0)[..., None] / 3

    best, null_best = (-2.0, None), np.full(N_NULL, -2.0)
    full = np.full((N_NULL, ns), -1); full[:, col_slots] = null_lab
    curve = []
    for L in LAGS:
        a = align(L)
        if a is None:
            curve.append(None); continue
        k, ok, M, _, _ = a
        kk, Mo = k[ok], M[ok]
        r = float(corr(template(lab[kk]), Mo))
        curve.append(round(r, 3))
        if r > best[0]:
            best = (r, int(L))
        null_best = np.maximum(null_best, np.nan_to_num(corr(template(full[:, kk]), Mo), nan=0.0))
    rho, L = best
    if L is None:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "flags": flags + ["no_guard_slot"]}

    def rho_all(Lx):                                        # lag estimate WITHOUT the transition mask (sharper peak)
        a = align(Lx, guard=False)
        if a is None:
            return -2
        k2, ok2, M2, _, _ = a
        return corr(template(lab[k2[ok2]]), M2[ok2])
    lag_ms = int(LAGS[int(np.argmax([rho_all(Lx) for Lx in LAGS]))])
    p = (1 + int((null_best >= rho).sum())) / (1 + N_NULL)

    k, ok, M, med, base = align(L)
    per_seg, dvec, correct = [], [], 0
    for s in col_slots:
        sel = ok & (k == s)
        if sel.sum() < MIN_FR_SLOT:
            per_seg.append({"slot": int(s), "colour": slots[s], "n": int(sel.sum()), "ok": None}); continue
        d = np.median(M[sel], 0); pred = int(np.argmax(d)); dvec.append(d)
        correct += int(pred == lab[s])
        per_seg.append({"slot": int(s), "colour": slots[s], "n": int(sel.sum()),
                        "pred": ["red", "green", "blue"][pred], "d": np.round(d, 4).tolist(),
                        "ok": bool(pred == lab[s])})
    nval = len(dvec)
    resid = np.concatenate([Z[ok & (k == s)] - med[s] for s in med]) if med else np.zeros((1, 3))
    sigma = float(np.median(1.4826 * np.median(np.abs(resid - np.median(resid, 0)), 0))) + 1e-6
    A = float(np.median([np.linalg.norm(d) for d in dvec])) if dvec else 0.0
    snr = A / sigma
    acc = correct / nval if nval else 0.0
    score = 0.5 * float(np.clip((acc - 1 / 3) / (2 / 3), 0, 1)) + 0.5 * float(np.clip(rho / 0.8, 0, 1))
    if p > 0.05:
        score = min(score, 0.45)
    lag_bad = not (LAG_OK[0] <= lag_ms <= LAG_OK[1])
    if lag_bad:
        flags.append("lag_out_of_range")
    if nval < MIN_COLOUR_SLOTS:
        verdict = "insufficient"; flags.append("few_valid_slots")
    elif score >= SCORE_PASS and p <= P_MAX:
        verdict = "pass"
    elif snr < SNR_MIN or A < A_MIN:
        verdict = "insufficient"; flags.append("low_snr")   # flat: daylight OR an injected video → step-up
    elif score >= SCORE_REVIEW:
        verdict = "review"
    else:
        verdict = "fail"
    if verdict == "pass" and lag_bad:
        verdict = "review"                                  # right colours, suspicious delay
    print(f"[light_check] verdict={verdict} score={score:.3f} rho={rho:.3f} acc={acc:.2f} p={p:.4f} "
          f"snr={snr:.1f} amp={A:.4f} lag={lag_ms}ms frames={len(frames)}/{n_face} bg={bg_ok} flags={flags}")
    return {"ok": verdict == "pass", "verdict": verdict, "score": round(score, 3),
            "rho": round(float(rho), 3), "acc": round(acc, 3), "p": round(float(p), 4),
            "lag_ms": lag_ms, "lag_fit_ms": int(L), "snr": round(snr, 2), "amp": round(A, 4),
            "sigma": round(sigma, 4), "bg_reference": bg_ok, "n_frames": len(frames),
            "n_face_frames": n_face, "iod_px": None, "skin_px": int(np.median(nskin)),
            "bg_px": int(np.median(nbg)), "per_segment": per_seg, "flags": flags,
            "lag_curve": {"lag_ms": LAGS.tolist(), "rho": curve},
            "series": {"t_ms": np.round(t - edges[0], 1).tolist(),
                       "z": np.round(Z, 4).tolist(), "base": np.round(base, 4).tolist(),
                       "slot": k.tolist(), "used": ok.tolist(),
                       "edges_ms": np.round(edges - edges[0], 1).tolist(), "slots": slots}}


# ============================================================================ glue for /capture
def colour_log_from_meta(light_meta: dict) -> list[dict]:
    """meta.light.switches → [{rgb, t_switch}], using the paint time (next rAF) when logged."""
    out = []
    for s in light_meta.get("switches") or []:
        if s.get("name") == "end":
            continue
        t = s.get("t_painted") if s.get("t_painted") is not None else s.get("t_raf")
        if t is None:
            continue
        out.append({"rgb": s.get("name"), "t_switch": float(t)})
    return out
