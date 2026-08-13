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
    # Moving a zone to another layer invalidates its fill, and KiCad drops the
    # filled polygons on save -- leaving an OUTLINE-ONLY pour that connects
    # nothing. That costs twice: every GND pad reads as unrouted ratsnest
    # (measured at 11 of 20 "unrouted" connections on a 38-part board), and the
    # board that ships has a physically floating ground plane. Refill after the
    # move, before saving.
    force_gnd_zones(b)
    pcbnew.SaveBoard(routed_pcb, b)


def _track_sig(t):
    """Identity of a track, stable across a save/load round-trip."""
    s, e = t.GetStart(), t.GetEnd()
    return (t.GetClass(), s.x, s.y, e.x, e.y, t.GetWidth(), t.GetLayer(),
            t.GetNetCode())


def route_stage2_bridges(routed_pcb: str, jar: str, passes: int,
                         stem: str = None, timeout: int = 1800) -> dict:
    """Second stage: finish an incomplete single-sided board with wire bridges.

    An arbitrary netlist is almost never planar, so a genuinely single-sided
    board cannot reach 100% by routing alone -- what a person does is finish the
    last few connections as wire links on the component side. This does that
    deliberately instead of handing back unrouted nets: stage-1 copper is
    **locked** so FreeRouting cannot rip it up, the second layer is enabled, and
    the router is asked to close only what is left. Whatever lands on F.Cu is a
    bridge to solder, and it is counted and reported rather than hidden.

    Two traps, both learned from the laser pipeline and both handled:

    * FreeRouting does **not** re-emit fixed wires in its session file, so a raw
      SES import deletes the stage-1 copper. We snapshot the tracks first and
      re-add whatever the import dropped.
    * On KiCad 10 ``GetTracks()`` is not iterable after ``ImportSpecctraSES``, so
      the comparison is done on a freshly loaded copy rather than in-process.
    """
    if stem is None:
        stem = os.path.splitext(routed_pcb)[0] + "_s2"
    board = pcbnew.LoadBoard(routed_pcb)
    board.SetCopperLayerCount(2)                 # F.Cu becomes the bridge layer
    for t in board.GetTracks():
        t.SetLocked(True)                        # exported as (type fix)
    force_gnd_zones(board)
    before = [(_track_sig(t), t.Duplicate()) for t in board.GetTracks()]

    dsn, ses = stem + ".dsn", stem + ".ses"
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise RuntimeError("stage-2 DSN export failed")
    if os.path.exists(ses):
        os.remove(ses)
    t0 = time.time()
    proc = subprocess.run(["java", "-jar", jar, "-de", dsn, "-do", ses,
                           "-mp", str(passes)],
                          capture_output=True, text=True, timeout=timeout)
    dt = time.time() - t0
    if not os.path.exists(ses) or os.path.getsize(ses) == 0:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
        raise RuntimeError(f"stage-2 FreeRouting produced no SES "
                           f"(exit {proc.returncode}).\n{tail}")

    pcbnew.ImportSpecctraSES(board, ses)
    out = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(out, board)

    # Re-add stage-1 copper the import dropped (fresh load: iterable again).
    fresh = pcbnew.LoadBoard(out)
    have = {_track_sig(t) for t in fresh.GetTracks()}
    readded = 0
    for sig, dup in before:
        if sig not in have:
            fresh.Add(dup)
            readded += 1
    for t in fresh.GetTracks():
        t.SetLocked(False)                       # don't leave the board locked
    force_gnd_zones(fresh)
    pcbnew.SaveBoard(out, fresh)

    final = pcbnew.LoadBoard(out)
    # A bridge is a WIRE the user has to solder, not a track segment. FreeRouting
    # emits a corner as several segments, so counting segments overstates the
    # build cost badly (26 segments for 9 connections on the first real run).
    # Count connected runs per net instead: segments that share an endpoint are
    # one wire.
    f_segs = [t for t in final.GetTracks()
              if t.GetClass() != "PCB_VIA" and t.GetLayer() == pcbnew.F_Cu]
    bridges = _count_runs(f_segs)
    vias = sum(1 for t in final.GetTracks() if t.GetClass() == "PCB_VIA")
    left = unrouted_count(final)
    return {"routed_pcb": out, "unrouted": left, "bridges": bridges,
            "bridge_segments": len(f_segs), "vias": vias, "readded": readded,
            "seconds": round(dt, 1)}


def _count_runs(segs) -> int:
    """Number of connected runs among track segments (union-find on endpoints).

    Two segments belong to the same physical wire when they share an endpoint
    and a net.
    """
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, t in enumerate(segs):
        net = t.GetNetCode()
        a = (net, t.GetStart().x, t.GetStart().y)
        b = (net, t.GetEnd().x, t.GetEnd().y)
        find(("seg", i))
        union(("seg", i), a)
        union(a, b)
    return len({find(("seg", i)) for i in range(len(segs))})


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
    routed_pcb = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(routed_pcb, board)
    if sides == 1:
        _flip_to_bottom(routed_pcb)             # single-sided copper -> B.Cu
        # Measure the board we actually hand back, not the pre-flip one. The flip
        # rebuilds the pour, and connectivity is only meaningful after that; the
        # refine loop steers on this number, so it has to describe the real file.
        left = unrouted_count(pcbnew.LoadBoard(routed_pcb))
    else:
        left = unrouted_count(board)
    routed = total - left
    return {
        "total": total, "routed": routed, "unrouted": left,
        "pct": (100.0 * routed / total if total else 100.0),
        "ses_path": ses, "seconds": round(dt, 1), "routed_pcb": routed_pcb,
    }
