"""Read and grow the Edge.Cuts board outline.

The outline is the placement boundary, and the user chose it -- it usually
reflects an enclosure, a piece of stock, or the machine's fixturing. So the
engine treats it as a constraint to respect, not a suggestion: it grows the board
only when placement inside the given outline cannot be made routable, only in
whole steps, and never past the caller's cap. Every growth is reported in the
result so a board that came back 5 mm larger than requested says so.

``grow_rect`` is the pure geometry (unit-tested); ``grow_board`` applies it to a
live pcbnew board.
"""
from __future__ import annotations


def rect_of(segments) -> tuple[float, float, float, float] | None:
    """Bounding rect (x0, y0, x1, y1) of Edge.Cuts geometry given as segments
    [(x1, y1, x2, y2), ...]. None when there is no outline."""
    if not segments:
        return None
    xs = [v for s in segments for v in (s[0], s[2])]
    ys = [v for s in segments for v in (s[1], s[3])]
    return (min(xs), min(ys), max(xs), max(ys))


def grow_rect(rect, mm: float, *, anchor: str = "centre"):
    """Grow a rect by ``mm`` on each side (anchor='centre') or only right/bottom.

    Centre growth keeps the board concentric with the existing placement, so
    parts stay where they are relative to the outline and nothing needs moving.
    'corner' growth keeps the top-left origin fixed, which is what you want when
    the stock is registered against a jig corner.
    """
    x0, y0, x1, y1 = rect
    if mm <= 0:
        return (x0, y0, x1, y1)
    if anchor == "corner":
        return (x0, y0, x1 + 2 * mm, y1 + 2 * mm)
    return (x0 - mm, y0 - mm, x1 + mm, y1 + mm)


def fits_inside(rect, items, clearance: float = 0.0) -> bool:
    """Do all (x0, y0, x1, y1) item boxes sit inside rect (with clearance)?"""
    bx0, by0, bx1, by1 = rect
    for x0, y0, x1, y1 in items:
        if (x0 < bx0 + clearance or y0 < by0 + clearance
                or x1 > bx1 - clearance or y1 > by1 - clearance):
            return False
    return True


def required_growth(rect, items, clearance: float = 0.0,
                    step: float = 5.0, cap: float = 20.0) -> float:
    """Smallest multiple of ``step`` (<= cap) that fits every item, else -1.

    Used to answer "is the outline itself the problem?" before assuming the
    placement is at fault.
    """
    grown = 0.0
    while grown <= cap:
        if fits_inside(grow_rect(rect, grown), items, clearance):
            return grown
        grown += step
    return -1.0


def grow_board(pcb, mm: float, *, anchor: str = "centre") -> dict:
    """Grow a live board's Edge.Cuts rectangle by ``mm`` per side.

    Handles the two ways an outline is drawn: a single ``SHAPE_T_RECT`` (what
    this pipeline emits) is resized directly; four segments are moved outward.
    Returns the old and new rect so the caller can report the change.
    """
    import pcbnew

    shapes = [d for d in pcb.GetDrawings()
              if d.GetLayer() == pcbnew.Edge_Cuts and d.GetClass() == "PCB_SHAPE"]
    if not shapes:
        raise RuntimeError("no Edge.Cuts outline to grow")
    segs = [(pcbnew.ToMM(s.GetStart().x), pcbnew.ToMM(s.GetStart().y),
             pcbnew.ToMM(s.GetEnd().x), pcbnew.ToMM(s.GetEnd().y)) for s in shapes]
    old = rect_of(segs)
    new = grow_rect(old, mm, anchor=anchor)

    rects = [s for s in shapes if s.GetShape() == pcbnew.SHAPE_T_RECT]
    if rects:
        r = rects[0]
        r.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(new[0]), pcbnew.FromMM(new[1])))
        r.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(new[2]), pcbnew.FromMM(new[3])))
        for extra in rects[1:]:
            pcb.Remove(extra)
    else:
        # Segment outline: push each endpoint out to the new rect.
        def snap(v, lo, hi, nlo, nhi):
            return nlo if abs(v - lo) <= abs(v - hi) else nhi
        for s in shapes:
            for setter, getter in ((s.SetStart, s.GetStart), (s.SetEnd, s.GetEnd)):
                p = getter()
                x = snap(pcbnew.ToMM(p.x), old[0], old[2], new[0], new[2])
                y = snap(pcbnew.ToMM(p.y), old[1], old[3], new[1], new[3])
                setter(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    return {"old": old, "new": new, "grew_mm": mm,
            "size_before": (round(old[2] - old[0], 2), round(old[3] - old[1], 2)),
            "size_after": (round(new[2] - new[0], 2), round(new[3] - new[1], 2))}
