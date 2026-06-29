"""Legalisation: turn the force-directed result into a manufacturable layout.

1. Iteratively push apart any remaining overlaps (free parts only).
2. Snap free components to the placement grid.
3. Clamp everything inside the outline.

Locked components are obstacles but are never moved. Mirrors the
overlap-free guarantee asserted by the DTU ``place_system3.py``.
"""
from __future__ import annotations

from .model import Board, Component
from .metrics import overlaps


def _snap(v: float, grid: float) -> float:
    return round(v / grid) * grid


def _clamp(c: Component, board: Board, margin: float):
    hw, hh = c.eff_w / 2, c.eff_h / 2
    c.x = min(max(c.x, board.x0 + hw + margin), board.x1 - hw - margin)
    c.y = min(max(c.y, board.y0 + hh + margin), board.y1 - hh - margin)


def push_apart(board: Board, *, margin: float = 0.8, iters: int = 200):
    # edge connectors are fixed obstacles here: they were already placed on the
    # edge and slid along it during annealing; legalize must not move them off.
    free = {c.ref for c in board.free() if not c.edge}
    comps = list(board.components.values())
    for _ in range(iters):
        moved = False
        for i in range(len(comps)):
            a = comps[i]
            for j in range(i + 1, len(comps)):
                b = comps[j]
                ox = (a.eff_w + b.eff_w) / 2 + margin - abs(a.x - b.x)
                oy = (a.eff_h + b.eff_h) / 2 + margin - abs(a.y - b.y)
                if ox <= 0 or oy <= 0:
                    continue
                moved = True
                dx = a.x - b.x or 0.01
                dy = a.y - b.y or 0.01
                if ox < oy:
                    shift = ox / 2 if (a.ref in free and b.ref in free) else ox
                    s = shift if dx > 0 else -shift
                    if a.ref in free:
                        a.x += s
                    if b.ref in free:
                        b.x -= s
                else:
                    shift = oy / 2 if (a.ref in free and b.ref in free) else oy
                    s = shift if dy > 0 else -shift
                    if a.ref in free:
                        a.y += s
                    if b.ref in free:
                        b.y -= s
        for c in comps:
            if c.ref in free:
                _clamp(c, board, margin)
        if not moved:
            break
    return board


def legalize(board: Board, *, grid: float = 0.5, margin: float = 0.8):
    push_apart(board, margin=margin)
    for c in board.free():
        if c.edge:
            continue
        c.x = _snap(c.x, grid)
        c.y = _snap(c.y, grid)
        _clamp(c, board, margin)
    # snapping can re-introduce a touch of overlap; one more gentle pass
    push_apart(board, margin=margin, iters=60)
    return overlaps(board)
