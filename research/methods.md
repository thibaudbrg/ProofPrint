# Proofprint — how each check works (short version)

One phone session produces six signals. Each is computed **on the server** from what the phone
uploaded, gets a verdict, and the verdicts are fused into one decision. The phone only captures.

| # | Check | Question it answers | Library |
|---|---|---|---|
| 1 | Face match | Is the selfie the person on the ID? | OpenCV YuNet (detect) + SFace (embed) |
| 2 | Document | Is the ID readable and internally consistent? | OpenCV (deskew) + PassportEye/Tesseract (MRZ) |
| 3 | Capture integrity | Is this a real phone camera with sensors? | none (metadata rules) |
| 4 | Phone move | Does the video move the way the gyroscope says the phone moved? | OpenCV optical flow + NumPy |
| 5 | Light | Does the skin reflect the colours the screen just showed? | OpenCV YuNet + NumPy |
| 6 | Profile turn | Can the face turn to the side *we* chose, live? | OpenCV YuNet + NumPy |

Verdict vocabulary, the same everywhere: **pass · review · fail · insufficient · absent/skipped**.

---

## 1 · Face match

Detect the largest face on the document portrait and on the selfie, turn each into a 128-number
vector (an "embedding" that describes the face, not the pixels), and compare directions:

$$s = \cos\theta = \frac{a \cdot b}{\|a\|\,\|b\|}$$

Same person → vectors point the same way → $s$ close to 1. Different people → $s$ near 0.

| $s \ge 0.363$ | $0.28 \le s < 0.363$ | $s < 0.28$ | no face found |
|---|---|---|---|
| match | review | mismatch | none |

(0.363 is OpenCV's published SFace threshold. Real pairs so far: 0.55–0.69.)

## 2 · Document

Find the largest quadrilateral, warp it flat (ID-1 aspect 1.585), cut out the portrait for check 1,
and read the machine-readable zone. Every MRZ number carries a check digit that is recomputed:

$$c = \Big(\sum_i d_i \cdot w_i\Big) \bmod 10,\qquad w = 7,3,1,7,3,1,\dots$$

If the recomputed digit ≠ the printed one, or the expiry date is past → flag. Flags only ever
raise the decision to *review*; a document without an MRZ (driving licence) is fine.

## 3 · Capture integrity

Rules on the capture metadata: camera label matches a known virtual camera (OBS, ManyCam…) →
**hard flag**; front camera not reported; no motion sensors on a phone → soft flags. No model.

## 4 · Phone move

The user turns the phone left/right for 6 s. Two independent recordings of the *same* motion:

- **video** — background points tracked frame to frame (Lucas–Kanade optical flow; the face is
  masked out so head movement does not count) → horizontal and vertical speed $v_x(t), v_y(t)$ in px/s
- **gyroscope** — angular rates $\omega_{yaw}(t), \omega_{pitch}(t)$ in °/s, resampled to the frame times

A real camera turning right makes the background slide left: the two curves are the same shape.
Their agreement is the Pearson correlation, searched over a camera delay $L \in [0, 300]$ ms:

$$r_{yaw} = \max_L \big|\,\mathrm{corr}\big(v_x(t),\ \omega_{yaw}(t-L)\big)\big|,\qquad
r_{pitch} = \max_L \big|\,\mathrm{corr}\big(v_y(t),\ \omega_{pitch}(t-L)\big)\big|$$

Weighted by how much the user actually moved on each axis ($e = \mathrm{var}(\omega)$):

$$\text{score} = \frac{e_{yaw}\, r_{yaw} + e_{pitch}\, r_{pitch}}{e_{yaw} + e_{pitch}}$$

| score ≥ 0.60 | 0.35 ≤ score < 0.60 | score < 0.35 | phone barely moved (gyro rms < 8 °/s) |
|---|---|---|---|
| pass | review | **fail** | insufficient |

Also **fail**: the video pans while the gyroscope says the phone is still, or gyroscope *and*
accelerometer are perfectly flat (phone on a stand / emulator). Real runs: 0.74–0.98.

## 5 · Light

The server rolls a random colour sequence *at that moment* (grey · R,G,B ×2 + grey ×2 in random
order, 380–560 ms each · grey ≈ 4.9 s) and the phone paints it full-screen while filming the face.

Per frame, on skin patches (forehead, cheeks — from the face landmarks) and on the background:
the colour balance, not the brightness, as log-ratios; face minus background cancels the camera's
auto-exposure and white balance; the grey slots give the baseline:

$$z_c = \log F_c - \overline{\log F} \;-\; \big(\log B_c - \overline{\log B}\big),\qquad
M_c(t) = z_c(t) - \text{baseline}_c(t)$$

Under a red flash the prediction is $M \approx (+\tfrac{2}{3}, -\tfrac{1}{3}, -\tfrac{1}{3})\cdot k$:
red up, the others down. Three separate questions, over a camera delay $L \in [-40, 420]$ ms:

- **acc** — in each of the 6 colour slots, is the channel that rose most the right one? (6/6 = 1.0)
- **ρ** — Pearson correlation between the predicted square waves and the measured $M(t)$, best $L$
- **p** — chance: the same score against 1000 *random* sequences; $p$ = fraction that did as well

$$\text{score} = \tfrac12\,\frac{acc - \tfrac13}{\tfrac23} + \tfrac12\,\min\!\Big(1, \frac{\rho}{0.8}\Big),
\qquad \text{score} \le 0.45 \text{ if } p > 0.05$$

| score ≥ 0.75 and $p \le 0.01$ | flat response (SNR < 3) | 0.50 ≤ score | else |
|---|---|---|---|
| pass (→ review if lag > 300 ms) | insufficient | review | **fail** |

*Flat* = daylight **or** an injected video: indistinguishable, so never a pass. *Fail* = a strong
answer to the **wrong** colours = a replay. Real runs: ρ 0.92–0.99, 6/6, $p \le 0.005$, lag 80–100 ms.

## 6 · Profile turn

The server picks a side per session. Yaw is estimated from the face landmarks:

$$y = \frac{x_{nose} - \tfrac{1}{2}(x_{eye,L} + x_{eye,R})}{w_{face}}$$

(0 = frontal, |y| ≈ 0.12 ≈ 45°, 0.22 ≈ profile; the face detector losing the face after a clear
trend also counts as a full profile.) With the sign set so + = the requested side:

$$\text{turn} = \min\!\Big(1, \frac{\max_t y(t)}{0.22}\Big),\qquad
\text{score} = \text{turn} - 0.35\cdot[\text{a jump} > 0.20 \text{ between two frames}]$$

| turned to the **wrong** side | no turn (max y < 0.12) | score ≥ 0.60 | 0.35 ≤ score | else |
|---|---|---|---|---|
| **fail** | insufficient | pass | review | fail |

The jump penalty is the face-swap signature: a mask "snaps" when the mesh loses the face.

---

## How the decision is made

Not an average — a rule, evaluated in order. The naïve app (Act 2) stops after the first line.

```
naive          →  match → pass · review → second check · else → block
BLOCK   if     virtual camera  OR  motion fail  OR  light fail  OR  profile fail
        OR     face mismatch / no face
SECOND  if     face review  OR  integrity flag  OR  document flag
CHECK   OR     motion   ∈ {review, insufficient, absent, skipped}
        OR     light    ∈ {review, insufficient, absent, skipped}
        OR     profile  ∈ {review, insufficient, absent}
PASS    otherwise  — the face matches AND every enabled check passed
```

So: **every enabled check must pass** for a pass (AND); **any one hard failure blocks** (OR);
anything in between — including a user who skipped a step — goes to a second factor, never to a
silent pass. A check switched off on the phone is *absent* and simply not counted.

Each `result` carries `reasons[]`: the exact lines above that fired, in plain English, shown in
the dashboard trace.

## What it does not catch (say it out loud)

A face-swap that also copies the flash colour onto the fake face within ~150 ms, or an attacker
who controls the browser and feeds fake sensor data consistent with a fake video. Proofprint
raises the cost from "a video file" to "a real-time, physically consistent, multi-sensor forgery";
it is a fusion of signals, not a single proof.
