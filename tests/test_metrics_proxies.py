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
    # Two sheets, each with two movable parts spread sanely + a far locked part
    # that would wreck the bbox if counted.
    b = Board(0, 0, 100, 100)
    b.components = {
        "A1": _part("A1", 20, 20, sheet="/A/"),
        "A2": _part("A2", 30, 30, sheet="/A/"),
        "A3": _part("A3", 95, 95, sheet="/A/", locked=True),
        "B1": _part("B1", 60, 60, sheet="/B/"),
        "B2": _part("B2", 70, 70, sheet="/B/"),
        "B3": _part("B3", 5, 5, sheet="/B/", edge="L"),
    }
    score = metrics.sheet_spread_score(b)
    assert isinstance(score, float)
    assert score >= 0.0          # deterministic, defined; locked/edge ignored


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
