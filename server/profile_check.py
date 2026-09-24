"""Check 6 — the "ID next to face + profile turn" challenge (the killer feature).

In the SAME continuous capture as the selfie, the server asks the user to hold
their ID card up beside their face, covering one eye, and turn their head to a
FULL PROFILE on a side the server picked at session start.

What the literature says, and where each part lives here:
  * Profile turn breaks real-time face swap (Metaphysic / Biometric Update):
    2D alignment maps only 50-60 % of landmarks side-on, the swap warps at ~90.
    -> `yaw_proxy` from YuNet's 5 landmarks; wrong side = hard fail; "face lost
       after trending the right way" = full profile reached.
  * Occlusion breaks it too (KnowBe4): a hand or card crossing the face ghosts at
    the mask edge, because the swap paints its face OVER the occluder.
    -> the card must actually overlap the face box (`occlusion_seen`), and while
       it does, the card's straight edge must stay visible INSIDE the face box
       (`occlusion_q`). A swap that paints over the card erases that edge.
  * Cross-capture consistency (our notes): card and face are lit by the same
    source in the same frame -> colour temperature of the two crops must agree.
  * Same document: the card in the frame is matched by ORB features against the
    ID photo taken in step 1 (homography). A different or generated card, or no
    card, scores 0 on `card_frac`.

Verdicts: pass | review | fail | insufficient | absent. Wrong side is a hard
fail. No turn at all is `insufficient` (ask again), not fail, so a shy user is
stepped up rather than blocked.

Frames are RAW (unmirrored) front-camera pixels. The user's LEFT is the
camera's RIGHT, so turning left moves the nose to +x in the image. Confirmed on
a real phone 2026-09-24 (asked RIGHT, peak_ok on the right).
"""
from __future__ import annotations
import numpy as np
import cv2

import face_match

SIDE_SIGN = {"left": +1.0, "right": -1.0}
TURN_MIN = 0.12          # |yaw proxy| that counts as "turned" (~45 deg). Real phone: 0.17 for a modest turn.
TURN_FULL = 0.22         # yaw proxy that scores 1.0 (near profile)
LOST_MIN_FRAMES = 3      # face lost for >= this many frames after trending = full profile
MIN_FRAMES = 8
MIN_FACE_FRAMES = 4
SNAP_JUMP = 0.20         # yaw jump between consecutive frames that looks like a swap glitch
SCORE_PASS = 0.55
SCORE_REVIEW = 0.30
ORB_MIN_MATCHES = 12     # good matches needed to accept the ID card in a frame
ORB_RATIO = 0.75
CARD_MIN_AREA = 0.02     # contour fallback
CARD_ASPECT = (1.25, 2.0)
OCCLUSION_MIN_OVERLAP = 0.10   # card/face IoU-ish overlap that counts as occlusion

_orb = cv2.ORB_create(nfeatures=800)
_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


def _landmarks(bgr: np.ndarray) -> dict | None:
    """Best YuNet face in a frame -> {x, y, w, h, eyes, nose, score, img} or None."""
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


class CardTemplate:
    """ORB descriptors of the ID photo from step 1, matched into each frame."""

    def __init__(self, id_bgr: np.ndarray | None):
        self.ok = False
        if id_bgr is None:
            return
        g = cv2.cvtColor(face_match._prep(id_bgr), cv2.COLOR_BGR2GRAY)
        self.h, self.w = g.shape
        self.kp, self.des = _orb.detectAndCompute(g, None)
        self.ok = self.des is not None and len(self.kp) >= ORB_MIN_MATCHES

    def find(self, img: np.ndarray) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
        """-> ((x, y, w, h) bbox, 4x2 quad) of the card in `img`, or None."""
        if not self.ok:
            return None
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kp, des = _orb.detectAndCompute(g, None)
        if des is None or len(kp) < ORB_MIN_MATCHES:
            return None
        good = []
        for pair in _bf.knnMatch(self.des, des, k=2):
            if len(pair) == 2 and pair[0].distance < ORB_RATIO * pair[1].distance:
                good.append(pair[0])
        if len(good) < ORB_MIN_MATCHES:
            return None
        src = np.float32([self.kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or inl is None or int(inl.sum()) < ORB_MIN_MATCHES:
            return None
        quad = cv2.perspectiveTransform(
            np.float32([[0, 0], [self.w, 0], [self.w, self.h], [0, self.h]]).reshape(-1, 1, 2), H).reshape(4, 2)
        if not cv2.isContourConvex(quad.astype(np.float32)):
            return None
        area = cv2.contourArea(quad.astype(np.float32))
        if area < 0.005 * img.shape[0] * img.shape[1] or area > 0.9 * img.shape[0] * img.shape[1]:
            return None
        x, y, w, h = cv2.boundingRect(quad.astype(np.int32))
        return (x, y, w, h), quad


def _find_card_contour(img: np.ndarray, face: dict | None) -> tuple[int, int, int, int] | None:
    """Fallback: card-shaped quadrilateral near the face -> (x, y, w, h) or None."""
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
            if abs(cx - fx) > 1.6 * face["w"] or abs(cy - fy) > 1.2 * face["h"]:
                continue
        x, y, w, h = cv2.boundingRect(approx)
        if best is None or area > best[0]:
            best = (area, (x, y, w, h))
    return None if best is None else best[1]


def _find_card(img: np.ndarray, face: dict | None, tpl: CardTemplate | None = None):
    """-> (bbox, quad-or-None, source) or None. ORB against the step-1 ID first."""
    if tpl is not None:
        r = tpl.find(img)
        if r is not None:
            return r[0], r[1], "orb"
    r = _find_card_contour(img, face)
    return None if r is None else (r, None, "contour")


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection over the SMALLER box (a card partly over a face)."""
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / max(1.0, min(aw * ah, bw * bh))


def occlusion_edge_quality(img: np.ndarray, quad: np.ndarray | None, card_box, face_box) -> float | None:
    """How much of the card's boundary that lies INSIDE the face box is still a
    real edge. A face-swap paints its face over the card and erases that edge.
    Returns a 0..1 fraction, or None when the card does not cross the face."""
    fx, fy, fw, fh = face_box
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 50, 150)
    if quad is None:
        x, y, w, h = card_box
        quad = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    pts_on, pts_in = 0, 0
    for i in range(4):
        p, q = quad[i], quad[(i + 1) % 4]
        n = int(max(2, np.hypot(*(q - p)) / 3))
        for t in np.linspace(0, 1, n):
            x, y = p + t * (q - p)
            if not (fx <= x < fx + fw and fy <= y < fy + fh):
                continue
            xi, yi = int(round(x)), int(round(y))
            if 0 <= yi < edges.shape[0] and 0 <= xi < edges.shape[1]:
                pts_in += 1
                y0, y1 = max(0, yi - 2), min(edges.shape[0], yi + 3)
                x0, x1 = max(0, xi - 2), min(edges.shape[1], xi + 3)
                if edges[y0:y1, x0:x1].any():
                    pts_on += 1
    if pts_in < 8:
        return None
    return pts_on / pts_in


def _colour_temp(bgr_crop: np.ndarray) -> float | None:
    if bgr_crop.size == 0:
        return None
    m = bgr_crop.reshape(-1, 3).mean(axis=0)   # B, G, R
    return float(m[0] / max(m[2], 1.0))         # blue/red: >1 cool, <1 warm


def analyse(track: list[dict | None], side: str, cards: list[bool] | None = None,
            light: list[float] | None = None, occl: list[float] | None = None) -> dict:
    """Pure geometry on a per-frame track of {"yaw": float} (None = face lost).

    cards: per face-frame, was the card found.  light: per card-frame |log ct ratio|.
    occl: per occluded frame, edge quality 0..1 (empty = card never crossed the face).
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
    card_frac = float(np.mean(cards)) if cards else 0.0
    light_q = float(np.clip(1.0 - np.mean(light) / 0.4, 0.0, 1.0)) if light else 0.0
    occlusion_seen = bool(occl)
    occl_q = float(np.mean(occl)) if occl else 0.0
    score = 0.45 * turn_q + 0.20 * card_frac + 0.20 * occl_q + 0.15 * light_q
    flags = []
    if snap >= SNAP_JUMP:
        score -= 0.2; flags.append("yaw_snap")
    if cards and card_frac < 0.2:
        flags.append("card_not_seen")
    if cards and card_frac >= 0.2 and not occlusion_seen:
        flags.append("card_never_crossed_face")
    if occlusion_seen and occl_q < 0.35:
        flags.append("card_edge_erased_over_face")     # the KnowBe4 ghosting signature
    score = round(float(max(0.0, score)), 3)
    verdict = "pass" if score >= SCORE_PASS else ("review" if score >= SCORE_REVIEW else "fail")
    return {"ok": verdict == "pass", "verdict": verdict, "score": score,
            "peak_ok": round(peak_ok, 3), "peak_wrong": round(peak_wrong, 3),
            "turn_q": round(turn_q, 2), "card_frac": round(card_frac, 2),
            "occlusion_seen": occlusion_seen, "occlusion_q": round(occl_q, 2),
            "light_q": round(light_q, 2), "snap": round(snap, 3),
            "lost_at_profile": lost_after_turn, "flags": flags,
            "yaw_series": [None if y is None else round(y, 3) for y in yaws]}


def check(frames: list[tuple[float, np.ndarray]] | None, side: str | None, enabled: bool,
          id_bgr: np.ndarray | None = None) -> dict:
    """frames: [(t_ms, bgr)], side: server-minted 'left'|'right', id_bgr: the step-1 ID photo."""
    if not enabled:
        return {"ok": True, "verdict": "absent", "enabled": False}
    if not frames:
        return {"ok": False, "verdict": "absent", "enabled": True, "score": 0.0,
                "reason": "no profile burst"}
    frames = sorted(frames, key=lambda f: f[0])
    tpl = CardTemplate(id_bgr)
    track, cards, light, occl, sources = [], [], [], [], {"orb": 0, "contour": 0}
    for _, bgr in frames:
        lm = _landmarks(bgr)
        if lm is None:
            track.append(None)
            continue
        track.append({"yaw": yaw_proxy(lm)})
        img = lm["img"]
        found = _find_card(img, lm, tpl)
        cards.append(found is not None)
        if found is None:
            continue
        (x, y, w, h), quad, src = found
        sources[src] += 1
        face_box = (int(lm["x"]), int(lm["y"]), int(lm["w"]), int(lm["h"]))
        fx, fy, fw, fh = face_box
        ct_face = _colour_temp(img[max(0, fy):fy + fh, max(0, fx):fx + fw])
        ct_card = _colour_temp(img[max(0, y):y + h, max(0, x):x + w])
        if ct_face and ct_card:
            light.append(abs(float(np.log(ct_face / ct_card))))
        if _overlap((x, y, w, h), face_box) >= OCCLUSION_MIN_OVERLAP:
            q = occlusion_edge_quality(img, quad, (x, y, w, h), face_box)
            if q is not None:
                occl.append(q)
    out = analyse(track, side or "", cards, light, occl)
    out.update({"enabled": True, "n_frames": len(frames), "side": side,
                "card_source": sources, "id_template": tpl.ok})
    print(f"[profile_check] side={side} verdict={out['verdict']} score={out.get('score')} "
          f"peak_ok={out.get('peak_ok')} peak_wrong={out.get('peak_wrong')} "
          f"card_frac={out.get('card_frac')} card_src={sources} occl_seen={out.get('occlusion_seen')} "
          f"occl_q={out.get('occlusion_q')} light_q={out.get('light_q')} "
          f"frames={len(frames)} faces={sum(1 for t in track if t)} flags={out.get('flags')}")
    return out
