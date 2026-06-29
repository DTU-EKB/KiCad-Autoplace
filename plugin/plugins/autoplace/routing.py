"""FreeRouting bridge: route a placed board once and report completion.

The only engine module besides ``kicad_io`` that imports ``pcbnew``; it also
shells out to FreeRouting. Extracted from ``tools/route_check.py`` so the
refinement loop (``refine.py``) can route a board repeatedly.
"""
from __future__ import annotations

import os
import subprocess
import time

import pcbnew

from .kicad_io import unrouted_count


def clear_tracks(pcb: "pcbnew.BOARD") -> None:
    """Remove every track and via so the next DSN export is unrouted."""
    for t in list(pcb.GetTracks()):
        pcb.Remove(t)
    pcb.BuildConnectivity()


def route_once(pcb: "pcbnew.BOARD", jar: str, passes: int, stem: str) -> dict:
    """Export DSN, run FreeRouting head-less, import the SES, count unrouted.

    Leaves the routed tracks on ``pcb`` (the caller clears them before the next
    export, which ``clear_tracks`` at the top of this function also does). Writes
    ``stem.dsn`` and ``stem.ses``.
    """
    clear_tracks(pcb)
    total = unrouted_count(pcb)                 # ratsnest before routing
    dsn, ses = stem + ".dsn", stem + ".ses"
    if not pcbnew.ExportSpecctraDSN(pcb, dsn):
        raise RuntimeError("DSN export failed")
    if os.path.exists(ses):
        os.remove(ses)

    t0 = time.time()
    proc = subprocess.run(
        ["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes)],
        capture_output=True, text=True, timeout=1800)
    dt = time.time() - t0

    if not os.path.exists(ses) or os.path.getsize(ses) == 0:
        tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-400:]
        raise RuntimeError(
            f"FreeRouting produced no usable SES (exit {proc.returncode}).\n{tail}")

    pcbnew.ImportSpecctraSES(pcb, ses)
    left = unrouted_count(pcb)
    routed = total - left
    return {
        "total": total, "routed": routed, "unrouted": left,
        "pct": (100.0 * routed / total if total else 100.0),
        "ses_path": ses, "seconds": round(dt, 1),
    }
