"""Shared geometry helpers (pure-Python, no pcbnew)."""
from __future__ import annotations

from .model import Board, Component


def clamp_center(c: Component, board: Board, margin: float) -> None:
    """Clamp a component centre so its effective bbox stays inside the outline,
    inset by ``margin + board.edge_keepout`` on every side. ``edge_keepout``
    defaults to 0.0, so the inset reduces to ``margin`` -- identical to the
    pre-existing per-phase clamps."""
    inset = margin + board.edge_keepout
    hw, hh = c.eff_w / 2, c.eff_h / 2
    c.x = min(max(c.x, board.x0 + hw + inset), board.x1 - hw - inset)
    c.y = min(max(c.y, board.y0 + hh + inset), board.y1 - hh - inset)
