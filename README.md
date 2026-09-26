# ProofPrint

> *A deepfake can fake your face. It can't fake your phone.*

ProofPrint is a phone-based identity-verification demo that stops deepfake selfies during onboarding. Instead of only matching a face to an ID photo - a check that a live face-swap now walks straight through - it verifies the physics a real capture can't fake: the screen flashes a random colour sequence that must reflect on the skin, the video must move exactly as the phone's gyroscope reports, and the person turns to a side the server picks live. A deepfake fools the naïve face-only app; ProofPrint blocks the same attack in about three seconds while a real person passes.

🏆 **Winner - Public Ranking** · 🥉 **3rd - General Ranking** at the [Swiss {ai} Weeks Zürich Hackathon 2026](https://zh.ai-weeks.ch/)

🎥 **[Watch the presentation](https://drive.google.com/file/d/1BzfnLIVb5rpAt1cAn4GUuOA4censL_nl/view?usp=sharing)**

## Run

```bash
./demo.sh          # starts the server + a public HTTPS tunnel, prints a link to open on your phone
./demo.sh naive    # the "naïve" face-only app a deepfake fools
```

The phone captures; every decision is made server-side. An analyst view of the checks and past sessions is at `/dashboard`.

## The challenge

Built for Swisscom's *Fighting Identity Fraud in the Age of AI* challenge - full brief in [CHALLENGE.md](CHALLENGE.md).
