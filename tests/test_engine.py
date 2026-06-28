"""Headless tests for the placement core. No pcbnew required -- runs on any Python.

  python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import engine, metrics              # noqa: E402
from autoplace.model import Board, Component, Pad   # noqa: E402


def _two_pin(ref, x, y, neta, netb, w=2.0, h=1.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y, pads=[
        Pad("1", neta, -w / 2 + 0.2, 0.0),
        Pad("2", netb, w / 2 - 0.2, 0.0),
    ])


def _board():
    # Four parts in a chain N1-N2-N3, deliberately placed far apart.
    b = Board(0, 0, 60, 60)
    b.components = {
        "R1": _two_pin("R1", 5, 5, "VIN", "N1"),
        "R2": _two_pin("R2", 55, 55, "N1", "N2"),
        "R3": _two_pin("R3", 5, 55, "N2", "N3"),
        "R4": _two_pin("R4", 55, 5, "N3", "GND"),
    }
    return b


def test_overlaps_detected_and_cleared():
    b = Board(0, 0, 30, 30)
    b.components = {
        "A": Component("A", 4, 4, x=10, y=10),
        "B": Component("B", 4, 4, x=11, y=11),   # overlapping A
    }
    assert metrics.overlaps(b)                    # starts overlapping
    engine.place(b, seed=1)
    assert metrics.overlaps(b) == []              # legal after placement


def test_placement_reduces_wirelength():
    b = _board()
    before = metrics.hpwl(b)
    engine.place(b, seed=0)
    after = metrics.hpwl(b)
    assert after < before                         # connected parts pulled together
    assert metrics.overlaps(b) == []


def test_locked_parts_never_move():
    b = _board()
    b.components["R1"].locked = True
    x0, y0 = b.components["R1"].x, b.components["R1"].y
    engine.place(b, seed=0)
    assert (b.components["R1"].x, b.components["R1"].y) == (x0, y0)


def test_deterministic():
    b1, b2 = _board(), _board()
    engine.place(b1, seed=42)
    engine.place(b2, seed=42)
    for ref in b1.components:
        assert b1.components[ref].x == b2.components[ref].x
        assert b1.components[ref].y == b2.components[ref].y


def test_all_parts_inside_outline():
    b = _board()
    engine.place(b, seed=3)
    for c in b.components.values():
        assert c.left >= b.x0 - 1e-6 and c.right <= b.x1 + 1e-6
        assert c.top >= b.y0 - 1e-6 and c.bottom <= b.y1 + 1e-6
