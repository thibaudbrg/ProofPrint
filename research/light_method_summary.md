# Check 5 — Light pulse: why we chose this method (summary)

Short summary of [`light_algorithm.md`](light_algorithm.md) (the full server algorithm) for teammates and the pitch.
Client-side capture details are in [`light_capture.md`](light_capture.md).

## The idea

While the selfie camera films the user, the phone screen flashes a **random sequence of colours**: red, green and
blue, with grey pauses in between. A real face lit by the screen **reflects those colours back**. A pre-recorded
video, or a face-swap that isn't lit by the screen, does not. The server compares the colours it asked for with the
colours it sees on the skin.

## Why each design choice

### The phone screen as the light source
- Every phone already has one, so no extra hardware is needed.
- The screen is about 25 cm from the face but more than 1 m from the wall behind. The face gets a clear colour
  change while the background barely changes, and that difference is what we measure.

### Face compared with the background
- Phone cameras constantly auto-adjust exposure and white balance (AE/AWB), which shifts the colours of the whole picture.
- We subtract the background's colour from the face's colour (in log-chromaticity). That cancels the camera's automatic
  adjustments, skin tone, distance and overall brightness. What remains is only the colour the screen added to the face.
- If the background is unusable (for example, a bright window), we fall back to the face alone and flag `no_bg_reference`.

### Pure red, green and blue, with grey pauses
- Pure primaries are the easiest colours for a camera to tell apart.
- Grey pauses rather than black: the colour contrast is the same, the face stays lit so the face detector works in dark
  rooms, and there is less flicker and exposure pumping.
- The grey pauses are also reference points that remove slow camera drift.

### A random sequence, minted by the server just in time
- The server picks the colour order and each colour's duration at random: 462 possible orders, each colour lasting a
  random 380–560 ms, about 4.9 s in total.
- It is created only when the user taps "I'm ready", and it can be used once.
- A video recorded earlier cannot follow it: the colours come out wrong.
- The server judges the result against its own copy of the sequence, not what the phone claims.

### A statistical test, not "does it look right"
- We measure how closely the skin's colour changes follow the planned sequence (correlation ρ), and how many colour
  slots were right.
- A **permutation test** asks how well 1,000 random sequences would have matched by chance, which gives a p-value.
  Getting 5 of 6 slots right by guessing has a probability of about 1.8 %.
- We search a range of camera delays (−40 to +420 ms), because phones differ in how late the camera sees the screen change.

### Never an automatic pass when unsure
- Almost no colour response → **insufficient → one more step**. Strong daylight and an injected video look the same,
  so this case retries or asks for another check. It **never passes**.
- A strong response with the **wrong** colours means a replay → **block**.

### Safe for users
- At most 1.3 flashes per second, below the WCAG accessibility limit of 3 per second.
- A photosensitivity warning and a **Skip** button. Skipping leads to an extra check and is never a penalty.

## What it catches, and what it doesn't

| Attack | Result |
|---|---|
| Pre-recorded video through a virtual camera | **Caught**: wrong colours → block, or no response → extra step |
| Replay of a real session under a new sequence | **Caught**: the colours don't match |
| Common face-swap tools (the face doesn't react to the light) | **Caught** as "insufficient" → extra step |
| Face-swap that copies lighting from the attacker's real face, less than ~150 ms late | **Not caught** |
| Attacker who controls the browser and tints the fake face in real time | **Not caught** |

This is why check 5 is **one signal combined with** check 4 (phone motion) and check 6 (profile turn), not a
standalone defence. iProov's Flashmark patents cover the basic flashing technique.

## Where it comes from

Adapted from three published methods:
- Tang et al., *Face Flashing*, NDSS 2018.
- Gerstner & Farid, *Detecting Real-Time Deep-Fake Videos Using Active Illumination*, CVPR Workshops 2022.
- Farrukh et al., *FaceRevelio*, MobiCom 2020.

Those relied on native camera control or a fixed laptop setup, which a phone browser doesn't have. The
face-minus-background measurement and the delay search are what make it work in a browser.

## Honest status

Tested on **synthetic video only**. There, genuine users passed and replays failed 8 out of 8 times. Nothing has been
run on real phones yet, so every threshold (`SCORE_PASS`, `P_MAX`, `SNR_MIN`, `LAG_OK`…) still needs calibrating with
the light lab (`/lab/light`).
