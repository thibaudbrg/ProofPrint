"""Face-vs-neck lighting consistency — EXPERIMENTAL, report-only (never fused).

deepidv and its partners list boundary artefacts, lighting consistency and skin
texture among their checks. This is our cheap version of the lighting one, riding
on check 5: the phone screen flashes the same random colours onto everything
close to it, so a REAL head answers everywhere at once — cheeks, neck, ears,
hairline — in the same colour direction and with the same timing. A face pasted
in by a swap tool only answers where the swap put it:

  * swap with no colour transfer: the cheeks stay flat while the real neck, ears
    and hairline around them still take the flash      -> face_flat_periphery_live
  * the injected face is tinted to fake the flash (the attacker reads the colour
    from the DOM) but nothing else in the frame is     -> periphery_flat_face_live
  * swap WITH colour transfer, i.e. "the flash leaks through" and check 5 passes:
    the face follows, but through the swap pipeline — later, or in another
    direction, than the real skin next to it           -> <region>_lag / _direction
So this is also the fallback if the live-swap test shows the flash leaking through.

Regions, in the eye-line frame of check 5 (d = inter-ocular distance, vm = eye
line -> mouth line): face = the two cheek quads only (inside every swap mask);
neck = below the chin; ears = both sides at eye/nose height; hairline = above the
forehead (outside "full face" masks). Each periphery region keeps only pixels near
its own per-frame median colour, so background slivers and collar edges drop out.

Per region: log-chromaticity minus the background's (cancels AE/AWB), detrended
by the grey guard slots, one response vector per colour slot (as check 5), each
region at its OWN best lag. Then, per region vs face: cosine of the stacked
responses, least-squares gain, and the lag difference.

Nothing here decides: the result carries `fused: False`. Keep it only if it
separates OUR genuine and attack runs — `python neck_check.py eval runs.jsonl`
prints KEEP / DROP. Every threshold below is a guess from synthetic frames.
"""
from __future__ import annotations

import json
import sys

import cv2
import numpy as np

CH = {"red": 0, "green": 1, "blue": 2}          # any other slot name (grey/black) = guard
REGIONS = ("face", "neck", "ears", "hairline")
PERIPHERY = REGIONS[1:]
POST_MS, PRE_MS = 120, 40                       # ignore after / before each switch (as check 5)
LAGS = np.arange(-40, 421, 20)                  # per-region lag search (ms), as check 5
MIN_FR_SLOT, MIN_COLOUR_SLOTS = 2, 4
MIN_FRAMES = 20
MIN_PX = {"face": 400, "neck": 250, "ears": 150, "hairline": 150}
SKIN_TOL = 0.30        # periphery pixel kept if its log-chroma is within this of the region's frame median
A_MIN = 0.012          # response amplitude below this = flat (same as check 5)
ASYM = 4.0             # "flat vs live" needs the live side >= 2*A_MIN and >= ASYM x the flat side
PAIR_AMP = 3 * A_MIN   # direction / lag / gain compared only when BOTH sides answer this clearly
                       # (near the noise floor cos just decays: weak daylight -> insufficient)
RHO_LIVE, RHO_FLAT = 0.50, 0.20
COS_MIN = 0.50         # live region whose response points elsewhere than the face's
DLAG_MAX = 120         # ms; a swap that smooths the face in time answers later than the neck
GAIN_BAND = (0.15, 4.0)  # neck/ears in shade can out-answer the cheeks; outside this is odd
_x = np.arange(256) / 255.0
LUT = np.where(_x <= 0.04045, _x / 12.92, ((_x + 0.055) / 1.055) ** 2.4).astype(np.float32)


# ----------------------------------------------------------------------------- geometry
def _quad(shape, c, u, v, d, u0, u1, v0, v1):
    """Rotated rectangle u in [u0,u1]*d, v in [v0,v1] px around c (v already in px)."""
    pts = [c + a * d * u + b * v for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, np.round(pts).astype(np.int32), 1)
    return m.astype(bool)


def rois(shape, lm) -> tuple[dict, np.ndarray, float]:
    """Region masks, background mask, inter-ocular distance (px)."""
    e1, e2, n, m1, m2 = np.asarray(lm["pts"], float)
    if e1[0] > e2[0]:
        e1, e2, m1, m2 = e2, e1, m2, m1          # e1 = image-left eye
    c = (e1 + e2) / 2
    d = float(np.linalg.norm(e2 - e1))
    u = (e2 - e1) / max(d, 1e-6)
    v = np.array([-u[1], u[0]])
    v = v if (n - c) @ v > 0 else -v             # v points down the face
    vm = max(((m1 + m2) / 2 - c) @ v, 0.8 * d)   # eye line -> mouth line (px); chin ~ vm + 0.85 d
    q = lambda u0, u1, v0, v1: _quad(shape, c, u, v, d, u0, u1, v0, v1)
    masks = {
        "face": q(-0.72, -0.30, 0.33 * vm, 0.72 * vm) | q(0.30, 0.72, 0.33 * vm, 0.72 * vm),
        "neck": q(-0.45, 0.45, vm + 1.10 * d, vm + 1.70 * d),
        "ears": q(-1.40, -1.12, 0.0, 0.8 * vm) | q(1.12, 1.40, 0.0, 0.8 * vm),   # cheekbone edge ~1.1 d
        "hairline": q(-0.45, 0.45, -1.55 * d, -1.15 * d),
    }
    x, y, w, h = lm["box"]
    H, W = shape[:2]
    cx, cy = x + w / 2, y + h / 2
    bg = np.zeros((H, W), bool)                  # top half, outside a 1.9x face box (as check 5)
    bg[int(0.03 * H):int(0.55 * H), int(0.03 * W):int(0.97 * W)] = True
    bg[max(0, int(cy - 0.95 * h)):int(cy + 0.95 * h) + 1, max(0, int(cx - 0.95 * w)):int(cx + 0.95 * w) + 1] = False
    return masks, bg, d


def _logchroma(px: np.ndarray) -> np.ndarray:
    lg = np.log(np.maximum(px, 1e-4))
    return lg - lg.mean(-1, keepdims=True)


def _trimmed_mean(px: np.ndarray) -> np.ndarray:
    lum = px.sum(1)
    lo, hi = np.percentile(lum, [10, 90])
    return px[(lum >= lo) & (lum <= hi)].mean(0)


# ----------------------------------------------------------------------------- time series
def _series(frames, landmarks_fn):
    """-> t (n,), Z {region: (n,3) log-chroma minus background, NaN where unusable}, flags."""
    t, raw, last = [], [], None
    for tf, img in frames:
        lm = landmarks_fn(img)
        if lm is not None:
            last = (tf, lm)
        elif last is None or tf - last[0] > 250:    # carry landmarks forward <= 250 ms
            continue
        masks, bg, d = rois(img.shape, last[1])
        if d < 40:
            continue
        enc = img[..., ::-1]
        lin = LUT[enc]
        ok = (enc.max(2) < 250) & (enc.max(2) > 20)
        entry = {r: lin[masks[r] & ok] for r in REGIONS}
        b = lin[bg & ok]
        entry["_bg"] = _trimmed_mean(b) if len(b) >= 2000 else None
        t.append(float(tf))
        raw.append(entry)
    if not raw:
        return np.zeros(0), {}, ["few_face_frames"]
    flags = []
    hasb = np.array([e["_bg"] is not None for e in raw])
    use_bg = hasb.mean() >= 0.9
    if not use_bg:
        flags.append("no_bg_reference")
    Z = {r: np.full((len(raw), 3), np.nan) for r in REGIONS}
    for i, e in enumerate(raw):
        zb = _logchroma(e["_bg"]) if use_bg and e["_bg"] is not None else 0.0
        if use_bg and e["_bg"] is None:
            continue
        for r in REGIONS:
            px = e[r]
            if r != "face" and len(px) >= MIN_PX[r]:      # drop minority background / collar pixels
                z = _logchroma(px)
                px = px[np.linalg.norm(z - np.median(z, 0), axis=1) < SKIN_TOL]
            if len(px) >= MIN_PX[r]:
                Z[r][i] = _logchroma(_trimmed_mean(px)) - zb
    return np.asarray(t), Z, flags


def _corr(T, M):
    """Mean per-channel Pearson (as check 5)."""
    T = T - T.mean(0)
    M = M - M.mean(0)
    den = np.sqrt((T ** 2).sum(0) * (M ** 2).sum(0)) + 1e-12
    return float(((T * M).sum(0) / den).mean())


def _template(lb):
    return np.eye(3)[np.maximum(lb, 0)] * (lb >= 0)[:, None] - (lb >= 0)[:, None] / 3


def _align(t, Z, edges, lab, L, guard=True):
    """Guard-detrended response at lag L -> (k, ok, M) or None."""
    ns = len(lab)
    tau = t - L
    k = np.clip(np.searchsorted(edges, tau, "right") - 1, 0, ns - 1)
    ok = (tau >= edges[0]) & (tau < edges[-1]) & ~np.isnan(Z).any(1)
    if guard:
        ok &= (tau - edges[k] >= POST_MS) & (edges[k + 1] - tau >= PRE_MS)
    med = {s: np.median(Z[ok & (k == s)], 0) for s in range(ns) if (ok & (k == s)).any()}
    kc = [s for s in range(ns) if lab[s] < 0 and s in med]
    if not kc or ok.sum() < 6:
        return None
    mid = (edges[:-1] + edges[1:]) / 2
    base = np.stack([np.interp(tau, mid[kc], [med[s][c] for s in kc]) for c in range(3)], 1)
    return k, ok, Z - base


def _rho(t, Z, edges, lab, L, guard=True):
    a = _align(t, Z, edges, lab, L, guard)
    if a is None:
        return -2.0
    k, ok, M = a
    return _corr(_template(lab[k[ok]]), M[ok])


def _region(t, Z, edges, lab) -> dict:
    """One region's response to the colour slots, at its own best lag."""
    n = int((~np.isnan(Z).any(1)).sum())
    if n < MIN_FRAMES:
        return {"n": n, "valid": False}
    L = int(LAGS[int(np.argmax([_rho(t, Z, edges, lab, Lx) for Lx in LAGS]))])
    a = _align(t, Z, edges, lab, L)
    if a is None:
        return {"n": n, "valid": False}
    k, ok, M = a
    d = {}
    for s in np.where(lab >= 0)[0]:
        sel = ok & (k == s)
        if sel.sum() >= MIN_FR_SLOT:
            d[int(s)] = np.median(M[sel], 0)
    if len(d) < MIN_COLOUR_SLOTS:
        return {"n": n, "valid": False}
    rho = _corr(_template(lab[k[ok]]), M[ok])
    amp = float(np.median([np.linalg.norm(x) for x in d.values()]))
    acc = float(np.mean([int(np.argmax(x)) == lab[s] for s, x in d.items()]))
    own = [_rho(t, Z, edges, lab, Lx, guard=False) for Lx in LAGS]   # unmasked = sharper peak
    return {"n": n, "valid": True, "rho": round(rho, 3), "amp": round(amp, 4), "acc": round(acc, 3),
            "lag_ms": int(LAGS[int(np.argmax(own))]),
            "live": bool(amp >= A_MIN and rho >= RHO_LIVE),
            "flat": bool(amp < A_MIN or rho < RHO_FLAT), "_d": d}


# ----------------------------------------------------------------------------- check
def check(frames, colour_log, challenge, landmarks_fn=None) -> dict:
    """frames [(t_ms, bgr)], colour_log [{rgb, t_switch}], challenge {slots, dur_ms}:
    the same inputs as check 5. Returns verdict consistent | inconsistent |
    insufficient, always `fused: False`.
    """
    out = {"experimental": True, "fused": False, "ok": True, "score": None, "flags": []}
    if landmarks_fn is None:
        import light_check                        # lazy: same YuNet wrapper as check 5
        landmarks_fn = light_check.landmarks
    slots = list(challenge["slots"])
    if [c["rgb"] for c in colour_log] != slots:
        return {**out, "verdict": "insufficient", "flags": ["colour_log_mismatch"]}
    ts = np.array([c["t_switch"] for c in colour_log], float)
    edges = np.r_[ts, ts[-1] + float(challenge["dur_ms"][-1])]
    lab = np.array([CH.get(s, -1) for s in slots])

    t, Z, flags = _series(frames, landmarks_fn)
    out["flags"] = flags
    if len(t) < MIN_FRAMES:
        return {**out, "verdict": "insufficient", "flags": flags + ["few_face_frames"]}
    reg = {r: _region(t, Z[r], edges, lab) for r in REGIONS}
    face = reg["face"]
    periph = [r for r in PERIPHERY if reg[r]["valid"]]
    if not face["valid"]:
        flags.append("few_face_frames")
    if not periph:
        flags.append("no_periphery")              # neck out of frame, ears/hairline hidden
    if not face["valid"] or not periph:
        return {**out, "verdict": "insufficient", "regions": _public(reg), "flags": flags}

    bad, pair_scores = [], []
    top = max(reg[r]["amp"] for r in periph)
    if (not face["live"] and any(reg[r]["live"] and reg[r]["amp"] >= 2 * A_MIN for r in periph)
            and face["amp"] * ASYM <= top):
        bad.append("face_flat_periphery_live")
    if (face["live"] and face["amp"] >= 2 * A_MIN and not any(reg[r]["live"] for r in periph)
            and top * ASYM <= face["amp"]):
        bad.append("periphery_flat_face_live")
    for r in periph:
        common = sorted(set(reg[r]["_d"]) & set(face["_d"]))
        Dr = np.concatenate([reg[r]["_d"][s] for s in common])
        Df = np.concatenate([face["_d"][s] for s in common])
        cos = float(Dr @ Df / (np.linalg.norm(Dr) * np.linalg.norm(Df) + 1e-12))
        gain = float(Dr @ Df / (Df @ Df + 1e-12))
        dlag = reg[r]["lag_ms"] - face["lag_ms"]
        reg[r].update(cos=round(cos, 3), gain=round(gain, 3), dlag_ms=int(dlag))
        if not (face["live"] and reg[r]["live"] and min(face["amp"], reg[r]["amp"]) >= PAIR_AMP):
            continue
        pen = 1.0
        if cos < COS_MIN:
            bad.append(f"{r}_direction")
        if abs(dlag) > DLAG_MAX:
            bad.append(f"{r}_lag")
            pen = 0.5
        if not GAIN_BAND[0] <= gain <= GAIN_BAND[1]:
            bad.append(f"{r}_gain")
        pair_scores.append(float(np.clip(cos, 0, 1)) * pen)

    live_p = [r for r in periph if reg[r]["live"]]
    fa = [reg[r]["amp"] / max(face["amp"], 1e-4) for r in live_p] or \
         [reg[r]["amp"] / max(face["amp"], 1e-4) for r in periph]
    out.update(log_amp_ratio=round(float(np.log10(np.median(fa) + 1e-6)), 3),
               max_abs_dlag_ms=max([abs(reg[r].get("dlag_ms", 0)) for r in live_p], default=None))
    if bad:
        verdict, score = "inconsistent", 0.0 if any("periphery" in b for b in bad) else min(pair_scores)
    elif pair_scores:
        verdict, score = "consistent", min(pair_scores)
    else:
        verdict, score = "insufficient", None     # nothing live on both sides: daylight or all-injected
        flags.append("no_live_pair")
    out.update(verdict=verdict, score=None if score is None else round(score, 3),
               regions=_public(reg), flags=flags + bad)
    print(f"[neck_check] verdict={verdict} score={out['score']} lag={face['lag_ms']} "
          + " ".join(f"{r}:amp={reg[r].get('amp')},rho={reg[r].get('rho')},cos={reg[r].get('cos')},"
                     f"gain={reg[r].get('gain')},dlag={reg[r].get('dlag_ms')}" for r in REGIONS)
          + f" flags={out['flags']}")
    return out


def _public(reg):
    return {r: {k: v for k, v in x.items() if not k.startswith("_")} for r, x in reg.items()}


# ----------------------------------------------------------------------------- keep / drop
def _auc(pos, neg):
    """P(attack feature > genuine feature), ties count half (Mann-Whitney)."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    return float(((pos[:, None] > neg[None]).sum() + 0.5 * (pos[:, None] == neg[None]).sum())
                 / (len(pos) * len(neg)))


def _neck_of(row):
    if "neck" in row:
        return row["neck"]
    light = ((row.get("signals") or {}).get("light")) or {}
    return light.get("neck") or row


def separation(runs, min_per_class=5) -> dict:
    """runs = [{"label": "genuine"|"attack", ...neck result, or a full /result with signals.light.neck}].

    KEEP if, on >= 5 runs per class, the verdict flags <= 20 % of genuine runs and
    >= 60 % of attack runs, or one feature alone reaches AUC >= 0.90. Otherwise DROP.
    """
    g = [_neck_of(r) for r in runs if r.get("label") == "genuine"]
    a = [_neck_of(r) for r in runs if r.get("label") == "attack"]
    rate = lambda xs: (sum(x.get("verdict") == "inconsistent" for x in xs) / len(xs)) if xs else None
    out = {"n_genuine": len(g), "n_attack": len(a), "genuine_flagged": rate(g), "attack_flagged": rate(a),
           "insufficient": {"genuine": sum(x.get("verdict") == "insufficient" for x in g),
                            "attack": sum(x.get("verdict") == "insufficient" for x in a)},
           "features": {}}
    for f, sign in (("score", -1), ("max_abs_dlag_ms", +1), ("log_amp_ratio", 0)):
        gv = [x[f] for x in g if x.get(f) is not None]
        av = [x[f] for x in a if x.get(f) is not None]
        if gv and av:
            auc = _auc(av, gv) if sign >= 0 else _auc([-v for v in av], [-v for v in gv])
            if sign == 0:                            # attacks can push the ratio either way
                auc = max(auc, 1 - auc)
            out["features"][f] = {"auc": round(auc, 3), "n": [len(gv), len(av)]}
    best = max([v["auc"] for v in out["features"].values()], default=0.0)
    if len(g) < min_per_class or len(a) < min_per_class:
        out["decision"] = "MORE DATA"
    elif (out["genuine_flagged"] <= 0.2 and out["attack_flagged"] >= 0.6) or best >= 0.90:
        out["decision"] = "KEEP"
    else:
        out["decision"] = "DROP"
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "eval":
        sys.exit("usage: python neck_check.py eval runs.jsonl   "
                 "(one JSON per line: {\"label\": \"genuine\"|\"attack\", ...result})")
    with open(sys.argv[2]) as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    print(json.dumps(separation(rows), indent=2))
