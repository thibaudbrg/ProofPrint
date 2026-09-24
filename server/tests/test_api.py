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


# ---- intro switches (meta.mode / meta.checks) ----

def _switch_post(client, meta):
    import cv2, numpy as np
    jpg = cv2.imencode(".jpg", np.full((240, 320, 3), 128, np.uint8))[1].tobytes()
    sid = client.post("/session").json()["session_id"]
    files = [("id_photo", ("id.jpg", jpg, "image/jpeg")), ("selfie", ("selfie.jpg", jpg, "image/jpeg"))]
    r = client.post(f"/session/{sid}/capture", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_motion_switch_off_is_ignored(monkeypatch):
    # The phone-move check was switched off on the intro: no burst is sent, and its
    # absence must NOT step the user up.
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    res = _switch_post(TestClient(main.app), {
        "label": "front", "settings": {"facingMode": "user"}, "hasMotion": True, "claimsMobile": True,
        "mode": "full", "checks": {"motion": False, "profile": False, "doc_back": False}, "profile": {"enabled": False}})
    assert res["signals"]["motion"] == {"ok": True, "verdict": "absent", "enabled": False, "score": 0.0}
    assert res["decision"] == "pass" and res["mode"] == "full"


def test_client_naive_mode_decides_on_face_only(monkeypatch):
    # Intro switch "Demo: naive app": no liveness burst at all (would be step_up in full
    # mode) but the naive app passes on the face alone.
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    meta = {"label": "front", "settings": {"facingMode": "user"}, "hasMotion": True, "claimsMobile": True,
            "checks": {"motion": True, "profile": True}, "profile": {"enabled": False}}
    full = _switch_post(TestClient(main.app), {**meta, "mode": "full"})
    naive = _switch_post(TestClient(main.app), {**meta, "mode": "naive"})
    assert full["decision"] == "step_up" and full["mode"] == "full"          # motion absent -> step up
    assert naive["decision"] == "pass" and naive["mode"] == "naive"          # the broken app is fooled


# ---- check 5: light challenge plumbing + fusion (the light algorithm is stubbed) ----

def _light_post(client, monkeypatch, verdict, light_meta=None, checks_light=True, mint=True, n_frames=3):
    import light_check
    if verdict is not None:
        monkeypatch.setattr(light_check, "check",
                            lambda frames, log, ch, landmarks_fn=None: {"ok": verdict == "pass", "verdict": verdict,
                                                                        "score": 0.9 if verdict == "pass" else 0.1,
                                                                        "flags": [], "n_frames": len(frames)})
    sid = client.post("/session").json()["session_id"]
    ch = client.post(f"/session/{sid}/light").json() if mint else None
    if light_meta is None:
        t0 = 1000.0
        switches = []
        for i, s in enumerate(ch["slots"]):
            switches.append({"i": i, "name": s["name"], "ms": s["ms"], "t_planned": t0, "t_raf": t0, "t_painted": t0 + 16})
            t0 += s["ms"]
        switches.append({"i": len(ch["slots"]), "name": "end", "ms": 0, "t_planned": t0, "t_raf": t0, "t_painted": t0 + 16})
        light_meta = {"enabled": True, "opted_out": False, "challenge_id": ch["challenge_id"], "switches": switches,
                      "frames": [{"file": f"l{i+1:04d}.jpg", "t": 1000.0 + 40 * i} for i in range(n_frames)],
                      "client_checks": {"ok": True}}
    meta = {"label": "front", "settings": {"facingMode": "user"}, "hasMotion": True, "claimsMobile": True,
            "mode": "full", "checks": {"motion": False, "profile": False, "doc_back": False, "light": checks_light},
            "profile": {"enabled": False}, "light": light_meta}
    files = [("id_photo", ("id.jpg", JPG, "image/jpeg")), ("selfie", ("selfie.jpg", JPG, "image/jpeg"))]
    for i in range(n_frames):
        files.append(("light_frames", (f"l{i+1:04d}.jpg", JPG, "image/jpeg")))
    r = client.post(f"/session/{sid}/capture", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 200, r.text
    return sid, ch, r.json()


def test_light_mint_is_random_single_use_and_server_judged(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    c = TestClient(main.app)
    sid = c.post("/session").json()["session_id"]
    a, b = c.post(f"/session/{sid}/light").json(), c.post(f"/session/{sid}/light").json()
    assert a["challenge_id"] != b["challenge_id"]
    assert [s["name"] for s in b["slots"]] == main.SESSIONS[sid]["light"]["challenge"]["slots"]   # server copy
    assert b["slots"][0]["name"] == "grey" and b["slots"][0]["rgb"] == [128, 128, 128]
    assert 4000 < b["total_ms"] < 6000
    assert c.post("/session/nope/light").status_code == 404


def test_light_pass_passes_and_reaches_the_audit_row(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    _, _, res = _light_post(TestClient(main.app), monkeypatch, "pass")
    assert res["signals"]["light"]["verdict"] == "pass" and res["signals"]["light"]["n_frames"] == 3
    assert res["decision"] == "pass"


def test_light_wrong_colours_block_and_flat_response_steps_up(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    assert _light_post(TestClient(main.app), monkeypatch, "fail")[2]["decision"] == "block"
    assert _light_post(TestClient(main.app), monkeypatch, "insufficient")[2]["decision"] == "step_up"
    assert _light_post(TestClient(main.app), monkeypatch, "review")[2]["decision"] == "step_up"


def test_light_switch_off_is_ignored_but_opt_out_steps_up(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    _, _, off = _light_post(TestClient(main.app), monkeypatch, None, light_meta={}, checks_light=False, mint=False, n_frames=0)
    assert off["signals"]["light"] == {"ok": True, "verdict": "absent", "enabled": False, "score": 0.0}
    assert off["decision"] == "pass"
    _, _, skip = _light_post(TestClient(main.app), monkeypatch, None, light_meta={"enabled": True, "opted_out": True}, n_frames=0)
    assert skip["signals"]["light"]["verdict"] == "skipped" and skip["decision"] == "step_up"


def test_light_challenge_must_be_the_minted_one_and_used_once(monkeypatch):
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    c = TestClient(main.app)
    # a challenge id the server never minted (client-invented sequence) -> fail -> block
    _, _, res = _light_post(c, monkeypatch, "pass", light_meta={"enabled": True, "challenge_id": "lc_forged", "switches": [], "frames": []}, n_frames=0)
    assert res["signals"]["light"]["flags"] == ["challenge_unknown"] and res["decision"] == "block"
    # replaying the same challenge in a second capture -> reused
    sid, ch, first = _light_post(c, monkeypatch, "pass")
    assert first["decision"] == "pass"
    meta = {"mode": "full", "checks": {"motion": False, "profile": False, "light": True}, "profile": {"enabled": False},
            "light": {"enabled": True, "challenge_id": ch["challenge_id"], "switches": [], "frames": []}}
    files = [("id_photo", ("id.jpg", JPG, "image/jpeg")), ("selfie", ("selfie.jpg", JPG, "image/jpeg"))]
    again = c.post(f"/session/{sid}/capture", data={"meta": json.dumps(meta)}, files=files).json()
    assert again["signals"]["light"]["flags"] == ["challenge_reused"] and again["decision"] == "block"


def test_light_absent_on_old_clients_does_not_step_up(monkeypatch):
    # A client that never heard of check 5 (no meta.light, no checks.light) is unaffected.
    monkeypatch.setattr(main.face_match, "match", lambda a, b: {"ok": True, "score": 0.7, "verdict": "match"})
    monkeypatch.setattr(main, "NAIVE", False)
    res = _switch_post(TestClient(main.app), {"label": "front", "settings": {"facingMode": "user"}, "hasMotion": True,
                                              "claimsMobile": True, "mode": "full",
                                              "checks": {"motion": False, "profile": False}, "profile": {"enabled": False}})
    assert res["signals"]["light"]["enabled"] is False and res["decision"] == "pass"
