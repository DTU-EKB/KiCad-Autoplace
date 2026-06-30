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

from . import strip as strip_mod
from .kicad_io import force_gnd_zones, unrouted_count


def _flip_to_bottom(routed_pcb: str) -> None:
    """Move all routed copper from F.Cu to B.Cu in a saved single-sided board.

    FreeRouting routes the one copper layer KiCad exposes (F.Cu); a CNC/etch board
    wants the copper on the bottom. Reload fresh (``GetTracks`` is iterable again
    after a load, unlike on the just-imported board), re-enable B.Cu, and move
    every F.Cu track and pour to B.Cu. Footprint pads are untouched, so components
    stay on top. The board keeps two layers with F.Cu empty -- fine for a
    single-sided etch.
    """
    b = pcbnew.LoadBoard(routed_pcb)
    b.SetCopperLayerCount(2)                     # re-enable B.Cu as a target
    for t in b.GetTracks():
        if t.GetLayer() == pcbnew.F_Cu:
            t.SetLayer(pcbnew.B_Cu)
    for i in range(b.GetAreaCount()):
        z = b.GetArea(i)
        if z.IsOnLayer(pcbnew.F_Cu):
            z.SetLayer(pcbnew.B_Cu)
    pcbnew.SaveBoard(routed_pcb, b)


def route_once(pcb_path: str, jar: str, passes: int, stem: str = None,
               sides: int = 2) -> dict:
    """Load ``pcb_path`` fresh, route it once with FreeRouting, report completion.

    Writes ``stem.dsn`` / ``stem.ses`` / ``stem.routed.kicad_pcb`` (``stem``
    defaults to the input path without extension). Net-class widths come from the
    board's ``.kicad_pro`` -- ensure it sits next to ``pcb_path``.

    ``sides == 1`` forces single-sided routing on a clean slate: any existing
    routing in ``pcb_path`` is stripped (textually -- in-process pcbnew track
    removal access-violates), then the board is reduced to one copper layer
    (``SetCopperLayerCount(1)`` -> F.Cu) and re-routed from scratch, leaving
    uncrossable nets unrouted. (FreeRouting ignores Specctra layer ``type``, so
    cutting the layer count is the reliable lever.) FreeRouting routes the front
    layer KiCad exposes; the result is then flipped to B.Cu so the copper lands on
    the bottom (etch side).
    """
    if sides == 1:
        # Clean slate: drop any prior routing so we re-route on one layer (and so
        # no leftover B.Cu wire references the layer we are about to remove).
        with open(pcb_path, encoding="utf-8") as f:
            stripped, _ = strip_mod.strip_tracks(f.read())
        with open(pcb_path, "w", encoding="utf-8") as f:
            f.write(stripped)
    board = pcbnew.LoadBoard(pcb_path)
    if board is None:
        raise RuntimeError(f"could not load {pcb_path}")
    if stem is None:
        stem = os.path.splitext(pcb_path)[0]
    if sides == 1:
        board.SetCopperLayerCount(1)            # one copper layer (F.Cu)
        # Move any B.Cu pour onto F.Cu: with B.Cu gone from the layer structure,
        # exporting a zone that still references it makes FreeRouting reject the
        # DSN ("layer name 'B.Cu' not found"). SetLayer avoids pcb.Remove(), which
        # corrupts connectivity on KiCad 10.
        for i in range(board.GetAreaCount()):
            z = board.GetArea(i)
            if z.IsOnLayer(pcbnew.B_Cu):
                z.SetLayer(pcbnew.F_Cu)
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
    if sides == 1:
        _flip_to_bottom(routed_pcb)             # single-sided copper -> B.Cu
    return {
        "total": total, "routed": routed, "unrouted": left,
        "pct": (100.0 * routed / total if total else 100.0),
        "ses_path": ses, "seconds": round(dt, 1), "routed_pcb": routed_pcb,
    }
