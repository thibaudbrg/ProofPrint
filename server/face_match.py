"""Face detection + recognition with OpenCV YuNet + SFace (both commercial-safe).

YuNet - detector, MIT       (MIR 2023)
SFace - recognizer, Apache  (IEEE TIP 2021)
Models ship in the OpenCV Zoo; this downloads them on first run if missing.
"""
from __future__ import annotations
import os
import urllib.request
import numpy as np
import cv2

HERE = os.path.dirname(__file__)
MODELS = os.path.join(HERE, "models")

# Pinned model files from the OpenCV Zoo (raw GitHub).
YUNET = ("face_detection_yunet_2023mar.onnx",
         "https://github.com/opencv/opencv_zoo/raw/main/models/"
         "face_detection_yunet/face_detection_yunet_2023mar.onnx")
SFACE = ("face_recognition_sface_2021dec.onnx",
         "https://github.com/opencv/opencv_zoo/raw/main/models/"
         "face_recognition_sface/face_recognition_sface_2021dec.onnx")

# OpenCV Zoo's own suggested thresholds. CALIBRATE the band on your own
# ID-vs-selfie pairs - expect lower scores than selfie-vs-selfie (see DocFace+).
COSINE_MATCH = 0.363   # >= this = same person (OpenCV default)
COSINE_LOW = 0.28      # < this = clear mismatch; between the two = review

_detector = None
_recognizer = None


def _ensure(name_url):
    name, url = name_url
    path = os.path.join(MODELS, name)
    if not os.path.exists(path):
        os.makedirs(MODELS, exist_ok=True)
        print(f"[face_match] downloading {name} ...")
        urllib.request.urlretrieve(url, path)
    return path


def _load():
    global _detector, _recognizer
    if _detector is None:
        # score_threshold 0.6 (default 0.9 misses small document portraits)
        _detector = cv2.FaceDetectorYN.create(_ensure(YUNET), "", (320, 320), 0.6)
        _recognizer = cv2.FaceRecognizerSF.create(_ensure(SFACE), "")
    return _detector, _recognizer


def _prep(bgr):
    """Downscale huge phone photos so detection is stable and fast."""
    h, w = bgr.shape[:2]
    m = max(h, w)
    if m > 1024:
        s = 1024.0 / m
        bgr = cv2.resize(bgr, (int(w * s), int(h * s)))
    return bgr


def _orientations(bgr):
    # Phone JPEGs carry EXIF rotation that cv2.imdecode ignores, so the face
    # can arrive sideways. Try all four; YuNet is not rotation-invariant.
    return [bgr,
            cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE),
            cv2.rotate(bgr, cv2.ROTATE_180),
            cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)]


def _best_face(bgr: np.ndarray):
    """Find the best face across the 4 rotations. Returns (face_row, image, score, w, h) or None.

    Prefer the highest DETECTION SCORE (col 14), not the biggest box - a low
    threshold can otherwise grab a large non-face patch on a busy document.
    """
    det, _ = _load()
    best = None
    for img in _orientations(_prep(bgr)):
        ih, iw = img.shape[:2]
        det.setInputSize((iw, ih))
        _, faces = det.detect(img)
        if faces is None:
            continue
        for f in faces:
            score = float(f[14])
            if best is None or score > best[2]:
                best = (f, img, score, int(f[2]), int(f[3]))
    return best


def embed(bgr: np.ndarray):
    """Return the 128-d embedding of the best face across rotations, or None."""
    _, rec = _load()
    best = _best_face(bgr)
    if best is None:
        return None
    face, img = best[0], best[1]
    return rec.feature(rec.alignCrop(img, face))


def match(id_bgr: np.ndarray, selfie_bgr: np.ndarray) -> dict:
    """Compare an ID-photo face to a selfie face. Returns a signal dict."""
    _, rec = _load()
    bi, bs = _best_face(id_bgr), _best_face(selfie_bgr)
    if bi is None:
        return {"ok": False, "score": 0.0, "reason": "no face on ID"}
    if bs is None:
        return {"ok": False, "score": 0.0, "reason": "no face in selfie"}

    e_id = rec.feature(rec.alignCrop(bi[1], bi[0]))
    e_self = rec.feature(rec.alignCrop(bs[1], bs[0]))
    score = float(rec.match(e_id, e_self, cv2.FaceRecognizerSF_FR_COSINE))

    if score >= COSINE_MATCH:
        verdict = "match"
    elif score >= COSINE_LOW:
        verdict = "review"
    else:
        verdict = "mismatch"

    dbg = {"id_face": f"{bi[3]}x{bi[4]}@{bi[2]:.2f}",
           "selfie_face": f"{bs[3]}x{bs[4]}@{bs[2]:.2f}"}
    # server-log line so you can see what was detected
    print(f"[face_match] score={score:.3f} verdict={verdict} "
          f"id={dbg['id_face']} selfie={dbg['selfie_face']}")
    return {"ok": verdict == "match", "score": round(score, 3),
            "verdict": verdict, "debug": dbg}


def imdecode(raw: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
