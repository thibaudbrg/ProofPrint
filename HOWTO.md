# Proofprint — starter

The walking skeleton: **phone films ID + selfie → uploads → server face-matches → returns a decision.**
This is Act 1 of the demo, and the base every other check plugs into.

## Run it (about 5 minutes)

```bash
brew install tesseract          # once — OCR engine for the ID's machine-readable zone (check 2)
cd server
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```
Face-match models download automatically on the first identity check (YuNet + SFace,
from the OpenCV Zoo).

Open on the phone over **HTTPS** (camera needs a secure context):
```bash
# second terminal
cloudflared tunnel --url http://localhost:8000
```
Open the printed `https://….trycloudflare.com` link on the phone.

## Try the demo
1. **Real person:** photograph an ID, take a matching selfie → **✓ Passed**.
2. **Broken-app attack (Act 2):** feed a deepfake through a virtual camera into desktop
   Chrome; the selfie still "matches" the ID → it passes. This is the weakness.
3. **Mitigated app (Act 3):** add the liveness checks (below) so the same attack is caught.

## What to build next (order)
1. ✅ Face match — this skeleton.
2. ID card check — detect + de-skew the card, crop the face into `face_match`.
3. Capture integrity — read `track.label`, frame timing, sensors-present (cheap).
4. **Gyroscope ↔ video** — the core; give it the most time.
5. Light pulse — flash the `challenge`, check the skin reflects it.

Each is a module behind the SAME 3 endpoints (see `contract/contract.md`).
Methods, papers and thresholds: `swisscom-research/CHECKS_INDEX.md`.

## Layout
```
server/    main.py (3 routes), face_match.py (YuNet+SFace), requirements.txt
web/       index.html (phone capture page)
contract/  contract.md (the 3 JSON shapes — the line between the two devs)
```

## First-hour device tests
- iPhone: is `captureTime` present in rVFC? real fps?
- `rotationRate` sign/axes on both phones (deg/s on iOS!).
- Colour reflection SNR at 25 cm in venue light.
- `track.label` strings on both phones.
