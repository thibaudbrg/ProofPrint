"""Check 6 — the "ID next to face + profile turn" challenge (the killer feature).

At the end of the selfie the server asks the user to hold their ID card next to
their face and turn their head to a FULL PROFILE on a side the server picked at
session start. Real-time face-swap tools work head-on; they warp at ~90 deg, and
they ghost where a card crosses the face. The side is a nonce, so a pre-recorded
clip cannot follow it.

Per frame we take YuNet's 5 landmarks (right eye, left eye, nose tip, mouth
corners) and derive a yaw proxy: how far the nose tip sits from the eye midpoint,
normalised by face width. Frontal ~0, 45 deg ~0.15, and near full profile YuNet
usually loses the face altogether -- which we also count as "turned" if the last
frames before the loss were trending to the requested side.

Signals:
  turn        peak yaw on the requested side (hard: wrong side -> fail)
  card        fraction of frames with a card-shaped rectangle next to the face
  lighting    face crop vs card crop colour-temperature agreement
  snap        largest frame-to-frame yaw jump (face-swap "snap back")

Verdicts: pass | review | fail | insufficient | absent. Wrong side is a hard
fail. No turn at all is `insufficient` (ask again), not fail, so a shy user is
stepped up rather than blocked.

Frames are RAW (unmirrored) front-camera pixels. The user's LEFT is the
camera's RIGHT, so turning left moves the nose to +x in the image. Flip
SIDE_SIGN if a phone test shows the opposite.
"""
from __future__ import annotations
import numpy as np
import cv2

import face_match

SIDE_SIGN = {"left": +1.0, "right": -1.0}
TURN_MIN = 0.12          # |yaw proxy| that counts as "turned" (~45 deg). Calibrate.
TURN_FULL = 0.22         # yaw proxy that scores 1.0 (near profile)
LOST_MIN_FRAMES = 3      # face lost for >= this many frames after trending = full profile
MIN_FRAMES = 8
MIN_FACE_FRAMES = 4      # need at least this many frames with a face to judge
SNAP_JUMP = 0.20         # yaw jump between consecutive frames that looks like a swap glitch
SCORE_PASS = 0.55
SCORE_REVIEW = 0.30
CARD_MIN_AREA = 0.02     # of the frame
CARD_ASPECT = (1.25, 2.0)


def _landmarks(bgr: np.ndarray) -> dict | None:
    """Best YuNet face in a frame -> {x, y, w, h, eyes, nose, score} or None."""
    det, _ = face_match._load()
    img = face_match._prep(bgr)
    ih, iw = img.shape[:2]
    det.setInputSize((iw, ih))
    _, faces = det.detect(img)
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda r: float(r[14]))
    return {"x": float(f[0]), "y": float(f[1]), "w": float(f[2]), "h": float(f[3]),
            "eyes": ((float(f[4]), float(f[5])), (float(f[6]), float(f[7]))),
            "nose": (float(f[8]), float(f[9])), "score": float(f[14]),
            "img": img}


def yaw_proxy(lm: dict) -> float:
    (x1, _), (x2, _) = lm["eyes"]
    return (lm["nose"][0] - (x1 + x2) / 2.0) / max(lm["w"], 1.0)


def _find_card(img: np.ndarray, face: dict | None) -> tuple[float, float, float, float] | None:
    """Card-shaped quadrilateral near the face -> (x, y, w, h) or None."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    edges = cv2.Canny(g, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ih, iw = g.shape
    best = None
    for c in cnts:
        area = cv2.contourArea(c)
        if area < CARD_MIN_AREA * iw * ih:
            continue
        approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        (cx, cy), (rw, rh), _ = cv2.minAreaRect(approx)
        if min(rw, rh) <= 0:
            continue
        aspect = max(rw, rh) / min(rw, rh)
        if not (CARD_ASPECT[0] <= aspect <= CARD_ASPECT[1]):
            continue
        if face is not None:
            fx, fy = face["x"] + face["w"] / 2, face["y"] + face["h"] / 2
            # the card must overlap the face box or sit right next to it
            if abs(cx - fx) > 1.6 * face["w"] or abs(cy - fy) > 1.2 * face["h"]:
                continue
        x, y, w, h = cv2.boundingRect(approx)
        if best is None or area > best[0]:
            best = (area, (x, y, w, h))
    return None if best is None else best[1]


def _colour_temp(bgr_crop: np.ndarray) -> float | None:
    if bgr_crop.size == 0:
        return None
    m = bgr_crop.reshape(-1, 3).mean(axis=0)   # B, G, R
    return float(m[0] / max(m[2], 1.0))         # blue/red: >1 cool, <1 warm


def analyse(track: list[dict | None], side: str, cards: list[bool] | None = None,
            light: list[float] | None = None) -> dict:
    """Pure geometry on a per-frame track of landmark dicts (None = face lost).

    track items need only {"yaw": float} (from yaw_proxy) so tests can drive it.
    """
    if side not in SIDE_SIGN:
        return {"ok": False, "verdict": "absent", "score": 0.0, "reason": "no side"}
    sign = SIDE_SIGN[side]
    n = len(track)
    if n < MIN_FRAMES:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "reason": f"frames={n}"}
    yaws = [t["yaw"] if t else None for t in track]
    seen = [y for y in yaws if y is not None]
    if len(seen) < MIN_FACE_FRAMES:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "reason": "no face"}

    signed = [y * sign for y in seen]               # + = towards requested side
    peak_ok = max(signed)
    peak_wrong = max(-y for y in signed)

    # Face lost at the end after trending the right way = full profile reached.
    lost_tail = 0
    for y in reversed(yaws):
        if y is not None:
            break
        lost_tail += 1
    last_seen = [y * sign for y in yaws[:n - lost_tail] if y is not None][-3:]
    lost_after_turn = lost_tail >= LOST_MIN_FRAMES and last_seen and np.mean(last_seen) > TURN_MIN * 0.6
    if lost_after_turn:
        peak_ok = max(peak_ok, TURN_FULL)

    jumps = [abs(b - a) for a, b in zip(seen, seen[1:])]
    snap = max(jumps) if jumps else 0.0

    if peak_wrong >= TURN_MIN and peak_ok < TURN_MIN:
        return {"ok": False, "verdict": "fail", "score": 0.0, "reason": "wrong_side",
                "peak_ok": round(peak_ok, 3), "peak_wrong": round(peak_wrong, 3)}
    if peak_ok < TURN_MIN:
        return {"ok": False, "verdict": "insufficient", "score": 0.0, "reason": "no_turn",
                "peak_ok": round(peak_ok, 3), "peak_wrong": round(peak_wrong, 3)}

    turn_q = min(1.0, peak_ok / TURN_FULL)
    card_frac = float(np.mean(cards)) if cards else 0.0
    light_q = 0.0
    if light:
        # light: per-frame |log(face_ct / card_ct)|, 0 = identical colour temperature
        light_q = float(np.clip(1.0 - np.mean(light) / 0.4, 0.0, 1.0))
    score = 0.6 * turn_q + 0.25 * card_frac + 0.15 * light_q
    if snap >= SNAP_JUMP:
        score -= 0.2
    score = round(float(max(0.0, score)), 3)
    verdict = "pass" if score >= SCORE_PASS else ("review" if score >= SCORE_REVIEW else "fail")
    flags = []
    if snap >= SNAP_JUMP:
        flags.append("yaw_snap")
    if cards and card_frac < 0.2:
        flags.append("card_not_seen")
    return {"ok": verdict == "pass", "verdict": verdict, "score": score,
            "peak_ok": round(peak_ok, 3), "peak_wrong": round(peak_wrong, 3),
            "turn_q": round(turn_q, 2), "card_frac": round(card_frac, 2),
            "light_q": round(light_q, 2), "snap": round(snap, 3),
            "lost_at_profile": bool(lost_after_turn), "flags": flags,
            "yaw_series": [None if y is None else round(y, 3) for y in yaws]}


def check(frames: list[tuple[float, np.ndarray]] | None, side: str | None, enabled: bool) -> dict:
    """frames: [(t_ms, bgr)], side: server-minted 'left'|'right'."""
    if not enabled:
        return {"ok": True, "verdict": "absent", "enabled": False}
    if not frames:
        return {"ok": False, "verdict": "absent", "enabled": True, "score": 0.0,
                "reason": "no profile burst"}
    frames = sorted(frames, key=lambda f: f[0])
    track, cards, light = [], [], []
    for _, bgr in frames:
        lm = _landmarks(bgr)
        if lm is None:
            track.append(None)
            continue
        track.append({"yaw": yaw_proxy(lm)})
        img = lm["img"]
        card = _find_card(img, lm)
        cards.append(card is not None)
        if card is not None:
            x, y, w, h = card
            fx, fy, fw, fh = int(lm["x"]), int(lm["y"]), int(lm["w"]), int(lm["h"])
            ct_face = _colour_temp(img[max(0, fy):fy + fh, max(0, fx):fx + fw])
            ct_card = _colour_temp(img[y:y + h, x:x + w])
            if ct_face and ct_card:
                light.append(abs(float(np.log(ct_face / ct_card))))
    out = analyse(track, side or "", cards, light)
    out["enabled"] = True
    out["n_frames"] = len(frames)
    out["side"] = side
    print(f"[profile_check] side={side} verdict={out['verdict']} score={out.get('score')} "
          f"peak_ok={out.get('peak_ok')} peak_wrong={out.get('peak_wrong')} "
          f"card_frac={out.get('card_frac')} frames={len(frames)} "
          f"faces={sum(1 for t in track if t)}")
    return out
