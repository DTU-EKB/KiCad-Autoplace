"""Headless tests for the pure candidate-ranking policy. No pcbnew.

  python -m pytest tests/test_candidate_ranking.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import ranking                          # noqa: E402


def _c(seed, overlaps=0, spread=0.0, pinch=0.0, hpwl=100.0):
    return {"seed": seed, "overlaps": overlaps, "sheet_spread_score": spread,
            "pinch_fraction": pinch, "hpwl_mm": hpwl,
            "whitespace_connectivity": 0.5}


def test_legal_beats_illegal():
    legal = _c(1, overlaps=0, hpwl=999.0)
    illegal = _c(2, overlaps=3, hpwl=1.0)              # tiny HPWL but has overlaps
    assert ranking.pre_rank([illegal, legal])[0]["seed"] == 1


def test_spread_then_pinch_then_hpwl():
    a = _c(1, spread=0.2, pinch=0.5, hpwl=100.0)
    b = _c(2, spread=0.1, pinch=0.9, hpwl=100.0)       # better spread wins first
    c = _c(3, spread=0.1, pinch=0.1, hpwl=500.0)       # ties a-on-spread? no: 0.1<0.2
    order = [x["seed"] for x in ranking.pre_rank([a, b, c])]
    assert order[0] in (2, 3)                          # both spread 0.1 beat a's 0.2
    # between b and c (spread tie 0.1): lower pinch wins -> c before b
    assert order.index(3) < order.index(2)


def test_hpwl_then_seed_tiebreak():
    a = _c(5, hpwl=100.0)
    b = _c(2, hpwl=100.0)                              # identical except seed
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 5]


def test_final_order_routed_finalists_float_to_top():
    a = _c(1, spread=0.0, hpwl=100.0)                  # pre-rank #1
    b = _c(2, spread=0.0, hpwl=200.0)                  # pre-rank #2
    c = _c(3, spread=0.0, hpwl=300.0)                  # pre-rank #3 (not routed)
    routed = {1: 80.0, 2: 95.0}                        # finalist 2 routes better
    order = [x["seed"] for x in ranking.final_order([a, b, c], routed)]
    assert order == [2, 1, 3]                          # routed best, routed, then rest


def test_final_order_no_routes_is_pre_rank():
    a = _c(1, hpwl=100.0)
    b = _c(2, hpwl=50.0)
    assert [x["seed"] for x in ranking.final_order([a, b], {})] == [2, 1]


def test_closer_decaps_outrank_equal_candidate():
    a = _c(1, hpwl=100.0); a["decap_proximity"] = 12.0
    b = _c(2, hpwl=100.0); b["decap_proximity"] = 3.0     # tighter decaps
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_decap_absent_does_not_change_ranking():
    a = _c(1, hpwl=100.0)        # no decap_proximity key
    b = _c(2, hpwl=50.0)         # no decap_proximity key
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]
