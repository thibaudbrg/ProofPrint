# PROJECT.md — Proofprint · single source of truth

> **READ THIS FIRST, EVERY TIME. UPDATE IT AFTER EVERY CHANGE.**
> This is the shared brain for all teammates and all Claude instances working on
> this repo. If something here is stale, fix it. Keep the **STATUS / LOG** at the
> top current. When in doubt, this file wins over memory.

---

## 0. STATUS / LOG (update this after every change)

**Last updated:** 2026-09-24 (evening) · by: Tibo's Claude session — PR #2 merged into `main`
(`b5d1678`), then check 2 + audit log re-applied on top. **Note:** PR #2's rewrite had deleted
`startCamera()`/`stopStream()` from `web/index.html` while still calling them (Begin threw) — restored.

**⚠ Ownership note:** `feat/motion-check` (merged) and `feat/profile-challenge` touch BOTH `server/` (check 4 +
fusion + naive mode) AND `web/` (3 s liveness burst on the selfie shutter) AND
`contract/` (frames + motion keys, `motion` signal shape). One feature, one PR —
review it together before merging.

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
- [x] **Check 4 — Gyroscope ↔ video** ★ THE CORE. `server/motion_check.py` v2:
  **background-only** sparse flow (Shi-Tomasi + LK, face box masked via YuNet) vs devicemotion
  rotationRate, lag search ±300 ms, energy-weighted |Pearson|; accelerometer recorded too;
  **stationary-device test** (gyro AND acc flat → block, per arXiv 2605.00218). Head movement
  with a still phone is now `insufficient` (step_up), NOT fail — that was the first phone
  run's false block. No MediaPipe needed (OpenCV only).
- [x] **Naive mode** for Act 2: `PROOFPRINT_MODE=naive ./dev.sh` → decides on `face` only.
- [x] **Check 6 — ID next to face + profile turn** ★ THE KILLER FEATURE (branch
  `feat/profile-challenge`, v2 after the first phone run). Toggle on the intro screen
  ("Killer feature", default ON). ONE continuous capture on the selfie screen: tilt phase
  (check 4, ends early once ~35° of rotation is banked, 1.5–4 s) → "hold your ID over one eye
  and turn <side>" phase (2.8 s) with a combined face-circle + card-slot guide → auto-submit.
  No confirm screens. `server/profile_check.py`: yaw from YuNet landmarks, side compliance
  vs the server nonce (wrong side → block), **ORB match of the step-1 ID photo** in every
  frame (same document, not just "a rectangle"), **occlusion**: card must cross the face and
  its edge must survive inside the face box (a swap paints over it → `card_edge_erased_over_face`),
  face↔card colour temperature, snap-back. 34 tests green. First phone run: side sign
  confirmed, `peak_ok=0.17` on a modest turn; card contour detector scored 0 → replaced by ORB.
- [ ] **Check 5 — Light pulse** (flash random colours, skin must reflect them).
- [x] **Check 2 — ID document** (`id_check.py`): de-skew → portrait crop (now what check 1 matches
  against) → MRZ via PassportEye + Tesseract with ICAO 9303 check digits **recomputed**, expiry
  check → optional back-of-card capture (Skip for licences) → document card on the result screen.
  Flags (`document_expired`, `mrz_checksum_failed`) only ever raise to step-up. No MRZ = fine.
- [x] **Audit log** (`audit_log.py`, SQLite `server/data/proofprint.db`, gitignored): one row per
  scored capture — date, decision, face/motion scores, doc type, flags, phone/desktop, camera
  label, git SHA. **First given name only**, no other personal data, no images. `GET /log`.
- [ ] **Attack rig** (OBS + deepfake) → milestone M1 ("naïve app fooled").
- [ ] **Analyst dashboard** (two motion curves overlaid — the money shot).
- [ ] **APCER/BPCER** evaluation run.

**Open TODO / next actions**
0. **Live now:** `https://jvc-fisheries-six-utc.trycloudflare.com` (Vasiliy's Mac, uvicorn
   --reload on **:8010** + cloudflared; dies with that process — restart and update this line).
1. **Calibrate on phones** (what "phone-test" means: the code is complete, the THRESHOLDS
   were set on synthetic data — every real run either confirms them or moves them). Do 5
   genuine runs (2 phones, venue light) and paste the two log lines per run here:
   `[motion_check] verdict=… score=… gyro_rms=… acc_rms=… flow_rms=… method=…` — want
   score > 0.6, `method=lk_background`, `face_masked=True`; if `insufficient` the user tilted
   the head not the phone (the UI now nags + shows a tilting-phone icon).
   `[profile_check] … peak_ok=… card_frac=… card_src=… occl_seen=… occl_q=…` — want
   peak_ok ≥ 0.22 on a full turn (0.17 seen on a modest one), card_src mostly `orb`,
   occl_seen True when the card really covers an eye. Knobs: `TURN_MIN/TURN_FULL`,
   `ORB_MIN_MATCHES`, `OCCLUSION_MIN_OVERLAP`, `MIN_GYRO_RMS_DPS`.
2. **Attack runs** (own faces only): still deepfake via OBS while waving the phone; screen
   replay; phone on a stand (→ `stationary_device`). Record APCER/BPCER from 1 + 2.
3. Calibrate face threshold on real ID-vs-selfie pairs (see `COSINE_MATCH`/`COSINE_LOW`).
   First real pair scored 0.424 (match; band 0.363/0.28 holds so far). Check 1 now matches the
   cropped PORTRAIT, so re-measure; use `GET /log` → `stats` (min/avg/max per decision).
3b. Phone-test check 2 on a real back-of-card: does the MRZ read? (`[id_check]` server log line.)
3c. ⚠ `swisscom-research/` is MISSING from disk (deleted?) — §5 pointers are dead. Restore or drop.
4. Analyst dashboard: plot `signals.motion.series` + `signals.profile.yaw_series`.
5. Check 5 (light pulse) still open; the `challenge` colours are minted but unused.

**Recent changes**
- **PR #2 merged** (checks 4 v2 + 6). Reconciled on top of it: check 2, audit log, and the
  front-end polish (silhouette guide, dashed card frame, tap-to-focus, blur check, ID back-of-card
  step, document card, `object-fit:contain` preview). **Fix:** restored `startCamera`/`stopStream`
  (deleted by the v2 rewrite; Begin threw a ReferenceError). Fusion: no usable face → `block`
  (was falling through); `id.flags` → step_up. 34 tests still green.
- **v2 of checks 4 + 6 after the first phone run** (`feat/profile-challenge`): one
  continuous capture, no confirm screens, ~5–7 s total after the ID; combined face + card
  guide; tilt phase ends early on banked rotation; accelerometer + `events` in meta;
  background-only flow with the face masked (head motion can no longer block a real user);
  stationary-device rejection; ORB card matching against the step-1 ID; occlusion-edge
  check (KnowBe4 ghosting signature); all early-return paths now log their numbers.
- **Check 6 implemented** (`feat/profile-challenge`): "Killer feature" toggle on intro; the
  session is now minted at **Begin** (was: at submit) so the phone knows `profile_side`;
  Step 3 screen with mirrored arrow; `profile_frames` upload; `signals.profile` in the result;
  fusion: profile `fail` → block, `review|insufficient|absent` (when enabled) → step_up.
  Step counters now say "of 3" when the toggle is on.
- **Check 4 implemented** (`feat/motion-check`): selfie shutter now records a 3 s burst
  ("slowly tilt the phone") of ~36 low-res frames + gyro samples, uploads them as repeated
  `frames` parts + `meta.frames/motion`; server correlates global image shift with
  rotationRate and returns `signals.motion` (`pass|review|fail|insufficient|absent`, score,
  lag, aligned series). Fusion: motion `fail` → block; `review/insufficient/absent` → step_up.
- `PROOFPRINT_MODE=naive` env switch → face-only decision, `mode` echoed in the result.
- Tests: `server/tests/` (synthetic camera pan renderer + TestClient e2e).
  Run: `cd server && ./.venv/bin/python -m pytest tests -q`.
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
| 2 · ID document (de-skew, portrait, MRZ + check digits, expiry) | both | No | ✅ |
| 3 · Capture integrity (virtual-cam, sensors) | mitigated | Partly (off-the-shelf tools) | ✅ |
| 4 · **Gyroscope ↔ video** ★ | mitigated | **Yes** (the core) | ✅ code + synthetic tests · ⏳ phone calibration |
| 5 · Light pulse (colour reflection) | mitigated | Mostly | ⏳ |
| 6 · **ID next to face + profile turn** ★ | mitigated (toggle) | **Yes** — face-swaps warp at 90°, ghost on occlusion | ✅ code + tests · ⏳ phone calibration |

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
2. `POST /session/{id}/capture` (multipart) → parts `id_photo` (front), `selfie`, optional
   `id_back` (TD1 cards carry the MRZ on the back), repeated `frames` + `profile_frames`, and a
   `meta` JSON string: `{ label, settings{facingMode,...}, hasMotion, claimsMobile,
   frameIntervals[], frames[], motion[], events[], profile{} }`.
3. `GET /session/{id}/result` → `{ decision, mode, signals:{ face, id, integrity, motion, profile } }`
   `decision ∈ pass | step_up | block | pending`. Also `GET /log` (audit rows + score stats).

**Fusion rule (current):** virtual-cam label OR motion `fail` OR profile `fail` (wrong side) → block ·
face mismatch OR **no usable face** → block · (face review OR any integrity flag OR any `id` flag
OR motion `review|insufficient|absent` OR [toggle on AND profile `review|insufficient|absent`])
→ step_up · else pass. Check 1 matches against the document PORTRAIT cut out by check 2.
In `PROOFPRINT_MODE=naive`: face match → pass, review → step_up, else block (liveness ignored).

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
  id_check.py         ← check 2 (deskew, portrait, MRZ + ICAO check digits, expiry)
  integrity_check.py  ← check 3
  motion_check.py     ← check 4 (gyro ↔ video, background flow + lag search)
  profile_check.py    ← check 6 (ID + profile turn: yaw from YuNet landmarks, card, lighting)
  audit_log.py        ← SQLite log of scores per session (first name only) + GET /log
  data/               ← proofprint.db lives here (gitignored)
  tests/              ← pytest: synthetic pan renderer + e2e via TestClient
  requirements.txt    ← pinned (OpenCV + numpy suffice for check 4; pytest + httpx for tests)
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
- **Check 4 (built, v2):** BACKGROUND shift per frame pair: Shi-Tomasi corners outside the
  (grown) YuNet face box + Lucas-Kanade, median displacement → px/s; whole-frame
  `cv2.phaseCorrelate` (face masked) as fallback on featureless backgrounds; gyro (`beta`→pitch↔vertical flow, `gamma`→yaw↔horizontal
  flow) interpolated to frame midpoints; |Pearson| per axis, energy-weighted, best over
  lags ±300 ms. Thresholds `SCORE_PASS=0.60`, `SCORE_REVIEW=0.35`, `MIN_GYRO_RMS_DPS=8`
  (from synthetic tests — calibrate on phones). Still video + moving phone → fail; moving
  video + still phone → fail; nothing moved → insufficient (step_up, "please tilt").
  Head-pose (MediaPipe) was NOT needed; revisit only if background-less scenes break flow.
- **Check 6 (built, v2):** score = 0.45·turn + 0.20·card(ORB vs step-1 ID) + 0.20·occlusion-edge
  + 0.15·lighting − 0.2·snap. Occlusion-edge = fraction of the card boundary INSIDE the face
  box that is still a Canny edge (swap paints over the occluder → edge gone).
  Yaw proxy = (nose_x − eye_mid_x) / face_w from YuNet's 5 landmarks
  (frontal ≈ 0, ~45° ≈ 0.15; YuNet usually loses the face near 90°, which counts as "reached
  profile" if the last frames trended the right way). `SIDE_SIGN={"left":+1,"right":-1}` for
  RAW (unmirrored) front-camera frames — flip if a phone test disagrees. `TURN_MIN=0.12`,
  `TURN_FULL=0.22`, score = 0.6·turn + 0.25·card_seen + 0.15·lighting − 0.2·snap;
  `SCORE_PASS=0.55`, `SCORE_REVIEW=0.30`. Wrong side is a hard fail regardless of score.
  Why this is the killer: DeepFaceLive-class tools map only 50–60 % of landmarks in profile
  and warp at 90°; a card crossing the face ghosts at the mask edge; the side is a nonce so a
  pre-recorded clip can't follow it; human video-ident agents already ask for exactly this.
- **Check 5 (planned):** server mints random colour seq; skin ROI ÷ background;
  daylight → low SNR → step-up (not fail). Overlaps iProov patent → pitch as fusion.

---

## 8. Gotchas (things that already bit us / will)

- `DeviceMotionEvent.rotationRate` is **deg/s on both iOS and Android** (W3C). The rad/s
  trap is the Generic Sensor API `Gyroscope` — we don't use it. Check 4 uses |correlation|,
  so a sign flip between platforms can't break it; a unit mix-up only affects `gyro_rms_dps`.
- iOS needs `DeviceMotionEvent.requestPermission()` inside a user tap; no brightness control.
- cloudflared quick-tunnel URL changes if the process restarts.
- **VS Code Live Share forwards a teammate's :8000 onto your own 127.0.0.1:8000.** If your
  tunnel or curl shows the OLD app, that's why — run your uvicorn on another port (8010).
- `pip install mediapipe` pulls a 2nd OpenCV — keep only one.
- Attack only ever on OUR app, with CONSENTING teammate faces; delete synthetic media after.

---

## 9. Milestones — the log (update the Status column as things land)

| # | Milestone | Definition of done | Status |
|---|---|---|---|
| **M0** | Walking skeleton | Phone → HTTPS → server face-match → decision shown, on a real phone. | ✅ 24 Sep |
| **M0.5** | Front end + document read + integrity | Card crop, silhouette guide, tap-focus, blur check, phone layout. Check 2 (portrait, de-skew, MRZ + check digits, back-of-card). Check 3. Audit log. | ✅ 24 Sep |
| **M1** | Attack beats the naïve app | A deepfake through a virtual camera gets `pass` from `PROOFPRINT_MODE=naive`. This is Act 2. | ⏳ **next** — naive mode ✅, attack rig ⏳ |
| **M2** | Proofprint blocks the same attack | Checks 4 + 6 live and fused (code ✅ merged as PR #1/#2); identical attack → `step_up`/`block`; real user still passes. This is Act 3. | ⏳ phone calibration |
| **M3** | Numbers on the board | APCER / BPCER table from recorded genuine + attack sessions (`GET /log` feeds it), plus the analyst replay (motion curves overlaid). | ⏳ |
| M4 | Light pulse (check 5) fused | Random colour challenge reflected on skin; low SNR → step-up. | ⏳ optional if time |

**How to close a milestone:** tick it here with the date, add a line under *Recent changes*,
and make sure the demo script still runs end to end on the phone.

---

## 10. Design mockups (private artifacts — owner must Share to open)

- App screens: Proofprint onboarding flow (6 phone screens).
- Explainer, architecture, signal-choice, implementation pages exist as artifacts.
- Ask Tibo for the Share links.
