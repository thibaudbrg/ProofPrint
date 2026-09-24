# PROJECT.md — Proofprint · single source of truth

> **READ THIS FIRST, EVERY TIME. UPDATE IT AFTER EVERY CHANGE.**
> This is the shared brain for all teammates and all Claude instances working on
> this repo. If something here is stale, fix it. Keep the **STATUS / LOG** at the
> top current. When in doubt, this file wins over memory.

---

## 0. STATUS / LOG (update this after every change)

**Last updated:** 2026-09-24 · by: Tibo's Claude session

**What runs right now**
- Server: FastAPI, auto-reload, on `:8000`. Start with `./dev.sh` (uvicorn --reload).
- Tunnel: `cloudflared tunnel --url http://localhost:8000` (quick tunnel, no account).
  - Current session URL: **https://vary-sunshine-hit-pal.trycloudflare.com**
  - ⚠ This URL only lives while that one cloudflared process runs. If the phone
    shows 502/530, the tunnel died — restart it and paste the NEW url here.
- Models cached in `server/models/` (YuNet + SFace, auto-downloaded first run).

**Build progress (the 5 checks)**
- [x] **Check 1 — Face match** (walking skeleton, Act 1). Robust to EXIF rotation + small document faces.
- [x] **Check 3 — Capture integrity** (virtual-cam label, facingMode, no-motion; timing = info only).
- [ ] **Check 4 — Gyroscope ↔ video** ★ THE CORE. Next up. Needs MediaPipe (pin 0.10.35).
- [ ] **Check 5 — Light pulse** (flash random colours, skin must reflect them).
- [ ] **Check 2 — ID card MRZ** (optional; face extraction already works via check 1).
- [ ] **Attack rig** (OBS + deepfake) → milestone M1 ("naïve app fooled").
- [ ] **Analyst dashboard** (two motion curves overlaid — the money shot).
- [ ] **APCER/BPCER** evaluation run.

**Open TODO / next actions**
1. Calibrate face threshold on real ID-vs-selfie pairs (see `COSINE_MATCH`/`COSINE_LOW`).
2. Build check 4 (gyroscope ↔ video).
3. Set up the attack rig to lock M1.

**Recent changes**
- Face match: try all 4 rotations (EXIF fix), score_threshold 0.6, downscale >1024px.
- Integrity: demoted `timing_too_regular` → informational (`info[]`), was false-flagging real phones.
- Web: mirrored the selfie preview (`transform:scaleX(-1)`); ID copy now "ID/passport/driving licence".

---

## 1. What we're building

**Event:** Swiss {ai} Weeks Zurich hackathon, 24–25 Sep 2026.
**Challenge:** Swisscom — *Fighting Identity Fraud in the Age of AI*.
**Chosen attack vector:** deepfake video **injection** (a fake video pushed past the
camera through a virtual camera) into a selfie-video onboarding for Qualified
Electronic Signatures.

**Product name:** Proofprint.
**One-line pitch:** *"A deepfake can fake your face. It can't fake your phone."*

**The core idea:** a deepfake copies a face but cannot reproduce the *physics of a
real phone capturing a real person live* — the phone's motion, and the screen's
light reflecting on skin. So we check the physics, not just the face.

**The demo (3 acts):**
1. Real person onboards → ✓ passes.
2. Deepfake through a virtual camera fools a **naïve** app (face check only) → ✓ (wrong!).
3. Same attack hits **Proofprint** → ✗ blocked; real user still passes in ~3 s.

**Judged on:** (1) explain the attack + impact, (2) a technical AND UX-friendly
mitigation, (3) a validation concept (APCER/BPCER), (4) how internal data would improve it.

---

## 2. Broken app vs mitigated app (what goes where)

The **broken app** uses only the checks a deepfake PASSES (so it looks real and gets
fooled). The **mitigated app** keeps those, then ADDS checks that prove the capture
was physically live.

| Check | App | Stops a deepfake? | Status |
|---|---|---|---|
| 1 · Face match (selfie vs ID) | both | No — it's the identity anchor | ✅ |
| 2 · ID card (face + optional MRZ) | both | No | face part ✅ / MRZ ⏳ |
| 3 · Capture integrity (virtual-cam, sensors) | mitigated | Partly (off-the-shelf tools) | ✅ |
| 4 · **Gyroscope ↔ video** ★ | mitigated | **Yes** (the core) | ⏳ |
| 5 · Light pulse (colour reflection) | mitigated | Mostly | ⏳ |

**Deliberately NOT used:** iris (needs IR hardware), typing rhythm (no profile at
first onboarding), mouse (no mouse on phone — its useful bit folds into check 3),
heartbeat/rPPG (deepfakes inherit the pulse; weak).

---

## 3. Architecture

```
PHONE (web app, Vite/plain — HTTPS via cloudflared)        SERVER (FastAPI, Python)
  captures ID photo + selfie (+ motion + light + meta)  →   /session          mint challenge
  everything on one performance.now() clock                 /session/{id}/capture  run checks
  shows: pass / step-up / blocked                       ←   /session/{id}/result   decision
                                                            checks: face | integrity | (motion) | (light)
                                                            → fusion → decision → (SQLite later)
                                                            analyst dashboard reads storage (later)
```

**All judgement is server-side** (the phone is attacker-controllable). The phone only
captures and displays.

**Ownership (so two Claudes don't collide):**
- **Person A** → `server/` only.
- **Person B** → `web/` only.
- `contract/` changes ONLY by agreement (it's the interface).

---

## 4. Data contract (the 3 messages)

Full detail in `contract/contract.md`. Summary:

1. `POST /session` → `{ session_id, challenge:[colours], segment_ms }`
2. `POST /session/{id}/capture` (multipart) → parts `id_photo`, `selfie`, and a
   `meta` JSON string: `{ label, settings{facingMode,...}, hasMotion, claimsMobile,
   frameIntervals[] }`. (Checks 4/5 will add `frames[]`, `motion[]`, `colour[]`.)
3. `GET /session/{id}/result` → `{ decision, signals:{ face, integrity, ... } }`
   `decision ∈ pass | step_up | block | pending`.

**Fusion rule (current):** virtual-cam label → block · face mismatch → block ·
(face review OR any integrity flag) → step_up · else pass.

---

## 5. Repo layout

```
PROJECT.md            ← this file (source of truth)
CLAUDE.md             ← working rules (read PROJECT.md, update it)
HOWTO.md              ← run instructions
dev.sh                ← start the auto-reload server
run.sh                ← one-shot venv+run
contract/contract.md  ← the 3 JSON shapes (shared — change together)
server/               ← Person A
  main.py             ← the 3 routes + fusion
  face_match.py       ← check 1 (YuNet + SFace, rotation-robust)
  integrity_check.py  ← check 3
  requirements.txt    ← pinned; add mediapipe 0.10.35 + scipy for check 4
  models/             ← YuNet/SFace .onnx (gitignored, auto-download)
  .venv/              ← gitignored
web/                  ← Person B
  index.html          ← phone capture page
swisscom-research/    ← the research (READ THESE for methods/papers):
  CHECKS_INDEX.md     ← per-check: papers, models, thresholds, day-1 tests
  defence_science.md  ← papers, standards, patents, evaluation plan
  web_feasibility.md  ← browser APIs, algorithms, measured speeds, gotchas
  tool_landscape.md   ← attack tooling, datasets, detectors, ethics
```

---

## 6. How to run (dev)

```bash
# one-time
cd server && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# every time (auto-reloads on edits — no manual restart)
./dev.sh                      # from repo root; serves :8000
# separate terminal, keep it open (URL stays stable while it lives):
cloudflared tunnel --url http://localhost:8000
```
Open the printed `https://…trycloudflare.com` on the phone. `brew install cloudflared`
if missing. Camera needs HTTPS — that's why the tunnel exists.

---

## 7. Key decisions & thresholds

- **Face:** OpenCV **YuNet** (detect, MIT) + **SFace** (recognise, Apache) — commercial-safe.
  Avoid InsightFace weights (non-commercial). Cosine band in `face_match.py`:
  `COSINE_MATCH=0.363` (pass), `COSINE_LOW=0.28` (mismatch), between = review.
  **CALIBRATE on real ID-vs-selfie pairs** (ID photos score lower — see DocFace+).
- **EXIF rotation:** phone JPEGs come in sideways via `cv2.imdecode`; we try 4 rotations.
- **Integrity timing:** weak/noisy → informational only, never blocks.
- **Check 4 (planned):** MediaPipe Face Landmarker head pose + OpenCV optical flow vs
  gyro; cross-correlate for lag; pin **mediapipe==0.10.35** (1.0.1 crashes on macOS arm64).
- **Check 5 (planned):** server mints random colour seq; skin ROI ÷ background;
  daylight → low SNR → step-up (not fail). Overlaps iProov patent → pitch as fusion.

---

## 8. Gotchas (things that already bit us / will)

- iOS `rotationRate` is **degrees/s**; Android Gyroscope API is rad/s (57× bug).
- iOS needs `DeviceMotionEvent.requestPermission()` inside a user tap; no brightness control.
- cloudflared quick-tunnel URL changes if the process restarts.
- `pip install mediapipe` pulls a 2nd OpenCV — keep only one.
- Attack only ever on OUR app, with CONSENTING teammate faces; delete synthetic media after.

---

## 9. Milestones

- **M1** — attack beats the naïve app (needs check 1 + attack rig). *← current target*
- **M2** — Proofprint blocks the same attack, real user still passes (needs check 4).
- **M3** — numbers on the board (APCER/BPCER) + analyst replay dashboard.

---

## 10. Design mockups (private artifacts — owner must Share to open)

- App screens: Proofprint onboarding flow (6 phone screens).
- Explainer, architecture, signal-choice, implementation pages exist as artifacts.
- Ask Tibo for the Share links.
