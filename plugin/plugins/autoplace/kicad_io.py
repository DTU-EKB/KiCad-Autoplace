"""The only module that imports ``pcbnew``.

Builds a plain :class:`~autoplace.model.Board` from a KiCad board and writes a
computed placement back. Keeping all ``pcbnew`` access here lets the engine and
metrics run headless under any Python for tests and CI.

M2 translates components only (orientation unchanged), so each footprint is moved
by ``new_centre - old_centre`` -- exact, and it sidesteps the bbox-vs-pad1 anchor
quirk the DTU ``pcb_build.py`` has to fight.
"""
from __future__ import annotations

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


def load_board(path: str) -> tuple[Board, "pcbnew.BOARD"]:
    """Return (model Board, live pcbnew board). Keep the live board to write back."""
    pcb = pcbnew.LoadBoard(path)
    if pcb is None:
        raise RuntimeError(
            f"KiCad could not load {path!r}. The file is likely saved in a newer "
            f"KiCad format than this pcbnew ({pcbnew.GetBuildVersion()}). Open it in "
            f"the matching KiCad version, or run with that version's Python."
        )
    edge = pcb.GetBoardEdgesBoundingBox()
    board = Board(
        x0=_mm(edge.GetLeft()), y0=_mm(edge.GetTop()),
        x1=_mm(edge.GetRight()), y1=_mm(edge.GetBottom()),
    )
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        bb = fp.GetBoundingBox(False)               # geometry, no text
        cx, cy = _mm(bb.GetCenter().x), _mm(bb.GetCenter().y)
        comp = Component(
            ref=ref,
            w=_mm(bb.GetWidth()), h=_mm(bb.GetHeight()),
            x=cx, y=cy,
            locked=fp.IsLocked(),
            is_connector=_is_connector(fp),
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
    return board, pcb


def apply_placement(board: Board, pcb: "pcbnew.BOARD", out_path: str):
    """Apply each footprint's computed rotation + centre and save to out_path.

    Rotation is applied about the footprint's bounding-box centre first (so the
    model's rotated pad offsets match the board exactly -- verified against
    pcbnew's Rotate(+deg): (x, y) -> (y, -x)), then the part is translated so its
    bbox centre lands on the computed position.
    """
    by_ref = {fp.GetReference(): fp for fp in pcb.GetFootprints()}
    for ref, comp in board.components.items():
        fp = by_ref.get(ref)
        if fp is None:
            continue
        if comp.rot:
            centre = fp.GetBoundingBox(False).GetCenter()
            fp.Rotate(centre, pcbnew.EDA_ANGLE(comp.rot, pcbnew.DEGREES_T))
        cur = fp.GetBoundingBox(False).GetCenter()
        dx = pcbnew.FromMM(comp.x) - cur.x
        dy = pcbnew.FromMM(comp.y) - cur.y
        if dx or dy:
            fp.Move(pcbnew.VECTOR2I(int(dx), int(dy)))
    pcbnew.SaveBoard(out_path, pcb)
