"""The app sidecar's pins (dragged positions + locks) must reach the engine."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import engine, metrics, sidecar  # noqa: E402
from autoplace.model import Board, Component, Pad  # noqa: E402


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


def test_apply_pins_moves_and_locks():
    b = _board()
    moved, locked = sidecar.apply_pins(
        b, positions={"R2": [30.0, 31.5]}, locked=["R2", "R3"])
    assert (moved, locked) == (1, 2)
    assert (b.components["R2"].x, b.components["R2"].y) == (30.0, 31.5)
    assert b.components["R2"].locked and b.components["R3"].locked
    assert not b.components["R1"].locked


def test_apply_pins_ignores_stale_refs():
    b = _board()
    moved, locked = sidecar.apply_pins(
        b, positions={"GONE": [1, 2], "R1": [9.0, 9.0]}, locked=["ALSO_GONE"])
    assert (moved, locked) == (1, 0)
    assert (b.components["R1"].x, b.components["R1"].y) == (9.0, 9.0)


def test_apply_pins_handles_empty_sidecar():
    b = _board()
    assert sidecar.apply_pins(b, positions=None, locked=None) == (0, 0)


def test_pinned_part_survives_placement():
    b = _board()
    sidecar.apply_pins(b, positions={"R4": [20.0, 20.0]}, locked=["R4"])
    engine.place(b, seed=0)
    c = b.components["R4"]
    assert (c.x, c.y) == (20.0, 20.0)          # pinned exactly where dragged
    assert metrics.overlaps(b) == []           # others placed legally around it
