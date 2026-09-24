"""Proofprint — walking skeleton (Step 0 + Step 1).

One thread that runs end to end:
  phone films ID + selfie -> uploads -> server face-matches -> returns a decision.

This is Act 1 of the demo. Turning the (not-yet-built) liveness checks off is the
naive app of Act 2. The mitigated app adds checks 3/4/5 behind the SAME endpoints.

Run:
  pip install -r requirements.txt
  uvicorn main:app --host 0.0.0.0 --port 8000
  # in another terminal, expose it over HTTPS for the phone:
  cloudflared tunnel --url http://localhost:8000
"""
from __future__ import annotations
import secrets
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import os

import audit_log
import face_match
import id_check
import integrity_check
import lab
import light_check
import motion_check
import neck_check
import profile_check

app = FastAPI(title="Proofprint")
app.include_router(lab.router)        # /lab — check-4 + check-5 debugging dashboards, isolated from the flow

# In-memory session store. Fine for a hackathon; swap for SQLite later.
SESSIONS: dict[str, dict] = {}

# Demo switch. PROOFPRINT_MODE=naive = the "broken app" of Act 2: decide on the
# face match only, ignore every liveness signal (they are still computed and
# returned so the analyst view can show what the naive app threw away).
NAIVE = os.environ.get("PROOFPRINT_MODE", "full").lower() == "naive"


@app.post("/session")
def start_session():
    """Step: start a session and mint the per-session nonces.

    Check 6: the side of the profile turn is minted here and judged against the
    SERVER's copy, never the client's. Check 5's colour sequence is NOT minted here:
    the phone asks for it just in time (POST /session/{sid}/light) so it cannot be
    pre-computed.
    """
    sid = secrets.token_hex(4)
    profile_side = secrets.choice(["left", "right"])
    SESSIONS[sid] = {"profile_side": profile_side, "light": None, "result": None}
    return {"session_id": sid, "profile_side": profile_side}


@app.post("/session/{sid}/light")
def mint_light(sid: str):
    """Check 5: mint the colour sequence just in time (single use, short TTL).

    Called when the user taps Start on the light instruction screen. Re-minting
    replaces the previous challenge (a retry after client-side jank). The server
    judges the upload against ITS stored copy, never the echoed names.
    """
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")
    ch = light_check.mint()
    cid = "lc_" + secrets.token_hex(4)
    SESSIONS[sid]["light"] = {"id": cid, "challenge": ch, "minted": time.time(), "used": False}
    return light_check.to_client(ch, cid)


def _light_signal(sid: str, meta_obj: dict, lframes: list, enabled: bool) -> dict:
    """Check 5 verdict for this capture, or the neutral 'absent' signal when the check is off."""
    lm = meta_obj.get("light") or {}
    if not enabled:
        return {"ok": True, "verdict": "absent", "enabled": False, "score": 0.0}
    if lm.get("opted_out"):
        # Photosensitivity opt-out: never a penalty, but not a liveness proof either.
        return {"ok": False, "verdict": "skipped", "enabled": True, "opted_out": True, "score": 0.0, "flags": []}
    if not lm:
        return {"ok": False, "verdict": "absent", "enabled": True, "score": 0.0, "flags": ["no_light_meta"]}
    stored = SESSIONS[sid].get("light")
    if not stored or stored["id"] != lm.get("challenge_id"):
        return {"ok": False, "verdict": "fail", "enabled": True, "score": 0.0, "flags": ["challenge_unknown"]}
    if stored["used"]:
        return {"ok": False, "verdict": "fail", "enabled": True, "score": 0.0, "flags": ["challenge_reused"]}
    stored["used"] = True
    total_s = sum(stored["challenge"]["dur_ms"]) / 1000
    expired = time.time() - stored["minted"] > total_s + light_check.CHALLENGE_TTL_S
    sig = light_check.check(lframes, light_check.colour_log_from_meta(lm), stored["challenge"])
    # Experimental, report-only (never fused): do the neck, ears and hairline take the
    # flash the same way the cheeks do? Kept only if it separates our own runs.
    try:
        sig["neck"] = neck_check.check(lframes, light_check.colour_log_from_meta(lm), stored["challenge"])
    except Exception as exc:
        sig["neck"] = {"verdict": "error", "fused": False, "error": str(exc)}
    sig["enabled"] = True
    sig["attempt"] = lm.get("attempt")
    sig["client_checks"] = lm.get("client_checks")
    if expired:
        sig["flags"] = list(sig.get("flags", [])) + ["challenge_expired"]
        if sig["verdict"] == "pass":
            sig["verdict"], sig["ok"] = "review", False
    return sig


def _decide(naive, face, idsig, integ, motion, motion_on, light, light_on, profile, prof_on):
    """The fusion rule + a human-readable reason per trigger (shown in the analyst trace).

    Naive app (Act 2): face only. Mitigated app: a known virtual camera, a video that
    does not move with the phone, skin that answers the WRONG colours, or a turn to
    the wrong side -> block; wrong person / no face -> block; anything doubtful
    (flat light response, opted out, no burst, document or capture flags) -> step up,
    never a silent pass.
    """
    fv, mv, lv, pv = face.get("verdict"), motion.get("verdict"), light.get("verdict"), profile.get("verdict")
    fs = face.get("score")
    if naive:
        d = {"match": "pass", "review": "step_up"}.get(fv, "block")
        return d, [f"Naïve mode: decided on the face match alone ({fv or 'no face'}, score {fs}). "
                   "Every liveness signal below was computed but ignored."]
    hard, soft = [], []
    if integ.get("hard"):
        hard.append("Virtual camera detected in the capture (integrity hard flag): " + ", ".join(integ.get("flags") or []))
    if motion_on and mv == "fail":
        hard.append("The video did not move with the phone's sensors (motion fail" +
                    (", " + ", ".join(motion.get("flags")) if motion.get("flags") else "") + ")")
    if light_on and lv == "fail":
        hard.append("The skin answered the wrong colour sequence (light fail" +
                    (", " + ", ".join(light.get("flags")) if light.get("flags") else "") + ")")
    if pv == "fail":
        hard.append("Turned to the wrong side (profile fail)")
    if fv is None:
        hard.append("No usable face on the document or the selfie")
    elif fv == "mismatch":
        hard.append(f"Selfie does not match the document portrait (score {fs})")
    if hard:
        return "block", hard
    if fv == "review":
        soft.append(f"Face match is borderline (score {fs})")
    if not integ.get("ok"):
        soft.append("Capture integrity flags: " + ", ".join(integ.get("flags") or []))
    if idsig.get("flags"):
        soft.append("Document flags: " + ", ".join(idsig["flags"]))
    if motion_on and mv in ("review", "insufficient", "absent", "skipped"):
        soft.append({"review": "Motion only partly matched the sensors", "insufficient": "Not enough phone movement to judge",
                     "absent": "No motion burst was received", "skipped": "User skipped the phone-move check (travelling in a vehicle)"}[mv]
                    + f" (motion {mv}" + (f", score {motion.get('score')}" if mv != "skipped" else "") + ")")
    if light_on and lv in ("review", "insufficient", "absent", "skipped"):
        soft.append({"review": "Skin answered the right colours but late / expired challenge",
                     "insufficient": "Flat light response — too bright, or the video ignores the screen",
                     "absent": "No light capture was received", "skipped": "User skipped the light check (photosensitivity)"}[lv]
                    + f" (light {lv}" + (f", score {light.get('score')}" if light.get("score") is not None else "") + ")")
    if prof_on and pv in ("review", "insufficient", "absent"):
        soft.append({"review": "Profile turn was partial", "insufficient": "No usable profile turn",
                     "absent": "No profile burst was received"}[pv] + f" (profile {pv})")
    if soft:
        return "step_up", soft
    return "pass", ["Face matches the document and every enabled liveness check passed"]


@app.post("/session/{sid}/capture")
async def capture(sid: str,
                  id_photo: UploadFile = File(...),
                  selfie: UploadFile = File(...),
                  id_back: UploadFile | None = File(None),
                  meta: str = Form(None),
                  frames: list[UploadFile] = File(default=[]),
                  profile_frames: list[UploadFile] = File(default=[]),
                  light_frames: list[UploadFile] = File(default=[])):
    """Receive the document photo(s) + selfie (+ liveness bursts + metadata), score, decide."""
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")

    id_img = face_match.imdecode(await id_photo.read())
    self_img = face_match.imdecode(await selfie.read())
    if id_img is None or self_img is None:
        raise HTTPException(400, "could not decode an image")
    back_img = face_match.imdecode(await id_back.read()) if id_back is not None else None

    timings: dict[str, float] = {}
    # Check 2 — document: straighten, extract the portrait, read the MRZ (optional back)
    t0 = time.perf_counter()
    idsig, portrait = id_check.check(id_img, back_img)
    timings["id"] = time.perf_counter() - t0
    # Check 1 — identity: match the selfie against the document PORTRAIT (falls back to the card)
    t0 = time.perf_counter()
    face = face_match.match(portrait if portrait is not None else id_img, self_img)
    timings["face"] = time.perf_counter() - t0
    # Check 3 — capture integrity
    try:
        meta_obj = json.loads(meta) if meta else {}
    except json.JSONDecodeError:
        meta_obj = {}
    integ = integrity_check.check(meta_obj)

    # Demo switches from the intro screen. `mode: naive` = the broken app of Act 2;
    # `checks.motion: false` = the phone-move check was switched off, so its absence
    # must not count against the user. (PROOFPRINT_MODE=naive still forces naive.)
    checks = meta_obj.get("checks") or {}
    naive = NAIVE or meta_obj.get("mode") == "naive"
    motion_enabled = bool(checks.get("motion", True))

    # Check 4 — gyroscope <-> video. Frame timestamps come from meta.frames
    # (matched by filename); the JPEGs come as repeated `frames` parts.
    t_by_name = {f.get("file"): f.get("t") for f in (meta_obj.get("frames") or [])}
    burst = []
    for uf in frames:
        t = t_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            burst.append((float(t), img))
    t0 = time.perf_counter()
    if motion_enabled:
        motion = motion_check.check(meta_obj.get("motion"), burst)
        timings["motion"] = time.perf_counter() - t0
    else:
        motion = {"ok": True, "verdict": "absent", "enabled": False, "score": 0.0}
    if motion_enabled and (meta_obj.get("skipped") or {}).get("motion"):
        # "I'm in a vehicle" skip on the instruction page: not a penalty, not a proof either.
        motion = {"ok": False, "verdict": "skipped", "enabled": True, "score": 0.0,
                  "reason": "user_in_vehicle", "flags": []}

    # Check 6 — ID next to face + profile turn (the killer feature). Only when
    # the client enabled it; the expected side comes from the session, not meta.
    prof_meta = meta_obj.get("profile") or {}
    prof_enabled = bool(prof_meta.get("enabled"))
    pt_by_name = {f.get("file"): f.get("t") for f in (prof_meta.get("frames") or [])}
    pburst = []
    for uf in profile_frames:
        t = pt_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            pburst.append((float(t), img))
    t0 = time.perf_counter()
    profile = profile_check.check(pburst, SESSIONS[sid].get("profile_side"), prof_enabled)
    if prof_enabled:
        timings["profile"] = time.perf_counter() - t0

    # Check 5 — light pulse: skin must reflect the colours the screen showed. Frames
    # come as `light_frames` parts matched to meta.light.frames[].file (grab time `t`;
    # the fitted lag absorbs the constant pipeline delay). Enabled when the intro
    # switch says so (older clients: only if they sent meta.light at all).
    light_enabled = bool(checks.get("light", "light" in meta_obj))
    lt_by_name = {f.get("file"): f.get("t") for f in ((meta_obj.get("light") or {}).get("frames") or [])}
    lburst = []
    for uf in light_frames:
        t = lt_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            lburst.append((float(t), img))
    t0 = time.perf_counter()
    light = _light_signal(sid, meta_obj, sorted(lburst, key=lambda x: x[0]), light_enabled)
    if light_enabled:
        timings["light"] = time.perf_counter() - t0

    # Fusion. The naive app (Act 2) reads `face` only. The mitigated app also
    # weighs integrity + motion + light + profile: a known virtual camera, a video
    # that does not move with the phone, skin that answers the WRONG colours, or a
    # turn to the wrong side -> block; a suspicious-but-not-damning capture (flat
    # light response, opted out, no burst…) -> step up, never a silent pass.
    decision, reasons = _decide(naive, face, idsig, integ, motion, motion_enabled,
                                light, light_enabled, profile, prof_enabled)

    result = {"decision": decision, "mode": "naive" if naive else "full", "reasons": reasons,
              "checks_enabled": {"motion": motion_enabled, "light": light_enabled, "profile": prof_enabled,
                                 "doc_back": back_img is not None},
              "timings_ms": {k: round(v * 1000) for k, v in timings.items()},
              "signals": {"face": face, "id": idsig, "integrity": integ,
                          "motion": motion, "light": light, "profile": profile}}
    SESSIONS[sid]["result"] = result
    audit_log.record(sid, result, meta_obj)      # scores + first name only, never images
    return result


@app.get("/log")
def log(limit: int = 50):
    """Recent scored sessions (newest first) + counts and score spreads per decision.
    Internal / analyst use: threshold calibration and the dashboard."""
    return {"stats": audit_log.stats(), "rows": audit_log.recent(limit)}


@app.get("/log/{session_id}")
def log_one(session_id: str):
    """The full trace of one scored session: every signal, its curves and the decision reasons."""
    row = audit_log.get(session_id)
    if row is None:
        raise HTTPException(404, "unknown session")
    return row


@app.get("/dashboard")
def dashboard():
    """Analyst dashboard: aggregates the audit log and shows the trace of any session."""
    return FileResponse(os.path.join(WEB, "dashboard.html"))


@app.get("/session/{sid}/result")
def result(sid: str):
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")
    r = SESSIONS[sid]["result"]
    if r is None:
        return JSONResponse({"decision": "pending"}, status_code=202)
    return r


# Serve the phone web app at /
WEB = os.path.join(os.path.dirname(__file__), "..", "web")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/web", StaticFiles(directory=WEB), name="web")
