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

app = FastAPI(title="Proofprint")

# In-memory session store. Fine for a hackathon; swap for SQLite later.
SESSIONS: dict[str, dict] = {}

COLORS = ["black", "red", "green", "blue"]


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
                  meta: str = Form(None)):
    """Receive the ID photo + selfie (+ capture metadata), score, decide."""
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

    # Fusion. Broken app would read `face` only. Mitigated app also weighs
    # integrity: identity OK but a suspicious capture -> step up, never a
    # silent pass; a known virtual camera -> block.
    # (checks 4 motion + 5 light plug in here next.)
    if integ["hard"]:
        decision = "block"
    elif face.get("verdict") == "mismatch":
        decision = "block"
    elif face.get("verdict") == "review" or not integ["ok"]:
        decision = "step_up"
    else:
        decision = "pass"

    result = {"decision": decision, "signals": {"face": face, "integrity": integ}}
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
