"""Check 6 - the profile-turn challenge (the killer feature, simplified).

In the SAME continuous capture as the selfie, right after the phone-tilt phase,
the user holds their ID in their LEFT hand (a fixed, simple instruction - not
verified by computer vision) and turns their HEAD to a FULL PROFILE on a side
the server picked at session start.

Why this alone is the killer signal (see notes/07 for the full write-up):
  * Real-time face swap breaks at a full profile (Metaphysic / Biometric Update):
    2D alignment maps only 50-60% of landmarks side-on, and the swap warps at
    ~90 deg. -> `yaw_proxy` from YuNet's 5 landmarks.
  * The side is a per-session NONCE minted by the server, never trusted from the
    client -> a pre-recorded clip cannot know which way to turn. Wrong side is a
    hard fail.
  * "Face lost after trending the right way" counts as reaching full profile,
    because YuNet (a frontal-ish detector) usually loses the face before 90 deg.
  * A sudden yaw jump mid-turn ("snap") is the visible seam where a face-swap's
    tracking loses lock and re-centres -> penalised, not blocking on its own.

v1 of this check also tried to (a) find and (b) same-document-match the ID card
via ORB features, and (c) check whether a face-swap "painted over" the card at
the occlusion edge. Simplified away 2026-09-24: card detection scored 0 on the
first real phone run (contour heuristic never found the card in venue light),
and the extra machinery added failure surface without a validated benefit. The
ID-in-hand is now a UX/demo instruction only - what is actually verified is the
head turn, which is the one signal that was confirmed correct on a real phone.

Verdicts: pass | review | fail | insufficient | absent. Wrong side is a hard
fail. No turn at all is `insufficient` (ask again), not fail, so a shy or
confused user is stepped up rather than blocked.

Frames are RAW (unmirrored) front-camera pixels. The user's LEFT is the
camera's RIGHT, so turning left moves the nose to +x in the image - confirmed
on a real phone 2026-09-24 (asked RIGHT, peak_ok landed on the right).
"""
from __future__ import annotations
import numpy as np

import face_match

SIDE_SIGN = {"left": +1.0, "right": -1.0}
TURN_MIN = 0.12          # |yaw proxy| that counts as "turned" (~45 deg). Real phone: 0.17 on a modest turn.
TURN_FULL = 0.22         # yaw proxy that scores 1.0 (near profile)
LOST_MIN_FRAMES = 3      # face lost for >= this many frames after trending = full profile
MIN_FRAMES = 8
MIN_FACE_FRAMES = 4
SNAP_JUMP = 0.20         # yaw jump between consecutive frames that looks like a swap glitch
SNAP_PENALTY = 0.35
SCORE_PASS = 0.60
SCORE_REVIEW = 0.35


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
            "nose": (float(f[8]), float(f[9])), "score": float(f[14])}


def yaw_proxy(lm: dict) -> float:
    (x1, _), (x2, _) = lm["eyes"]
    return (lm["nose"][0] - (x1 + x2) / 2.0) / max(lm["w"], 1.0)


def analyse(track: list[dict | None], side: str) -> dict:
    """Pure geometry on a per-frame track of {"yaw": float} (None = face lost)."""
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

    lost_tail = 0
    for y in reversed(yaws):
        if y is not None:
            break
        lost_tail += 1
    last_seen = [y * sign for y in yaws[:n - lost_tail] if y is not None][-3:]
    lost_after_turn = bool(lost_tail >= LOST_MIN_FRAMES and last_seen and np.mean(last_seen) > TURN_MIN * 0.6)
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
    flags = []
    score = turn_q
    if snap >= SNAP_JUMP:
        score -= SNAP_PENALTY
        flags.append("yaw_snap")
    score = round(float(max(0.0, score)), 3)
    verdict = "pass" if score >= SCORE_PASS else ("review" if score >= SCORE_REVIEW else "fail")
    return {"ok": verdict == "pass", "verdict": verdict, "score": score,
            "peak_ok": round(peak_ok, 3), "peak_wrong": round(peak_wrong, 3),
            "turn_q": round(turn_q, 2), "snap": round(snap, 3),
            "lost_at_profile": lost_after_turn, "flags": flags,
            "yaw_series": [None if y is None else round(y, 3) for y in yaws]}


def check(frames: list[tuple[float, np.ndarray]] | None, side: str | None, enabled: bool) -> dict:
    """frames: [(t_ms, bgr)], side: server-minted 'left'|'right'."""
    if not enabled:
        return {"ok": True, "verdict": "absent", "enabled": False}
    if not frames:
        return {"ok": False, "verdict": "absent", "enabled": True, "score": 0.0,
                "reason": "no profile burst"}
    frames = sorted(frames, key=lambda f: f[0])
    track = []
    for _, bgr in frames:
        lm = _landmarks(bgr)
        track.append(None if lm is None else {"yaw": yaw_proxy(lm)})
    out = analyse(track, side or "")
    out.update({"enabled": True, "n_frames": len(frames), "side": side})
    print(f"[profile_check] side={side} verdict={out['verdict']} score={out.get('score')} "
          f"peak_ok={out.get('peak_ok')} peak_wrong={out.get('peak_wrong')} "
          f"frames={len(frames)} faces={sum(1 for t in track if t)} flags={out.get('flags')}")
    return out
