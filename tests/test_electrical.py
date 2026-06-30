"""Headless tests for electrical structural detectors. No pcbnew.

  python -m pytest tests/test_electrical.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import electrical                       # noqa: E402
from autoplace.model import Board, Component, Pad       # noqa: E402


def _cap(ref, x, y, rail, gnd):
    return Component(ref, 2, 1, x=x, y=y,
                     pads=[Pad("1", rail, -0.8, 0.0), Pad("2", gnd, 0.8, 0.0)])


def _ic(ref, x, y, rail):
    return Component(ref, 6, 6, x=x, y=y, pads=[
        Pad("1", rail, -2.0, 0.0), Pad("2", "GND", 2.0, 0.0), Pad("3", "SIG", 0.0, 2.0)])


def test_decap_pairs_to_nearest_ic_on_rail():
    b = Board(0, 0, 100, 100)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "U2": _ic("U2", 90, 90, "+5V"),     # same rail, far away
        "C1": _cap("C1", 14, 10, "+5V", "GND"),  # near U1
    }
    pairs = electrical.decoupling_pairs(b)
    assert pairs["C1"][1] == "U1"           # nearest IC on the rail
    assert pairs["C1"][0] == 0              # cap rail pad index (pad "1" -> +5V)


def test_decap_skipped_when_no_ic_on_rail():
    b = Board(0, 0, 50, 50)
    b.components = {"C1": _cap("C1", 10, 10, "+5V", "GND")}  # no IC at all
    assert electrical.decoupling_pairs(b) == {}


def test_two_pin_non_power_gnd_is_not_a_decap():
    b = Board(0, 0, 50, 50)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "R1": Component("R1", 2, 1, x=20, y=20,
                        pads=[Pad("1", "SIG", -0.8, 0), Pad("2", "N1", 0.8, 0)]),
    }
    assert electrical.decoupling_pairs(b) == {}


def test_three_pad_part_is_not_a_decap():
    b = Board(0, 0, 50, 50)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "U2": _ic("U2", 20, 20, "+5V"),     # 3-pad, on the rail+gnd, but not a 2-pad cap
    }
    pairs = electrical.decoupling_pairs(b)
    assert "U2" not in pairs                # 3-pad part is never classified a decap


def test_nearest_tie_broken_by_ref():
    b = Board(0, 0, 100, 100)
    b.components = {
        "U2": _ic("U2", 10, 10, "+5V"),
        "U1": _ic("U1", 18, 10, "+5V"),     # equidistant-ish; force a tie below
        "C1": _cap("C1", 14, 10, "+5V", "GND"),
    }
    # both IC rail pads at x=10-2=8 (U2) and 18-2=16 (U1); cap rail pad at 14-0.8=13.2
    # dist to U2 pad = |13.2-8|=5.2 ; to U1 pad = |16-13.2|=2.8 -> U1 nearer (not a tie),
    # so assert the nearer one wins deterministically.
    assert electrical.decoupling_pairs(b)["C1"][1] == "U1"
