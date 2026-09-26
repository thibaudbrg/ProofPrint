"""Session log - a small SQLite audit trail for calibration and the analyst view.

Privacy by design: we keep the SCORES, never the person.
  - only the FIRST given name from the MRZ (to tell test runs apart) - no surname,
    no birth date, no document number, no nationality
  - no images, no embeddings, no raw sensor traces
  - device info is limited to "phone vs desktop" and the camera label (attack forensics)

The database lives in server/data/ which is gitignored. Logging is best-effort:
a failure here never breaks a capture.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import os
import sqlite3
import subprocess
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "proofprint.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id      TEXT    NOT NULL,
  created_at      TEXT    NOT NULL,   -- ISO 8601, UTC
  first_name      TEXT,               -- first given name from the MRZ, or NULL
  decision        TEXT    NOT NULL,   -- pass | step_up | block
  face_score      REAL,               -- cosine similarity, selfie vs document portrait
  face_verdict    TEXT,               -- match | review | mismatch | NULL = no face found
  doc_type        TEXT,               -- passport | id_card | no_mrz
  id_flags        TEXT,               -- JSON list
  integrity_ok    INTEGER,            -- 1 / 0
  integrity_flags TEXT,               -- JSON list
  motion_score    REAL,               -- check 4, NULL until frames are sent
  motion_verdict  TEXT,
  light_score     REAL,               -- check 5, NULL when off / skipped
  light_verdict   TEXT,
  profile_score   REAL,               -- check 6
  profile_verdict TEXT,
  mode            TEXT,               -- full | naive
  platform        TEXT,               -- phone | desktop
  camera_label    TEXT,
  pipeline        TEXT,               -- git short SHA of the code that scored it
  result_json     TEXT                -- the full result (signals, series, reasons) minus images
)
"""

COLUMNS = ("session_id", "created_at", "first_name", "decision", "face_score", "face_verdict",
           "doc_type", "id_flags", "integrity_ok", "integrity_flags", "motion_score",
           "motion_verdict", "light_score", "light_verdict",
           "profile_score", "profile_verdict", "mode", "platform", "camera_label", "pipeline", "result_json")

# Columns added after the first deployment; ALTERed in when an older DB is opened.
MIGRATIONS = {"light_score": "REAL", "light_verdict": "TEXT",
              "profile_score": "REAL", "profile_verdict": "TEXT", "mode": "TEXT", "result_json": "TEXT"}

# Never persisted: anything that is a picture of the person.
_IMAGE_KEYS = ("portrait_thumb", "thumb", "debug_image")


def _scrub(obj):
    """Deep-copy `obj` without image keys / data-URLs (the trace keeps scores and curves only)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if k not in _IMAGE_KEYS and not (isinstance(v, str) and v.startswith("data:image"))}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    for col, typ in MIGRATIONS.items():
        if col not in have:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typ}")
    return con


@functools.lru_cache(maxsize=1)
def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=os.path.dirname(__file__), text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _first_name(idsig: dict) -> Optional[str]:
    """Only the first given name, title-cased. Never the surname."""
    mrz = (idsig or {}).get("mrz") or {}
    names = (mrz.get("names") or "").split()
    return names[0].title() if names else None


def record(session_id: str, result: dict, meta: dict) -> Optional[int]:
    """Insert one row for a scored capture. Returns the row id, or None on failure."""
    if os.environ.get("PYTEST_CURRENT_TEST"):      # keep test runs out of the real log
        return None
    try:
        s = result.get("signals", {})
        face, idsig = s.get("face") or {}, s.get("id") or {}
        integ, motion = s.get("integrity") or {}, s.get("motion") or {}
        light, profile = s.get("light") or {}, s.get("profile") or {}
        row = {
            "session_id": session_id,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "first_name": _first_name(idsig),
            "decision": result.get("decision"),
            "face_score": face.get("score"),
            "face_verdict": face.get("verdict"),
            "doc_type": idsig.get("doc_type"),
            "id_flags": json.dumps(idsig.get("flags") or []),
            "integrity_ok": int(bool(integ.get("ok"))),
            "integrity_flags": json.dumps(integ.get("flags") or []),
            "motion_score": motion.get("score"),
            "motion_verdict": motion.get("verdict"),
            "light_score": light.get("score") if light.get("enabled") else None,
            "light_verdict": light.get("verdict") if light.get("enabled") else None,
            "profile_score": profile.get("score") if profile.get("enabled", True) else None,
            "profile_verdict": profile.get("verdict") if profile.get("enabled", True) else None,
            "mode": result.get("mode"),
            "platform": "phone" if (meta or {}).get("claimsMobile") else "desktop",
            "camera_label": (meta or {}).get("label") or None,
            "pipeline": _git_sha(),
            "result_json": json.dumps(_scrub({**result, "meta": _meta_summary(meta)})),
        }
        placeholders = ", ".join("?" for _ in COLUMNS)
        with _connect() as con:
            cur = con.execute(f"INSERT INTO sessions ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                              tuple(row[c] for c in COLUMNS))
            return cur.lastrowid
    except Exception as exc:                      # never let logging break a capture
        print(f"[audit_log] failed: {exc}")
        return None


def _meta_summary(meta: dict) -> dict:
    """The non-personal capture facts worth keeping with the trace (no sensor traces, no frames)."""
    m = meta or {}
    ev = {e.get("name"): e.get("t") for e in (m.get("events") or []) if isinstance(e, dict)}
    return {"mode": m.get("mode"), "checks": m.get("checks"), "claimsMobile": m.get("claimsMobile"),
            "hasMotion": m.get("hasMotion"), "label": m.get("label"),
            "facingMode": (m.get("settings") or {}).get("facingMode"),
            "n_frames": len(m.get("frames") or []), "n_motion": len(m.get("motion") or []),
            "n_light_frames": len((m.get("light") or {}).get("frames") or []),
            "n_profile_frames": len((m.get("profile") or {}).get("frames") or []),
            "events": ev}


_LIST_COLS = [c for c in COLUMNS if c != "result_json"]


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Latest rows, newest first, JSON columns decoded (without the heavy result_json)."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(f"SELECT id, {', '.join(_LIST_COLS)} FROM sessions ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["id_flags"] = json.loads(d["id_flags"] or "[]")
        d["integrity_flags"] = json.loads(d["integrity_flags"] or "[]")
        out.append(d)
    return out


def get(session_id: str) -> Optional[dict[str, Any]]:
    """One session's full trace: the row + the scrubbed result (signals, series, reasons)."""
    with _connect() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM sessions WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                        (session_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["id_flags"] = json.loads(d["id_flags"] or "[]")
    d["integrity_flags"] = json.loads(d["integrity_flags"] or "[]")
    d["result"] = json.loads(d.pop("result_json") or "null")
    return d


def stats() -> dict[str, Any]:
    """Counts per decision / mode / check verdict, and score spreads per decision for each
    scored check - the numbers you need to set the thresholds and to fill the dashboard."""
    with _connect() as con:
        n_total = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        by_decision = dict(con.execute("SELECT decision, COUNT(*) FROM sessions GROUP BY decision").fetchall())
        by_mode = dict(con.execute("SELECT COALESCE(mode,'full'), COUNT(*) FROM sessions GROUP BY 1").fetchall())
        by_platform = dict(con.execute("SELECT platform, COUNT(*) FROM sessions GROUP BY platform").fetchall())
        checks = {}
        for name, col in (("face", "face_verdict"), ("light", "light_verdict"), ("motion", "motion_verdict"),
                          ("profile", "profile_verdict")):
            checks[name] = dict(con.execute(
                f"SELECT COALESCE({col}, 'none'), COUNT(*) FROM sessions GROUP BY 1").fetchall())
        checks["integrity"] = dict(con.execute(
            "SELECT CASE integrity_ok WHEN 1 THEN 'clean' ELSE 'flagged' END, COUNT(*) FROM sessions GROUP BY 1").fetchall())
        checks["document"] = dict(con.execute("SELECT COALESCE(doc_type,'none'), COUNT(*) FROM sessions GROUP BY 1").fetchall())
        spreads = {}
        for name, col in (("face", "face_score"), ("light", "light_score"), ("motion", "motion_score"),
                          ("profile", "profile_score")):
            rows = con.execute(f"SELECT decision, COUNT(*), MIN({col}), AVG({col}), MAX({col}) "
                               f"FROM sessions WHERE {col} IS NOT NULL GROUP BY decision").fetchall()
            spreads[name] = {d: {"n": n, "min": mn, "avg": round(av, 3) if av is not None else None, "max": mx}
                             for d, n, mn, av, mx in rows}
    # `face` at the top level keeps the old shape (the first calibration notes read it)
    return {"n": n_total, "by_decision": by_decision, "by_mode": by_mode, "by_platform": by_platform,
            "checks": checks, "spreads": spreads, **spreads.get("face", {})}
