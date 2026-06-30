"""Headless tests for connector edge assignment. No pcbnew. Pure Python."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import edge                                   # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402


def _conn(ref, x, y, net, w=4.0, h=4.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y,
                     pads=[Pad("1", net, 0.0, 0.0)])


def _part(ref, x, y, net):
    return Component(ref=ref, w=4.0, h=2.0, x=x, y=y,
                     pads=[Pad("1", net, 0.0, 0.0)])


def test_connector_assigned_to_edge_nearest_its_partners():
    # J1 wired to P1 which sits on the right side -> J1 belongs on edge R.
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": _conn("J1", 50, 30, "SIG"),
        "P1": _part("P1", 92, 30, "SIG"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge == "R"


def test_connector_lands_on_its_edge_line():
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": _conn("J1", 50, 30, "SIG"),
        "P1": _part("P1", 92, 30, "SIG"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    c = b.components["J1"]
    # right edge: right side of courtyard within one margin of the outline edge
    assert abs(c.right - b.x1) <= 0.8 + 1e-6


def test_connectors_on_same_edge_do_not_overlap():
    b = Board(0, 0, 60, 100)
    # two connectors both pulled left
    b.components = {
        "J1": _conn("J1", 30, 40, "A"),
        "J2": _conn("J2", 30, 44, "B"),
        "PA": _part("PA", 4, 40, "A"),
        "PB": _part("PB", 4, 44, "B"),
    }
    edge.assign_edges(b, ["J1", "J2"], margin=0.8)
    a, c = b.components["J1"], b.components["J2"]
    assert a.edge == "L" and c.edge == "L"
    gap = abs(a.y - c.y) - (a.eff_h + c.eff_h) / 2
    assert gap >= 0.8 - 1e-6


def test_connector_with_no_signal_partners_still_gets_an_edge():
    b = Board(0, 0, 100, 60)
    b.components = {"J1": _conn("J1", 10, 30, "")}   # empty net == unconnected
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge in ("L", "R", "T", "B")


def test_locked_connector_is_left_alone():
    b = Board(0, 0, 100, 60)
    b.components = {"J1": _conn("J1", 50, 30, "SIG")}
    b.components["J1"].locked = True
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge == ""          # untouched


def test_orient_toward_picks_rotation_facing_target():
    # Pads offset +x in local coords (centroid offset (1.5, 0) from body centre).
    # At rot=90:  pad_world rotates (ox, oy) -> (oy, -ox) => (−0.5, −1.5) & (+0.5, −1.5)
    #             pad centroid relative to body = (0, -1.5) => faces -y (upward in KiCad coords)
    # At rot=270: (ox, oy) -> (−oy, ox) => (+0.5, +1.5) & (−0.5, +1.5)
    #             pad centroid relative to body = (0, +1.5) => faces +y (downward)
    c = Component("J1", 4, 8, x=10, y=30,
                  pads=[Pad("1", "A", 1.5, -0.5), Pad("2", "B", 1.5, 0.5)])
    rot_before = c.rot
    assert edge._orient_toward(c, "L", 10, 50) == 270   # target below (+y) -> rot270 faces it
    assert edge._orient_toward(c, "L", 10, 10) == 90    # target above (-y) -> rot90 faces it
    assert c.rot == rot_before                            # _orient_toward must not permanently mutate c.rot
