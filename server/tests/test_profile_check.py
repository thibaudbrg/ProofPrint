"""Tests for check 6 (ID + profile turn). Geometry is driven with synthetic
yaw tracks (no consented faces in the repo); card detection uses a rendered
card; the YuNet path is covered only by a "no face in a blank frame" test.
Run from server/:  ./.venv/bin/python -m pytest tests -q
"""
import numpy as np
import cv2
import profile_check as pc


def track(yaws):
    return [None if y is None else {"yaw": y} for y in yaws]


def ramp(peak, n=24, sign=+1.0):
    return list(sign * peak * np.sin(np.linspace(0, np.pi, n)))


def test_correct_side_full_turn_passes():
    r = pc.analyse(track(ramp(0.25)), "left", cards=[True] * 20, light=[0.05] * 20)
    assert r["verdict"] == "pass", r
    assert r["peak_ok"] >= pc.TURN_FULL


def test_wrong_side_is_hard_fail():
    r = pc.analyse(track(ramp(0.25, sign=-1)), "left", cards=[True] * 20)
    assert r["verdict"] == "fail" and r["reason"] == "wrong_side", r


def test_no_turn_is_insufficient_not_fail():
    r = pc.analyse(track([0.01, -0.02, 0.0, 0.02, 0.01, 0.0, -0.01, 0.0, 0.01, 0.0]), "right")
    assert r["verdict"] == "insufficient" and r["reason"] == "no_turn", r


def test_face_lost_after_trending_counts_as_full_profile():
    yaws = list(0.14 * np.linspace(0, 1, 10)) + [None] * 6      # turning left, then YuNet loses it
    r = pc.analyse(track(yaws), "left", cards=[True] * 10)
    assert r["lost_at_profile"] is True
    assert r["verdict"] == "pass", r


def test_snap_back_is_penalised():
    yaws = ramp(0.25)
    yaws[12] = -0.05                                             # face-swap glitch mid-turn
    r = pc.analyse(track(yaws), "left", cards=[True] * 20, light=[0.05] * 20)
    assert "yaw_snap" in r["flags"]
    clean = pc.analyse(track(ramp(0.25)), "left", cards=[True] * 20, light=[0.05] * 20)
    assert r["score"] < clean["score"]


def test_no_card_lowers_score_and_flags():
    with_card = pc.analyse(track(ramp(0.25)), "left", cards=[True] * 20)
    no_card = pc.analyse(track(ramp(0.25)), "left", cards=[False] * 20)
    assert no_card["score"] < with_card["score"]
    assert "card_not_seen" in no_card["flags"]


def test_too_few_frames_is_insufficient():
    assert pc.analyse(track(ramp(0.25, n=5)), "left")["verdict"] == "insufficient"


def test_disabled_and_missing():
    assert pc.check(None, "left", enabled=False)["verdict"] == "absent"
    assert pc.check([], "left", enabled=True)["verdict"] == "absent"
    assert pc.check([], "left", enabled=True)["ok"] is False


def test_card_detector_finds_rendered_card():
    img = np.full((480, 640, 3), 40, np.uint8)
    cv2.rectangle(img, (330, 150), (590, 315), (230, 230, 230), -1)     # 260x165 ~ ID aspect 1.58
    face = {"x": 120, "y": 120, "w": 180, "h": 220}
    card = pc._find_card_contour(img, face)
    assert card is not None
    x, y, w, h = card
    assert abs(x - 330) < 8 and abs(y - 150) < 8 and 1.3 < w / h < 1.9


def test_card_detector_ignores_far_rectangle():
    img = np.full((480, 640, 3), 40, np.uint8)
    cv2.rectangle(img, (20, 380), (220, 470), (230, 230, 230), -1)      # bottom-left, far from face
    face = {"x": 380, "y": 40, "w": 180, "h": 220}
    assert pc._find_card_contour(img, face) is None


def test_blank_frame_has_no_face():
    assert pc._landmarks(np.zeros((240, 320, 3), np.uint8)) is None


def _textured_card(w=320, h=200, seed=3):
    rng = np.random.default_rng(seed)
    card = cv2.resize(rng.integers(0, 255, (10, 16, 3), dtype=np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)
    cv2.putText(card, "SWISS ID 1234", (20, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.rectangle(card, (10, 10), (w - 10, h - 10), (0, 0, 0), 3)
    return card


def test_orb_template_finds_the_same_card_in_a_frame():
    card = _textured_card()
    tpl = pc.CardTemplate(card)
    assert tpl.ok
    frame = np.full((480, 640, 3), 60, np.uint8)
    small = cv2.resize(card, (240, 150))
    frame[100:250, 360:600] = small                      # card pasted at a known spot
    r = tpl.find(frame)
    assert r is not None, "ORB should find the step-1 card in the frame"
    (x, y, w, h), quad = r
    assert abs(x - 360) < 15 and abs(y - 100) < 15 and abs(w - 240) < 30 and abs(h - 150) < 30


def test_orb_template_rejects_a_different_card():
    tpl = pc.CardTemplate(_textured_card(seed=3))
    frame = np.full((480, 640, 3), 60, np.uint8)
    frame[100:250, 360:600] = cv2.resize(_textured_card(seed=11), (240, 150))
    assert tpl.find(frame) is None


def test_occlusion_edge_quality_real_vs_painted_over():
    # A card whose right half lies inside the "face box"; real = sharp edge kept.
    img = np.full((480, 640, 3), 120, np.uint8)
    face_box = (300, 80, 240, 300)
    card_box = (200, 150, 220, 140)
    real = img.copy(); cv2.rectangle(real, (200, 150), (420, 290), (240, 240, 240), -1)
    q_real = pc.occlusion_edge_quality(real, None, card_box, face_box)
    # "Swap painted over the card": inside the face box the card edge is gone (smooth skin tone).
    painted = real.copy(); painted[80:380, 300:540] = (120, 120, 120)
    q_fake = pc.occlusion_edge_quality(painted, None, card_box, face_box)
    assert q_real is not None and q_fake is not None
    assert q_real > 0.7 and q_fake < 0.35, (q_real, q_fake)


def test_occlusion_flags_feed_the_score():
    base = dict(cards=[True] * 20, light=[0.05] * 20)
    with_occl = pc.analyse(track(ramp(0.25)), "left", occl=[0.9] * 6, **base)
    erased = pc.analyse(track(ramp(0.25)), "left", occl=[0.1] * 6, **base)
    never = pc.analyse(track(ramp(0.25)), "left", occl=[], **base)
    assert with_occl["score"] > erased["score"]
    assert "card_edge_erased_over_face" in erased["flags"]
    assert "card_never_crossed_face" in never["flags"]
