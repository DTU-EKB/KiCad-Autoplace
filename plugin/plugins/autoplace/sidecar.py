"""Apply the desktop app's sidecar pins to a Board model (pure, no pcbnew).

The app stores user intent next to the board as ``<stem>.autoplace.json``:
``connectors`` (edge-pinned refs, consumed by ``engine.place``), plus the pins
this module applies -- ``positions`` (``{ref: [x, y]}`` in mm board coordinates,
from dragging parts on the canvas) and ``locked`` (refs the engine must not
move). Positions are applied first, then locks: a dragged+locked part is pinned
exactly where the user put it; a dragged-but-unlocked part is only a starting
suggestion (``place`` re-seeds it, ``refine`` anneals from it).
"""
from __future__ import annotations

from .model import Board


def apply_pins(board: Board, positions: dict | None = None,
               locked: list | None = None) -> tuple[int, int]:
    """Apply dragged positions and locks; returns ``(n_moved, n_locked)``.

    Refs that no longer exist on the board (stale sidecar) are ignored.
    """
    moved = 0
    for ref, xy in sorted((positions or {}).items()):
        c = board.components.get(ref)
        if c is not None and isinstance(xy, (list, tuple)) and len(xy) == 2:
            c.x, c.y = float(xy[0]), float(xy[1])
            moved += 1
    locked_n = 0
    for ref in sorted(locked or []):
        c = board.components.get(ref)
        if c is not None:
            c.locked = True
            locked_n += 1
    return moved, locked_n
