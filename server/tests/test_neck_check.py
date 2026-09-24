"""Tests for the face-vs-neck lighting consistency (experimental, report-only).

No consented faces in the repo, so heads are RENDERED: hair, ears, neck and a face
ellipse lit by ambient + the screen's colour slots, then a crude camera (AE, AWB,
noise, sRGB, JPEG q85, 15 fps). Landmarks come from the renderer, not YuNet.
The "swap" cases change only how the face ellipse answers the screen.
Run from server/:  ./.venv/bin/python -m pytest tests -q
"""
import random

import cv2
import numpy as np

import neck_check as nc

W, H = 320, 427
EYES = ((128.0, 170.0), (192.0, 170.0))            # d = 64 px
NOSE, MOUTH = (160.0, 205.0), ((135.0, 240.0), (185.0, 240.0))
LM = {"box": (92.0, 100.0, 136.0, 200.0), "pts": np.array([EYES[1], EYES[0], NOSE, MOUTH[1], MOUTH[0]])}
RGB = {"red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1), "grey": (.216, .216, .216)}


def mint(seed):
    r = random.Random(seed)
    while True:
        body = ["red", "red", "green", "green", "blue", "blue", "grey", "grey"]
        r.shuffle(body)
        seq = ["grey"] + body + ["grey"]
        if all(a != b for a, b in zip(seq, seq[1:])):
            return {"slots": seq, "dur_ms": [700] + [r.randint(380, 560) for _ in body] + [400]}


def _masks():
    m = {}
    for name, draw in {
        "hair": lambda im: cv2.ellipse(im, (160, 125), (82, 72), 0, 0, 360, 1, -1),
        "neck": lambda im: cv2.rectangle(im, (122, 280), (198, H), 1, -1),
        "ears": lambda im: (cv2.ellipse(im, (80, 200), (15, 32), 0, 0, 360, 1, -1),
                            cv2.ellipse(im, (240, 200), (15, 32), 0, 0, 360, 1, -1)),
        "face": lambda im: cv2.ellipse(im, (160, 200), (70, 102), 0, 0, 360, 1, -1),
    }.items():
        im = np.zeros((H, W), np.uint8)
        draw(im)
        m[name] = im.astype(bool)
    return m


ALBEDO = {"bg": (.35, .33, .30), "hair": (.30, .21, .15), "neck": (.52, .36, .27),
          "ears": (.56, .38, .29), "face": (.55, .38, .28)}
SCREEN = {"bg": 1 / 30, "hair": 0.9, "neck": 0.7, "ears": 0.5, "face": 1.0}   # geometry: share of screen light
SHADE = {"bg": 1.0, "hair": 1.0, "neck": 0.6, "ears": 0.8, "face": 1.0}       # neck sits in the chin's shadow


def render(ch, k=0.3, lat=120, face="real", face_delay=0, periph_k=None, seed=0):
    """-> frames, colour_log. face: real | flat (swap, no transfer) | tint (injected + tinted)."""
    rng = np.random.default_rng(seed)
    masks = _masks()
    edges = np.cumsum([0] + ch["dur_ms"]) + 1000.0
    amb = np.array([.8, .75, .6]) * rng.uniform(.8, 1.2)
    periph_k = k if periph_k is None else periph_k

    def screen(tau):
        s = int(np.clip(np.searchsorted(edges, tau, "right") - 1, 0, len(ch["slots"]) - 1))
        return np.array(RGB[ch["slots"][s]], float)

    frames, gain, awb = [], 1.0, np.ones(3)
    order = ["bg", "hair", "neck", "ears", "face"]
    for i, tf in enumerate(np.arange(edges[0] - 100, edges[-1] + 200, 1000 / 15)):
        tf = float(tf + rng.normal(0, 8))
        img = np.zeros((H, W, 3))
        for r in order:
            m = np.ones((H, W), bool) if r == "bg" else masks[r]
            kk, S = periph_k, screen(tf - lat)
            if r == "face":
                kk = k
                S = np.array(RGB["grey"]) if face == "flat" else screen(tf - lat - face_delay)
            light = SHADE[r] * amb + kk * SCREEN[r] * S * (1 + rng.normal(0, .004, 3))
            img[m] = np.array(ALBEDO[r]) * light * (1 + rng.normal(0, .03))   # 3 % per-region jitter
        img *= gain * awb
        lum = img.mean()
        gain = 0.65 * gain + 0.35 * (0.18 / max(lum / gain, 1e-6))          # AE chases mean
        gw = img.reshape(-1, 3).mean(0)
        awb = 0.8 * awb + 0.2 * awb * gw.mean() / np.maximum(gw, 1e-6)    # AWB chases grey world
        img = np.clip(img + rng.normal(0, .004, img.shape), 0, 1)
        enc = np.where(img <= .0031308, 12.92 * img, 1.055 * img ** (1 / 2.4) - .055)
        bgr = (enc[..., ::-1] * 255).round().astype(np.uint8)
        bgr = cv2.imdecode(cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1], 1)
        frames.append((tf, bgr))
    log = [{"rgb": s, "t_switch": float(e + rng.uniform(0, 12))} for s, e in zip(ch["slots"], edges)]
    return frames, log


def run(seed=0, **kw):
    ch = mint(seed)
    frames, log = render(ch, seed=seed, **kw)
    return nc.check(frames, log, ch, landmarks_fn=lambda _img: LM)


def test_genuine_is_consistent():
    for seed in range(3):
        r = run(seed)
        assert r["verdict"] == "consistent", r
        assert r["score"] >= 0.8 and r["fused"] is False
        assert r["regions"]["neck"]["live"] and abs(r["regions"]["neck"]["dlag_ms"]) <= nc.DLAG_MAX


def test_swap_without_colour_transfer_flat_face_live_neck():
    r = run(1, face="flat")
    assert r["verdict"] == "inconsistent", r
    assert "face_flat_periphery_live" in r["flags"] and r["score"] == 0.0


def test_injected_face_tinted_but_nothing_else_answers():
    r = run(2, face="tint", periph_k=0.0)
    assert r["verdict"] == "inconsistent", r
    assert "periphery_flat_face_live" in r["flags"]


def test_colour_transfer_swap_answers_late():
    r = run(3, face_delay=240)                    # flash leaks through, but via the swap's smoothing
    assert r["verdict"] == "inconsistent", r
    assert any(f.endswith("_lag") for f in r["flags"]), r


def test_daylight_is_insufficient_not_inconsistent():
    r = run(4, k=0.004)
    assert r["verdict"] == "insufficient", r


def test_no_face_is_insufficient():
    ch = mint(5)
    frames, log = render(ch, seed=5)
    r = nc.check(frames, log, ch, landmarks_fn=lambda _img: None)
    assert r["verdict"] == "insufficient" and "few_face_frames" in r["flags"]


def test_separation_keep_drop():
    good = [{"label": "genuine", "verdict": "consistent", "score": 0.9 + i / 100, "max_abs_dlag_ms": 20,
             "log_amp_ratio": -0.1} for i in range(5)]
    bad = [{"label": "attack", "verdict": "inconsistent", "score": 0.0, "max_abs_dlag_ms": 240,
            "log_amp_ratio": 0.8} for _ in range(5)]
    assert nc.separation(good + bad)["decision"] == "KEEP"
    same = [dict(b, label="attack", verdict="consistent", score=0.9 + i / 100, max_abs_dlag_ms=20,
                 log_amp_ratio=-0.1) for i, b in enumerate(bad)]
    assert nc.separation(good + same)["decision"] == "DROP"
    assert nc.separation(good[:2] + bad)["decision"] == "MORE DATA"
