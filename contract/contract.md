# Data contract — agree this at hour zero, then build in parallel

Three messages between the phone and the server. Person B builds the phone against
mock responses; Person A builds the server against saved sample captures. You only
reconnect for real at the attack step.

## 1. POST /session  → start; server mints the per-session nonces
```json
{ "session_id": "a1b2c3", "profile_side": "left" }
```
`profile_side` (check 6) is the per-session nonce for the ID + profile-turn challenge.
The phone must call `/session` at **Begin** (not at submit) so it knows the side; the
server judges against its own stored copy, never the value the client echoes back.

## 1b. POST /session/{id}/light  → check 5: the colour sequence, minted JUST IN TIME
Called when the user taps **I'm ready** on the light instruction screen (not at Begin, so it
cannot be pre-computed). Single use; a re-mint (retry after client jank) replaces it.
```json
{ "challenge_id": "lc_9f1c2a7b", "total_ms": 4920, "expires_in_ms": 49000,
  "slots": [ { "name": "grey",  "rgb": [128,128,128], "ms": 700 },
             { "name": "red",   "rgb": [255,0,0],     "ms": 430 },
             { "name": "green", "rgb": [0,255,0],     "ms": 512 },
             { "name": "grey",  "rgb": [128,128,128], "ms": 388 },
             { "name": "blue",  "rgb": [0,0,255],     "ms": 545 }, "…6 more…",
             { "name": "grey",  "rgb": [128,128,128], "ms": 400 } ] }
```
Structure: 700 ms grey lead-in · 8 body slots = R,G,B ×2 + grey ×2 in CSPRNG order with no two
equal neighbours, each 380–560 ms · 400 ms grey tail (≈ 4.9 s, ≤ 1.3 flashes/s — WCAG 2.3.1).

## 2. POST /session/{id}/capture  → phone uploads the bundle (multipart/form-data)
Skeleton parts: `id_photo` (JPEG, front of the document), `selfie` (JPEG), `meta` (JSON string).
Check 2 adds `id_back` (JPEG, OPTIONAL — the back of a TD1 card, where the MRZ lives; omitted
for passports and driving licences).
Mitigated app adds (check 4, **implemented**): repeated `frames` parts (JPEG, ~320 px
wide, ~12 fps for 3 s, filename `0001.jpg`…) and these keys inside `meta`:
```json
{
  "label": "front", "settings": { "facingMode": "user" }, "hasMotion": true,
  "claimsMobile": true, "frameIntervals": [33.4, 33.1],
  "frames": [{ "file": "0001.jpg", "t": 12.34 }],
  "motion": [{ "t": 12.30, "rx": 1.2, "ry": -0.4, "rz": 0.1, "ax": 0.02, "ay": -0.11, "az": 0.05 }],
  "events": [{ "name": "tilt_start", "t": 12.00 }, { "name": "tilt_end", "t": 14.10 },
             { "name": "profile_start", "t": 14.12 }, { "name": "profile_end", "t": 16.90 }],
  "profile": { "enabled": true, "side": "left", "frames": [{ "file": "p0001.jpg", "t": 15.20 }] },
  "light": {
    "enabled": true, "opted_out": false, "challenge_id": "lc_9f1c2a7b", "attempt": 1,
    "switches": [ { "i": 0, "name": "grey", "ms": 700, "t_planned": 20540.0, "t_raf": 20540.1, "t_painted": 20556.8 },
                  { "i": 10, "name": "end", "ms": 0, "t_planned": 25460.0, "t_raf": 25461.0, "t_painted": 25477.7 } ],
    "frames": [ { "file": "l0001.jpg", "t": 20561.2, "t_pres": 20560.9, "t_exp": 20577.5, "t_cap": null, "media_t": 12.3, "n": 611 } ],
    "grab": { "mode": "rvfc", "width": 320, "jpeg_q": 0.9, "min_gap_ms": 30 },
    "raf": { "median_ms": 16.7, "jank": 0 }, "cam_lock": { "exposure": "none", "white_balance": "none" },
    "display": { "dpr": 3, "vw": 390, "vh": 844, "reduced_motion": false }, "hidden": false,
    "client_checks": { "all_painted": true, "order_ok": true, "max_switch_err_ms": 4.1, "max_dur_err_ms": 17.0, "min_frames_per_seg": 9, "ok": true }
  }
}
```
Check 5 (**implemented**, "Light check" toggle): repeated `light_frames` parts (JPEG, 320 px,
**every camera frame** ≈ 25–30 fps, **quality 0.9**, filenames `l0001.jpg`…) matched to
`meta.light.frames[].file`. Recorded right after the still selfie, BEFORE the tilt phase (phone
still, exposure settled). `switches[]` is the phone's paint log: `t_raf` = the rAF that set the
colour, `t_painted` = the next rAF (≈ on the glass); the server uses `t_painted` as the switch
time and judges the ORDER against its stored challenge (`colour_log_mismatch` → fail). Frame
time = `t` (grab time; the fitted lag absorbs the constant pipeline delay). `opted_out: true`
(photosensitivity skip) → `verdict: skipped` → step-up, never a penalty. `checks.light=false`
→ `light: { "enabled": false, "verdict": "absent", "ok": true }`, ignored.
Check 6 (**implemented**, behind the "Killer feature" toggle): repeated `profile_frames`
parts (JPEG, ~480 px wide, ~9 fps for 2.8 s, filenames `p0001.jpg`…) matched to
`meta.profile.frames[].file`. Recorded in the SAME continuous stream right after the tilt
phase (no confirm screen in between). `meta.profile.enabled=false` (or toggle off) skips
the check. The user is asked to hold their ID in their left hand — this is a UX/demo cue
only; the server does not detect or match the card (v1/v2 tried ORB matching against the
`id_photo` part — removed as unreliable in venue light, see PROJECT.md), it scores only the
head-turn geometry.
**Demo switches** (intro screen) also travel in `meta`: `"mode": "full" | "naive"` (naive = the
broken app of Act 2: server decides on face + document only) and
`"checks": { "motion": true, "profile": true, "doc_back": true, "light": true }`. With
`checks.motion=false` the phone sends no burst and the server returns `motion: { "enabled":
false, "verdict": "absent", "ok": true }`, which does not affect the decision (same pattern
for `light`/`profile`). `PROOFPRINT_MODE=naive` on the server still forces naive regardless
of the client.
`t` is `performance.now()` milliseconds, one clock for frames and motion.
`rx/ry/rz` = `DeviceMotionEvent.rotationRate.alpha/beta/gamma` in **deg/s** — per the current
spec `alpha` = about the device X axis (**pitch**), `beta` = about Y (**yaw**, the door-turn),
`gamma` = about Z (**roll**). Verified on an iPhone (a door-turn shows up in `beta`). iOS and
Android both report deg/s for this event. `ax/ay/az` = `DeviceMotionEvent.acceleration`
(gravity removed, m/s²) — used for the stationary-device test. `events` marks the capture
phases on the same clock (arXiv 2605.00218 aligns motion to capture events; check 5 adds
`light_start` / `light_end`; check 6 adds `profile_turn_detected` = when the phone saw the head
start to move and its 4.5 s window began). Frame files are matched to `meta.frames[].file` by filename.

## 3. GET /session/{id}/result  → the decision + why
```json
{
  "decision": "pass",
  "mode": "full",
  "signals": {
    "face":      { "ok": true, "score": 0.71, "verdict": "match" },
    "id":        { "ok": true, "portrait_found": true, "deskewed": false,
                   "doc_type": "id_card",                 // passport | id_card | no_mrz
                   "mrz": { "format": "TD1", "surname": "BOURGEOIS", "names": "THIBAUD RENE",
                            "birth_date": "2002-01-05", "expiry_date": "2026-10-31", "expired": false,
                            "number": "…", "checks": { "number": true, "birth_date": true, "expiry_date": true },
                            "ocr_confidence": 0.9 },     // null when no MRZ was read
                   "flags": [],                           // document_expired | mrz_checksum_failed
                   "portrait_thumb": "data:image/jpeg;base64,…" },
    "motion":    { "ok": true, "score": 0.87, "verdict": "pass", "lag_ms": 40,
                   "r_yaw": 0.9, "r_pitch": 0.7, "gyro_rms_dps": 35.2, "flow_rms_pxs": 120.0,
                   "acc_rms_ms2": 0.41, "flow_method": "lk_background", "face_masked": true, "flags": [],
                   "series": { "t_ms": [], "flow_x": [], "flow_y": [], "gyro_yaw": [], "gyro_pitch": [] } },
    "light":     { "enabled": true, "ok": true, "verdict": "pass", "score": 0.93,
                   "rho": 0.88, "acc": 1.0, "p": 0.001, "lag_ms": 120, "lag_fit_ms": 120,
                   "snr": 18.4, "amp": 0.11, "sigma": 0.006, "bg_reference": true,
                   "n_frames": 138, "n_face_frames": 131, "skin_px": 2400, "bg_px": 21000,
                   "per_segment": [ { "slot": 1, "colour": "red", "n": 9, "pred": "red", "d": [0.071, -0.03, -0.041], "ok": true } ],
                   "flags": [], "attempt": 1, "client_checks": { "ok": true },
                   "lag_curve": { "lag_ms": [-40, -20, 0], "rho": [0.1, 0.3, 0.5] },
                   "series": { "t_ms": [], "z": [[]], "base": [[]], "slot": [], "used": [], "edges_ms": [], "slots": [] } },
    "integrity": { "ok": true, "flags": [] },
    "profile":   { "enabled": true, "ok": true, "verdict": "pass", "score": 0.81, "side": "left",
                   "peak_ok": 0.24, "peak_wrong": 0.02, "turn_q": 1.0, "snap": 0.04,
                   "lost_at_profile": true, "flags": [],
                   "yaw_series": [0.0, 0.05, 0.12, null] }
  }
}
```
`meta.skipped: { "motion": true }` = the user tapped Skip on the phone-move instruction page
("I'm in a car / train / tram") → `motion: { "verdict": "skipped", "enabled": true, "reason":
"user_in_vehicle" }` → step_up with a readable reason, never block.
`motion.flags` may contain `stationary_device` (no sensor jitter at all → block) or
`video_moves_phone_still` (background pans while the phone is still → block). Head movement
alone is masked out and yields `insufficient` ("tilt the phone, not your head").
`profile.verdict` ∈ `pass | review | fail | insufficient | absent`. `fail` = turned to the
**wrong side** (hard, → block). `profile.flags`: only `yaw_snap` (a mid-turn yaw jump that
looks like a face-swap losing tracking lock). `insufficient` = no turn / no face (→ step_up).
When the toggle is off the signal is `{ "enabled": false, "verdict": "absent", "ok": true }`
and does not affect the decision. **Simplified 2026-09-24**: the ID card itself is no longer
detected or matched — the user is asked to hold it in their left hand as a UX/demo
instruction, but only the head-turn geometry is scored. The earlier ORB card-matching and
occlusion-edge signals are removed (unreliable on the first phone run, added complexity
without a validated benefit).
`id` (check 2): check 1 matches the selfie against the document **portrait** cut out here
(falls back to the whole card). `id.flags` only ever raise to step_up. Also
`GET /log?limit=50` (internal): recent scored sessions + face-score min/avg/max per decision,
first given name only — for threshold calibration.
`decision` ∈ `pass | step_up | block | pending`. `mode` ∈ `full | naive`
(`PROOFPRINT_MODE=naive` = the broken app: decides on `face` only).
The result also carries `reasons: ["…"]` (one human-readable line per trigger of the decision),
`checks_enabled: { motion, light, profile, doc_back }` and `timings_ms: { id, face, light, motion, profile }`
(server compute per check). **Analyst endpoints:** `GET /log?limit=` = counts per decision / mode /
platform / check verdict + score spreads + the recent rows; `GET /log/{session_id}` = the full trace of
one session (every signal with its curves, reasons, a non-personal capture summary — images stripped);
`GET /dashboard` = the analyst dashboard that reads both.
`motion.verdict` ∈ `pass | review | fail | insufficient | absent`; `series` holds the
lag-aligned curves for the analyst dashboard.
`light.verdict` (check 5) ∈ `pass | review | fail | insufficient | skipped | absent`.
`fail` = a strong response to the WRONG colours (replay of another session, forged or reused
challenge: flags `colour_log_mismatch`, `challenge_unknown`, `challenge_reused`) → **block**.
`insufficient` = flat response (`low_snr`: daylight OR an injected video — indistinguishable, so
step-up, never pass; also `few_face_frames`, `few_valid_slots`). `review` = right colours but
the delay is implausible (`lag_out_of_range`, a relighting deepfake), or `challenge_expired`.
`rho` = mean per-channel Pearson at the best lag, `acc` = share of the 6 colour slots whose
dominant channel was right, `p` = permutation test vs 1000 random sequences, `snr` = median
slot amplitude / frame noise. `series` = per-frame face−background log-chroma `z`, the guard
baseline and the slot index, for the light lab (`/lab/light`) and the analyst dashboard.
