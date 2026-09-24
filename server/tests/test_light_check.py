"""Check 5 on synthetic captures: a rendered face + wall lit by ambient light and the
phone's screen, with simulated auto-exposure / auto-white-balance, JPEG q70, ~12 fps.
Landmarks are fixed (no YuNet) so this tests the physics + scoring, not the detector.
"""
import random

import cv2
import numpy as np

import light_check as lc

H, W = 427, 320
LM = {"box": (85, 110, 150, 190), "pts": [(128, 180), (192, 180), (160, 220), (135, 255), (185, 255)]}
yy, xx = np.mgrid[0:H, 0:W]
FACE = ((xx - 160) / 75) ** 2 + ((yy - 205) / 100) ** 2 <= 1
SCREEN = {"grey": np.array([.216, .216, .216]), "red": np.array([1, .04, .01]),
          "green": np.array([.05, 1, .12]), "blue": np.array([.01, .08, 1])}
SKIN = np.array([.55, .38, .28]); WALL = np.array([.35, .33, .30])


def _enc(lin):
    lin = np.clip(lin, 0, 1)
    s = np.where(lin <= .0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - .055)
    return (s * 255 + .5).astype(np.uint8)


def render(shown, responded, k, lat, rng, fps=12, bg_sat=False, jit=0.03):
    """Frames of a face responding to `responded` while the log says `shown` (same unless replay).
    k = screen/ambient irradiance ratio, lat = camera lag in ms."""
    edges = np.cumsum([0] + shown["dur_ms"]); T = edges[-1]
    e2 = np.cumsum([0] + responded["dur_ms"]); slots = responded["slots"]
    t = 30.0; frames = []; gain = 1.0; awb = np.ones(3)
    amb = np.array([.8, .75, .6]) * rng.uniform(.7, 1.3)
    while t < T - 10:
        te = t - lat
        s = np.clip(np.searchsorted(e2, te, "right") - 1, 0, len(slots) - 1)
        scr = SCREEN[slots[s]] if 0 <= te < e2[-1] else SCREEN["grey"]
        face = SKIN * (amb + k * scr) * (1 + rng.normal(0, jit)) * (1 + rng.normal(0, .004, 3))
        bg = WALL * (amb + k / 30 * scr) * (40 if bg_sat else 1)         # bg_sat: a window behind, clipped
        gain += 0.35 * (0.18 / (face.mean() * gain) - 1) * gain            # AE chases the face
        gw = (0.5 * face + 0.5 * bg) * gain
        awb += 0.2 * (gw.mean() / (gw * awb) - 1) * awb                   # AWB chases grey-world
        img = np.empty((H, W, 3)); img[:] = bg; img[FACE] = face
        img = img * gain * awb * (1 + 0.1 * (yy / H))[..., None]
        img += rng.normal(0, .004, img.shape)
        e = _enc(img)[..., ::-1]
        _, j = cv2.imencode(".jpg", e, [cv2.IMWRITE_JPEG_QUALITY, 70])
        frames.append((t + 1000, cv2.imdecode(j, 1)))
        t += 1000 / fps + rng.uniform(-8, 8)
    log = [{"rgb": s, "t_switch": 1000 + float(e) + rng.uniform(0, 12)} for s, e in zip(shown["slots"], edges[:-1])]
    return frames, log


def _run(k, lat, replay=False, bg_sat=False, seed=3):
    rng = np.random.default_rng(seed)
    ch = lc.mint(random.Random(seed * 7 + 3))
    resp = lc.mint(random.Random(seed * 7 + 999)) if replay else ch
    frames, log = render(ch, resp, k, lat, rng, bg_sat=bg_sat)
    return lc.check(frames, log, ch, lambda img: LM)


def test_mint_is_balanced_and_has_no_repeats():
    for seed in range(20):
        ch = lc.mint(random.Random(seed))
        assert ch["slots"][0] == lc.GUARD and ch["slots"][-1] == lc.GUARD
        assert sorted(ch["slots"][1:-1]) == ["blue", "blue", "green", "green", "grey", "grey", "red", "red"]
        assert all(a != b for a, b in zip(ch["slots"], ch["slots"][1:]))
        assert all(lc.SLOT_MIN_MS <= d <= lc.SLOT_MAX_MS for d in ch["dur_ms"][1:-1])


def test_genuine_indoor_passes():
    r = _run(k=.25, lat=120)
    assert r["verdict"] == "pass", r
    assert r["rho"] > 0.8 and r["acc"] >= 5 / 6 and r["p"] <= 0.01
    assert 60 <= r["lag_ms"] <= 200          # true lag 120 ms recovered
    assert r["bg_reference"]


def test_genuine_without_background_reference_still_passes():
    r = _run(k=.25, lat=120, bg_sat=True)     # wall clipped → face-only fallback
    assert r["verdict"] == "pass" and "no_bg_reference" in r["flags"], r


def test_replay_of_another_sequence_fails():
    r = _run(k=.25, lat=120, replay=True)     # strong response, to the WRONG colours
    assert r["verdict"] == "fail", r
    assert r["p"] > 0.05 and r["score"] <= 0.45


def test_static_injected_video_is_insufficient_not_pass():
    r = _run(k=0.0, lat=120)                  # the face ignores the screen: flat
    assert r["verdict"] == "insufficient" and "low_snr" in r["flags"], r


def test_relighting_deepfake_with_long_delay_is_review():
    r = _run(k=.25, lat=520)                  # right colours, 400 ms late → suspicious
    assert r["verdict"] == "review" and "lag_out_of_range" in r["flags"], r


def test_tampered_colour_log_fails():
    ch = lc.mint(random.Random(1))
    log = [{"rgb": s, "t_switch": 1000.0 + i * 500} for i, s in enumerate(reversed(ch["slots"]))]
    r = lc.check([], log, ch, lambda img: LM)
    assert r["verdict"] == "fail" and r["flags"] == ["colour_log_mismatch"]


def test_no_face_is_insufficient():
    ch = lc.mint(random.Random(1))
    log = [{"rgb": s, "t_switch": 1000.0 + float(e)} for s, e in zip(ch["slots"], np.cumsum([0] + ch["dur_ms"][:-1]))]
    frames = [(1000.0 + i * 80, np.full((240, 320, 3), 120, np.uint8)) for i in range(60)]
    r = lc.check(frames, log, ch, lambda img: None)
    assert r["verdict"] == "insufficient" and "few_face_frames" in r["flags"]


def test_colour_log_from_meta_prefers_paint_time():
    m = {"switches": [{"name": "grey", "t_raf": 10.0, "t_painted": 26.6},
                      {"name": "red", "t_raf": 710.0, "t_painted": None},
                      {"name": "end", "t_raf": 1200.0, "t_painted": 1216.0}]}
    assert lc.colour_log_from_meta(m) == [{"rgb": "grey", "t_switch": 26.6}, {"rgb": "red", "t_switch": 710.0}]
