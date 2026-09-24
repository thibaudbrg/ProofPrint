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
Skeleton parts: `id_photo` (JPEG), `selfie` (JPEG), `meta` (JSON string).
Mitigated app adds (check 4, **implemented**): repeated `frames` parts (JPEG, ~320 px
wide, ~12 fps for 3 s, filename `0001.jpg`…) and these keys inside `meta`:
```json
{
  "label": "front", "settings": { "facingMode": "user" }, "hasMotion": true,
  "claimsMobile": true, "frameIntervals": [33.4, 33.1],
  "frames": [{ "file": "0001.jpg", "t": 12.34 }],
  "motion": [{ "t": 12.30, "rx": 1.2, "ry": -0.4, "rz": 0.1 }],
  "colour": [{ "rgb": "red", "t_switch": 12.00 }],
  "profile": { "enabled": true, "side": "left", "frames": [{ "file": "p0001.jpg", "t": 15.20 }] }
}
```
Check 6 (**implemented**, behind the "Killer feature" toggle): repeated `profile_frames`
parts (JPEG, ~480 px wide, ~9 fps for 3.5 s, filenames `p0001.jpg`…) matched to
`meta.profile.frames[].file`. `meta.profile.enabled=false` (or toggle off) skips the check.
`t` is `performance.now()` milliseconds, one clock for frames and motion.
`rx/ry/rz` = `DeviceMotionEvent.rotationRate.beta/gamma/alpha` in **deg/s** (iOS and
Android both report deg/s for this event). Frame files are matched to `meta.frames[].file`
by filename. `colour` is reserved for check 5.

## 3. GET /session/{id}/result  → the decision + why
```json
{
  "decision": "pass",
  "mode": "full",
  "signals": {
    "face":      { "ok": true, "score": 0.71, "verdict": "match" },
    "motion":    { "ok": true, "score": 0.87, "verdict": "pass", "lag_ms": 40,
                   "r_yaw": 0.9, "r_pitch": 0.7, "gyro_rms_dps": 35.2, "flow_rms_pxs": 120.0,
                   "series": { "t_ms": [], "flow_x": [], "flow_y": [], "gyro_yaw": [], "gyro_pitch": [] } },
    "light":     { "ok": true, "score": 0.79 },
    "integrity": { "ok": true, "flags": [] },
    "profile":   { "enabled": true, "ok": true, "verdict": "pass", "score": 0.81, "side": "left",
                   "peak_ok": 0.24, "peak_wrong": 0.02, "turn_q": 1.0, "card_frac": 0.7,
                   "light_q": 0.8, "snap": 0.04, "lost_at_profile": true, "flags": [],
                   "yaw_series": [0.0, 0.05, 0.12, null] }
  }
}
```
`profile.verdict` ∈ `pass | review | fail | insufficient | absent`. `fail` = turned to the
**wrong side** (hard, → block). `insufficient` = no turn / no face (→ step_up). When the
toggle is off the signal is `{ "enabled": false, "verdict": "absent", "ok": true }` and
does not affect the decision.
`decision` ∈ `pass | step_up | block | pending`. `mode` ∈ `full | naive`
(`PROOFPRINT_MODE=naive` = the broken app: decides on `face` only).
`motion.verdict` ∈ `pass | review | fail | insufficient | absent`; `series` holds the
lag-aligned curves for the analyst dashboard. `light` is not implemented yet.
