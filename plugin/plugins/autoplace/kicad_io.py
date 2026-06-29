"""The only module that imports ``pcbnew``.

Builds a plain :class:`~autoplace.model.Board` from a KiCad board and writes a
computed placement back. Keeping all ``pcbnew`` access here lets the engine and
metrics run headless under any Python for tests and CI.

M2 translates components only (orientation unchanged), so each footprint is moved
by ``new_centre - old_centre`` -- exact, and it sidesteps the bbox-vs-pad1 anchor
quirk the DTU ``pcb_build.py`` has to fight.
"""
from __future__ import annotations

import os
import shutil

import pcbnew

from .model import Board, Component, Pad

_CONNECTOR_HINTS = ("Connector", "TerminalBlock", "PinHeader", "Screw")


def _mm(v) -> float:
    return pcbnew.ToMM(v)


def _is_connector(fp) -> bool:
    ref = fp.GetReference()
    if ref and ref[0] == "J":
        return True
    fpid = fp.GetFPIDAsString()
    return any(h in fpid for h in _CONNECTOR_HINTS)


def build_model(pcb: "pcbnew.BOARD") -> Board:
    """Build a plain Board model from a live pcbnew board (no disk I/O).

    Used by both ``load_board`` (file path) and the Action Plugin (the board
    already open in the editor).
    """
    edge = pcb.GetBoardEdgesBoundingBox()
    board = Board(
        x0=_mm(edge.GetLeft()), y0=_mm(edge.GetTop()),
        x1=_mm(edge.GetRight()), y1=_mm(edge.GetBottom()),
    )
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        bb = fp.GetBoundingBox(False)               # geometry, no text
        cx, cy = _mm(bb.GetCenter().x), _mm(bb.GetCenter().y)
        try:
            sheet = fp.GetSheetname() or ""
        except Exception:
            sheet = ""
        comp = Component(
            ref=ref,
            w=_mm(bb.GetWidth()), h=_mm(bb.GetHeight()),
            x=cx, y=cy,
            locked=fp.IsLocked(),
            is_connector=_is_connector(fp),
            sheet=sheet,
        )
        for pad in fp.Pads():
            pp = pad.GetPosition()
            comp.pads.append(Pad(
                name=pad.GetNumber(),
                net=pad.GetNetname() or "",
                ox=_mm(pp.x) - cx,                   # offset from bbox centre
                oy=_mm(pp.y) - cy,
            ))
        board.components[ref] = comp
    return board


def load_board(path: str) -> tuple[Board, "pcbnew.BOARD"]:
    """Return (model Board, live pcbnew board). Keep the live board to write back."""
    pcb = pcbnew.LoadBoard(path)
    if pcb is None:
        raise RuntimeError(
            f"KiCad could not load {path!r}. The file is likely saved in a newer "
            f"KiCad format than this pcbnew ({pcbnew.GetBuildVersion()}). Open it in "
            f"the matching KiCad version, or run with that version's Python."
        )
    return build_model(pcb), pcb


def copy_project(src_pcb_path: str, dst_pcb_path: str) -> bool:
    """Copy the source board's ``.kicad_pro`` next to the output board.

    Net-class rules (track width, clearance) live in the project file, and
    ``ExportSpecctraDSN`` reads widths from it -- without the project file every
    net falls back to KiCad's 0.2 mm default, so FreeRouting would route
    un-manufacturable hair-thin traces instead of the 1.0 mm fiber-laser tracks.
    """
    src_pro = os.path.splitext(src_pcb_path)[0] + ".kicad_pro"
    dst_pro = os.path.splitext(dst_pcb_path)[0] + ".kicad_pro"
    if os.path.exists(src_pro) and os.path.abspath(src_pro) != os.path.abspath(dst_pro):
        shutil.copyfile(src_pro, dst_pro)
        return True
    return False


def unrouted_count(pcb: "pcbnew.BOARD") -> int:
    """Number of unrouted ratsnest connections (0 == fully routed)."""
    pcb.BuildConnectivity()
    conn = pcb.GetConnectivity()
    try:
        return conn.GetUnconnectedCount(True)      # KiCad 8/9/10 signature
    except TypeError:
        return conn.GetUnconnectedCount()


def apply_to_board(board: Board, pcb: "pcbnew.BOARD"):
    """Apply each footprint's computed rotation + centre to a live board (no save).

    Rotation is applied about the footprint's bounding-box centre first (so the
    model's rotated pad offsets match the board exactly -- verified against
    pcbnew's Rotate(+deg): (x, y) -> (y, -x)), then the part is translated so its
    bbox centre lands on the computed position. Locked footprints are never moved.
    """
    by_ref = {fp.GetReference(): fp for fp in pcb.GetFootprints()}
    for ref, comp in board.components.items():
        fp = by_ref.get(ref)
        if fp is None or fp.IsLocked():
            continue
        if comp.rot:
            centre = fp.GetBoundingBox(False).GetCenter()
            fp.Rotate(centre, pcbnew.EDA_ANGLE(comp.rot, pcbnew.DEGREES_T))
        cur = fp.GetBoundingBox(False).GetCenter()
        dx = pcbnew.FromMM(comp.x) - cur.x
        dy = pcbnew.FromMM(comp.y) - cur.y
        if dx or dy:
            fp.Move(pcbnew.VECTOR2I(int(dx), int(dy)))


def apply_placement(board: Board, pcb: "pcbnew.BOARD", out_path: str):
    """Apply the placement to ``pcb`` and save to ``out_path`` (CLI / bench path)."""
    apply_to_board(board, pcb)
    pcbnew.SaveBoard(out_path, pcb)
