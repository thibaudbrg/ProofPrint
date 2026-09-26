"""Check 2 - the ID document.

Turns the card photo into (a) a clean portrait for face matching and (b) structured
document data, the way a real onboarding app does. Every step degrades gracefully:
nothing here can fail an honest user on its own.

    deskew()     largest quadrilateral -> perspective-warp to ID-1 proportions (fallback: input)
    portrait()   best face on the card, cropped with margin          -> fed to face_match
    read_mrz()   machine-readable zone via PassportEye + Tesseract;
                 ICAO 9303 check digits are RECOMPUTED here, not trusted from the OCR
    check()      assembles the `id` signal for fusion

The MRZ is optional by design:
  - TD1 cards (Swiss ID card, titre de séjour) carry it on the BACK  -> optional back capture
  - passports (TD3) carry it on the photo page                       -> front works
  - driving licences have none                                        -> face only, never blocked
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import tempfile
from typing import Optional

import cv2
import numpy as np

import face_match

# ID-1 card: 85.60 x 53.98 mm
CARD_ASPECT = 85.60 / 53.98
DESKEW_WIDTH = 1000          # px width of the straightened card
PORTRAIT_MARGIN = 0.35       # extra context around the detected face (fraction of box)
THUMB_WIDTH = 160            # px, for the result-screen thumbnail


# ----------------------------------------------------------------------------- deskew
def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def deskew(bgr: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find the card's outline and straighten it. Returns (image, did_deskew).

    If no convincing quadrilateral is found (e.g. the card already fills the crop),
    the input is returned unchanged - that is the common case with our guided capture.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(c)
        if not (0.30 * w * h < area < 0.97 * w * h):
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4:
            best = approx.reshape(4, 2).astype(np.float32)
            break
    if best is None:
        return bgr, False

    src = _order_corners(best)
    out_w, out_h = DESKEW_WIDTH, int(DESKEW_WIDTH / CARD_ASPECT)
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], np.float32)
    warped = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(src, dst), (out_w, out_h))
    return warped, True


# --------------------------------------------------------------------------- portrait
def portrait(card_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Crop the document's portrait (best face + margin). None if no face is found."""
    best = face_match._best_face(card_bgr)
    if best is None:
        return None
    face, img = best[0], best[1]
    x, y, fw, fh = (float(v) for v in face[:4])
    ih, iw = img.shape[:2]
    mx, my = fw * PORTRAIT_MARGIN, fh * PORTRAIT_MARGIN
    x0, y0 = int(max(0, x - mx)), int(max(0, y - my))
    x1, y1 = int(min(iw, x + fw + mx)), int(min(ih, y + fh + my))
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    return img[y0:y1, x0:x1]


def _thumb_data_url(bgr: np.ndarray, width: int = THUMB_WIDTH) -> str:
    h, w = bgr.shape[:2]
    small = cv2.resize(bgr, (width, int(h * width / w)))
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else ""


# -------------------------------------------------------------------------------- MRZ
def _check_digit(field: str) -> int:
    """ICAO 9303 check digit: weights 7-3-1 cycling; '<'=0, digits, A=10..Z=35."""
    total = 0
    for i, ch in enumerate(field.upper()):
        if ch.isdigit():
            v = int(ch)
        elif "A" <= ch <= "Z":
            v = ord(ch) - 55
        else:
            v = 0
        total += v * (7, 3, 1)[i % 3]
    return total % 10


def _iso_date(yymmdd: str, is_expiry: bool) -> Optional[str]:
    if not (isinstance(yymmdd, str) and len(yymmdd) == 6 and yymmdd.isdigit()):
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    year = 2000 + yy
    if not is_expiry and year > dt.date.today().year:   # a birth date can't be in the future
        year -= 100
    try:
        return dt.date(year, mm, dd).isoformat()
    except ValueError:
        return None


def read_mrz(bgr: np.ndarray) -> Optional[dict]:
    """OCR the machine-readable zone. Returns a parsed dict, or None if there is none."""
    try:
        from passporteye import read_mrz as _pe_read
    except ImportError:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            cv2.imwrite(f.name, bgr)
            mrz = _pe_read(f.name)
        if mrz is None:
            return None
        d = mrz.to_dict()
    except Exception as exc:              # OCR is best-effort; never take the pipeline down
        print(f"[id_check] mrz error: {exc}")
        return None

    number = (d.get("number") or "").replace(" ", "")
    dob, exp = d.get("date_of_birth") or "", d.get("expiration_date") or ""

    def _ok(field: str, given: str) -> Optional[bool]:
        return _check_digit(field) == int(given) if str(given).isdigit() else None

    checks = {
        "number": _ok(number.ljust(9, "<"), d.get("check_number", "")),
        "birth_date": _ok(dob, d.get("check_date_of_birth", "")),
        "expiry_date": _ok(exp, d.get("check_expiration_date", "")),
    }
    expiry_iso = _iso_date(exp, is_expiry=True)
    return {
        "format": d.get("mrz_type"),                       # TD1 / TD2 / TD3
        "type": (d.get("type") or "")[:1],                 # 'I' id/residence, 'P' passport
        "country": d.get("country"),
        "number": number.strip("<"),
        "surname": (d.get("surname") or "").replace("<", " ").strip(),
        "names": (d.get("names") or "").replace("<", " ").strip(),
        "nationality": d.get("nationality"),
        "sex": d.get("sex"),
        "birth_date": _iso_date(dob, is_expiry=False),
        "expiry_date": expiry_iso,
        "expired": (dt.date.fromisoformat(expiry_iso) < dt.date.today()) if expiry_iso else None,
        "checks": checks,
        "ocr_confidence": round(float(d.get("valid_score", 0)) / 100, 2),
    }


def _doc_type(mrz: Optional[dict]) -> str:
    if not mrz:
        return "no_mrz"                    # e.g. driving licence, or MRZ side not captured
    return {"P": "passport", "I": "id_card", "A": "id_card", "C": "id_card"}.get(mrz["type"], "id_card")


# ------------------------------------------------------------------------------ check
def check(front_bgr: np.ndarray, back_bgr: Optional[np.ndarray] = None) -> tuple[dict, Optional[np.ndarray]]:
    """Run the document checks. Returns (id_signal, portrait_bgr_or_None).

    id_signal flags only RAISE risk (-> step-up), they never block:
        document_expired      the MRZ expiry date is in the past
        mrz_checksum_failed   OCR'd fields don't match their ICAO check digits
    """
    card, did_deskew = deskew(front_bgr)
    port = portrait(card)

    mrz = read_mrz(back_bgr) if back_bgr is not None else None
    if mrz is None:                        # passports carry the MRZ on the front
        mrz = read_mrz(card)

    flags: list[str] = []
    if mrz:
        if mrz.get("expired"):
            flags.append("document_expired")
        if any(v is False for v in mrz["checks"].values()):
            flags.append("mrz_checksum_failed")

    signal = {
        "ok": port is not None and not flags,
        "portrait_found": port is not None,
        "deskewed": did_deskew,
        "doc_type": _doc_type(mrz),
        "mrz": mrz,
        "flags": flags,
        "portrait_thumb": _thumb_data_url(port) if port is not None else "",
    }
    print(f"[id_check] portrait={'yes' if port is not None else 'NO'} deskew={did_deskew} "
          f"doc={signal['doc_type']} flags={flags}")
    return signal, port
