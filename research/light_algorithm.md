# Check 5 — Light pulse: server-side algorithm (research notes, 2026-09-24)

Scope: the **server** algorithm for the screen-light colour-reflection check. The client-side capture
(full-screen colour, rAF switching, rVFC frame timestamps, photosensitivity UX) is in
`research/light_capture.md`. The two notes agree, with one difference: that note uses **black** guards and
I recommend **grey** guards. The server accepts either (see §3).

Status: a reference implementation (§8) is **tested on synthetic JPEG sequences only** (simulated AE/AWB,
JPEG q70, 12 fps, face-brightness jitter). **Nothing here has been run on real phone captures yet.** Every
threshold is a starting point to calibrate with `lab.py`.

---

## 0. TL;DR recipe

| Item | Choice |
|---|---|
| ROI | Forehead + 2 cheek quads in an eye-line frame built from YuNet's 5 landmarks. Background = top 55 % of the frame outside a 1.9× face box. |
| Pixel filter | Linearise sRGB with a LUT. Drop pixels with max channel ≥ 250 or ≤ 20. Keep the 10–90 % luminance-trimmed mean. Face ≥ 600 valid px, background ≥ 2000 px, inter-ocular distance ≥ 40 px. |
| **Primary feature** | `z = [log F − mean_c log F] − [log B − mean_c log B]`: the face's log-chromaticity minus the background's. This cancels AE, AWB (global per-channel gains), albedo, distance and face-only brightness changes. |
| Fallback | Face log-chromaticity only, when the background is clipped, too dark or missing. Flag `no_bg_reference`. |
| Baseline | Per-channel linear interpolation of the medians of the neighbouring **guard (K)** slots, which removes slow AE/AWB drift. |
| Sequence | Grey lead-in of 700 ms, then 8 body slots {R,G,B ×2, K ×2} in random order with no immediate repeats, each 380–560 ms (uniform, CSPRNG). Grey tail of 400 ms. About 4.9 s total, 462 symbol orders × duration jitter. ≤ 2.6 transitions/s, so ≤ 1.3 flashes/s (WCAG limit is 3). |
| Alignment | Search lag L ∈ [−40, 420] ms in 20 ms steps. Ignore frames within 120 ms after and 40 ms before each switch. Estimate lag without the guards (sharper peak). |
| Scoring | ρ = mean per-channel Pearson(expected one-hot − 1/3, measured z − baseline), maximised over L. acc = per-slot argmax accuracy. p = permutation test against 1000 random challenges with the same timing, with the max over L inside the null. `score = 0.5·clip((acc−⅓)/⅔) + 0.5·clip(ρ/0.8)`, capped at 0.45 if p > 0.05. |
| Decision | pass if score ≥ **0.75** and p ≤ **0.01** (downgraded to review if lag is outside [0, 300] ms). If not matched and SNR < **3** or amplitude < **0.012**: **insufficient → step-up**. Otherwise review if score ≥ **0.50**, else fail. |

---

## 1. Skin ROI from YuNet's 5 landmarks

YuNet row: `[x, y, w, h, re_x, re_y, le_x, le_y, nose_x, nose_y, rm_x, rm_y, lm_x, lm_y, score]`.
`profile_check._landmarks` currently drops the mouth corners (f[10:14]). Extend it or add a sibling.

Face frame (all in image pixels):
- `e1`, `e2` = the image-left and image-right eyes. `c = (e1+e2)/2`, `d = |e2−e1|` (inter-ocular distance,
  IOD), `u = (e2−e1)/d` (along the eye line, so it follows roll).
- `v = ⊥u`, oriented towards the nose (`(nose−c)·v > 0`).
- `vm = max((mouth_mid − c)·v, 0.8·d)` = eye-line-to-mouth-line distance (typically ≈ 1.0–1.1 d).

Patches are rotated rectangles filled with `cv2.fillConvexPoly`:

| Patch | u range (×d) | v range | What it avoids |
|---|---|---|---|
| Forehead | −0.40 … +0.40 | −1.00 d … −0.55 d | Brows sit about 0.25–0.4 d above the eye line. Bangs cover the top, and the pixel filter removes most hair. |
| Left cheek | −0.72 … −0.30 | 0.33 vm … 0.72 vm | Lower eyelid (< 0.3 d), nostrils / alae (\|u\| < 0.25 d, v ≈ 0.6 d), mouth corners (v = vm), face edge (\|u\| > 0.8 d). |
| Right cheek | +0.30 … +0.72 | same | same |

- Pool the three patches' pixels. Optionally keep them separate as a flag: all three should respond.
- **Background**: rows 3–55 % of H, columns 3–97 % of W, minus the face box enlarged to 1.9× (centred). The top
  half only, so shoulders and torso (close to the screen, and lit by it) are excluded. The screen is ≈ 25 cm
  from the face but ≥ 1 m from a wall, so the background receives less than 1/16–1/36 of the screen irradiance the face gets.
- **Minimums at 320 px width**: IOD ≥ 40 px (a face at 25 cm gives d ≈ 60–75 px). Skin ≥ 600 valid px
  (typical is ~2500). Background ≥ 2000 valid px. A frame below the minimum is dropped.
- **Pixel validity**: encoded max channel in (20, 250). Clipped pixels break the multiplicative model (Face
  Flashing removes saturated pixels for the same reason), and dark pixels are dominated by noise and JPEG.
  Then a 10–90 % trim on the linear luminance inside each ROI removes specular highlights and residual
  hair or brow.
- If YuNet misses a frame (for example a dark guard slot), **carry the last landmarks forward ≤ 250 ms**. The user
  is holding still.
- Yaw: if `|yaw_proxy| > 0.15`, the far cheek shrinks. It is simply dropped by the pixel minimum when it
  falls off the face. Not needed for a still, frontal pose.

## 2. Per-frame feature

Linearise first: `lin = LUT[u8]` with the sRGB inverse EOTF (`c/12.92` or `((c+0.055)/1.055)^2.4`).
FaceRevelio also inverts gamma before modelling. Phone tone curves are not pure sRGB (S-curves, local tone
mapping), so this is approximate. It is still better than working on encoded values, and the log-ratio design
below is tolerant of any per-channel monotone gain.

Physics: for a Lambertian skin pixel, `F_c = g_c · ρ_c · (A_c + S_c(t))`. Here g_c = camera gain (AE × AWB,
global), ρ_c = albedo, A_c = ambient, S_c = screen irradiance in channel c. The background gives `B_c = g_c · β_c · A_c`
(S negligible).

| Option | Formula | Cancels | Problems |
|---|---|---|---|
| (a) face ÷ bg per channel | `log F_c − log B_c` | g_c (AE and AWB, both global) and albedo (after baseline subtraction) | Keeps any **face-only** brightness change: iPhone front-camera AE is face-weighted, local tone mapping, head moving closer, shading. That is luminance noise leaking into all 3 channels. |
| (b) chromaticity difference | `r = R/(R+G+B)` etc., face − bg | global gain and face-only luminance | Not multiplicative, so AWB does not cancel exactly. Sensitivity depends on the operating point. |
| (c) circular hue vs emitted hue (Gerstner–Farid) | circular mean of `rgb_to_hsv(I/Î_0).h`, circular correlation | luminance | 1-DOF, noisy on low-saturation skin, needs the pre-flash face colour. Their range (yellow→magenta, isoluminant) is designed for a small window on a laptop at 30 Hz, where the killer feature is lag (2 frames at 30 Hz). At 12–25 fps with uncontrolled browser timing, that lag lever is not available (see §7). |

**Primary (recommended): log-chromaticity difference (a)+(b) in log form**

```
zf = log F − mean_c(log F)          # face log-chromaticity (removes face-only luminance)
zb = log B − mean_c(log B)          # background log-chromaticity
z  = zf − zb                        # removes per-channel global gains (AWB) exactly
M(t) = z(t) − baseline_K(t)         # removes albedo and ambient colour + slow drift
```
Under a red flash, `M ≈ (+2/3, −1/3, −1/3)·log(1+S_R/A_R)`, and similarly for G and B. The per-channel
division by the guard baseline is exactly Gerstner–Farid's "divide by the pre-illumination face colour"
(albedo cancels) and Face Flashing's "normalise by the background-challenge response". F and B are **means in
linear space** (irradiance averages); the log is taken after averaging.

**Fallback**: `z = zf` (no background term), still detrended by the interpolated K baseline. It survives slow
AWB. A fast AWB reaction *attenuates* the colour response but does not flip it. Flag `no_bg_reference`, and
optionally require score ≥ 0.85 for a pass.

Bonus property (untested): with a **replay screen or print held in front of the camera**, the phone's light
reflects on the display glass or paper uniformly, so the face and the in-video "background" get the same tint and z
cancels. The face-vs-background differential therefore also discriminates against flat presentation attacks.

## 3. Sequence the server mints

Default (`mint()` in §8):
- **Guard K = neutral grey `#808080`** (linear 0.216 ≈ red's luminance 0.2126). K is the baseline and the
  "blank frame" of Gerstner–Farid. Grey instead of black: (i) the chromatic contrast is the same. With S/A = 0.27 the
  red chroma response is 0.159 from black and 0.158 from grey, because grey's G and B light is *removed* when red comes on.
  (ii) The face stays lit, so YuNet keeps detecting in dark rooms. (iii) Luminance swings, AE pumping and photosensitivity load
  are smaller. **The server treats any non-RGB name (`black`, `grey`) as a guard**, so the client's choice of
  black also works.
- Colours: full primaries `#f00 #0f0 #00f`. They are maximally separable in camera RGB, and skin albedo cancels in
  the ratio. Isoluminant variants were considered and rejected: blue's luminance (0.07) caps it, so everything would be dim.
- Structure: `[K 700 ms] + shuffle(R,R,G,G,B,B,K,K) + [K 400 ms]`, with no two identical neighbours, and each body
  slot 380–560 ms (uniform, `secrets.SystemRandom`). That gives 462 distinct orders plus 8 continuous durations.
  About 4.9 s.
- The balanced design (each colour ×2) keeps the per-channel correlation well conditioned. The interior K slots
  let the baseline be re-estimated (Gerstner–Farid's randomly interjected blanks), which kills slow drift.
- Frames per slot: at 12 fps, a 470 ms slot has ~5.6 frames and **~3.7 usable** after the 160 ms transition mask.
  The minimum is 2 per slot, and ≥ 5 of 6 colour slots must be valid. At the capture note's 24–30 fps: ~8 usable per slot.
- Photosensitivity: WCAG 2.3.1 allows ≤ 3 flashes per 1 s window for a full-screen area (a flash is a pair of opposing
  luminance transitions; saturated red has its own threshold with the same 3/s count). The minimum slot is 380 ms,
  so there are ≤ 2.6 transitions/s ≤ 1.3 flashes/s. Keep the warning, Skip and `prefers-reduced-motion` from the capture note. A skip means
  step-up, never a penalty.
- Contract change: `challenge` becomes `{"slots": [...], "dur_ms": [...]}`. The current list has 5 i.i.d. colours
  and a fixed 500 ms. Store it server-side with a TTL (~60 s) and single use.
- Colour switch time used by the server: the client's `t_painted` (the next rAF after the style change). Fall back to `t_raf`.

## 4. Scoring

Inputs: frame times `t_f` (use rVFC `captureTime` when present, else the rVFC callback `now`, else the grab time)
and switch edges `e_0..e_n` from the colour log (checked against the server's copy: same order, each slot within ±100 ms
of plan, otherwise flag `slot_timing_jitter`. A different order is a hard fail, `colour_log_mismatch`).

For each lag L in `[−40, 420]` step 20 ms:
1. `τ_f = t_f − L`. Slot `k_f` = the slot containing τ_f. Drop the frame if `τ_f − e_k < 120 ms` (screen response +
   exposure ≤ 33–66 ms + rolling-shutter readout ≈ 30 ms + timestamp jitter) or `e_{k+1} − τ_f < 40 ms`.
2. Baseline: the median z of each K slot, placed at the slot mid-time, linearly interpolated per channel to every τ_f.
   `M_f = z_f − base(τ_f)`.
3. Template `T_f = onehot(colour) − 1/3` for colour slots and `0` for K. **ρ(L) = mean over c of Pearson(T[:,c], M[:,c])**.
   Per-channel Pearson makes it scale-free per channel (the blue response can be 2× the green under warm light).
4. Null: 1000 random label sequences (i.i.d. R/G/B on the same colour slots, same timing, same K). Compute ρ_null(L)
   the same way and keep `max_L` per null draw, which corrects for the lag search.

Then:
- `ρ* = max_L ρ(L)`, `L* = argmax`. `p = (1 + #{max_L ρ_null ≥ ρ*}) / 1001`.
- **Lag estimate**: repeat the ρ(L) scan *without* the transition mask. The mixed frames near the edges make the
  peak sharp. In simulation this recovers the true 120 ms within ±20 ms, whereas the masked scan plateaus over ~150 ms.
  Report it as `lag_ms`.
- Per slot at L*: `d_s = median(M_f)` over its frames (≥ 2). `pred = argmax_c d_s`. acc = correct/valid.
  A random guess gives acc ≈ ⅓. P(≥ 5/6 correct by chance) = 1.8 %.
- `score = 0.5·clip((acc−⅓)/⅔, 0, 1) + 0.5·clip(ρ*/0.8, 0, 1)`. If p > 0.05, cap it at 0.45.
- Lag: do **not** try to take camera latency from the motion check. The motion check has no externally timed
  stimulus (a user's head turn is not a clock), so it cannot calibrate the pipeline latency. Search a window
  instead, and log `lag_ms` per device model to tighten `LAG_OK` later.

## 5. SNR and ambient gate ("insufficient → step-up", never a pass)

- Noise σ = the median over channels of 1.4826·MAD of the within-slot residuals `z_f − median_slot(z)` (frame-level noise).
- Amplitude A = the median over valid colour slots of `‖d_s‖₂`. This is direction-agnostic: it measures *any* structured
  colour response, whether or not it matches.
- `SNR = A/σ`.
- Rule: if the response **matched** (score ≥ 0.75 and p ≤ 0.01), pass whatever the SNR. If it did not match and
  (`SNR < 3` or `A < 0.012` ≈ 1.2 % chroma shift), the verdict is **insufficient**. Otherwise score as review or fail. A strong
  structured response that is the *wrong* one (a replay of another session) is a fail, not insufficient.
- Physics estimate: a phone at ~500 cd/m², 7×15 cm, 25 cm away gives ≈ 80 lx white-equivalent at the face. One primary
  adds about ⅓ of that to its own channel. Indoors (100–500 lx) S/A ≈ 0.1–0.4, so |d| ≈ 0.07–0.25. By a window or
  outdoors (2–20 klx) |d| ≈ 0.003–0.03, so the gate trips. Brightness under auto-brightness can be 2–4× lower
  (see the capture note's table). FaceRevelio and Face Flashing both report dark ≥ daylight performance for this reason.
- **Honest limit**: a flat (no-response) stream is the same signature whether it comes from strong daylight or from an injected video
  that ignores the screen. Background brightness or clipping can *hint* at daylight (log it), but the attacker controls the pixels,
  so it must not be trusted. Therefore insufficient means **retry once with a fresh challenge and the tip "brightness up, closer, away
  from the window"**. A second insufficient goes to review or another factor, never an automatic pass.

## 6. Expected numbers (to verify on real captures)

| Scenario | ρ* | acc | p | SNR | Verdict |
|---|---|---|---|---|---|
| Genuine, indoor, 20–30 cm, brightness ≥ 60 % | 0.7–0.95 [est.; G&F real-world 0.93 for a 13″ light at 60 cm; FaceRevelio > 0.85 in 99.9 % of human trials] | 5/6–6/6 | ≤ 0.005 | 10–40 | pass |
| Genuine, near a window / auto-dim screen | 0.3–0.7 | 3/6–6/6 | varies | 2–8 | pass / insufficient |
| Replay of a pre-recorded session (other sequence) | −0.2–0.35 (G&F no-illumination baseline mean 0.09, max 0.34) | ≈ ⅓ | uniform | high | **fail** |
| Static injected video / deepfake without relighting | ≈ 0 ± 0.2 | ≈ ⅓ | uniform | < 3 | **insufficient** (flat) |
| Real-time relighting deepfake, total lag ≤ 300 ms | same as genuine | 6/6 | small | high | **pass (not caught)** |
| Real-time relighting, total lag > 300 ms | high | 6/6 | small | high | review (`lag_out_of_range`) |

Synthetic simulation (8 trials each, 12 fps, JPEG q70, simulated AE+AWB, 3 % face luminance jitter; optimistic):

```
genuine_indoor   (S/A≈.3)   8/8 pass   rho .99 acc 1.0  snr 26  amp .20  lag 120 (true 120)
genuine_no_bg (bg clipped)  8/8 pass   rho .99            fallback path
genuine_dim_screen (.1)     8/8 pass   rho .95 snr 21 amp .07
daylight_weak (.025)        6 pass 1 review 1 fail  rho .65 amp .024
daylight_vweak (.01)        6 insufficient, 1 review, 1 fail   snr 4.4 amp .008
replay_other_seq            8/8 fail   rho .19 acc .42 p .57
static_injected             8/8 insufficient  snr 2.9 amp .007
relight deepfake +150 ms    8/8 PASS   lag 270    <- honest miss
relight deepfake +400 ms    8/8 review (lag_out_of_range)
```
Real noise (JPEG at 320 px, micro-motion, AE hunting, local tone mapping) will be worse. Expect the daylight rows to
move towards insufficient.

**Thresholds to start with**: `SCORE_PASS = 0.75`, `SCORE_REVIEW = 0.50`, `P_MAX = 0.01`, `SNR_MIN = 3`,
`A_MIN = 0.012`, `LAG_OK = [0, 300] ms`, `POST/PRE = 120/40 ms`, `MIN_FR_SLOT = 2`, `MIN_COLOUR_SLOTS = 5`.

**Log per run (lab.py) for calibration**: UA/device, camera label, fps (median and p90 frame gap), frame-time source
(captureTime/now/grab), rAF interval and dropped-switch count, per-slot planned vs actual duration, IOD px, skin and background valid
px, clipped fraction, face and background mean luminance (encoded), `bg_ok` / fallback, the full ρ(L) curve, ρ*, L*, `lag_ms`,
acc, per-slot `d` vectors + expected colour + n_frames, p, σ, A, SNR, score, verdict, flags, plus the operator label
(room, window, brightness %, distance). Calibration: ≥ 20 genuine runs per phone × {dim room, office, window}, plus
replay (another session's frames uploaded under a new challenge) and a static video. Set SCORE_PASS at the genuine
p5, SNR_MIN at the replay/static p99, and LAG_OK at genuine p1–p99 + 40 ms per frame-time source.

## 7. Attacks and limits (honest)

| Attack | Caught? | Why |
|---|---|---|
| Pre-recorded video injected (OBS / virtual cam / JS hook) | **Yes** | It cannot follow a fresh random order and random durations. Wrong colours give fail. No response gives insufficient. |
| Replay of a genuine session's frames under a new challenge | **Yes** | p is uniform, acc ≈ ⅓. Chance of ≥ 5/6 is 1.8 %, and it must also match timing (ρ). |
| Screen or print held in front of the camera (presentation) | Likely | A uniform tint on the display cancels in face − background chroma (untested). |
| Off-the-shelf face swap / reenactment (Avatarify, most DeepFaceLive configs) | **Yes → insufficient** | Face colour does not follow the screen (G&F: Avatarify "flatlined", ρ ≈ 0). This routes to step-up, so step-up must be strong. |
| Face swap *with* colour transfer from the attacker's real face, sitting in front of the flashing phone | **No**, if the added delay is ≲ 150 ms | DeepFaceLive documents colour transfer from the source environment. The reflection then propagates. With 380–560 ms slots and 12–30 fps, a ~100–200 ms extra delay stays inside the lag window. G&F's lag lever needs 30 Hz and a fast pattern (they lose correlation at > 2 frames). We cannot flash faster than 3 flashes/s. |
| Attacker controls the client and reads the colour from the page, then tints the injected face (`pixels × gain_c`) | **No** | The colour must be in the DOM to be displayed. A per-pixel tint is < 10 ms of work. JIT delivery removes *lookahead* only, not knowledge. Physics consistency (3D shading of an area light, neck vs face, corneal reflections of the screen rectangle) would raise the bar but is not in this design. |
| Timestamp forgery (client lies about switch times or frame times) | Partly | The server validates order and durations against its own plan. A client that shifts frame timestamps can hide latency. This is the same trust boundary as the whole browser app (integrity check, check 4/6). |

Why the challenge must be server-minted, random, single-use and delivered just in time:
- Minted server-side with a CSPRNG and stored server-side: the client cannot pick an easy or pre-recorded sequence, and
  the server judges against *its* copy (same pattern as `profile_side`).
- Random order and random durations: a recording made earlier corresponds to another challenge (the replay row above). The
  permutation test turns this into a p-value.
- Minted at the start of the light phase with a short TTL and single use: this prevents pre-computing a response
  offline. The stronger variant streams each slot's colour over a WebSocket just before it is shown, so any forgery must
  be causal and real-time. **For the hackathon**: mint at `/session` (as today) but re-mint at the start of the light phase
  if the gap is > 60 s. Mention JIT streaming as the production upgrade, since network jitter would then need the logged `t_painted` anyway.
- Positioning: this is a **fusion** signal (with check 4 motion physics and check 6 profile + ID). It is not a standalone
  anti-deepfake primitive, and the iProov Flashmark patent covers the primitive.

## 8. Reference implementation (runnable, ~120 lines of logic)

Tested with the synthetic harness in the appendix (server venv: numpy + OpenCV 4.11). The `landmarks_fn(bgr)`
returns `{"box": (x,y,w,h), "pts": [[re],[le],[nose],[rm],[lm]]}` or None (wrap YuNet: `f[0:4]`, `f[4:14].reshape(5,2)`).
Frames: `[(t_ms, bgr_uint8)]`. `colour_log`: `[{"rgb": name, "t_switch": ms}]` (use `t_painted`).
`challenge`: `{"slots": [...], "dur_ms": [...]}`. Runtime is ≈ 0.3–0.5 s per run on a laptop (the null test is
vectorised: 24 lags × 1000 draws).

```python
import numpy as np, cv2

CH = {"red": 0, "green": 1, "blue": 2}          # RGB order; any other name (grey/black) = guard K
GUARD = "grey"                                  # #808080
POST_MS, PRE_MS = 120, 40                       # ignore after / before each switch
LAGS = np.arange(-40, 421, 20)                  # camera+pipeline lag search (ms)
LAG_OK = (0, 300)                               # plausible genuine lag; calibrate per device
MIN_FR_SLOT, MIN_COLOUR_SLOTS = 2, 5
SNR_MIN, A_MIN = 3.0, 0.012
SCORE_PASS, SCORE_REVIEW, P_MAX = 0.75, 0.50, 0.01
N_NULL = 1000
_x = np.arange(256) / 255.0
LUT = np.where(_x <= 0.04045, _x / 12.92, ((_x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _quad(shape, c, u, v, d, u0, u1, v0, v1):
    """Mask of the rotated rectangle u in [u0,u1]*d, v in [v0,v1] (pixels) around c."""
    pts = [c + a * d * u + b * v for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, np.round(pts).astype(np.int32), 1)
    return m.astype(bool)


def rois(shape, lm):
    """lm = dict(box=(x,y,w,h), pts=5x2 [eye_r, eye_l, nose, mouth_r, mouth_l]) (YuNet order)."""
    e1, e2, n, m1, m2 = np.asarray(lm["pts"], float)
    if e1[0] > e2[0]: e1, e2, m1, m2 = e2, e1, m2, m1   # e1 = image-left eye
    c = (e1 + e2) / 2; d = np.linalg.norm(e2 - e1); u = (e2 - e1) / d
    v = np.array([-u[1], u[0]]); v = v if (n - c) @ v > 0 else -v
    vm = max(((m1 + m2) / 2 - c) @ v, 0.8 * d)           # eye line -> mouth line (px)
    fore = _quad(shape, c, u, v, d, -0.40, 0.40, -1.00 * d, -0.55 * d)
    chk_l = _quad(shape, c, u, v, d, -0.72, -0.30, 0.33 * vm, 0.72 * vm)
    chk_r = _quad(shape, c, u, v, d, 0.30, 0.72, 0.33 * vm, 0.72 * vm)
    x, y, w, h = lm["box"]; H, W = shape[:2]; cx, cy = x + w / 2, y + h / 2
    bg = np.zeros((H, W), bool)
    bg[int(0.03 * H):int(0.55 * H), int(0.03 * W):int(0.97 * W)] = True
    bg[max(0, int(cy - 0.95 * h)):int(cy + 0.95 * h) + 1, max(0, int(cx - 0.95 * w)):int(cx + 0.95 * w) + 1] = False
    return [fore, chk_l, chk_r], bg, d


def _agg(lin, enc, mask, min_px):
    ok = mask & (enc.max(2) < 250) & (enc.max(2) > 20)          # no clipped / dark pixels
    if ok.sum() < min_px: return None, int(ok.sum())
    px = lin[ok]; lum = px.sum(1); lo, hi = np.percentile(lum, [10, 90])
    px = px[(lum >= lo) & (lum <= hi)]                          # trim specular / hair
    return px.mean(0), int(ok.sum())


def frame_feature(bgr, lm):
    enc = bgr[..., ::-1]; lin = LUT[enc]
    skins, bg, d = rois(bgr.shape, lm)
    if d < 40: return None
    F, nF = _agg(lin, enc, np.logical_or.reduce(skins), 600)
    B, nB = _agg(lin, enc, bg, 2000)
    if F is None: return None
    zf = np.log(F) - np.log(F).mean()                           # face log-chromaticity
    zb = None if B is None else np.log(B) - np.log(B).mean()
    return zf, zb, nF, nB


def check(frames, colour_log, challenge, landmarks_fn):
    """frames [(t_ms, bgr)], colour_log [{rgb, t_switch}], challenge {slots, dur_ms}."""
    flags = []
    names = [c["rgb"] for c in colour_log]
    if names != list(challenge["slots"]):
        return {"verdict": "fail", "score": 0.0, "flags": ["colour_log_mismatch"]}
    ts = np.array([c["t_switch"] for c in colour_log], float)
    plan = np.asarray(challenge["dur_ms"], float)
    edges = np.r_[ts, ts[-1] + plan[-1]]
    if np.abs(np.diff(edges) - plan).max() > 100: flags.append("slot_timing_jitter")
    # --- per-frame features (landmarks carried forward <=250 ms if a detection drops) ---
    t, ZF, ZB, last = [], [], [], None
    for tf, img in frames:
        lm = landmarks_fn(img)
        if lm is not None: last = (tf, lm)
        elif last is None or tf - last[0] > 250: continue
        f = frame_feature(img, last[1])
        if f is None: continue
        t.append(tf); ZF.append(f[0]); ZB.append(f[1])
    if len(t) < 20:
        return {"verdict": "insufficient", "score": 0.0, "flags": flags + ["few_face_frames"]}
    t = np.array(t); ZF = np.array(ZF); hasb = np.array([z is not None for z in ZB])
    if hasb.mean() >= 0.9:                                      # primary: face chroma - bg chroma
        t, Z = t[hasb], ZF[hasb] - np.array([z for z in ZB if z is not None])
    else:                                                       # fallback: face-only chroma
        Z = ZF; flags.append("no_bg_reference")
    slots = list(challenge["slots"]); ns = len(slots)
    lab = np.array([CH.get(s, -1) for s in slots])              # -1 = guard (K)
    col_slots = np.where(lab >= 0)[0]
    rng = np.random.default_rng(0)
    null_lab = rng.integers(0, 3, (N_NULL, len(col_slots)))     # random challenges, same timing

    def align(L, guard=True):
        tau = t - L
        k = np.clip(np.searchsorted(edges, tau, "right") - 1, 0, ns - 1)
        ok = (tau >= edges[0]) & (tau < edges[-1])
        if guard: ok &= (tau - edges[k] >= POST_MS) & (edges[k + 1] - tau >= PRE_MS)
        med = {s: np.median(Z[ok & (k == s)], 0) for s in range(ns) if (ok & (k == s)).sum() >= 1}
        kc = [s for s in range(ns) if lab[s] < 0 and s in med]
        if not kc: return None
        mid = (edges[:-1] + edges[1:]) / 2
        base = np.stack([np.interp(tau, mid[kc], [med[s][c] for s in kc]) for c in range(3)], 1)
        return k, ok, Z - base, med

    def corr(T, M):                                            # mean per-channel Pearson
        T = T - T.mean(-2, keepdims=True); M = M - M.mean(0)
        num = (T * M).sum(-2); den = np.sqrt((T ** 2).sum(-2) * (M ** 2).sum(0)) + 1e-12
        return (num / den).mean(-1)

    def template(lb):                                          # one-hot - 1/3 ; guard -> 0
        return np.eye(3)[np.maximum(lb, 0)] * (lb >= 0)[..., None] - (lb >= 0)[..., None] / 3

    best, null_best = (-2, None), np.full(N_NULL, -2.0)
    full = np.full((N_NULL, ns), -1); full[:, col_slots] = null_lab
    for L in LAGS:
        a = align(L)
        if a is None: continue
        k, ok, M, _ = a
        kk, Mo = k[ok], M[ok]
        r = corr(template(lab[kk]), Mo)
        if r > best[0]: best = (r, L)
        null_best = np.maximum(null_best, np.nan_to_num(corr(template(full[:, kk]), Mo), nan=0.0))
    rho, L = best
    if L is None:
        return {"verdict": "insufficient", "score": 0.0, "flags": flags + ["no_guard_slot"]}

    def rho_all(Lx):                                           # lag estimate WITHOUT guards (sharper)
        a = align(Lx, guard=False)
        if a is None: return -2
        k2, ok2, M2, _ = a
        return corr(template(lab[k2[ok2]]), M2[ok2])
    lag_ms = int(LAGS[int(np.argmax([rho_all(Lx) for Lx in LAGS]))])
    p = (1 + (null_best >= rho).sum()) / (1 + N_NULL)
    k, ok, M, med = align(L)
    per_seg, dvec, correct = [], [], 0
    for s in col_slots:
        sel = ok & (k == s)
        if sel.sum() < MIN_FR_SLOT:
            per_seg.append({"slot": int(s), "colour": slots[s], "n": int(sel.sum()), "ok": None}); continue
        d = np.median(M[sel], 0); pred = int(np.argmax(d)); dvec.append(d)
        correct += pred == lab[s]
        per_seg.append({"slot": int(s), "colour": slots[s], "n": int(sel.sum()),
                        "pred": ["red", "green", "blue"][pred], "d": np.round(d, 4).tolist(),
                        "ok": bool(pred == lab[s])})
    nval = len(dvec)
    resid = np.concatenate([Z[ok & (k == s)] - med[s] for s in med]) if med else np.zeros((1, 3))
    sigma = float(np.median(1.4826 * np.median(np.abs(resid - np.median(resid, 0)), 0))) + 1e-6
    A = float(np.median([np.linalg.norm(d) for d in dvec])) if dvec else 0.0
    snr = A / sigma
    acc = correct / nval if nval else 0.0
    score = 0.5 * np.clip((acc - 1 / 3) / (2 / 3), 0, 1) + 0.5 * np.clip(rho / 0.8, 0, 1)
    if p > 0.05: score = min(score, 0.45)
    lag_bad = not (LAG_OK[0] <= lag_ms <= LAG_OK[1])
    if lag_bad: flags.append("lag_out_of_range")
    if nval < MIN_COLOUR_SLOTS: verdict = "insufficient"; flags.append("few_valid_slots")
    elif score >= SCORE_PASS and p <= P_MAX: verdict = "pass"
    elif snr < SNR_MIN or A < A_MIN: verdict = "insufficient"; flags.append("low_snr")
    elif score >= SCORE_REVIEW: verdict = "review"
    else: verdict = "fail"
    if verdict == "pass" and lag_bad: verdict = "review"        # right colours, suspicious delay
    return {"verdict": verdict, "ok": verdict == "pass", "score": round(float(score), 3),
            "rho": round(float(rho), 3), "acc": round(acc, 3), "p": round(float(p), 4),
            "lag_ms": lag_ms, "lag_fit_ms": int(L), "snr": round(snr, 2), "amp": round(A, 4),
            "sigma": round(sigma, 4), "per_segment": per_seg, "flags": flags}


def mint(rng=None):
    """Default challenge: grey lead-in, 8 body slots (R,G,B x2 + 2 grey), grey tail."""
    import secrets
    r = secrets.SystemRandom() if rng is None else rng
    while True:
        body = ["red", "red", "green", "green", "blue", "blue", GUARD, GUARD]
        r.shuffle(body)
        seq = [GUARD] + body + [GUARD]
        if all(a != b for a, b in zip(seq, seq[1:])): break
    dur = [700] + [r.randint(380, 560) for _ in body] + [400]
    return {"slots": seq, "dur_ms": dur}
```

Integration notes:
- Frames arrive as a part (e.g. `light_frames`, `l0001.jpg`) plus `meta.light.frames[{file,t,captureTime?}]` and
  `meta.colour[{rgb, t_raf, t_painted}]`. Use `t = captureTime or t`, and `t_switch = t_painted or t_raf`.
- YuNet wrapper: reuse `face_match._load()` / `_prep()` (≤ 1024 px means no rescale at 320 px), but return all 5 points.
  Note `face_match._orientations`: the frames come from a canvas grab, so they should already be upright.
- The per-slot `d` vectors and the ρ(L) curve are good material for the analyst dashboard (an expected-vs-measured colour strip).

## Appendix: synthetic harness (how the sim numbers were produced)

A 320×427 frame, an elliptical face with skin albedo (.55,.38,.28), a background albedo (.35,.33,.30), ambient
(.8,.75,.6)×U(.7,1.3). Screen irradiance k·colour, with small subpixel crosstalk, delayed by `lat`. The background gets k/30. AE
chases face luminance (gain step 35 %/frame), AWB chases grey-world (20 %/frame). Face-only luminance jitter is 3 %, per-channel
jitter 0.4 %, sensor noise σ=.004, vignetting, sRGB encode, JPEG q70, 12 fps ± 8 ms, colour-log jitter 0–12 ms. The
"replay" condition renders the response to a *different* minted sequence. "Static" uses k=0. The "relight deepfake" condition
has k=.25 with an extra delay. It is a model, not a camera. The main gaps are real local tone mapping, motion blur, and JPEG at
real skin texture.

## Sources

- Tang et al., *Face Flashing: a Secure Liveness Detection Protocol based on Light Reflections*, NDSS 2018,
  arXiv:1801.01949. 8 random colours, ~3 s default. Timing verification uses rolling-shutter / screen-refresh at 60 fps,
  which needs native camera control and is not reproducible in a browser. AWB **disabled** natively ("we cannot use AWB").
  Saturated pixels removed, normalised by the background-challenge response. 98.8 % accuracy. Sunlight impact "ignorable"
  unless facing the sun; dark at 97.3 %. https://arxiv.org/abs/1801.01949
- Gerstner & Farid, *Detecting Real-Time Deep-Fake Videos Using Active Illumination*, CVPRW 2022, pp. 53–60.
  `H(t) = 0.1307·cos(t/8)`, t∈[0,16], S=V=1 (isoluminant yellow↔magenta, chosen to avoid AWB). Dlib face ellipse. Pixels
  divided by the pre-illumination mean face colour, then HSV, then circular mean hue, then circular Pearson. Real world: 15 users,
  24″ away, ρ 0.93 at a 13″ light down to 0.33 at 3″. No-illumination baseline mean 0.09, max 0.34. Lag of 1/2/3/4 frames at
  30 Hz gives 0.83/0.65/0.37/0.03. Recommend random blank frames. Avatarify flatlines.
  https://openaccess.thecvf.com/content/CVPR2022W/WMF/papers/Gerstner_Detecting_Real-Time_Deep-Fake_Videos_Using_Active_Illumination_CVPRW_2022_paper.pdf
- Farrukh et al., *FaceRevelio*, MobiCom 2020. Four screen quarters, random patterns low-passed at 3 Hz and made orthogonal and
  zero-mean (Gram–Schmidt), so ambient and base cancel in least squares. Manual ISO (Camera2), inverse gamma, grey-128 base,
  DTW alignment. Human ρ > 0.85 in 99.9 %. Video attacks < 0.84 (2 s passcode). EER 1.4 % (dark) / 0.3 % daylight (video). 200–5000 lx
  daylight, 20–40 cm. https://habiba-farrukh.github.io/files/FaceRevelio.pdf
- W3C, *Understanding SC 2.3.1 Three Flashes or Below Threshold*.
  https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html
- Related (not fetched in detail): Shang & Wu ICDCS 2020 (white/black at 0.2 Hz, brightness correlation), LiveScreen INFOCOM 2020
  (94.8 % / 1.6 %), and the iProov Flashmark patent family. The positioning is fusion, not the primitive.

## Uncertainty flags

1. **No real-phone data yet.** All ρ/SNR ranges for genuine captures are estimates from the papers + physics + simulation.
2. Genuine pipeline lag (`LAG_OK`) depends on the timestamp source (rVFC `captureTime` vs callback `now` vs setTimeout
   grab) and on iOS vs Android. It must be measured. `captureTime` availability on iOS Safari is uncertain.
3. The background-reference assumption fails if the background is < 50 cm away (it attenuates, does not flip), if it is a window
   (clipped, so the fallback is used), or if it changes (someone walks by). This is not detected beyond the pixel minimums.
4. Local tone mapping (iOS Smart HDR on video) is spatially varying. Chromaticity cancels its luminance part, not any colour part.
5. The p-value's null assumes independence of the colour labels. With only 6 colour slots the p-floor is ≈ 0.002, which is fine
   for a 0.01 gate.
6. The design does **not** stop an attacker who controls the client and does real-time face tinting, or a colour-transfer deepfake
   with < ~150 ms extra delay. Say so in the pitch.
