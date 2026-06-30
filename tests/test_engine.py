"""Headless tests for the placement core. No pcbnew required -- runs on any Python.

  python -m pytest tests/
"""
import os
import random
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


def _cohesion_trap_board(anchors=3):
    """Two free parts P (sheet A) and Q (sheet B) share net LINK, so wirelength
    wants them adjacent. Each sheet also has `anchors` LOCKED parts pinned at the
    opposite edges, dragging each sheet's block centroid outward -- so block
    cohesion pulls P and Q *apart*, fighting wirelength. P and Q are seeded
    adjacent in the middle (the low-wirelength layout).
    """
    b = Board(0, 0, 80, 24)
    comps = {
        "P": Component("P", 4, 2, x=38, y=12, sheet="/A/",
                       pads=[Pad("1", "LINK", -1.8, 0.0), Pad("2", "up", 1.8, 0.0)]),
        "Q": Component("Q", 4, 2, x=42, y=12, sheet="/B/",
                       pads=[Pad("1", "LINK", -1.8, 0.0), Pad("2", "uq", 1.8, 0.0)]),
    }
    for k in range(anchors):
        comps[f"AL{k}"] = Component(f"AL{k}", 2, 2, x=3, y=4 + 6 * k, sheet="/A/",
                                    locked=True, pads=[Pad("1", f"al{k}", 0.0, 0.0)])
        comps[f"BL{k}"] = Component(f"BL{k}", 2, 2, x=77, y=4 + 6 * k, sheet="/B/",
                                    locked=True, pads=[Pad("1", f"bl{k}", 0.0, 0.0)])
    b.components = comps
    return b


def test_anneal_returns_best_quality_not_lowest_cost():
    # Regression: the annealer kept the layout minimising its full internal cost
    # (overlap barrier + block cohesion), discarding the far lower-wirelength
    # layouts it actually visited. Here strong cohesion drags the two wired parts
    # apart: old selection returned HPWL ~62 (15x the adjacent seed), while
    # ranking kept layouts by placement quality (wirelength + overlap only)
    # returns < 20. Exercised on the annealer directly, independent of the
    # engine's channel-weight policy.
    from autoplace import anneal, blocks, legalize
    for seed in range(4):
        b = _cohesion_trap_board()
        blocks.detect_blocks(b)
        anneal.anneal(b, seed=seed, steps=6000, margin=0.8,
                      channel_scale=0.0, cohesion_scale=5.0)
        legalize.legalize(b, grid=0.5, margin=0.8)
        assert metrics.overlaps(b) == []
        assert metrics.hpwl(b) < 30, f"seed {seed}: HPWL {metrics.hpwl(b):.0f}"


def test_blocks_separates_clusters():
    from autoplace import blocks
    # Two internally-wired clusters with no signal net between them.
    b = Board(0, 0, 80, 80)
    b.components = {
        "A1": _two_pin("A1", 5, 5, "x1", "x2"),
        "A2": _two_pin("A2", 9, 5, "x2", "x3"),
        "A3": _two_pin("A3", 13, 5, "x3", "x1"),
        "B1": _two_pin("B1", 70, 70, "y1", "y2"),
        "B2": _two_pin("B2", 66, 70, "y2", "y3"),
        "B3": _two_pin("B3", 62, 70, "y3", "y1"),
    }
    label = blocks.detect_blocks(b)
    assert label["A1"] == label["A2"] == label["A3"]
    assert label["B1"] == label["B2"] == label["B3"]
    assert label["A1"] != label["B1"]


def test_all_parts_inside_outline():
    b = _board()
    engine.place(b, seed=3)
    for c in b.components.values():
        assert c.left >= b.x0 - 1e-6 and c.right <= b.x1 + 1e-6
        assert c.top >= b.y0 - 1e-6 and c.bottom <= b.y1 + 1e-6


def test_edge_connector_stays_on_its_edge_through_anneal():
    from autoplace import anneal, edge
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": Component("J1", 4, 4, x=50, y=30,
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "R1": _two_pin("R1", 20, 20, "SIG", "N1"),
        "R2": _two_pin("R2", 80, 40, "N1", "N2"),
        "R3": _two_pin("R3", 60, 10, "N2", "GND"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    j = b.components["J1"]
    assert j.edge in ("L", "R", "T", "B")
    pinned_axis = j.x if j.edge in ("L", "R") else j.y
    anneal.anneal(b, seed=0, steps=3000, margin=0.8)
    j = b.components["J1"]
    moved_axis = j.x if j.edge in ("L", "R") else j.y
    assert abs(moved_axis - pinned_axis) <= 1e-6   # never left the edge line


def test_legalize_keeps_edge_connector_on_edge():
    from autoplace import edge, legalize
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": Component("J1", 4, 4, x=50, y=30,
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "R1": _two_pin("R1", 90, 30, "SIG", "N1"),   # pulls J1 to edge R
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    x_before = b.components["J1"].x
    legalize.legalize(b, grid=0.5, margin=0.8)
    assert abs(b.components["J1"].x - x_before) <= 1e-6


def test_place_pins_explicit_connectors_to_edges():
    # hierarchical board so the floorplan path runs; J1 wired into block A
    b = Board(0, 0, 120, 80)
    b.components = {
        "J1": Component("J1", 4, 4, x=60, y=40, sheet="/A/",
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "A1": _two_pin("A1", 20, 20, "SIG", "a1"),
        "A2": _two_pin("A2", 24, 20, "a1", "a2"),
        "B1": _two_pin("B1", 100, 60, "b1", "b2"),
        "B2": _two_pin("B2", 96, 60, "b2", "b3"),
    }
    b.components["A1"].sheet = b.components["A2"].sheet = "/A/"
    b.components["B1"].sheet = b.components["B2"].sheet = "/B/"
    engine.place(b, seed=0, connectors=["J1"])
    j = b.components["J1"]
    assert j.edge in ("L", "R", "T", "B")
    # courtyard sits against its edge within one margin
    on_edge = (
        abs(j.left - b.x0) <= 0.8 + 1e-6 or abs(j.right - b.x1) <= 0.8 + 1e-6 or
        abs(j.top - b.y0) <= 0.8 + 1e-6 or abs(j.bottom - b.y1) <= 0.8 + 1e-6
    )
    assert on_edge
    assert metrics.overlaps(b) == []


def test_congestion_amplifies_channel_penalty():
    from autoplace import anneal
    b = Board(0, 0, 60, 60)
    b.components = {
        "A": Component("A", 4, 4, x=20, y=20),
        "B": Component("B", 4, 4, x=26.2, y=20),   # close: gx=2.2mm, gy=-4 -> channel term active
    }

    class HotField:
        empty = False
        def pressure_at(self, x, y):
            return 2.0

    base = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0)
    hot = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0,
                          congestion=HotField())
    a, bb = b.components["A"], b.components["B"]
    assert hot._pair_penalty(a, bb, 0.8) > base._pair_penalty(a, bb, 0.8)


def test_congestion_none_is_unchanged():
    from autoplace import anneal
    b1, b2 = _board(), _board()
    anneal.anneal(b1, seed=7, steps=2000, margin=0.8, channel_scale=0.5)
    anneal.anneal(b2, seed=7, steps=2000, margin=0.8, channel_scale=0.5,
                  congestion=None)
    for ref in b1.components:
        assert b1.components[ref].x == b2.components[ref].x
        assert b1.components[ref].y == b2.components[ref].y


def test_cross_block_gutter_widens_channel():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    a = Component("A", 4, 4, x=20, y=20, block="X")
    bb = Component("B", 4, 4, x=27.5, y=20, block="Y")   # gx = 7.5 - 4 = 3.5
    b.components = {"A": a, "B": bb}
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0)
    # gap 3.5 is beyond the single-track channel (2.6) but inside the cross-block
    # target (2.6 + gutter 1.8 = 4.4) -> cross-block pairs are penalised.
    cross = ann._pair_penalty(a, bb, 0.8)
    a.block = bb.block = "X"                              # same block now
    same = ann._pair_penalty(a, bb, 0.8)
    assert cross > 0
    assert same == 0
    assert cross > same


def test_dense_board_zeroes_the_gutter():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    a = Component("A", 4, 4, x=20, y=20, block="X")
    bb = Component("B", 4, 4, x=27.5, y=20, block="Y")
    b.components = {"A": a, "B": bb}
    # channel_scale 0 (dense board): the channel term is off entirely -> no gutter
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=0.0)
    assert ann._pair_penalty(a, bb, 0.8) == 0
