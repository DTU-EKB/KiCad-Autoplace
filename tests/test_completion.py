"""The escalation ladder: place -> grow -> bridge, cheapest first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "plugins"))

from autoplace.completion import (Attempt, CompletionState,  # noqa: E402
                                  growth_needed, next_action, summarise)


def test_a_fully_routed_board_needs_no_escalation():
    assert next_action(CompletionState(pct=100.0, missing=0)) == "done"


def test_placement_is_tried_first_because_it_costs_nothing():
    assert next_action(CompletionState(pct=94.0, missing=6)) == "place"


def test_placement_is_retried_while_it_keeps_improving():
    s = CompletionState(pct=96.0, missing=4, place_attempts=2, improved_last=True)
    assert next_action(s) == "place"


def test_placement_is_abandoned_once_it_stops_improving():
    s = CompletionState(pct=96.0, missing=4, place_attempts=1, improved_last=False)
    assert next_action(s) == "grow"


def test_growth_comes_before_bridges_so_the_board_stays_bridge_free():
    s = CompletionState(pct=96.0, missing=4, place_attempts=3)
    assert next_action(s) == "grow"


def test_growth_stops_at_the_cap_and_falls_through_to_bridging():
    s = CompletionState(pct=96.0, missing=4, place_attempts=3, growth_mm=20.0)
    assert next_action(s, max_growth_mm=20.0) == "bridge"


def test_growth_that_keeps_being_reverted_still_terminates():
    """Observed as a livelock in ``cli.py complete``: the caller gives the
    millimetres back when a growth step does not help, so ``growth_mm`` never
    advances, the millimetre cap is never reached, and the ladder asks to grow
    forever -- never reaching the bridge rung and never returning. Attempts have
    to be bounded, not just accumulated size."""
    s = CompletionState(pct=71.0, missing=17, place_attempts=3,
                        growth_mm=0.0, grow_attempts=4, improved_last=False)
    assert next_action(s, max_growth_mm=20.0, growth_step_mm=5.0) == "bridge"


def test_growth_attempts_are_capped_by_the_size_budget():
    budget = {"max_growth_mm": 20.0, "growth_step_mm": 5.0}      # 4 steps
    keep = CompletionState(pct=71.0, missing=9, place_attempts=3, grow_attempts=3)
    assert next_action(keep, **budget) == "grow"
    spent = CompletionState(pct=71.0, missing=9, place_attempts=3, grow_attempts=4)
    assert next_action(spent, **budget) == "bridge"


def test_growth_still_stops_on_the_millimetre_cap():
    """The size budget is the real constraint; the attempt cap only guarantees
    termination when reverts keep the size at zero."""
    s = CompletionState(pct=96.0, missing=4, place_attempts=3, growth_mm=18.0,
                        grow_attempts=1)
    assert next_action(s, max_growth_mm=20.0, growth_step_mm=5.0) == "bridge"


def test_growth_can_be_forbidden_when_the_outline_is_a_hard_constraint():
    s = CompletionState(pct=96.0, missing=4, place_attempts=3)
    assert next_action(s, allow_growth=False) == "bridge"


def test_with_nothing_left_to_try_it_stops_rather_than_pretending():
    s = CompletionState(pct=96.0, missing=4, place_attempts=3)
    assert next_action(s, allow_growth=False, allow_bridges=False) == "stop"


def test_growth_is_proportional_but_coarse():
    assert growth_needed(0) == 0.0
    assert growth_needed(3) == 5.0
    assert growth_needed(20) == 10.0


def test_summary_reports_completion_and_what_it_cost():
    hist = [Attempt("place", 94.0, 6), Attempt("grow", 97.0, 2, growth_mm=5.0),
            Attempt("bridge", 100.0, 0, bridges=2, growth_mm=5.0)]
    s = summarise(hist)
    assert s["complete"] is True
    assert s["bridges"] == 2 and s["growth_mm"] == 5.0
    assert len(s["steps"]) == 3


def test_summary_does_not_claim_completion_when_connections_remain():
    s = summarise([Attempt("bridge", 98.0, 3, bridges=1)])
    assert s["complete"] is False and s["missing"] == 3


def test_empty_history_is_not_reported_as_complete():
    assert summarise([])["complete"] is False
