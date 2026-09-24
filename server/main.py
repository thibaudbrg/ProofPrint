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
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import os

import face_match
import integrity_check
import motion_check

app = FastAPI(title="Proofprint")

# In-memory session store. Fine for a hackathon; swap for SQLite later.
SESSIONS: dict[str, dict] = {}

COLORS = ["black", "red", "green", "blue"]

# Demo switch. PROOFPRINT_MODE=naive = the "broken app" of Act 2: decide on the
# face match only, ignore every liveness signal (they are still computed and
# returned so the analyst view can show what the naive app threw away).
NAIVE = os.environ.get("PROOFPRINT_MODE", "full").lower() == "naive"


@app.post("/session")
def start_session():
    """Step: start a session and mint a random light challenge.

    The challenge is unused by the skeleton (check 5 will consume it), but it is
    minted here now so the contract is stable and the phone can flash it today.
    """
    sid = secrets.token_hex(4)
    challenge = [secrets.choice(COLORS) for _ in range(5)]
    SESSIONS[sid] = {"challenge": challenge, "result": None}
    return {"session_id": sid, "challenge": challenge, "segment_ms": 500}


@app.post("/session/{sid}/capture")
async def capture(sid: str,
                  id_photo: UploadFile = File(...),
                  selfie: UploadFile = File(...),
                  meta: str = Form(None),
                  frames: list[UploadFile] = File(default=[])):
    """Receive the ID photo + selfie (+ liveness burst frames + metadata), score, decide."""
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")

    id_img = face_match.imdecode(await id_photo.read())
    self_img = face_match.imdecode(await selfie.read())
    if id_img is None or self_img is None:
        raise HTTPException(400, "could not decode an image")

    # Check 1 — identity
    face = face_match.match(id_img, self_img)
    # Check 3 — capture integrity
    try:
        meta_obj = json.loads(meta) if meta else {}
    except json.JSONDecodeError:
        meta_obj = {}
    integ = integrity_check.check(meta_obj)

    # Check 4 — gyroscope <-> video. Frame timestamps come from meta.frames
    # (matched by filename); the JPEGs come as repeated `frames` parts.
    t_by_name = {f.get("file"): f.get("t") for f in (meta_obj.get("frames") or [])}
    burst = []
    for uf in frames:
        t = t_by_name.get(uf.filename)
        img = face_match.imdecode(await uf.read())
        if t is not None and img is not None:
            burst.append((float(t), img))
    motion = motion_check.check(meta_obj.get("motion"), burst)

    # Fusion. The naive app (Act 2) reads `face` only. The mitigated app also
    # weighs integrity + motion: a known virtual camera or a video that does not
    # move with the phone -> block; a suspicious-but-not-damning capture -> step
    # up, never a silent pass. (check 5 light plugs in here next.)
    if NAIVE:
        decision = {"match": "pass", "review": "step_up"}.get(face.get("verdict"), "block")
    elif integ["hard"] or motion["verdict"] == "fail":
        decision = "block"
    elif face.get("verdict") == "mismatch":
        decision = "block"
    elif (face.get("verdict") == "review" or not integ["ok"]
          or motion["verdict"] in ("review", "insufficient", "absent")):
        decision = "step_up"
    else:
        decision = "pass"

    result = {"decision": decision, "mode": "naive" if NAIVE else "full",
              "signals": {"face": face, "integrity": integ, "motion": motion}}
    SESSIONS[sid]["result"] = result
    return result


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
