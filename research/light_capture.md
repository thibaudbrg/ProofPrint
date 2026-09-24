# Check 5 "light pulse": phone-side capture design (research notes)

Scope: the browser side only (iOS Safari + Android Chrome). This covers flashing a server-minted colour
sequence full-screen, recording the front camera, putting every timestamp on one clock (`performance.now()`)
and uploading frames plus a switch log. Server-side verification is only mentioned where it drives a client
decision.

Legend: **[src]** = from a cited source, **[calc]** = my own arithmetic, **[est]** = engineering estimate
that has NOT been measured. Validate every [est] on the two demo phones before trusting it.

---

## 0. TL;DR parameters

| Item | Recommendation |
|---|---|
| Flash surface | one `position:fixed; inset:0` div, `transition:none`, `z-index` above everything; also set `<html>` background (safe-area and overscroll). Do not use a canvas. |
| Colours | alphabet {red `#f00`, green `#0f0`, blue `#00f`, black `#000`} at full saturation; optional white for an SNR probe. No isoluminant hues. |
| Sequence | 500 ms mid-grey lead-in, then 6–7 segments of 400–600 ms each (jittered), no immediate repeats, each of R/G/B at least once, 1–2 blacks at random positions. Total about 3.5–4 s, then keep recording 300 ms more. |
| Switch timing | rAF-driven. Switch on the first rAF with `ts >= t_planned - 4 ms`. Log `t_raf` (that callback's ts) and `t_painted` (the NEXT rAF ts). Schedule from the plan, not from `ts`, so there is no drift. |
| Frame grab | rVFC-driven when available (Safari ≥15.4, Chrome ≥83), fall back to the existing setTimeout loop at 40 ms. Log `t`, `presentationTime`, `expectedDisplayTime`, `captureTime` (if present), `mediaTime`, `presentedFrames`. |
| Resolution / rate / JPEG | 320 px wide, about 24–30 fps (every camera frame, min gap 30 ms), JPEG **q = 0.9**. That is about 100–120 frames at ~12–20 KB each, so ~1.5–2 MB. 12 fps is enough for colour means but too coarse for timing checks. |
| Preview | hide the full preview. Show a small (~25 vw) mirrored thumbnail right under the front camera, plus a small hint and a progress bar. Keep the `<video>` source element alive (it is already 1 px, opacity 0). |
| Safety | ≤ 2.5 transitions/s (min segment 400 ms, so ≤ 1.25 flashes/s, well under WCAG's 3). Red segments ≥ 500 ms and never red↔black adjacent. Show a warning and a **Skip** button before the flash, and honour `prefers-reduced-motion`. |
| Challenge | minted just-in-time by `POST /session/{id}/light` when the user taps Start. TTL about 15 s, single use. |
| Hopeless | when the estimated screen/ambient illuminance ratio at the face is < about 0.05–0.08 (outdoors, by a sunny window), return **inconclusive → step-up**, never "fail". |

---

## 1. Maximising reflected light

### 1.1 Div vs canvas
- A full-viewport `div` whose `background-color` changes is the cheapest possible paint. It commits in the same
  frame as the rAF callback that sets it. A 2D canvas `fillRect` gives the same pixels plus a canvas upload.
  WebGL adds nothing. **Use the div.** Set `transition:none` (a CSS transition would fade the colour and blur
  the step).
- Cover everything: `position:fixed; inset:0; z-index:9999`. The page already has
  `viewport-fit=cover`, so the div can paint under the notch and home indicator. Also set
  `document.documentElement.style.background` to the same colour so rubber-band overscroll never shows white.
- iPhone cannot go fullscreen, so Safari's own toolbar strip stays uncoloured. As a best effort, update
  `<meta name="theme-color">` at each switch. Safari has tinted its chrome from `theme-color` since iOS 15, but
  the behaviour changed across iOS versions **[uncertain]**. The strip is small, so this is not critical.
- Everything else on screen during the flash (thumbnail, hint text, bar) is emitting light that is NOT the
  challenge colour. Keep it at ≤ 5 % of the screen area.

### 1.2 Which colours
Model (Lambertian, linear camera, per channel c): `I_c = ρ_c · (E_amb,c + E_scr,c · s_c) · g_c`. Here `ρ` is skin
albedo, `s_c` is the screen drive of primary c, and `g_c` is the camera's exposure × white-balance gain.
- **Full-saturation primaries give the largest per-channel swing.** Red drives only the R channel, green only G,
  blue only B, so the face's chromaticity (log R/G, log B/G) jumps the most between primaries. Mixing (white,
  yellow…) spreads the same screen power over channels and lowers the chromatic contrast. Face Flashing uses
  pure colours as its "background challenge" (8 colours in its experiments) **[src: Tang et al.]**.
- The channels are not equally useful on skin:
  - **G**: 2× Bayer pixels, so the best SNR. Skin G reflectance is moderate (haemoglobin absorbs). This is the
    workhorse.
  - **R**: skin reflects red most, so the absolute swing is large. But R is often already high or saturated
    under warm indoor light (saturation removal is needed, **[src: Face Flashing, "saturated pixels"]**).
  - **B**: skin reflects blue least, especially darker skin, and the sensor B channel is the noisiest. Night
    Shift, True Tone and Android "eye comfort" also cut the screen's blue. It is the weakest symbol but still
    useful as a third direction.
- **Black** is the ambient-only reference. `(I_colour − I_black)/I_black = E_scr,c/E_amb,c` is independent of albedo
  ρ (it cancels) **[calc]**. That gives fairness across skin tones in the signal model. Only the noise floor
  depends on ρ.
- **White** gives the maximum total signal (all channels). It is useful as a one-off SNR probe but it hits AE hardest.
- **Isoluminant hues (Gerstner & Farid)**: they modulate hue only (HSV V=S=1, hue between yellow and magenta)
  so that webcam AE does not react. They also found blues and greens triggered AWB but yellow↔magenta did not
  **[src: Gerstner & Farid 2022]**. That design was for a small square on a desktop screen during a video call,
  at 1:1 area-to-ambient light. On a phone at 25 cm we want maximum SNR, and we cancel AE/AWB with ratios
  (face/background, colour/black) instead. Also note that HSV S=V=1 is not really isoluminant: yellow has
  Y≈0.93 and magenta Y≈0.28. **Don't use isoluminant hues. Use primaries and cancel gains analytically.**

### 1.3 Photometric budget
Screen as a Lambertian disc of luminance L and area A (6.1" phone ≈ 7.1×15.4 cm ≈ 109 cm², equivalent radius
a ≈ 5.9 cm). On-axis illuminance is `E = π·L·a²/(a²+d²)` **[calc]**:

| L (nits, full white) | 20 cm | 25 cm | 30 cm | 40 cm |
|---|---|---|---|---|
| 200 (auto-brightness, dim room) | 50 lx | 33 lx | 23 lx | 13 lx |
| 400 (typical indoor) | 100 lx | 66 lx | 47 lx | 27 lx |
| 800 (brightness at max, SDR) | 200 lx | 132 lx | 93 lx | 53 lx |

- A pure primary puts roughly that channel's share of white into the matching camera channel. So in camera
  terms each channel sees about "white-level" illuminance in its own channel, but only that channel.
- We cannot set brightness from the web. With auto-brightness on, phones indoors typically sit at about
  150–400 nits **[est]**. The 1000+ nit figures are the max SDR or outdoor boost, not what you get indoors.
- Ambient light at the face (vertical illuminance) **[est, typical]**: dim room 10–50 lx, living room or office
  100–300 lx (desk horizontal 300–500 lx), near a window 500–2 000 lx, overcast outdoors 1 000–10 000 lx,
  sun 30 000–100 000 lx.
- Ratio f = E_scr/E_amb at 25 cm and ~400 nits: dim room > 1 (big signal but AE/AWB fight and pixels saturate,
  and Face Flashing calls darkness "a devil"); office 0.2–0.7 (good); window 0.03–0.13 (marginal); outdoors
  ≤ 0.07, and ≤ 0.002 in sun (hopeless).
- Expected swing in 8-bit sRGB DN: roughly `ΔDN ≈ γ_slope(≈0.45) · f · DN_face` **[calc, gamma-2.2 approx]**. With
  DN_face ≈ 150: f = 0.3 gives ~20 DN (easy), f = 0.1 gives ~7 DN (OK), f = 0.05 gives ~3 DN (at the level of
  AE/AWB drift, motion and JPEG quantisation), and f = 0.01 gives < 1 DN (hopeless).
- Evidence from papers: FaceRevelio worked indoors at ~250 lx and in daylight of 200–5 000 lx, but with
  *manual/locked ISO* on native Android, a grey (128) base, and better results in the dark **[src]**. Face Flashing
  worked in sunlight "as long as the frontal camera does not face the sun directly", with errors "when the
  distance… is far and the environmental illumination is high" **[src]**. Both are native apps with more control
  than the web gives us, so expect worse.
- **When it is hopeless, go to step-up**: the server estimates f (or the fitted amplitude per channel relative to
  the black segments). If max-channel f < ~0.05–0.08 **[est, calibrate]**, verdict = `inconclusive`, and the UX
  says: "Too bright here for the light check. Move away from the window or sunlight, hold the phone a bit closer
  and turn the screen brightness up." Allow one retry with a *fresh* challenge, then step up to another factor or
  manual review. Never reject on a low-SNR result.
- The client cannot measure lux (the AmbientLightSensor API is not on iOS and is flagged in Chrome). An optional
  quick client-side estimate after the run is fine for UX only: centre-ROI mean of the black vs colour frames on a
  64 px copy.

## 2. Timing mechanics

### 2.1 Switching colours: rAF, not setTimeout
- A style change only reaches the glass at the next rendering opportunity (vsync). `setTimeout` fires at an
  arbitrary point in the frame (it is clamped, throttled, and the main thread may be busy with `toBlob`). You
  get up to one vsync of unknown extra delay and **no way to know when it painted**.
- **rAF**: the callback's `ts` argument is the frame's start time on the `performance.now()` clock. A style set
  inside the callback for frame N is rendered in frame N and presented at about the next vsync. The **next** rAF
  callback's `ts` (frame N+1) is the best web-visible estimate of "the new colour is now on screen". Actual
  photons arrive a little later (top→bottom scan-out ≈ one refresh, plus panel response; OLED is ≪1 ms). Chrome
  Android can pipeline one more frame. Both are small constant offsets that the server's lag fit absorbs.
- Log per switch: `t_planned`, `t_raf` (ts of the callback that set the colour), `t_painted` (ts of the next
  callback). If `t_painted − t_raf > ~25 ms`, a frame was dropped (jank). Count it.
- Schedule from the plan (`tNext += ms`) so that jitter does not accumulate. Switch when `ts >= tNext − 4 ms`
  (half a 120 Hz frame, well under a 60 Hz frame).
- Caveats **[src]**: Safari caps rAF at 60 Hz (even on ProMotion), iOS Low Power Mode throttles rAF to 30 fps,
  and cross-origin iframes are throttled to 30 fps before interaction. At 30 fps the switch granularity is
  33 ms. That is still fine for 400+ ms segments. Log the measured median rAF interval.

### 2.2 Grabbing frames: rVFC for this phase
- The existing loop runs `setTimeout(everyMs)` → `drawImage(video)` → `t = performance.now()`. Problems for
  check 5:
  1. `t` is the grab time, not the capture time. The camera→`<video>` pipeline adds about 30–150 ms **[est]**,
     and it can vary.
  2. It can grab the same camera frame twice or skip frames, and you cannot tell which.
  3. The 0.72 JPEG quality hurts chroma (see §3).
- **requestVideoFrameCallback** fires once per frame presented to the compositor and passes metadata:
  `presentationTime`, `expectedDisplayTime`, `mediaTime`, `presentedFrames` (gaps reveal skipped frames). It
  also passes `captureTime`, which the spec says is "the time at which the frame was captured by the camera" for
  local sources and SHOULD be present for getUserMedia **[src: WICG spec]**. All times are DOMHighResTimeStamp,
  the same clock as rAF and `performance.now()`. Callbacks are best effort: they can be one vsync late and can
  skip frames under load **[src: web.dev]**.
- Support: Chrome/Android since M83, Safari desktop and iOS **15.4+** **[src: caniuse]**. The page already uses
  rVFC on the 1 px source video to measure `frameIntervals`, so it works on that element.
  `captureTime` presence on iOS Safari for gUM tracks is **[uncertain]**. Log it when present and treat it as a
  bonus.
- **Recommendation**: for the light phase only, use an rVFC-driven grabber with the same signature as
  `grabFrames`. It calls `drawImage` inside the callback, where the video's current frame is the one the
  metadata describes. Fall back to the setTimeout loop (40 ms) when `requestVideoFrameCallback` is missing.
- **In both modes the server must fit the lag.** It cross-correlates the face-minus-background colour signal
  with the painted step function over lags 0–300 ms (or fits per switch). It then requires:
  - lag within a plausible window, about 30–250 ms **[est, calibrate per device]**;
  - small lag spread across switches, σ ≲ 35 ms **[est]**.
  A constant pipeline offset is harmless. Only consistency and plausibility matter. This is Face Flashing's
  "timing verification" transplanted to web precision **[src]**.
- Rolling shutter: a frame whose readout straddles a switch shows the old colour at the top and the new colour
  at the bottom. Face Flashing exploits exactly this **[src]**. Don't drop those frames client-side. They
  localise the switch within a frame.

## 3. Resolution, rate and JPEG
- **Spatial**: per-frame skin means average hundreds of pixels. At 320×240 the face is about 120–180 px wide, so
  a cheek or forehead ROI is ~25×25 px ≈ 600 px and the mean's noise is ≈ σ_pixel/√600 ≈ 0.1–0.2 DN **[calc]**.
  320 px is plenty. 240 px would also do. Keep 320 so the server's face detector (YuNet) and the other checks
  behave the same.
- **Temporal**: 12 fps (83 ms) gives 4–6 samples per 400–600 ms segment. That is enough for colour means, but it
  quantises the lag fit to ±40 ms, which is about the size of the effect we want to verify. Use **every camera
  frame (≈30 fps, min gap 30 ms)** for this ~4 s phase. The fallback at 40 ms gives ~25 fps. If upload size
  matters, 15 fps is the floor.
- **JPEG**: Chrome's canvas encoder is Skia/libjpeg-turbo with **4:2:0 chroma subsampling by default**
  **[src: SkJpegEncoder.h, `fDownsample = k420`]**. Safari uses ImageIO (subsampling not documented, assume 4:2:0).
  - Subsampling averages chroma over 2×2 blocks. For an ROI *mean* that is almost harmless (averaging commutes),
    but chroma bleeds ±2 px across ROI borders. So shrink skin ROIs by ≥ 4 px from face edges, hair and eyes.
  - **Quantisation is the real issue.** Chroma DC step ≈ (chroma table 17) × quality scale. At q = 0.72 the
    scale is 56 %, so the step is ~9.5, which is ~1.2 DN of block-mean chroma. At q = 0.9 the scale is 20 %, so
    the step is ~3.4, which is ~0.4 DN **[calc, IJG scaling]**. Our marginal signals are 3–7 DN, so q = 0.72
    throws away a noticeable fraction. **Use q = 0.9 for light frames** (about 12–20 KB each at 320×240 **[est]**).
    Add a `quality` parameter to `grabFrames`; it is hard-coded to 0.72 today.
  - Alternative: PNG is lossless but ~5–10× larger and slower to encode on phones. Not worth it.
- Upload budget: ~110 frames × ~16 KB ≈ 1.8 MB **[est]**. Acceptable for a demo on venue Wi-Fi.
- Colour pipeline: `drawImage(video)` onto a default (sRGB) 2D canvas. Colour-space conversions are the same for
  every frame, so relative measures are unaffected. Log `settings` (already done) for the analyst.

## 4. UX during the flash

- **Preview**: a full-screen live preview would itself light the face with the user's own (skin-coloured) image
  and fight the challenge. Hide the big preview. Show a **small mirrored thumbnail (~25 vw, ≤5 % area) at the
  top-centre, right under the front camera**, with a tiny oval outline. The user's gaze then lands near the lens
  (frontal face, specular highlights consistent) and they can still self-correct framing. Draw it from the
  painter's rAF loop.
- Keep the `<video>` source element as it is (1 px, opacity 0). Don't `display:none` it. WebKit pauses
  non-visible autoplay videos under some policies **[src: WebKit video policies]** and the grabber needs live
  frames. The page already grabs from this element successfully.
- **Before (instruction screen)**. Title: "Quick light check". Steps:
  1. "Hold the phone about a hand-span (20–25 cm) from your face, straight in front of you."
  2. "Look at the screen and keep still. It will show a few colours for about 4 seconds."
  3. "Tip: turn your screen brightness up and avoid direct sunlight."
  Warning line: "This step shows flashing colours. If flashing lights affect you, skip it." Buttons: **Start** /
  **Skip this step**. If `prefers-reduced-motion: reduce` is set, show the skip option prominently (optionally
  pre-select it).
- **During**: small hint "Keep still · look at the screen · 3", with the countdown from the planned total. Use
  white text with a dark shadow so it stays legible on every colour. Thin progress bar. No other animation.
- **After**: return immediately to the normal UI. If client checks fail (tab hidden, big jank, too few frames),
  show "Let's try that once more", mint a fresh challenge and rerun once.
- **Duration**: FaceRevelio used 1–3 s passcodes, and 1 s already worked but its TAR dropped at 1 s **[src]**.
  Face Flashing uses 3 s by default and says "1 second is enough… but the user will be in a hurry" **[src]**.
  iProov says "look at the screen for 2.5 seconds" **[src]**. **Ours: 0.5 s lead-in + ~3.2 s sequence + 0.3 s
  tail ≈ 4 s.**
- **Photosensitive epilepsy (WCAG 2.3.1)** **[src: W3C Understanding 2.3.1]**:
  - Nothing may flash more than 3 times in any 1 s period. A flash is a *pair of opposing* relative-luminance
    changes of ≥ 10 % (darker state < 0.80) over an area > 25 % of a 10° field. A full-screen change always
    exceeds the area limit, and R↔G (Y 0.21↔0.72), anything↔black, and B↔G are all ≥ 10 % changes. So **every
    switch counts as half a flash**.
  - Red flash: any pair of transitions involving a saturated red (R/(R+G+B) ≥ 0.8, and the change is > 0.2 in
    CIE 1976 u′v′). Pure `#f00` qualifies.
  - With min segment 400 ms there are ≤ 3 transitions in any 1 s window, so ≤ 1.5 flashes/s. That is compliant
    with margin. iProov locks Flashmark at < 2.86 flashes/s **[src]**.
  - Extra caution: red segments ≥ 500 ms, no red↔black adjacency (the largest red-contrast pair), at most one red
    per 1 s window, and no patterns or stripes (uniform fields only). Provide the opt-out. Opting out means the
    check is marked `opted_out` and the flow goes to step-up, with no penalty.

## 5. AE / AWB behaviour and mitigation

What to expect **[est unless marked]**:
- The web gives no control on iOS. Safari exposes no exposure or white-balance capabilities
  **[src: webrtcHacks]**.
- Android Chrome implements `exposureMode`, `exposureTime`, `whiteBalanceMode`, `colorTemperature`, `iso` and
  `exposureCompensation` in the Image Capture spec **[src: W3C implementation status]**. In practice the
  capability set depends on the device and camera HAL. `exposureMode` and `exposureTime` must be applied in two
  separate `applyConstraints` calls **[src: webrtcHacks]**. Many front cameras report only
  `continuous`/`manual` for some controls, or nothing.
  - **Use:** at the start of the light phase, if `track.getCapabilities().exposureMode` includes `"manual"`, apply
    it (and `whiteBalanceMode:"manual"` if offered). Record what was actually applied in `meta.light.cam_lock`.
  - **Restore** `continuous` afterwards (check 4 and check 6 need AE).
  - **Never rely on the lock.** Treat it as a bonus. Its reliability on front cameras is **[uncertain]**.
  - A reasonable alternative that is more widely supported is `exposureCompensation` plus leaving AE on.
- AE on iPhone front cameras is face-driven (it meters on the detected face). A black↔white step of the face's
  illumination is corrected over roughly 0.3–1 s, with damping. So the luminance component of the signal decays
  within a segment, and frames late in a segment are brighter or darker than early ones.
- AWB is slower (~1–3 s) and constrained toward the daylight/Planckian locus. Strongly off-locus casts (green,
  magenta) are partly "not believed" and corrected less. Blue/green casts do trigger AWB in webcams
  **[src: Gerstner & Farid]**. When the screen dominates (dark room, f > 1), expect AWB to drift toward
  neutralising the sequence's *average* colour.
- Android 3A is vendor-specific. Convergence is typically 5–15 frames (~0.2–0.5 s at 30 fps).

Mitigations built into the capture design:
1. **Short segments (400–600 ms)**: shorter than the AWB response and comparable to the AE response. Measure
   each segment's mean over its settled part, taking the first frames after (painted + lag) and not waiting
   for convergence.
2. **Balanced sequence**: R, G and B each appear, with equal total time per primary where possible. AWB and AE
   then see a roughly stationary mean colour and luminance, so they drift less.
3. **Black segments at random positions**: these are ambient-only references. Use (colour − nearest black) per
   channel. This is the additive model that cancels ambient light. FaceRevelio does the same with zero-mean
   patterns, and Face Flashing with a "background challenge" **[src]**.
4. **Face ÷ background ratio**: the camera's exposure and white-balance gains `g_c` are global per channel. So
   `I_face,c / I_bg,c` cancels them exactly in the linear model, and for any pure power-law tone curve.
   - The background (a wall 1–3 m away) receives ~1/(10…100) of the screen light (inverse square).
   - This is also the core anti-replay feature. A flat replay screen or photo held at 25 cm shows the same
     response on "face" and "background". A naive colour overlay tints everything.
   - Caveat: local tone mapping (iPhone/Pixel) breaks the global-gain assumption. Keep ROIs away from bright
     windows and use rank or correlation tests, not absolute amplitudes.
5. **Random order and random durations**: the attacker cannot pre-render, and AE/AWB cannot "lock into" a
   periodic pattern.
6. **Grey lead-in (500 ms)**: gives AE a stable start from the UI state and provides a baseline. Record frames
   through it.
7. **No motion**: run this phase while the phone is held still (not during check 4's tilt). Keep
   `recordingMotion` on so the server can drop high-motion frames.
8. Night Shift, True Tone and Android eye-comfort shift the screen's white point and reduce blue. We cannot detect
   these from the web **[uncertain]**. So the server should score direction and consistency per primary, not
   absolute chromaticity.

## 6. Upload contract proposal

### 6.1 Mint (just-in-time)
`POST /session/{id}/light` is called when the user taps **Start** on the light instruction screen. Today the
challenge is minted at `/session` (Begin). That is ~20 s early and should be replaced.
```json
{ "challenge_id": "lc_7f3a9c", "nonce": "q8Zp2w…", "expires_in_ms": 15000,
  "lead_in": { "name": "grey", "rgb": [110,110,110], "ms": 500 },
  "sequence": [ { "name": "green", "rgb": [0,255,0],   "ms": 470 },
                { "name": "black", "rgb": [0,0,0],     "ms": 430 },
                { "name": "red",   "rgb": [255,0,0],   "ms": 560 },
                { "name": "blue",  "rgb": [0,0,255],   "ms": 410 },
                { "name": "green", "rgb": [0,255,0],   "ms": 520 },
                { "name": "black", "rgb": [0,0,0],     "ms": 450 },
                { "name": "red",   "rgb": [255,0,0],   "ms": 500 } ],
  "tail_ms": 300 }
```
Server stores `{challenge_id, nonce, sequence, t_mint_server}` and makes it single-use. It rejects an upload when
`server_now − t_mint_server > sum(ms) + ~10 s`, or when the challenge_id is reused.

### 6.2 `meta.light` (replaces the reserved `meta.colour`)
Frames go as repeated multipart parts `light_frames` named `l0001.jpg`…
```json
"light": {
  "enabled": true, "opted_out": false,
  "challenge_id": "lc_7f3a9c", "nonce": "q8Zp2w…",
  "t_received": 20512.3,
  "switches": [
    { "i": 0, "name": "grey",  "rgb": [110,110,110], "ms": 500, "t_planned": 20540.0, "t_raf": 20540.1, "t_painted": 20556.8 },
    { "i": 1, "name": "green", "rgb": [0,255,0],     "ms": 470, "t_planned": 21040.0, "t_raf": 21040.2, "t_painted": 21056.9 },
    { "i": 8, "name": "end",   "rgb": null,           "ms": 0,   "t_planned": 23880.0, "t_raf": 23881.0, "t_painted": 23897.7 }
  ],
  "frames": [ { "file": "l0001.jpg", "t": 20561.2, "t_pres": 20560.9, "t_exp": 20577.5,
                "t_cap": 20470.4, "media_t": 12.345, "n": 611 } ],
  "grab":    { "mode": "rvfc", "width": 320, "height": 240, "jpeg_q": 0.9, "min_gap_ms": 30 },
  "raf":     { "median_ms": 16.7, "jank": 0 },
  "display": { "dpr": 3, "vw": 390, "vh": 844, "reduced_motion": false, "hdr": false },
  "thumb":   { "x": 146, "y": 40, "w": 98, "h": 130 },
  "cam_lock":{ "exposure": "none", "white_balance": "none" },
  "hidden": false,
  "client_checks": { "all_painted": true, "order_ok": true, "max_switch_err_ms": 4.1,
                     "max_dur_err_ms": 17.0, "min_frames_per_seg": 9, "ok": true }
}
```
`t_cap` is null when the browser does not provide it. All `t*` are `performance.now()` ms, the same clock as
`meta.frames`, `meta.motion` and `meta.events`. Add `events: light_start / light_end`.

The server judges against **its stored** sequence, never the echoed `rgb`. The echo is for the analyst dashboard
and for detecting a tampered client.

### 6.3 Client-side checks
These are all advisory. The client is untrusted, so the checks only exist to retry honest users early.
- Every planned segment has a `t_painted`, in order. `|t_raf − t_planned| ≤ 34 ms` and each painted duration is
  within ±34 ms of `ms`. Otherwise it is jank: retry once.
- `document.visibilityState` stayed `visible` (listen to `visibilitychange`). No resize or orientation change.
- There are at least 3 frames per segment whose `t` falls in `[t_painted_i + 150, t_painted_{i+1} + 50]`
  (nominal lag), and the total frame count is ≥ 70 % of expected.
- **Do not drop frames client-side** after switches. Upload everything and let the server mask
  `[t_painted + lag − exposure − readout, t_painted + lag + 1 frame]` using its *fitted* lag. The transition frames
  carry the timing evidence. For convenience you may tag frames with `seg_guess` / `settled` using a nominal
  120 ms lag.

## 7. Anti-abuse: what the page can and cannot do
- **The page cannot hide the sequence.** The page has to paint each colour, so anyone controlling the device
  can read it at paint time or earlier: JS injection, a hooked `getUserMedia`, an emulator, a rooted phone with
  a virtual camera, or a screen recorder. The full sequence is also in the network response. Obfuscation,
  streaming colours one at a time over WebSocket, or encrypting the challenge only change *when* the attacker
  learns it (at worst: at paint time). They do not change *whether*. Say this openly in the pitch.
- What just-in-time minting plus timing buys:
  1. **No pre-recording or replay.** A recorded video cannot contain an unpredictable sequence minted seconds
     ago (single-use, TTL).
  2. **Real-time relighting is required, with the latency of a real camera.** The injected face must show the
     right colour change, on the face and not on the background, with a natural shading and albedo
     response. It must also arrive within the natural lag window (~30–250 ms, consistent across switches). Face
     Flashing's security argument rests on this "indelible delay" **[src]**.
     Gerstner & Farid measured correlation falling from 0.83 to 0.03 as the synthesis delay went from 1 to 4
     frames at 30 fps **[src]**.
  3. **Physics.** A flat screen or print held in front of the camera reflects the flash uniformly, with specular
     screen glare at 20 cm **[src: Face Flashing]**, and without the face/background falloff.
- What it does NOT stop: a competent attacker who injects frames and applies a face-segmented,
  shading-aware colour transform in < ~100 ms. That is feasible with today's GPUs and simple colour math.
  Position check 5 as raising cost and fusing with checks 4 and 6 (motion-sensor consistency, profile + ID
  occlusion), not as a standalone guarantee. It also overlaps iProov's Flashmark patents (noted in PROJECT.md),
  so pitch it as "fusion".
- Server hygiene: bind challenge_id to session_id, make it single-use, give it a TTL, check the upload arrives
  within `sum(ms) + ~10 s` on the **server** clock, and never trust client `rgb` or `client_checks`.

## 8. Pseudo-code (fits `web/index.html` patterns)

Markup to add (inside `<body>`, outside the screens):
```html
<div id="lightFlash" hidden>
  <canvas id="lightThumb"></canvas>
  <div id="lightHint"></div>
  <div class="progress" id="lightBar"><i></i></div>
</div>
```
```css
#lightFlash{position:fixed;inset:0;z-index:9999;background:#000;transition:none}
#lightThumb{position:absolute;top:calc(env(safe-area-inset-top) + 8px);left:50%;transform:translateX(-50%);
            width:26vw;border-radius:40%;opacity:.95}
#lightHint{position:absolute;left:0;right:0;bottom:calc(env(safe-area-inset-bottom) + 40px);text-align:center;
           color:#fff;font:600 15px system-ui;text-shadow:0 1px 3px #000}
#lightBar{bottom:calc(env(safe-area-inset-bottom) + 24px)}
```
```js
// ---- Check 5: light pulse ----------------------------------------------------------
const L_W = 320, L_Q = 0.9, L_GAP_MS = 30, L_FALLBACK_MS = 40, L_TAIL_MS = 300;
let lightFrames = [];
const rgbCss = c => c ? `rgb(${c[0]},${c[1]},${c[2]})` : "";

// Same contract as grabFrames(), but one grab per CAMERA frame + rVFC metadata.
function grabFramesRVFC(videoEl, width, minGapMs, list, metaList, prefix, untilFn, onTick){
  if(!videoEl.requestVideoFrameCallback)       // old browsers: existing loop (add a quality arg to it!)
    return grabFrames(videoEl, width, L_FALLBACK_MS, list, metaList, prefix, untilFn, onTick);
  const c = document.createElement("canvas");
  const vw = videoEl.videoWidth || 640, vh = videoEl.videoHeight || 480;
  c.width = width; c.height = Math.round(vh * width / vw);
  const ctx = c.getContext("2d"), pending = []; let i = 0, last = -1e9, finished = false;
  return new Promise(done => {
    const finish = () => { if(!finished){ finished = true; Promise.all(pending).then(done); } };
    const onFrame = (_now, md) => {
      const t = performance.now();
      if(finished) return;
      if(untilFn(t)) return finish();
      if(t - last >= minGapMs){
        last = t;
        const name = prefix + String(++i).padStart(4, "0") + ".jpg";
        ctx.drawImage(videoEl, 0, 0, c.width, c.height);      // exactly the frame `md` describes
        metaList.push({ file:name, t, t_pres:md.presentationTime, t_exp:md.expectedDisplayTime,
                        t_cap:md.captureTime ?? null, media_t:md.mediaTime, n:md.presentedFrames });
        pending.push(new Promise(res => c.toBlob(b => { list.push({name, blob:b}); res(); }, "image/jpeg", L_Q)));
      }
      if(onTick) onTick(t);
      videoEl.requestVideoFrameCallback(onFrame);
    };
    videoEl.requestVideoFrameCallback(onFrame);
    const watchdog = setInterval(() => { if(untilFn(performance.now())){ clearInterval(watchdog); finish(); } }, 100);
  });
}

// rAF painter: switch on the first frame at/after the planned time; t_painted = next rAF ts.
function paintSequence(flash, seq, L, videoEl, thumb){
  const tctx = thumb.getContext("2d");
  return new Promise(done => {
    let i = -1, tNext = null, justSet = null, lastTs = 0; const iv = [];
    const step = ts => {
      if(justSet){ justSet.t_painted = ts; if(justSet.name === "end"){ L.raf = rafStats(iv, L); return done(); } justSet = null; }
      if(lastTs){ iv.push(ts - lastTs); if(ts - lastTs > 25) L.raf_jank = (L.raf_jank||0) + 1; }
      lastTs = ts;
      if(tNext === null) tNext = ts;
      if(ts >= tNext - 4){
        const s = ++i < seq.length ? seq[i] : { name:"end", rgb:null, ms:0 };
        flash.style.backgroundColor = rgbCss(s.rgb) || "#000";
        document.documentElement.style.background = flash.style.backgroundColor;
        justSet = { i, name:s.name, rgb:s.rgb, ms:s.ms, t_planned:tNext, t_raf:ts, t_painted:null };
        L.switches.push(justSet);
        tNext += s.ms;
      }
      tctx.save(); tctx.scale(-1, 1);                             // small mirrored self-view
      tctx.drawImage(videoEl, -thumb.width, 0, thumb.width, thumb.height); tctx.restore();
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
const rafStats = (iv, L) => ({ median_ms: iv.sort((a,b)=>a-b)[iv.length>>1] || null, jank: L.raf_jank || 0 });

async function phaseLight(videoEl, challenge){
  const flash = $("lightFlash"), thumb = $("lightThumb"), hint = $("lightHint"), fill = $("lightBar").querySelector("i");
  const seq = [challenge.lead_in, ...challenge.sequence];
  const total = seq.reduce((s, x) => s + x.ms, 0);
  lightFrames = [];
  const L = meta.light = { enabled:true, opted_out:false, challenge_id:challenge.challenge_id, nonce:challenge.nonce,
    t_received:challenge._t_received, switches:[], frames:[], hidden:false,
    grab:{ mode: videoEl.requestVideoFrameCallback ? "rvfc" : "timeout", width:L_W, jpeg_q:L_Q, min_gap_ms:L_GAP_MS },
    display:{ dpr:devicePixelRatio, vw:innerWidth, vh:innerHeight,
              reduced_motion: matchMedia("(prefers-reduced-motion: reduce)").matches } };
  const onVis = () => { if(document.visibilityState !== "visible") L.hidden = true; };
  document.addEventListener("visibilitychange", onVis);
  thumb.width = 120; thumb.height = Math.round(120 * (videoEl.videoHeight||480) / (videoEl.videoWidth||640));
  flash.style.backgroundColor = rgbCss(challenge.lead_in.rgb); flash.hidden = false;
  const r = thumb.getBoundingClientRect(); L.thumb = { x:r.x, y:r.y, w:r.width, h:r.height };
  L.cam_lock = await tryLockCamera(stream.getVideoTracks()[0]);       // Android bonus; iOS -> "none"
  meta.events.push({ name:"light_start", t:performance.now() });
  recordingMotion = true;
  let tEnd = null;
  const painter = paintSequence(flash, seq, L, videoEl, thumb).then(() => { tEnd = performance.now(); });
  const t0 = performance.now();
  await grabFramesRVFC(videoEl, L_W, L_GAP_MS, lightFrames, L.frames, "l",
    now => (tEnd !== null && now - tEnd >= L_TAIL_MS) || now - t0 > total + 3000,   // tail: catch lagged frames
    now => { const left = total - (now - t0);
             fill.style.width = (Math.min(1, (now - t0) / total) * 100).toFixed(0) + "%";
             hint.textContent = left > 0 ? `Keep still and look at the screen… ${Math.ceil(left / 1000)}` : "Done"; });
  await painter;
  recordingMotion = false;
  meta.events.push({ name:"light_end", t:performance.now() });
  document.removeEventListener("visibilitychange", onVis);
  await unlockCamera(stream.getVideoTracks()[0], L.cam_lock);
  flash.hidden = true; document.documentElement.style.background = "";
  L.client_checks = lightClientChecks(L, seq);
  return L.client_checks.ok;           // caller: if false -> fetch a NEW challenge and rerun once, then step-up
}

function lightClientChecks(L, seq){
  const sw = L.switches.filter(s => s.name !== "end"), end = L.switches.find(s => s.name === "end");
  const all_painted = sw.length === seq.length && L.switches.every(s => s.t_painted != null);
  const order_ok = sw.every((s, k) => s.name === seq[k].name);
  const max_switch_err_ms = Math.max(...L.switches.map(s => Math.abs(s.t_raf - s.t_planned)));
  let max_dur_err_ms = 0, min_frames_per_seg = Infinity;
  sw.forEach((s, k) => {
    const next = k + 1 < sw.length ? sw[k + 1] : end;
    max_dur_err_ms = Math.max(max_dur_err_ms, Math.abs((next.t_painted - s.t_painted) - s.ms));
    const n = L.frames.filter(f => f.t >= s.t_painted + 150 && f.t <= next.t_painted + 50).length;
    min_frames_per_seg = Math.min(min_frames_per_seg, n);
  });
  const ok = all_painted && order_ok && !L.hidden && max_switch_err_ms <= 34 && max_dur_err_ms <= 34 && min_frames_per_seg >= 3;
  return { all_painted, order_ok, max_switch_err_ms, max_dur_err_ms, min_frames_per_seg, ok };
}

async function tryLockCamera(track){
  const out = { exposure:"none", white_balance:"none" };
  try {
    const cap = track.getCapabilities ? track.getCapabilities() : {};
    if((cap.exposureMode||[]).includes("manual")){ await track.applyConstraints({ advanced:[{ exposureMode:"manual" }] }); out.exposure = "manual"; }
    if((cap.whiteBalanceMode||[]).includes("manual")){ await track.applyConstraints({ advanced:[{ whiteBalanceMode:"manual" }] }); out.white_balance = "manual"; }
  } catch(_){}
  return out;
}
async function unlockCamera(track, lock){
  try {
    if(lock.exposure === "manual") await track.applyConstraints({ advanced:[{ exposureMode:"continuous" }] });
    if(lock.white_balance === "manual") await track.applyConstraints({ advanced:[{ whiteBalanceMode:"continuous" }] });
  } catch(_){}
}

// Caller, after the user taps Start on the light instruction screen (just-in-time mint):
//   const ch = await fetch(`/session/${session.session_id}/light`, {method:"POST"}).then(r => r.json());
//   ch._t_received = performance.now();
//   if(!await phaseLight(videoEl, ch)) { /* one retry with a fresh challenge, then meta.light.verdict_hint = "retry_failed" */ }
//   upload: lightFrames.forEach(f => fd.append("light_frames", f.blob, f.name));
```
Notes on the code:
- The painter's rAF loop and the rVFC grabber both run on the main thread. `toBlob` is async (encoding happens
  off the main thread in Chrome and Safari), so switch jitter stays within a frame. If `max_switch_err_ms` is often
  > 17 ms on a device, move to a 2-frame min gap (15 fps).
- `paintSequence` switches to black at "end". The `t_painted` of "end" closes the last segment. The grabber keeps
  running `L_TAIL_MS` after that so it captures frames of the last colour that arrive late.
- Placement in the flow: run it after the selfie framing and before check 4's tilt, in the same continuous stream
  (phone still, face centred, AE settled).

## 9. Uncertainty flags (validate on the two demo phones)
1. Pipeline lag window (30–250 ms) and its per-device spread. Measure it with the live pipeline: plot face
   ROI vs `t_painted`.
2. AE/AWB time constants on iPhone and Android front cameras (my 0.3–1 s AE and 1–3 s AWB are estimates).
   Check whether the 400–600 ms segments show visible within-segment drift.
3. Whether iOS Safari fills `captureTime` for gUM frames. Whether Android front cameras accept
   `exposureMode:"manual"` and actually hold it.
4. The f-threshold for "inconclusive" (0.05–0.08). Calibrate it with the venue light, by a window and outdoors.
5. Safari `theme-color` tinting of the toolbar in the current iOS, and whether WebKit ever pauses the 1 px source
   video while a fixed full-screen div covers it. The existing page suggests not, but test it.
6. JPEG size and encode time at q = 0.9 and 30 fps on an older Android phone.
7. Effect of Night Shift / True Tone / eye-comfort modes on the blue primary.

## Sources
- Tang, Zhou, Zhang, Zhang, "Face Flashing: a Secure Liveness Detection Protocol based on Light Reflections", NDSS 2018. https://arxiv.org/abs/1801.01949 (PDF https://arxiv.org/pdf/1801.01949). Used: pure-colour background challenges, 8 colours, ~20 cm, 3 s default ("1 s is enough but hurried"), rolling-shutter / screen-refresh timing verification, sunlight OK unless camera faces sun, dark = saturation, AWB unusable, "indelible delay" argument.
- Farrukh, Aburas, Cao, Wang, "FaceRevelio: A Face Liveness Detection System for Smartphones with a Single Front Camera", MobiCom 2020. https://habiba-farrukh.github.io/files/FaceRevelio.pdf , https://dl.acm.org/doi/10.1145/3372224.3419206. Used: 1280×960 @30 fps, manual/fixed ISO on Android, inverse gamma, zero-mean passcodes cancel ambient, grey-128 base, 1–3 s passcodes, 0 lux / 200–5000 lux daylight / ~250 lux indoor, natural distance 27 cm (tested 20/30/40 cm), sensitive to changing ambient light.
- Gerstner & Farid, "Detecting Real-Time Deep-Fake Videos Using Active Illumination", CVPR Workshops 2022. Used: isoluminant yellow↔magenta hue modulation to avoid AE/AWB, blues/greens trigger AWB, baseline reflectance divided out, correlation 0.83/0.65/0.37/0.03 at 1/2/3/4-frame delay, random blank frames.
- WICG, requestVideoFrameCallback spec: https://wicg.github.io/video-rvfc/ ; MDN: https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback ; web.dev: https://web.dev/requestvideoframecallback-rvfc ; caniuse: https://caniuse.com/mdn-api_htmlvideoelement_requestvideoframecallback (Safari/iOS 15.4+).
- W3C MediaStream Image Capture: https://w3c.github.io/mediacapture-image/ ; implementation status: https://github.com/w3c/mediacapture-image/blob/main/implementation-status.md
- webrtcHacks, "Fix Bad Lighting with JavaScript Webcam Exposure Controls": https://webrtchacks.com/bad-lighting-fix-with-javascript-webcam-exposure/ (two-step exposureMode/exposureTime, Safari exposes nothing).
- W3C, Understanding SC 2.3.1 Three Flashes or Below Threshold: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- iProov, Flashmark: https://www.iproov.com/biometric-encyclopedia/flashmark ; epilepsy safeguards (< 2.86 flashes/s, Harding-tested): https://www.iproov.com/blog/iproov-safeguards-for-people-with-photosensitive-epilepsy ; 2.5 s look-at-screen: https://www.iproov.com/blog/biometric-security-systems-evolving-unknown-attacks
- Skia SkJpegEncoder default 4:2:0: https://github.com/google/skia/blob/main/include/encode/SkJpegEncoder.h
- rAF throttling on iOS (Low Power Mode 30 fps, 60 Hz cap): https://popmotion.io/blog/20180104-when-ios-throttles-requestanimationframe/ , https://motion.dev/magazine/when-browsers-throttle-requestanimationframe , https://bugs.webkit.org/show_bug.cgi?id=173434
- WebKit video policies (non-visible autoplay videos pause): https://webkit.org/blog/6784/new-video-policies-for-ios/
