"""Check 3 — capture integrity.

Cheap browser-only signals that say "this stream probably didn't come from a
real phone front camera". None is proof on its own (labels are spoofable), so
the output is a list of flags that raise risk, not a hard verdict — except a
known virtual-camera label, which we treat as a block.

Signals (see swisscom-research/web_feasibility.md §6):
  - virtual-camera device label (OBS, ManyCam, ...)   -> strong
  - camera is not the front camera (facingMode != user)
  - no motion sensors on a device claiming to be mobile -> strong (desktop attack)
  - frame timing suspiciously regular (real cameras jitter)
"""
from __future__ import annotations
import re
import statistics

VCAM = re.compile(r"obs|manycam|splitcam|droidcam|iriun|snap ?camera|v4l2|virtual", re.I)


def check(meta: dict | None) -> dict:
    meta = meta or {}
    flags: list[str] = []

    label = meta.get("label") or ""
    if VCAM.search(label):
        flags.append("virtual_cam_label")

    settings = meta.get("settings") or {}
    facing = settings.get("facingMode")
    if facing not in (None, "", "user"):
        flags.append("not_front_camera")

    # hasMotion is False only when the client explicitly saw zero motion events
    # on a device that claims to be a phone (a laptop attack has no sensors).
    if meta.get("hasMotion") is False and meta.get("claimsMobile"):
        flags.append("no_motion_sensors")

    # Frame timing is a WEAK, noisy signal — a good phone camera in steady light
    # is genuinely very regular, so it false-flags. Keep it informational only:
    # it does NOT affect the decision (see research: weak on its own).
    info = []
    dts = meta.get("frameIntervals") or []
    if len(dts) >= 8:
        m = statistics.mean(dts)
        sd = statistics.pstdev(dts)
        if m > 0 and (sd / m) < 0.004:         # <0.4% jitter = suspiciously clean
            info.append("timing_very_regular")

    hard = "virtual_cam_label" in flags
    return {"ok": len(flags) == 0, "flags": flags, "info": info, "hard": hard}
