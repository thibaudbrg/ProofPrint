# Data contract — agree this at hour zero, then build in parallel

Three messages between the phone and the server. Person B builds the phone against
mock responses; Person A builds the server against saved sample captures. You only
reconnect for real at the attack step.

## 1. POST /session  → start; server mints the random light challenge
```json
{ "session_id": "a1b2c3", "challenge": ["black","red","green","blue","red"], "segment_ms": 500 }
```

## 2. POST /session/{id}/capture  → phone uploads the bundle (multipart/form-data)
Skeleton parts: `id_photo` (JPEG), `selfie` (JPEG).
Mitigated app adds: `frames/*.jpg`, and a `meta.json`:
```json
{
  "frames": [{ "file": "0001.jpg", "t": 12.34 }],
  "motion": [{ "t": 12.30, "rx": 1.2, "ry": -0.4, "rz": 0.1 }],
  "colour": [{ "rgb": "red", "t_switch": 12.00 }],
  "camera": { "label": "front", "reported_fps": 30, "hasMotion": true }
}
```
`t` is `performance.now()` milliseconds. `rx/ry/rz` are rotationRate in **deg/s** (iOS).

## 3. GET /session/{id}/result  → the decision + why
```json
{
  "decision": "pass",
  "signals": {
    "face":      { "ok": true, "score": 0.71, "verdict": "match" },
    "motion":    { "ok": true, "score": 0.87, "lag_ms": 40 },
    "light":     { "ok": true, "score": 0.79 },
    "integrity": { "ok": true, "flags": [] }
  }
}
```
`decision` ∈ `pass | step_up | block | pending`. The skeleton returns only `face`.
