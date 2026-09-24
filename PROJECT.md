# PROJECT.md — Proofprint · single source of truth

> **READ THIS FIRST, EVERY TIME. UPDATE IT AFTER EVERY CHANGE.**
> This is the shared brain for all teammates and all Claude instances working on
> this repo. If something here is stale, fix it. Keep the **STATUS / LOG** at the
> top current. When in doubt, this file wins over memory.

---

## 0. STATUS / LOG (update this after every change)

**Last updated:** 2026-09-24 (night) · merged Vasiliy's `feat/simplify-killer-feature` (check 6
simplified to v3, prep-screen UX, `passporteye`-venv fix, `demo.sh`) with Tibo's check 5
(light pulse + lab) landing on `main` in parallel. **Check 6 is now v3**, superseding the v2
(ORB card-matching + occlusion) description that was current when check 5 was built — see the
"Recent changes" entries below for both branches' full history. `web/index.html`'s big
instruction-screen restructuring (Tibo's) was kept; the Phase-B copy/guide inside it was
adjusted for the v3 simplification (ID in the left hand, no on-screen card slot).

**⚠ Ownership note:** touches `server/profile_check.py`, `server/main.py` (one call site),
`web/index.html` (Phase B copy + guide, plus a new prep screen before the live capture),
`contract/contract.md`, this file. One feature, small diff — see "Recent changes" below.

**⚠ If you pull this branch, re-run `pip install -r requirements.txt` in your venv.** A venv
created before check 2 landed does NOT have `passporteye`, and `id_check.py` swallows that
as an `ImportError` and silently returns "no MRZ" for every document — looks exactly like a
document-specific bug (we chased it as "maybe Cyrillic ID cards don't parse") but it was
purely a stale venv on one machine. `tesseract` (the OCR binary, not a pip package) still
needs `brew install tesseract` separately either way.

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
- [x] **Check 6 — profile turn** ★ THE KILLER FEATURE, now **v3 (simplified)**, replacing the
  v2 description below. Toggle on the intro screen ("ID + profile turn", default ON). Runs as
  one of the live phases after the still selfie (see check 5's UX note below for the shared
  instruct-then-record flow): "hold your ID in your LEFT hand, turn `<side>`" for ~2.8 s.
  `server/profile_check.py`: yaw from YuNet landmarks only, side compliance vs the server
  nonce (wrong side → hard fail → block), "face lost after trending the right way" = full
  profile, snap-back penalty. **v1/v2 also tried ORB card matching + occlusion-edge
  detection — removed**: card detection scored 0 on the first phone run (contour heuristic
  never found the card in venue light), and the extra machinery added failure surface without
  a validated benefit. The ID-in-hand is now UX only (no on-screen card-slot guide either);
  the head turn is the one signal that was confirmed correct on a real phone (`peak_ok=0.17`
  on a modest turn, correct side). 28 tests green (v2 had 34; the removed ORB/occlusion tests
  were deleted, not skipped).
  <details><summary>v2 description (superseded, kept for history)</summary>
  Branch `feat/profile-challenge`, v2 after the first phone run. ONE continuous capture on the
  selfie screen with a combined face-circle + card-slot guide. `server/profile_check.py`:
  **ORB match of the step-1 ID photo** in every frame (same document, not just "a rectangle"),
  **occlusion**: card must cross the face and its edge must survive inside the face box (a
  swap paints over it → `card_edge_erased_over_face`), face↔card colour temperature,
  snap-back. 34 tests green.
  </details>
- [x] **Check 5 — Light pulse** (`server/light_check.py`, `web/light.js`, toggle "Light check" on the
  intro, default ON; research in `research/light_algorithm.md` + `research/light_capture.md`).
  The screen paints a **server-minted** sequence (700 ms grey lead-in · R,G,B ×2 + grey ×2 in random
  order, 380–560 ms each · 400 ms grey tail ≈ 4.9 s) minted **just in time** by `POST /session/{id}/light`
  when the user taps "I'm ready" (single use, TTL). Phone: rAF painter logs `t_raf`/`t_painted` per
  switch; rVFC grabber takes EVERY camera frame at 320 px / JPEG 0.9; full-screen div + tiny mirrored
  self-view; photosensitivity warning + **Skip** (→ step-up, never a penalty); one retry on client jank.
  Server: skin = forehead + cheeks from YuNet's 5 landmarks, background = top of frame minus 1.9× face
  box; feature = face log-chroma − background log-chroma (cancels AE/AWB/albedo), guard-slot baseline;
  ρ = per-channel Pearson vs the one-hot template over a −40…+420 ms lag search, acc = slots whose
  dominant channel is right, p = permutation test (1000 random sequences); flat response (`low_snr`)
  → `insufficient` → step-up (daylight and an injected video look the same — never a pass); wrong
  colours (replay) → `fail` → **block**; right colours but lag > 300 ms → `review`. Runs before the
  tilt phase. 15 new tests (synthetic captures with simulated AE/AWB + API plumbing). **Thresholds
  are synthetic — calibrate with the light lab** (see below). Audit log now stores `light_score/verdict`.
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
0. **Live now:** `https://clients-ignored-kids-lite.trycloudflare.com` (Vasiliy's Mac, uvicorn
   --reload on **:8010** + cloudflared; dies with that process — restart and update this line).
   Quick tunnels have died twice tonight on their own (`Register tunnel error: Unauthorized:
   Tunnel not found` after an idle gap) — only fix is a fresh `cloudflared` process (new URL).
   Start it fresh right before the live demo; consider a named tunnel if it bites again.
1. **Calibrate on phones** (what "phone-test" means: the code is complete, the THRESHOLDS
   were set on synthetic data — every real run either confirms them or moves them). Do 5
   genuine runs (2 phones, venue light) and paste the two log lines per run here:
   `[motion_check] verdict=… score=… gyro_rms=… acc_rms=… flow_rms=… method=…` — want
   score > 0.6, `method=lk_background`, `face_masked=True`; if `insufficient` the user tilted
   the head not the phone (the UI now nags + shows a tilting-phone icon).
   `[profile_check] … peak_ok=… peak_wrong=…` — want peak_ok ≥ 0.22 on a full turn (0.17 seen
   on a modest one) and peak_wrong small. Knobs: `TURN_MIN/TURN_FULL`, `MIN_GYRO_RMS_DPS`.
2. **Attack runs** (own faces only): still deepfake via OBS while waving the phone; screen
   replay; phone on a stand (→ `stationary_device`). Record APCER/BPCER from 1 + 2.
3. Calibrate face threshold on real ID-vs-selfie pairs (see `COSINE_MATCH`/`COSINE_LOW`).
   First real pair scored 0.424 (match; band 0.363/0.28 holds so far). Check 1 now matches the
   cropped PORTRAIT, so re-measure; use `GET /log` → `stats` (min/avg/max per decision).
3b. Phone-test check 2 on a real back-of-card: does the MRZ read? (`[id_check]` server log line.)
3c. ⚠ `swisscom-research/` is MISSING from disk (deleted?) — §5 pointers are dead. Restore or drop.
4. Analyst dashboard: plot `signals.motion.series` + `signals.profile.yaw_series` + `signals.light.series`.
5. **Calibrate check 5 on phones** with `/lab/light` (laptop) + `<tunnel>/lab/light/phone` (phone):
   ≥ 5 genuine runs per phone × {office light, near a window, dim} + a replay (play a recorded
   genuine run back to the camera) + a static video. Want genuine: ρ ≥ 0.7, 5–6/6 slots, p ≤ 0.01,
   SNR ≥ 10, lag 60–250 ms, `grab=rvfc`, fps ≥ 20; replay: p > 0.05 → fail; static: `low_snr`.
   Knobs in `light_check.py`: `SCORE_PASS/REVIEW`, `P_MAX`, `SNR_MIN/A_MIN`, `LAG_OK`, `POST_MS/PRE_MS`.
   Unknowns to confirm: iOS Safari fills `captureTime`? rAF throttled to 30 fps in Low Power Mode?
   Does the Safari toolbar strip / Night Shift hurt the blue slot? (all logged per run in the lab)

**Check-4 lab (debug check 4 in isolation, live)** — `server/lab.py`, `web/lab.html`, `web/lab_phone.html`:
- Laptop: **http://localhost:8000/lab** — polls every second; shows aligned video-vs-gyro curves
  (z-scored, H and V) + residuals, the score-vs-lag sweep with PASS/REVIEW lines, raw unaligned
  signals, medians, energy weights, timing (fps, jitter, motion Hz), thresholds, and a table of runs.
- Phone: **`<tunnel>/lab/phone`** — tilt burst ONLY (no ID, no face, no profile), fixed 2/3/4 s,
  a label per run ("genuine", "on stand", "head only", "OBS"…), live gyro readout.
- Own endpoints (`POST /lab/motion`, `GET /lab/motion/latest|{id}`, `DELETE /lab/motion`), in-memory,
  last 40 runs. Calls the same `motion_check.check()` as production for the verdict, then re-runs its
  building blocks to expose the internals. Touches nothing in the main flow.

**Check-5 lab (debug the light pulse in isolation, live)** — `server/lab.py`, `web/lab_light.html`,
`web/lab_light_phone.html` (uses the very same `web/light.js` as the app):
- Laptop: **http://localhost:8000/lab/light** — polls every second; shows the sequence shown vs the
  colour the skin answered per slot (with the `d` chroma-shift vectors), the per-channel chroma response
  over time with the on-screen colour shaded behind it, the ρ-vs-lag sweep with the plausible-lag band,
  raw face/background log-chroma with the guard baseline (AE/AWB drift made visible), numbers, timing
  (fps, grab mode, `captureTime` present?, rAF jank, painter error, camera lock, UA) and a runs table.
- Phone: **`<tunnel>/lab/light/phone`** — mint (`POST /lab/light/mint`) → flash + record → upload
  (`POST /lab/light`) → compact result + colour strip. Label per run ("genuine office", "window", "replay"…).
- Own endpoints (`GET /lab/light/latest|{id}`, `DELETE /lab/light`), in-memory, last 40 runs.

**Recent changes**
- **Merge reconciliation (2026-09-24 night):** `feat/simplify-killer-feature` (check 6 v3,
  passporteye fix, `demo.sh`) merged with `main`'s check-5 work, which had landed in parallel
  and diverged from BEFORE the v3 simplification. Concretely: my earlier "get ready" prep
  screen (a standalone screen just for the killer feature) is now **superseded** by Tibo's
  `instructScreen` — a more general full-page instruction shown before EVERY live phase
  (light, motion, profile), which does the same job better. `phaseProfile()`'s body, the
  intro toggle copy, and `instruct()`'s "profile" text were all hand-edited during the merge
  to match the v3 simplification (ID in the left hand, no on-screen card-slot guide, no
  `card_frac` in the result line — that field no longer exists). `motion_check.py`'s comments
  were also corrected to match Tibo's verified-on-a-real-iPhone axis mapping (see the gotcha
  below) — the field-name CONTRACT between client and server (`rx`→vertical-flow-correlate,
  `ry`→horizontal-flow-correlate) was never actually broken, only the code comments describing
  it were stale. 51+28-ish tests green after the merge (server-side test files merged cleanly
  with no manual edits needed).
- **UX: a "get ready" prep screen before the live capture** (killer feature only, superseded
  above by the merge with check 5's `instructScreen`, kept here for history). Previously the
  user only learned "hold your ID in your left hand, turn LEFT/RIGHT" a couple of seconds
  before it started happening, mid-recording. The prep screen explained both moves plainly
  before the camera even opened — including the actual side, since the session (and its
  nonce) is already minted at Begin. Revealing the side early doesn't weaken anything: the
  protection is the SERVER picking it per-session and checking server-side, not hiding it
  from the honest user in front of the phone.
- **Fixed: `passporteye` missing from one venv** → every document showed `doc_type: no_mrz`
  regardless of content. Root cause was a stale venv (created before check 2 added the
  dependency), not anything about the document. `pip install passporteye==2.2.2` into the
  existing venv fixed it; `tesseract` was already present via Homebrew. See the ownership
  note above.
- **Check 6 simplified to v3** (`feat/simplify-killer-feature`, branched fresh off `main` after
  PR #1 + PR #2 + check 2 + audit log all landed there). Removed: `CardTemplate`/ORB matching,
  `_find_card`/`_find_card_contour`, `occlusion_edge_quality`, `_colour_temp`, the `id_bgr`
  param on `profile_check.check()`. Score is now just turn quality minus a snap penalty
  (`score = turn_q`, `−0.35` if a yaw-snap is seen). 28 tests (down from 34 — the removed
  ORB/occlusion tests deleted, not skipped). Rationale: `card_frac=0` on the first phone run
  showed the contour detector didn't work in venue light, and the team decided the fix (ORB)
  added complexity without a demo benefit — the head turn alone was already the validated,
  working signal.
- **Check 5 light pulse + light lab** (see the ticked item above). New: `server/light_check.py`,
  `web/light.js`, `web/lab_light*.html`, `tests/test_light_check.py`; `POST /session/{id}/light`;
  `/session` no longer returns the old unused `challenge` list; intro has a 5th switch; result line
  shows `light <score> (<verdict>, ρ, n/6, p, lag)`; audit log gained `light_score`/`light_verdict`
  (auto-migrated). 51 tests green.
- **Intro feature switches + new selfie flow.** Intro now has 4 switches: *Demo: naïve app (Act 2)*
  (red; dims/skips the liveness ones), *Document back & MRZ*, *Phone-move check (gyro ↔ video)*,
  *ID + profile turn*. They travel as `meta.mode` + `meta.checks` and the server honours them
  (`checks.motion=false` → motion `enabled:false`, ignored; `mode=naive` → face+doc only; the env
  var still forces naive). Selfie flow is now: shutter → still photo + "✓ Photo taken" + blur hint +
  **Retake / Continue** → then, for each enabled phase, a **full-page instruction screen**
  (big TURNING phone logo — CSS 3-D rotateY — numbered steps in plain text, "I'm ready") → the live
  phase on the feed. **Phone-move phase** now lasts **≥ 5 s (max 7)** with a countdown, the turning
  phone centred over the feed, static silhouette. Then the profile instruction screen (arrow +
  card art, side from the session) → profile phase → submit. Naïve mode skips the phases.
  (A sliding-outline "wiggle" variant was tried and dropped.)
- **Check-4 lab added** (see above) — for calibrating `SCORE_PASS/REVIEW`, `MIN_GYRO_RMS_DPS`,
  the lag, and for reproducing attack cases (stand, head-only, OBS) with labelled runs.
- **PR #2 merged** (checks 4 v2 + 6). Reconciled on top of it: check 2, audit log, and the
  front-end polish (silhouette guide, dashed card frame, tap-to-focus, blur check, ID back-of-card
  step, document card, `object-fit:contain` preview). **Fix:** restored `startCamera`/`stopStream`
  (deleted by the v2 rewrite; Begin threw a ReferenceError). Fusion: no usable face → `block`
  (was falling through); `id.flags` → step_up.
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
| 5 · Light pulse (screen colours ↔ skin) | mitigated (toggle) | Mostly — stops replays and non-relighting swaps; a colour-transfer swap with < 150 ms delay slips through (fusion, not a primitive) | ✅ code + synthetic tests · ⏳ phone calibration (`/lab/light`) |
| 6 · **Profile turn** ★ | mitigated (toggle) | **Yes** — face-swaps warp at ~90° | ✅ code + tests, side sign confirmed on a phone · ⏳ full calibration |

**Deliberately NOT used:** iris (needs IR hardware), typing rhythm (no profile at
first onboarding), mouse (no mouse on phone — its useful bit folds into check 3),
heartbeat/rPPG (deepfakes inherit the pulse; weak).

---

## 3. Architecture

```
PHONE (web app, Vite/plain — HTTPS via cloudflared)        SERVER (FastAPI, Python)
  captures ID photo + selfie (+ light + motion + profile)→  /session          mint profile side
  everything on one performance.now() clock                 /session/{id}/light    mint colour seq (JIT)
  shows: pass / step-up / blocked                            /session/{id}/capture  run checks
                                                        ←   /session/{id}/result   decision
                                                            checks: face | id | integrity | light | motion | profile
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
  light_check.py      ← check 5 (screen colours ↔ skin: mint, skin/background ROIs, lag search, permutation test)
  profile_check.py    ← check 6, v3 simplified (profile turn: yaw from YuNet landmarks, side-nonce, snap-back)
  lab.py              ← /lab (check 4) + /lab/light (check 5) debugging endpoints
  audit_log.py        ← SQLite log of scores per session (first name only) + GET /log
  data/               ← proofprint.db lives here (gitignored)
  tests/              ← pytest: synthetic pan renderer + e2e via TestClient
  requirements.txt    ← pinned (OpenCV + numpy suffice for check 4; pytest + httpx for tests)
  models/             ← YuNet/SFace .onnx (gitignored, auto-download)
  .venv/              ← gitignored
web/                  ← Person B
  index.html          ← phone capture page
  light.js            ← check 5 capture (painter + rVFC grabber), shared with the lab
  lab.html, lab_phone.html, lab_light.html, lab_light_phone.html  ← the two labs
research/             ← check-5 research notes (algorithm + phone capture), 24 Sep
swisscom-research/    ← the research (READ THESE for methods/papers):
  CHECKS_INDEX.md     ← per-check: papers, models, thresholds, day-1 tests
  defence_science.md  ← papers, standards, patents, evaluation plan
  web_feasibility.md  ← browser APIs, algorithms, measured speeds, gotchas
  tool_landscape.md   ← attack tooling, datasets, detectors, ethics
```

---

## 6. How to run (dev)

**One command, does everything:**
```bash
./demo.sh          # normal (mitigated) app
./demo.sh naive    # Act 2 of the demo: decides on face-match only
```
Sets up the venv on first run, starts the auto-reloading server on `:8010` (not `:8000` —
see the Live Share gotcha below), opens a `cloudflared` quick tunnel, and prints the
`https://…trycloudflare.com` link to open on the phone. **Ctrl+C stops both.** If a process
ever gets stuck (rare — quick tunnels are known to loop-retry after dying, see the gotcha
below), `pkill -f 'uvicorn main:app'` and `pkill -f 'cloudflared tunnel'` clear it manually.

**Manual / step-by-step**, if you want the two pieces separately:
```bash
# one-time
cd server && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# every time (auto-reloads on edits — no manual restart)
./dev.sh                      # from repo root; serves :8000
# separate terminal, keep it open (URL stays stable while it lives):
cloudflared tunnel --url http://localhost:8000
```
`brew install cloudflared` if missing. Camera needs HTTPS — that's why the tunnel exists.

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
- **Check 6 (built, v3, simplified):** score = `turn_q` (peak yaw / `TURN_FULL`, clipped to 1)
  minus `0.35` if a yaw-snap is seen. Yaw proxy = (nose_x − eye_mid_x) / face_w from YuNet's
  5 landmarks (frontal ≈ 0, ~45° ≈ 0.15; YuNet usually loses the face near 90°, which counts
  as "reached profile" if the last frames trended the right way). `SIDE_SIGN={"left":+1,
  "right":-1}` for RAW (unmirrored) front-camera frames — confirmed correct on a real phone
  2026-09-24. `TURN_MIN=0.12`, `TURN_FULL=0.22`, `SCORE_PASS=0.60`, `SCORE_REVIEW=0.35`. Wrong
  side is a hard fail regardless of score, and is judged against the SERVER's own session
  nonce, never the client's claim. **Card detection removed** (v1/v2 tried ORB matching +
  occlusion-edge scoring — see the superseded description under §0's "Recent changes") — the
  ID is held in the left hand as a UX/demo cue only; nothing about the card is verified.
  Why this is still the killer signal: DeepFaceLive-class tools map only 50–60 % of landmarks
  in profile and warp at ~90°; the side is a nonce so a pre-recorded clip can't follow it;
  human video-ident agents already ask for a profile turn, so it reads as legitimate, not
  exotic, to Swisscom's validation specialists.
- **Check 5 (`light_check.py`):** grey guards (not black: same chroma contrast, face stays lit for
  YuNet, fewer AE swings), pure primaries (max per-channel swing; isoluminant hues rejected).
  Feature = face log-chroma − background log-chroma: cancels AE/AWB (global per-channel gains),
  albedo and face-only brightness; fallback face-only when the background is clipped (window) with
  flag `no_bg_reference`. Pixels with max channel ≥ 250 or ≤ 20 dropped, 10–90 % luminance trim.
  Frames within 120 ms after / 40 ms before a switch masked (screen + exposure + rolling shutter).
  `score = 0.5·clip((acc−⅓)/⅔) + 0.5·clip(ρ/0.8)`, capped 0.45 if p > 0.05. `SCORE_PASS=0.75`,
  `P_MAX=0.01`, `SCORE_REVIEW=0.50`, `SNR_MIN=3`, `A_MIN=0.012`, `LAG_OK=0–300 ms`. Synthetic:
  genuine ρ .99, replay ρ ≈ .2 → fail, static → insufficient, +400 ms relight → review.
  Honest limits (say so in the pitch): a colour-transfer face-swap with < ~150 ms extra delay, or
  a client that reads the colour from the DOM and tints the injected face, gets through — check 5
  raises cost and is fused with 4 + 6. iProov Flashmark patents cover the primitive.
- **Check 5 timing:** switch time = `t_painted` (the rAF after the one that set the colour); frame
  time = grab time `t` (not `captureTime` — may be absent / unverified clock on iOS); the fitted lag
  absorbs the constant offset, `lag_ms` per device is logged for tightening `LAG_OK` later.

---

## 8. Gotchas (things that already bit us / will)

- **`rotationRate` axis names are NOT what you'd guess:** current spec = `alpha` about X (pitch),
  `beta` about Y (yaw = the door-turn), `gamma` about Z (roll). The first code assumed the old
  beta=pitch/gamma=yaw naming, so a genuine door-turn scored r_yaw≈0.01 (run #14, 24 Sep 19:07).
  Fixed on both phone pages (`rx=alpha, ry=beta, rz=gamma`). The lab shows an "axis check" per run.
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
| M4 | Light pulse (check 5) fused | Random colour challenge reflected on skin; low SNR → step-up; replay → block. | ✅ code + tests + lab 24 Sep · ⏳ phone calibration |

**How to close a milestone:** tick it here with the date, add a line under *Recent changes*,
and make sure the demo script still runs end to end on the phone.

---

## 10. Design mockups (private artifacts — owner must Share to open)

- App screens: Proofprint onboarding flow (6 phone screens).
- Explainer, architecture, signal-choice, implementation pages exist as artifacts.
- Ask Tibo for the Share links.
