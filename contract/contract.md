# Data contract — agree this at hour zero, then build in parallel

Three messages between the phone and the server. Person B builds the phone against
mock responses; Person A builds the server against saved sample captures. You only
reconnect for real at the attack step.

## 1. POST /session  → start; server mints the random light challenge
```json
{ "session_id": "a1b2c3", "challenge": ["black","red","green","blue","red"], "segment_ms": 500 }
```

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
  "colour": [{ "rgb": "red", "t_switch": 12.00 }]
}
```
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
    "integrity": { "ok": true, "flags": [] }
  }
}
```
`decision` ∈ `pass | step_up | block | pending`. `mode` ∈ `full | naive`
(`PROOFPRINT_MODE=naive` = the broken app: decides on `face` only).
`motion.verdict` ∈ `pass | review | fail | insufficient | absent`; `series` holds the
lag-aligned curves for the analyst dashboard. `light` is not implemented yet.
