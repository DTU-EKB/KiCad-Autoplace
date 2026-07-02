"""Headless tests for the multi-seed candidate generator. No pcbnew required.

  python -m pytest tests/test_multiseed.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import multiseed                       # noqa: E402
from autoplace.model import Board, Component, Pad      # noqa: E402


def _two_pin(ref, x, y, neta, netb, w=2.0, h=1.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y, pads=[
        Pad("1", neta, -w / 2 + 0.2, 0.0),
        Pad("2", netb, w / 2 - 0.2, 0.0),
    ])


def _board():
    b = Board(0, 0, 60, 60)
    b.components = {
        "R1": _two_pin("R1", 5, 5, "VIN", "N1"),
        "R2": _two_pin("R2", 55, 55, "N1", "N2"),
        "R3": _two_pin("R3", 5, 55, "N2", "N3"),
        "R4": _two_pin("R4", 55, 5, "N3", "GND"),
    }
    return b


def _geom(cand):
    """Stable tuple of component centres for comparing layouts."""
    return tuple(sorted(
        (f["ref"], round(f["x"], 4), round(f["y"], 4))
        for f in cand["board"]["footprints"]
    ))


def test_count_and_shape():
    cands = list(multiseed.run_candidates(_board(), 6))
    assert len(cands) == 6
    for c in cands:
        assert set(c) >= {"seed", "hpwl_mm", "crossings", "overlaps",
                          "hpwl_delta_pct", "sheet_spread_score",
                          "pinch_fraction", "whitespace_connectivity",
                          "decap_proximity", "board"}
        assert isinstance(c["sheet_spread_score"], float)
        assert isinstance(c["pinch_fraction"], float)
        assert isinstance(c["whitespace_connectivity"], float)
        assert isinstance(c["decap_proximity"], float)
        assert c["board"]["footprints"]


def test_seeds_in_order_and_varied():
    cands = list(multiseed.run_candidates(_board(), 6))
    assert [c["seed"] for c in cands] == [0, 1, 2, 3, 4, 5]
    geoms = {_geom(c) for c in cands}
    assert len(geoms) > 1                       # different seeds -> different layouts


def test_same_seed_deterministic():
    a = list(multiseed.run_candidates(_board(), 1))[0]
    b = list(multiseed.run_candidates(_board(), 1))[0]
    assert _geom(a) == _geom(b)


def test_parallel_matches_serial():
    """parallel=True must produce byte-identical candidates (any yield order)."""
    serial = {c["seed"]: c for c in multiseed.run_candidates(_board(), 3)}
    par = {c["seed"]: c for c in
           multiseed.run_candidates(_board(), 3, parallel=True)}
    assert set(par) == set(serial) == {0, 1, 2}
    for seed in serial:
        assert par[seed] == serial[seed]


def test_bad_seed_does_not_abort(monkeypatch):
    from autoplace import engine
    real_place = engine.place
    calls = {"n": 0}

    def flaky(board, *, seed=0, **kw):
        calls["n"] += 1
        if seed == 1:
            raise RuntimeError("boom")
        return real_place(board, seed=seed, **kw)

    monkeypatch.setattr(multiseed.engine, "place", flaky)
    cands = list(multiseed.run_candidates(_board(), 3))
    assert len(cands) == 3
    assert cands[1]["type"] == "candidate-error"
    assert cands[1]["seed"] == 1
    assert "boom" in cands[1]["error"]
    assert cands[0]["seed"] == 0 and "board" in cands[0]   # others still produced
    assert cands[2]["seed"] == 2 and "board" in cands[2]
