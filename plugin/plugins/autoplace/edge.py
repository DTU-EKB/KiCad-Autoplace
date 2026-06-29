"""Connector edge assignment (pure-Python, no pcbnew).

A connector flagged by the user is pinned to the board edge nearest the
circuitry it feeds, then slides ALONG that edge during annealing (see
``anneal.py``). This module computes the edge and the on-edge position; the
annealer keeps it there via ``pin_to_edge``.
"""
from __future__ import annotations

from .metrics import _is_power
from .model import Board, Component

EDGES = ("L", "R", "T", "B")


def nearest_edge(board: Board, x: float, y: float) -> str:
    """The board edge ('L'/'R'/'T'/'B') closest to point (x, y)."""
    dists = {
        "L": x - board.x0,
        "R": board.x1 - x,
        "T": y - board.y0,
        "B": board.y1 - y,
    }
    return min(dists, key=dists.get)


def pin_to_edge(c: Component, board: Board, margin: float = 0.8) -> None:
    """Set the perpendicular coordinate so c's courtyard sits against c.edge."""
    if c.edge == "L":
        c.x = board.x0 + margin + c.eff_w / 2
    elif c.edge == "R":
        c.x = board.x1 - margin - c.eff_w / 2
    elif c.edge == "T":
        c.y = board.y0 + margin + c.eff_h / 2
    elif c.edge == "B":
        c.y = board.y1 - margin - c.eff_h / 2


def _partner_centroid(board: Board, c: Component) -> tuple[float, float]:
    """Centroid of pad positions on OTHER comps sharing c's signal nets."""
    my_nets = {p.net for p in c.pads if p.net and not _is_power(p.net)}
    pts = []
    for other in board.components.values():
        if other is c:
            continue
        for p in other.pads:
            if p.net in my_nets:
                pts.append(other.pad_world(p))
    if not pts:
        return c.x, c.y
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def _along(c: Component) -> float:
    """The coordinate that varies along c's edge (y on L/R, x on T/B)."""
    return c.y if c.edge in ("L", "R") else c.x


def _set_along(c: Component, v: float) -> None:
    if c.edge in ("L", "R"):
        c.y = v
    else:
        c.x = v


def _clamp_along(c: Component, board: Board, margin: float) -> None:
    if c.edge in ("L", "R"):
        lo, hi = board.y0 + margin + c.eff_h / 2, board.y1 - margin - c.eff_h / 2
    else:
        lo, hi = board.x0 + margin + c.eff_w / 2, board.x1 - margin - c.eff_w / 2
    _set_along(c, min(max(_along(c), lo), hi))


def _span(c: Component) -> float:
    """c's extent along its edge."""
    return c.eff_h if c.edge in ("L", "R") else c.eff_w


def assign_edges(board: Board, connectors, margin: float = 0.8) -> None:
    """Pin each given connector to the edge nearest its net partners."""
    conns = [board.components[r] for r in connectors
             if r in board.components and not board.components[r].locked]
    for c in conns:
        c.is_connector = True
        cx, cy = _partner_centroid(board, c)
        c.edge = nearest_edge(board, cx, cy)
        c.rot = 90 if c.edge in ("L", "R") else 0
        _set_along(c, cy if c.edge in ("L", "R") else cx)
        pin_to_edge(c, board, margin)
        _clamp_along(c, board, margin)
    # de-collide connectors sharing an edge: sort along the edge, push apart
    for e in EDGES:
        group = sorted((c for c in conns if c.edge == e), key=_along)
        for i in range(1, len(group)):
            prev, cur = group[i - 1], group[i]
            need = (_span(prev) + _span(cur)) / 2 + margin
            if _along(cur) - _along(prev) < need:
                _set_along(cur, _along(prev) + need)
        for c in group:
            _clamp_along(c, board, margin)
            pin_to_edge(c, board, margin)
