"""End-to-end: the capture endpoint accepts the liveness burst and fuses check 4.

Face matching is stubbed (no consented faces in the repo); the point here is
the multipart plumbing (frames + meta.frames + meta.motion) and the fusion rule.
"""
import json
import cv2
import numpy as np
from fastapi.testclient import TestClient

import main
from tests.test_motion_check import gyro_trace, render_frames

JPG = cv2.imencode(".jpg", np.full((64, 64, 3), 128, np.uint8))[1].tobytes()


def _post(client, motion, frames, meta_extra=None):
    sid = client.post("/session").json()["session_id"]
    meta = {"label": "front", "settings": {"facingMode": "user"}, "hasMotion": True,
            "claimsMobile": True, "frameIntervals": [], "motion": motion,
            "frames": [{"file": f"{i+1:04d}.jpg", "t": t} for i, (t, _) in enumerate(frames)]}
    meta.update(meta_extra or {})
    files = [("id_photo", ("id.jpg", JPG, "image/jpeg")), ("selfie", ("selfie.jpg", JPG, "image/jpeg"))]
    for i, (_, img) in enumerate(frames):
        files.append(("frames", (f"{i+1:04d}.jpg", cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")))
    r = client.post(f"/session/{sid}/capture", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_genuine_burst_passes_end_to_end(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    m = gyro_trace()
    res = _post(TestClient(main.app), m, render_frames(m))
    assert res["signals"]["motion"]["verdict"] == "pass", res["signals"]["motion"]
    assert res["decision"] == "pass"


def test_injected_video_is_blocked_end_to_end(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    m = gyro_trace(seed=4)
    res = _post(TestClient(main.app), m, render_frames(m, static=True))   # video ignores the phone
    assert res["signals"]["motion"]["verdict"] == "fail"
    assert res["decision"] == "block"


def test_no_burst_is_step_up(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    res = _post(TestClient(main.app), [], [])
    assert res["signals"]["motion"]["verdict"] == "absent"
    assert res["decision"] == "step_up"


def test_naive_mode_ignores_motion(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", True)
    m = gyro_trace(seed=4)
    res = _post(TestClient(main.app), m, render_frames(m, static=True))
    assert res["signals"]["motion"]["verdict"] == "fail"     # still computed…
    assert res["decision"] == "pass" and res["mode"] == "naive"   # …but the naive app is fooled


# ---- check 6: profile challenge plumbing + fusion (landmarks stubbed) ----

def _profile_post(client, monkeypatch, yaw_track, enabled=True, side_override=None):
    """Post a capture with a profile burst whose per-frame yaw is stubbed."""
    import profile_check
    it = iter(yaw_track)
    monkeypatch.setattr(profile_check, "_landmarks",
                        lambda bgr: (lambda y: None if y is None else
                                     {"x": 10, "y": 10, "w": 100, "h": 120,
                                      "eyes": ((30, 40), (90, 40)), "nose": (60 + y * 100, 80),
                                      "score": 0.9})(next(it)))
    s = client.post("/session").json()
    assert s["profile_side"] in ("left", "right")
    sid = s["session_id"]
    if side_override:
        main.SESSIONS[sid]["profile_side"] = side_override
    frames = [(i * 110.0, np.full((64, 64, 3), 128, np.uint8)) for i in range(len(yaw_track))]
    meta = {"label": "front", "settings": {"facingMode": "user"}, "hasMotion": True,
            "claimsMobile": True, "frameIntervals": [], "motion": [], "frames": [],
            "profile": {"enabled": enabled, "side": "whatever-the-client-says",
                        "frames": [{"file": f"p{i+1:04d}.jpg", "t": t} for i, (t, _) in enumerate(frames)]}}
    files = [("id_photo", ("id.jpg", JPG, "image/jpeg")), ("selfie", ("selfie.jpg", JPG, "image/jpeg"))]
    for i, (_, img) in enumerate(frames):
        files.append(("profile_frames", (f"p{i+1:04d}.jpg", cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")))
    r = client.post(f"/session/{sid}/capture", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_profile_correct_side_passes(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    track = list(0.25 * np.sin(np.linspace(0, np.pi, 24)))          # nose moves +x = user's LEFT
    res = _profile_post(TestClient(main.app), monkeypatch, track, side_override="left")
    p = res["signals"]["profile"]
    assert p["enabled"] and p["verdict"] == "pass", p
    assert res["signals"]["motion"]["verdict"] == "absent"         # no motion burst sent here
    assert res["decision"] == "step_up"                             # …so motion still steps up


def test_profile_wrong_side_blocks(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    track = list(0.25 * np.sin(np.linspace(0, np.pi, 24)))          # turns LEFT…
    res = _profile_post(TestClient(main.app), monkeypatch, track, side_override="right")   # …server asked RIGHT
    assert res["signals"]["profile"]["verdict"] == "fail"
    assert res["decision"] == "block"


def test_profile_disabled_is_ignored(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    res = _profile_post(TestClient(main.app), monkeypatch, [None] * 10, enabled=False)
    assert res["signals"]["profile"] == {"ok": True, "verdict": "absent", "enabled": False}


def test_profile_server_side_wins_over_client_claim(monkeypatch):
    # The client claims a side in meta; the server must judge by its own nonce.
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    track = list(-0.25 * np.sin(np.linspace(0, np.pi, 24)))         # turns RIGHT
    res = _profile_post(TestClient(main.app), monkeypatch, track, side_override="right")
    assert res["signals"]["profile"]["side"] == "right"
    assert res["signals"]["profile"]["verdict"] == "pass"
