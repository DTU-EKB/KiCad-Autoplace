"""Deciding single-sided vs double-sided, and being honest about which is which.

Two different kinds of claim live here and they must not be blurred:

* **Exact.** Whether the netlist can be single-sided at all, and how many wire
  bridges it forces. That is decided from the graph and holds for every
  placement (``planarity.forced_bridges``).
* **Advisory.** Whether this placer, today, is likely to *find* such a layout.
  That is a heuristic fitted to a handful of boards, and it changes whenever the
  placer improves.

The tool should always attempt single-sided and report what it actually got.
The advice exists so the user is not surprised, never to skip the attempt.

  python -m pytest tests/test_advise.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import advise                              # noqa: E402
from autoplace.model import Board, Component, Pad         # noqa: E402


def _part(ref, *nets, w=5.0, h=5.0):
    return Component(ref=ref, w=w, h=h,
                     pads=[Pad(str(i + 1), n, 0.0, 0.0) for i, n in enumerate(nets)])


def _chain(n, w=100.0, h=100.0):
    """n parts in a line: planar, and easy for any placer."""
    b = Board(0.0, 0.0, w, h)
    b.components = {f"R{i}": _part(f"R{i}", f"N{i}", f"N{i + 1}") for i in range(n)}
    return b


def _k5():
    """Five parts each wired to all the others: provably not single-sided."""
    b = Board(0.0, 0.0, 100.0, 100.0)
    for i in range(5):
        nets = [f"N{min(i, j)}{max(i, j)}" for j in range(5) if j != i]
        b.components[f"U{i}"] = _part(f"U{i}", *nets)
    return b


# --------------------------------------------------------------------------
# the exact half
# --------------------------------------------------------------------------

def test_a_planar_netlist_is_reported_as_single_sided_capable():
    a = advise.assess(_chain(8))
    assert a.single_sided_possible is True
    assert a.forced_bridges == 0


def test_a_nonplanar_netlist_reports_the_bridges_it_forces():
    a = advise.assess(_k5())
    assert a.single_sided_possible is False
    assert a.forced_bridges == 1
    assert any("bridge" in r.lower() for r in a.reasons)


def test_forced_bridges_are_never_presented_as_a_placement_failure():
    """A board that forces bridges has not been placed badly -- the circuit
    demands them. Conflating the two is what made every bridge look avoidable."""
    a = advise.assess(_k5())
    assert a.recommend in ("single-with-bridges", "double")
    assert a.forced_bridges == 1


# --------------------------------------------------------------------------
# the advisory half -- and it must be labelled
# --------------------------------------------------------------------------

def test_a_small_planar_board_is_recommended_single_sided():
    a = advise.assess(_chain(10))
    assert a.recommend == "single"
    assert a.difficulty == "easy"


def test_a_large_board_is_recommended_double_sided():
    a = advise.assess(_chain(90))
    assert a.recommend == "double"
    assert a.difficulty == "hard"


def test_difficulty_is_flagged_as_an_estimate_not_a_fact():
    for b in (_chain(10), _chain(90)):
        a = advise.assess(b)
        assert a.confidence in ("measured", "estimated")
    assert advise.assess(_chain(90)).confidence == "estimated"


def test_the_recommendation_never_suppresses_the_attempt():
    """Even when double-sided is advised, the contract is to try single-sided
    first and report the result -- the user decides, not the heuristic."""
    a = advise.assess(_chain(90))
    assert a.recommend == "double"
    assert a.try_single_sided_first is True


# --------------------------------------------------------------------------
# the user's own tolerance decides, not the tool
# --------------------------------------------------------------------------

def test_a_big_board_is_recommended_double_even_when_bridges_are_tolerable():
    """A 90-part board that forces one bridge must not be recommended
    'single-with-bridges' while the same advice says two layers is the better
    trade. Size decides before bridge tolerance does."""
    b = _chain(90)
    b.components["X"] = _part("X", "N0", "N5", "N9")     # nudge it off planar-trivial
    a = advise.assess(b, max_bridges=4)
    assert a.difficulty == "hard"
    assert a.recommend == "double"


def test_bridge_tolerance_moves_the_recommendation():
    board = _k5()
    assert advise.assess(board, max_bridges=0).recommend == "double"
    assert advise.assess(board, max_bridges=4).recommend == "single-with-bridges"


def test_zero_tolerance_on_a_planar_board_still_recommends_single():
    assert advise.assess(_chain(8), max_bridges=0).recommend == "single"


# --------------------------------------------------------------------------
# after the attempt: judge on what actually happened
# --------------------------------------------------------------------------

def test_a_successful_attempt_is_kept():
    v = advise.verdict(advise.assess(_chain(10)), closed=True, bridges=0)
    assert v["keep_single_sided"] is True
    assert v["at_the_floor"] is True


def test_bridges_above_the_floor_are_named_as_avoidable():
    v = advise.verdict(advise.assess(_chain(10)), closed=True, bridges=3)
    assert v["keep_single_sided"] is True
    assert v["at_the_floor"] is False
    assert v["avoidable_bridges"] == 3


def test_bridges_at_the_floor_are_not_called_avoidable():
    v = advise.verdict(advise.assess(_k5()), closed=True, bridges=1)
    assert v["avoidable_bridges"] == 0
    assert v["at_the_floor"] is True


def test_a_failed_attempt_recommends_double_sided():
    v = advise.verdict(advise.assess(_chain(10)), closed=False, bridges=2,
                       missing=5)
    assert v["keep_single_sided"] is False
    assert v["recommend"] == "double"
    assert any("5" in r for r in v["reasons"])


def test_too_many_bridges_for_the_users_taste_recommends_double():
    a = advise.assess(_chain(10), max_bridges=2)
    v = advise.verdict(a, closed=True, bridges=9)
    assert v["keep_single_sided"] is False
    assert v["recommend"] == "double"


def test_verdict_reasons_are_never_empty():
    for closed, bridges in ((True, 0), (True, 9), (False, 1)):
        v = advise.verdict(advise.assess(_chain(10)), closed=closed,
                           bridges=bridges, missing=0 if closed else 3)
        assert v["reasons"]
