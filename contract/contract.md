# Data contract — agree this at hour zero, then build in parallel

Three messages between the phone and the server. Person B builds the phone against
mock responses; Person A builds the server against saved sample captures. You only
reconnect for real at the attack step.

## 1. POST /session  → start; server mints the random light challenge
```json
{ "session_id": "a1b2c3", "challenge": ["black","red","green","blue","red"], "segment_ms": 500,
  "profile_side": "left" }
```
`profile_side` (check 6) is the per-session nonce for the ID + profile-turn challenge.
The phone must call `/session` at **Begin** (not at submit) so it knows the side; the
server judges against its own stored copy, never the value the client echoes back.

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
  "colour": [{ "rgb": "red", "t_switch": 12.00 }],
  "profile": { "enabled": true, "side": "left", "frames": [{ "file": "p0001.jpg", "t": 15.20 }] }
}
```
Check 6 (**implemented**, behind the "Killer feature" toggle): repeated `profile_frames`
parts (JPEG, ~480 px wide, ~9 fps for 2.8 s, filenames `p0001.jpg`…) matched to
`meta.profile.frames[].file`. Recorded in the SAME continuous stream right after the tilt
phase (no confirm screen in between). `meta.profile.enabled=false` (or toggle off) skips
the check. The server matches the card in these frames against the `id_photo` part (ORB).
`t` is `performance.now()` milliseconds, one clock for frames and motion.
`rx/ry/rz` = `DeviceMotionEvent.rotationRate.beta/gamma/alpha` in **deg/s** (iOS and
Android both report deg/s for this event). `ax/ay/az` = `DeviceMotionEvent.acceleration`
(gravity removed, m/s²) — used for the stationary-device test. `events` marks the capture
phases on the same clock (arXiv 2605.00218 aligns motion to capture events). Frame files are matched to `meta.frames[].file`
by filename. `colour` is reserved for check 5.

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
    "light":     { "ok": true, "score": 0.79 },
    "integrity": { "ok": true, "flags": [] },
    "profile":   { "enabled": true, "ok": true, "verdict": "pass", "score": 0.81, "side": "left",
                   "peak_ok": 0.24, "peak_wrong": 0.02, "turn_q": 1.0, "card_frac": 0.7,
                   "occlusion_seen": true, "occlusion_q": 0.82, "light_q": 0.8, "snap": 0.04,
                   "lost_at_profile": true, "card_source": { "orb": 14, "contour": 2 },
                   "id_template": true, "flags": [],
                   "yaw_series": [0.0, 0.05, 0.12, null] }
  }
}
```
`motion.flags` may contain `stationary_device` (no sensor jitter at all → block) or
`video_moves_phone_still` (background pans while the phone is still → block). Head movement
alone is masked out and yields `insufficient` ("tilt the phone, not your head").
`profile.verdict` ∈ `pass | review | fail | insufficient | absent`. `fail` = turned to the
**wrong side** (hard, → block). `profile.flags`: `card_not_seen`, `card_never_crossed_face`,
`card_edge_erased_over_face` (the swap painted over the occluder), `yaw_snap`. `insufficient` = no turn / no face (→ step_up). When the
toggle is off the signal is `{ "enabled": false, "verdict": "absent", "ok": true }` and
does not affect the decision.
`id` (check 2): check 1 matches the selfie against the document **portrait** cut out here
(falls back to the whole card). `id.flags` only ever raise to step_up. Also
`GET /log?limit=50` (internal): recent scored sessions + face-score min/avg/max per decision,
first given name only — for threshold calibration.
`decision` ∈ `pass | step_up | block | pending`. `mode` ∈ `full | naive`
(`PROOFPRINT_MODE=naive` = the broken app: decides on `face` only).
`motion.verdict` ∈ `pass | review | fail | insufficient | absent`; `series` holds the
lag-aligned curves for the analyst dashboard. `light` is not implemented yet.
