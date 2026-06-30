"""Headless tests for the shared clamp helper + Board.edge_keepout. No pcbnew.

  python -m pytest tests/test_edge_keepout.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import engine, geom                   # noqa: E402
from autoplace.model import Board, Component, Pad     # noqa: E402


def _two_pin(ref, x, y, neta, netb, w=2.0, h=1.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y, pads=[
        Pad("1", neta, -w / 2 + 0.2, 0.0),
        Pad("2", netb, w / 2 - 0.2, 0.0),
    ])


def _board():
    b = Board(0, 0, 60, 60)
    b.components = {
        "R1": _two_pin("R1", 5, 5, "VIN", "N1"),
        "R2": _two_pin("R2", 55, 55, "N1", "N2"),
        "R3": _two_pin("R3", 5, 55, "N2", "N3"),
        "R4": _two_pin("R4", 55, 5, "N3", "GND"),
    }
    return b


def test_clamp_center_no_keepout_matches_margin():
    b = Board(0, 0, 20, 20)                 # edge_keepout defaults to 0.0
    c = Component("C", 4, 4, x=100, y=100)
    geom.clamp_center(c, b, 0.8)
    assert c.x == 20 - 2 - 0.8              # x1 - half_w - (margin + 0)
    assert c.y == 20 - 2 - 0.8


def test_clamp_center_insets_by_keepout():
    b = Board(0, 0, 20, 20, edge_keepout=2.0)
    c = Component("C", 4, 4, x=-100, y=-100)
    geom.clamp_center(c, b, 0.8)
    assert c.x == 0 + 2 + 0.8 + 2.0         # x0 + half_w + margin + keepout
    assert c.y == 0 + 2 + 0.8 + 2.0


def test_place_respects_edge_keepout():
    b = _board()
    b.edge_keepout = 3.0
    engine.place(b, seed=0)
    for c in b.components.values():
        assert c.left >= b.x0 + 3.0 - 1e-6
        assert c.right <= b.x1 - 3.0 + 1e-6
        assert c.top >= b.y0 + 3.0 - 1e-6
        assert c.bottom <= b.y1 - 3.0 + 1e-6
