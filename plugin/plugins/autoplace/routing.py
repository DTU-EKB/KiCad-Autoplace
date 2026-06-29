"""FreeRouting bridge: route a placed board once and report completion.

The only engine module besides ``kicad_io`` that imports ``pcbnew``; it also
shells out to FreeRouting. Extracted from ``tools/route_check.py`` so the
refinement loop (``refine.py``) can route a board repeatedly.

``route_once`` takes a board *file path* and loads it FRESH on every call. This
is deliberate: KiCad 10's pcbnew cannot iterate ``GetTracks()`` after
``ImportSpecctraSES`` ("SwigPyObject is not iterable"), so a board cannot be
cleared and reused for a second route. Loading fresh each time sidesteps that
entirely -- the refine loop saves each candidate placement to a file and routes
that file.
"""
from __future__ import annotations

import os
import subprocess
import time

import pcbnew

from .kicad_io import force_gnd_zones, unrouted_count


def route_once(pcb_path: str, jar: str, passes: int, stem: str = None) -> dict:
    """Load ``pcb_path`` fresh, route it once with FreeRouting, report completion.

    Writes ``stem.dsn`` / ``stem.ses`` / ``stem.routed.kicad_pcb`` (``stem``
    defaults to the input path without extension). Net-class widths come from the
    board's ``.kicad_pro`` -- ensure it sits next to ``pcb_path``.
    """
    board = pcbnew.LoadBoard(pcb_path)
    if board is None:
        raise RuntimeError(f"could not load {pcb_path}")
    if stem is None:
        stem = os.path.splitext(pcb_path)[0]
    # Ensure a filled GND plane BEFORE export: FreeRouting then sees GND pads
    # already connected by the pour and won't waste tracks routing ground.
    force_gnd_zones(board)
    total = unrouted_count(board)               # ratsnest before routing
    dsn, ses = stem + ".dsn", stem + ".ses"
    if not pcbnew.ExportSpecctraDSN(board, dsn):
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

    pcbnew.ImportSpecctraSES(board, ses)
    force_gnd_zones(board)                      # refill the GND pour after import
    left = unrouted_count(board)
    routed = total - left
    routed_pcb = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(routed_pcb, board)
    return {
        "total": total, "routed": routed, "unrouted": left,
        "pct": (100.0 * routed / total if total else 100.0),
        "ses_path": ses, "seconds": round(dt, 1), "routed_pcb": routed_pcb,
    }
