# Working rules (read me before editing)

## ⚠️ PROJECT.md IS THE SOURCE OF TRUTH — READ IT AND UPDATE IT

**EVERYONE (every teammate, every Claude instance) MUST:**
1. **READ `PROJECT.md` at the START of every session / task**, before doing anything
   else. It holds the whole project state, architecture, contract, decisions and
   what's done vs pending.
2. **UPDATE `PROJECT.md` AFTER EVERY CHANGE** — no matter how small. Especially the
   **STATUS / LOG** section at the top: what you built, what now runs, the current
   tunnel URL, and the next TODO. Stale = broken. Leave it correct for the next person.

If you skip this, two people (or two Claudes) will duplicate work or overwrite each
other. The whole point of `PROJECT.md` is that nobody has to be online to hand off.

## Ownership (so the AI assistants don't collide)
- **Person A** edits `server/` only.
- **Person B** edits `web/` only.
- `contract/` changes ONLY by agreement between both — it's the interface.
- Never edit a file outside your folder without saying so in `PROJECT.md`.

## Habits
- Small, frequent commits. Pull `main` often. Short branches (`a/…`, `b/…`).
- Never reformat a file you didn't change (false merge conflicts).
- Run the app before committing something that changes how it runs.
- Report what you actually tested. If a step was skipped, say so.

## Commands
- Server (auto-reload): `./dev.sh`  (from repo root, serves :8000)
- Tunnel (HTTPS for phones): `cloudflared tunnel --url http://localhost:8000`
- Methods / papers / thresholds for each check: `swisscom-research/CHECKS_INDEX.md`

## The point
Broken app = `face` + `id` only. Mitigated app = also `integrity` + `motion` + `light`,
fused into pass / step_up / block. Details, status and contract: **`PROJECT.md`**.
