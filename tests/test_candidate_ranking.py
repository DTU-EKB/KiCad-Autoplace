"""Headless tests for the pure candidate-ranking policy. No pcbnew.

  python -m pytest tests/test_candidate_ranking.py

The ordering these tests pin was changed on 2026-08-13 on the strength of a
measurement, not a preference. 76 placements across 4 boards (subxo, buck_v2,
c2000_feedback, motor_power) were routed for real and scored against the wire
bridges each placement actually forces. The old lexicographic key --
(overlaps, sheet_spread, pinch_fraction, decap_proximity, hpwl_mm, seed) --
correlated with that outcome at rho = 0.01, i.e. not at all: pinch_fraction and
decap_proximity are near-continuous, so one of them decided the order before
hpwl_mm was ever compared, and neither predicts routability (rho 0.05 and
-0.18). Ranking on crossings + hpwl instead scores rho = 0.63 and cuts the
bridges the user hand-solders on the top pick from 21 to 6 across those boards.

So: routability first, aesthetics as tie-breakers.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import ranking                          # noqa: E402


def _c(seed, overlaps=0, spread=0.0, pinch=0.0, hpwl=100.0, crossings=10):
    return {"seed": seed, "overlaps": overlaps, "sheet_spread_score": spread,
            "pinch_fraction": pinch, "hpwl_mm": hpwl, "crossings": crossings,
            "whitespace_connectivity": 0.5}


def test_legal_beats_illegal():
    legal = _c(1, overlaps=0, hpwl=999.0, crossings=99)
    illegal = _c(2, overlaps=3, hpwl=1.0, crossings=0)   # perfect but overlapping
    assert ranking.pre_rank([illegal, legal])[0]["seed"] == 1


def test_routability_outranks_aesthetics():
    """The change that matters: a placement that routes better wins even when it
    looks worse on the spread/pinch/decap terms. Under the old key the aesthetic
    terms decided first and this assertion failed."""
    routable = _c(1, crossings=5, hpwl=100.0, spread=0.9, pinch=0.9)
    pretty = _c(2, crossings=80, hpwl=900.0, spread=0.0, pinch=0.0)
    pretty["decap_proximity"] = 0.5
    routable["decap_proximity"] = 40.0
    assert [x["seed"] for x in ranking.pre_rank([pretty, routable])] == [1, 2]


def test_crossings_and_hpwl_are_combined_not_lexicographic():
    """Best-on-crossings does not automatically win; the two are summed as ranks
    so neither dominates on scale. Here each wins one term, so they tie and the
    seed breaks it."""
    a = _c(5, crossings=1, hpwl=1000.0)
    b = _c(2, crossings=10, hpwl=100.0)
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 5]


def test_lower_crossings_wins_when_hpwl_ties():
    a = _c(1, crossings=40, hpwl=100.0)
    b = _c(2, crossings=4, hpwl=100.0)
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_lower_hpwl_wins_when_crossings_tie():
    a = _c(1, crossings=10, hpwl=500.0)
    b = _c(2, crossings=10, hpwl=50.0)
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_aesthetics_break_exact_ties():
    """They still count -- just below routability, not above it."""
    a = _c(1, crossings=10, hpwl=100.0, pinch=0.9)
    b = _c(2, crossings=10, hpwl=100.0, pinch=0.1)
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_closer_decaps_break_an_otherwise_exact_tie():
    a = _c(1); a["decap_proximity"] = 12.0
    b = _c(2); b["decap_proximity"] = 3.0
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_missing_crossings_degrades_to_hpwl_ranking():
    """Older callers (and any candidate dict built before crossings was carried
    through) must still rank sensibly rather than crash or go arbitrary."""
    a = {"seed": 1, "overlaps": 0, "sheet_spread_score": 0.0,
         "pinch_fraction": 0.0, "hpwl_mm": 500.0}
    b = {"seed": 2, "overlaps": 0, "sheet_spread_score": 0.0,
         "pinch_fraction": 0.0, "hpwl_mm": 50.0}
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_seed_gives_a_total_order():
    a = _c(5)
    b = _c(2)                                          # identical except seed
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 5]


def test_empty_and_single_candidate_lists():
    assert ranking.pre_rank([]) == []
    only = _c(7)
    assert ranking.pre_rank([only]) == [only]


def test_final_order_routed_finalists_float_to_top():
    a = _c(1, hpwl=100.0, crossings=1)                 # pre-rank #1
    b = _c(2, hpwl=200.0, crossings=2)                 # pre-rank #2
    c = _c(3, hpwl=300.0, crossings=3)                 # pre-rank #3 (not routed)
    routed = {1: 80.0, 2: 95.0}                        # finalist 2 routes better
    order = [x["seed"] for x in ranking.final_order([a, b, c], routed)]
    assert order == [2, 1, 3]                          # routed best, routed, rest


def test_final_order_no_routes_is_pre_rank():
    a = _c(1, hpwl=100.0, crossings=5)
    b = _c(2, hpwl=50.0, crossings=5)
    assert [x["seed"] for x in ranking.final_order([a, b], {})] == [2, 1]


def test_ranking_is_independent_of_input_order():
    """Candidates arrive in completion order from the parallel pool, so the
    result must not depend on it."""
    cands = [_c(i, crossings=20 - i, hpwl=100.0 + 3 * i) for i in range(6)]
    fwd = [x["seed"] for x in ranking.pre_rank(list(cands))]
    rev = [x["seed"] for x in ranking.pre_rank(list(reversed(cands)))]
    assert fwd == rev
