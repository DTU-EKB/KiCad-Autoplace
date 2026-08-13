"""Outline growth: respect the user's boundary, grow only as far as needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "plugins"))

from autoplace.outline import (fits_inside, grow_rect,  # noqa: E402
                               rect_of, required_growth)

BOARD = (0.0, 0.0, 100.0, 100.0)


def test_rect_of_finds_the_bounding_box_of_segments():
    segs = [(0, 0, 100, 0), (100, 0, 100, 100), (100, 100, 0, 100), (0, 100, 0, 0)]
    assert rect_of(segs) == (0, 0, 100, 100)


def test_no_outline_is_reported_as_none_not_a_zero_rect():
    assert rect_of([]) is None


def test_centre_growth_keeps_the_board_concentric():
    assert grow_rect(BOARD, 5.0) == (-5.0, -5.0, 105.0, 105.0)


def test_corner_growth_keeps_the_origin_fixed_for_jig_registration():
    assert grow_rect(BOARD, 5.0, anchor="corner") == (0.0, 0.0, 110.0, 110.0)


def test_growing_by_zero_is_a_no_op():
    assert grow_rect(BOARD, 0.0) == BOARD


def test_parts_inside_the_outline_fit():
    assert fits_inside(BOARD, [(10, 10, 20, 20), (80, 80, 95, 95)])


def test_a_part_over_the_edge_does_not_fit():
    assert not fits_inside(BOARD, [(95, 10, 105, 20)])


def test_clearance_pulls_the_usable_area_in():
    assert not fits_inside(BOARD, [(1, 1, 20, 20)], clearance=2.0)


def test_no_growth_needed_when_everything_already_fits():
    assert required_growth(BOARD, [(10, 10, 20, 20)]) == 0.0


def test_growth_is_the_smallest_step_that_fits():
    # 6 mm over the right edge -> one 5 mm step is not enough, two are
    assert required_growth(BOARD, [(90, 10, 106, 20)], step=5.0) == 10.0


def test_growth_gives_up_rather_than_expanding_without_limit():
    assert required_growth(BOARD, [(0, 0, 500, 500)], step=5.0, cap=20.0) == -1.0
