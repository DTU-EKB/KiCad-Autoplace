"""Headless tests for the Phase 0 cheap placement proxies. No pcbnew.

  python -m pytest tests/test_metrics_proxies.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import metrics                         # noqa: E402
from autoplace.model import Board, Component, Pad      # noqa: E402


def _part(ref, x, y, w=4.0, h=4.0, sheet="", locked=False, edge=""):
    return Component(ref=ref, w=w, h=h, x=x, y=y, sheet=sheet,
                     locked=locked, edge=edge, pads=[Pad("1", "N", 0.0, 0.0)])


def test_channel_width_is_track_plus_two_clearances():
    assert metrics.channel_width(0.8, 1.0) == 2.6     # laser default == today
    assert metrics.channel_width(0.85, 1.0) == 2.7    # cnc (the fixed value)


def test_sheet_spread_single_sheet_is_zero_sentinel():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 10, 10), "B": _part("B", 20, 20)}  # sheet ""
    # one qualifying sheet -> not enough to judge spread -> sentinel
    assert metrics.sheet_spread_score(b) == 0.0


def test_sheet_spread_excludes_locked_and_edge():
    # Geometry: two 4x4 parts per sheet at centres (a, a) and (a+4, a+4).
    #   bbox  = 8 x 8 = 64,  used = 16+16 = 32,  fill = 0.5  -> in [0.35, 0.6]
    #   -> penalty = 0  -> mean score = 0.0  (the discriminating value).
    #
    # Locked / edge parts are placed far from both clusters.  If the code
    # regressed to include them, the bbox of their sheet would explode, fill
    # would fall well below SPREAD_LO=0.35, and the score would become > 0.
    # Asserting == 0.0 therefore catches that regression; isinstance/>=0 would not.
    b = Board(0, 0, 200, 200)
    b.components = {
        # Sheet /A/ movable pair: centres (10,10) and (14,14) -> fill 0.5
        "A1": _part("A1", 10, 10, sheet="/A/"),
        "A2": _part("A2", 14, 14, sheet="/A/"),
        # Locked part far from /A/ cluster; if counted, bbox grows ~10x -> fill << 0.35
        "A3": _part("A3", 190, 190, sheet="/A/", locked=True),
        # Sheet /B/ movable pair: centres (60,60) and (64,64) -> fill 0.5
        "B1": _part("B1", 60, 60, sheet="/B/"),
        "B2": _part("B2", 64, 64, sheet="/B/"),
        # Edge part far from /B/ cluster; same regression check
        "B3": _part("B3", 5, 5, sheet="/B/", edge="L"),
    }
    # With correct code (locked/edge excluded): both sheets score 0 -> mean 0.0
    assert metrics.sheet_spread_score(b) == 0.0


def test_pinch_fraction_close_pair_is_pinched():
    b = Board(0, 0, 60, 60)
    # gap along x = 7.5 - 4 = 3.5 ... set so 0 <= gap < channel(2.6) is FALSE,
    # then bring them closer so gap < channel is TRUE.
    b.components = {"A": _part("A", 20, 20), "B": _part("B", 25.5, 20)}  # gx = 1.5
    # gx = |25.5-20| - (4+4)/2 = 5.5 - 4 = 1.5 ; gy = -4 -> shadow; gap=1.5<2.6 -> pinch
    assert metrics.pinch_fraction(b, 0.8, 1.0) == 1.0


def test_pinch_fraction_far_pair_not_pinched():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 5, 5), "B": _part("B", 55, 55)}      # far apart
    assert metrics.pinch_fraction(b, 0.8, 1.0) == 0.0                    # no shadow


def test_whitespace_connectivity_open_board_is_one():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 30, 30)}            # one small part in the middle
    # all empty cells stay 4-connected around the single obstacle
    assert metrics.whitespace_connectivity(b) == 1.0


def test_whitespace_connectivity_full_board_is_zero():
    b = Board(0, 0, 10, 10)
    b.components = {"A": _part("A", 5, 5, w=20, h=20)}  # covers the whole grid
    assert metrics.whitespace_connectivity(b) == 0.0


def test_decap_proximity_mean_distance_and_zero_when_none():
    from autoplace.model import Board, Component, Pad
    b = Board(0, 0, 100, 100)
    b.components = {
        "U1": Component("U1", 6, 6, x=10, y=10, pads=[
            Pad("1", "+5V", -2.0, 0.0), Pad("2", "GND", 2.0, 0.0), Pad("3", "SIG", 0.0, 2.0)]),
        "C1": Component("C1", 2, 1, x=10, y=40, pads=[
            Pad("1", "+5V", -0.8, 0.0), Pad("2", "GND", 0.8, 0.0)]),
    }
    # cap rail pad world = (10-0.8, 40) = (9.2, 40); IC rail pad = (10-2, 10) = (8, 10)
    # dist = hypot(1.2, 30) ~= 30.024
    d = metrics.decap_proximity(b)
    assert 29.5 < d < 30.5

    b2 = Board(0, 0, 50, 50)
    b2.components = {"R1": Component("R1", 2, 1, x=5, y=5,
                                     pads=[Pad("1", "A", -0.8, 0), Pad("2", "B", 0.8, 0)])}
    assert metrics.decap_proximity(b2) == 0.0


def test_tall_clearance_penalises_short_near_tall_and_zero_when_none():
    from autoplace.model import Board, Component
    # tall part U1 (height 18) with a short R1 (height 3) inside its halo
    b = Board(0, 0, 80, 80)
    u1 = Component("U1", 4, 4, x=20, y=20, height=18.0)
    r1 = Component("R1", 4, 4, x=27.5, y=20, height=3.0)   # gx = 3.5
    b.components = {"U1": u1, "R1": r1}
    # halo target = channel_width(0.8,1.0)=2.6 + TALL_HALO_MM 2.0 = 4.6; gap 3.5 < 4.6 -> shortfall 1.1
    d = metrics.tall_clearance(b)
    assert 1.0 < d < 1.2

    # no tall parts -> 0.0
    b2 = Board(0, 0, 80, 80)
    b2.components = {"A": Component("A", 4, 4, x=20, y=20, height=3.0),
                     "B": Component("B", 4, 4, x=27.5, y=20, height=3.0)}
    assert metrics.tall_clearance(b2) == 0.0


# ---------- G3: alignment_score ----------

def _part_block(ref, x, y, block="BLK", w=4.0, h=4.0, locked=False, edge=""):
    """Helper for alignment_score tests (adds block= kwarg)."""
    return Component(ref=ref, w=w, h=h, x=x, y=y, block=block,
                     locked=locked, edge=edge, pads=[Pad("1", "N", 0.0, 0.0)])


def test_alignment_score_lower_after_align():
    """alignment_score improves (decreases) after aesthetic.align on a clusterable board."""
    from autoplace import aesthetic
    b = Board(0, 0, 100, 100)
    # Three parts in the same block at nearly-same X (within tol) but not on-grid.
    b.components = {
        "R1": _part_block("R1", 10.0, 10.0),
        "R2": _part_block("R2", 10.4, 30.0),
        "R3": _part_block("R3", 11.2, 50.0),
    }
    score_before = metrics.alignment_score(b)
    assert score_before > 0.0, f"Expected nonzero score before align, got {score_before}"
    aesthetic.align(b, grid=0.5, margin=0.8)
    score_after = metrics.alignment_score(b)
    assert score_after < score_before, (
        f"Expected alignment_score to decrease; before={score_before}, after={score_after}")


def test_alignment_score_zero_when_no_clusterable_block():
    """alignment_score returns 0.0 when no block has >=2 parts within tol on any axis."""
    b = Board(0, 0, 100, 100)
    # Two singletons in different blocks, far apart on both axes.
    b.components = {
        "R1": _part_block("R1", 10.0, 10.0, block="A"),
        "R2": _part_block("R2", 50.0, 50.0, block="B"),
    }
    assert metrics.alignment_score(b) == 0.0


def test_alignment_score_zero_after_perfect_alignment():
    """alignment_score returns 0.0 when all clusterable parts are already on one line."""
    b = Board(0, 0, 100, 100)
    # All three parts already share the same X (already aligned).
    b.components = {
        "R1": _part_block("R1", 10.0, 10.0),
        "R2": _part_block("R2", 10.0, 30.0),
        "R3": _part_block("R3", 10.0, 50.0),
    }
    assert metrics.alignment_score(b) == 0.0
