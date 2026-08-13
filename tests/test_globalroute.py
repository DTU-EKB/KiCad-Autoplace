"""Headless tests for the coarse global router (congestion + planarity).

No pcbnew, no Java -- the whole point of the middle tier is that it runs in the
annealer's loop.

  python -m pytest tests/test_globalroute.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute                       # noqa: E402
from autoplace.model import Board, Component, Pad       # noqa: E402


def _term(ref, net, x, y, size=0.6):
    """A one-pad 'terminal' part: its single pad sits exactly at (x, y)."""
    return Component(ref=ref, w=size, h=size, x=x, y=y,
                     pads=[Pad("1", net, 0.0, 0.0)])


def _board(*parts, w=100.0, h=100.0):
    b = Board(0.0, 0.0, w, h)
    b.components = {p.ref: p for p in parts}
    return b


def _wire(tag, net, x1, y1, x2, y2):
    """Two terminals forming one 2-pad net (i.e. exactly one routed segment)."""
    return (_term(f"{tag}a", net, x1, y1), _term(f"{tag}b", net, x2, y2))


# --------------------------------------------------------------------------
# net trees: what actually has to be routed
# --------------------------------------------------------------------------

def test_two_pad_net_yields_one_segment():
    b = _board(*_wire("W", "SIG", 10, 10, 40, 40))
    segs = globalroute.net_segments(b)
    assert len(segs) == 1
    assert segs[0].net == "SIG"
    assert {(segs[0].ax, segs[0].ay), (segs[0].bx, segs[0].by)} == {(10, 10), (40, 40)}


def test_n_pad_net_yields_n_minus_one_segments():
    parts = [_term(f"P{i}", "BUS", 10 + 10 * i, 10) for i in range(5)]
    segs = globalroute.net_segments(_board(*parts))
    assert len(segs) == 4          # spanning tree over 5 terminals


def test_single_pad_net_yields_no_segment():
    b = _board(_term("A", "LONELY", 10, 10))
    assert globalroute.net_segments(b) == []


def test_plane_nets_are_not_routed():
    """A poured net is connected by copper fill -- it needs no track and must not
    contribute crossings, or every GND pad would look like a forced bridge."""
    b = _board(*_wire("G", "/GND", 10, 10, 40, 40),
               *_wire("S", "/SIG", 10, 40, 40, 10))
    segs = globalroute.net_segments(b)
    assert [s.net for s in segs] == ["/SIG"]


def test_board_planes_take_precedence_over_the_name_guess():
    """The real pipeline gets the poured nets from the board's actual zones. A
    board that pours +12V instead of ground must route ground and skip +12V --
    guessing by name would get both backwards."""
    b = _board(*_wire("P", "/V12", 10, 10, 40, 40),
               *_wire("G", "/GND", 10, 40, 40, 10))
    b.planes = {"/V12"}
    assert [s.net for s in globalroute.net_segments(b)] == ["/GND"]


def test_gnd_name_guess_applies_only_without_declared_planes():
    b = _board(*_wire("P", "/V12", 10, 10, 40, 40),
               *_wire("G", "/GND", 10, 40, 40, 10))
    assert [s.net for s in globalroute.net_segments(b)] == ["/V12"]


def test_explicit_planes_override_the_gnd_default():
    b = _board(*_wire("P", "/V12", 10, 10, 40, 40),
               *_wire("S", "/SIG", 10, 40, 40, 10))
    segs = globalroute.net_segments(b, planes={"/V12"})
    assert [s.net for s in segs] == ["/SIG"]


# --------------------------------------------------------------------------
# conflicts: which segments cannot coexist on one copper layer
# --------------------------------------------------------------------------

def test_crossing_segments_of_different_nets_conflict():
    b = _board(*_wire("A", "N1", 0, 0, 40, 40),
               *_wire("B", "N2", 0, 40, 40, 0))
    segs = globalroute.net_segments(b)
    assert globalroute.conflicts(segs) == [(0, 1)]


def test_disjoint_segments_do_not_conflict():
    b = _board(*_wire("A", "N1", 0, 0, 10, 10),
               *_wire("B", "N2", 60, 60, 70, 70))
    assert globalroute.conflicts(globalroute.net_segments(b)) == []


def test_same_net_segments_never_conflict():
    """Two branches of one net may overlap freely -- it is the same copper."""
    parts = [_term("A", "N", 0, 0), _term("B", "N", 40, 40),
             _term("C", "N", 0, 40), _term("D", "N", 40, 0)]
    segs = globalroute.net_segments(_board(*parts))
    assert globalroute.conflicts(segs) == []


def test_segments_sharing_an_endpoint_do_not_conflict():
    # Two nets meeting at a pad location: touching is not crossing.
    b = _board(*_wire("A", "N1", 20, 20, 40, 40),
               *_wire("B", "N2", 20, 20, 40, 0))
    assert globalroute.conflicts(globalroute.net_segments(b)) == []


# --------------------------------------------------------------------------
# minimum bridges == minimum vertex cover of the conflict graph
# --------------------------------------------------------------------------

def test_no_conflicts_needs_no_bridges():
    assert globalroute.min_bridges(4, []) == 0


def test_one_conflict_needs_one_bridge():
    assert globalroute.min_bridges(2, [(0, 1)]) == 1


def test_triangle_of_mutual_crossings_needs_two_bridges():
    """Three segments each crossing the other two. Lifting one still leaves the
    other pair crossing, so the answer is 2, not 1 and not 3."""
    assert globalroute.min_bridges(3, [(0, 1), (1, 2), (0, 2)]) == 2


def test_hub_crossing_three_others_needs_one_bridge():
    """A star: lift the hub and every conflict is gone. A per-conflict count
    would say 3; a min vertex cover says 1."""
    assert globalroute.min_bridges(4, [(0, 1), (0, 2), (0, 3)]) == 1


def test_path_of_two_conflicts_needs_one_bridge():
    assert globalroute.min_bridges(3, [(0, 1), (1, 2)]) == 1


def test_two_independent_conflicts_need_two_bridges():
    assert globalroute.min_bridges(4, [(0, 1), (2, 3)]) == 2


def test_min_bridges_never_exceeds_conflict_count():
    conf = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 4), (1, 3)]
    assert globalroute.min_bridges(5, conf) <= len(conf)


def _brute_force_cover(n, conf):
    """Smallest vertex set touching every conflict, by exhaustive search."""
    from itertools import combinations
    for k in range(n + 1):
        for pick in combinations(range(n), k):
            s = set(pick)
            if all(a in s or b in s for a, b in conf):
                return k
    return n


def test_min_bridges_matches_exhaustive_search():
    """The bridge count is the number the placer optimises against, so an
    approximation that quietly overshoots would mis-rank every candidate.
    Checked against brute force on a spread of small graphs, including the
    shapes greedy vertex cover is known to get wrong."""
    import random
    cases = [
        (6, [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)]),   # two triangles
        (6, [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5)]),           # star
        (6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]),           # path
        (6, [(0, 3), (0, 4), (1, 3), (1, 5), (2, 4), (2, 5)]),   # bipartite
        (5, [(0, 1), (0, 2), (0, 3), (0, 4), (1, 2), (1, 3),
             (1, 4), (2, 3), (2, 4), (3, 4)]),                   # complete K5
    ]
    rng = random.Random(20260813)
    for _ in range(40):                       # random sparse graphs
        n = rng.randint(4, 9)
        edges = sorted({tuple(sorted(rng.sample(range(n), 2)))
                        for _ in range(rng.randint(1, 12))})
        cases.append((n, edges))
    for n, conf in cases:
        assert globalroute.min_bridges(n, conf) == _brute_force_cover(n, conf), \
            f"n={n} conf={conf}"


# --------------------------------------------------------------------------
# congestion grid
# --------------------------------------------------------------------------

def test_empty_board_has_no_demand():
    g = globalroute.congestion(_board(), cell_mm=10.0)
    assert g.total_demand == 0.0
    assert g.overflow == 0.0


def test_a_segment_raises_demand_on_the_cells_it_crosses():
    b = _board(*_wire("A", "N1", 5, 5, 5, 95))       # vertical, one column
    g = globalroute.congestion(b, cell_mm=10.0)
    col = [g.demand[0][iy] for iy in range(g.ny)]
    assert all(d > 0 for d in col), col               # every cell in the column
    assert g.demand[5][5] == 0.0                      # far column untouched


def test_capacity_is_cell_width_over_track_pitch():
    """A 10 mm cell fits 10 / (1.0 + 0.85) tracks side by side.

    Fractional on purpose: truncating to whole tracks puts a cliff in the middle
    of a metric the annealer optimises against, and discards up to a full track
    per cell on a coarse grid."""
    g = globalroute.congestion(_board(), cell_mm=10.0, track=1.0, clearance=0.85)
    assert abs(g.capacity[0][0] - 10.0 / 1.85) < 1e-9


def test_pads_reduce_the_capacity_of_their_cell():
    """A through-hole pad is an obstacle on the routing layer: other nets cannot
    pass through it, so the cell holding it carries fewer tracks."""
    empty = globalroute.congestion(_board(), cell_mm=10.0)
    crowded = globalroute.congestion(
        _board(*[_term(f"P{i}", "", 2 + i, 5, size=2.0) for i in range(4)]),
        cell_mm=10.0)
    assert crowded.capacity[0][0] < empty.capacity[0][0]


def test_overflow_appears_only_when_demand_beats_capacity():
    # 8 parallel nets squeezed through one 10 mm cell -- capacity is 5.
    parts = []
    for i in range(8):
        parts += list(_wire(f"W{i}", f"N{i}", 1 + i, 0.5, 1 + i, 99.5))
    g = globalroute.congestion(_board(*parts), cell_mm=10.0)
    assert g.overflow > 0.0
    assert g.peak > 1.0


def test_light_load_produces_no_overflow():
    b = _board(*_wire("A", "N1", 5, 5, 5, 95))
    g = globalroute.congestion(b, cell_mm=10.0)
    assert g.overflow == 0.0


# --------------------------------------------------------------------------
# the public estimate
# --------------------------------------------------------------------------

def test_estimate_reports_bridges_and_congestion_together():
    b = _board(*_wire("A", "N1", 0, 0, 40, 40),
               *_wire("B", "N2", 0, 40, 40, 0))
    est = globalroute.analyse(b)
    assert est.bridges == 1
    assert est.conflicts == 1
    assert est.segments == 2
    assert est.overflow >= 0.0


def test_planar_placement_predicts_zero_bridges():
    b = _board(*_wire("A", "N1", 0, 0, 10, 10),
               *_wire("B", "N2", 60, 60, 70, 70))
    assert globalroute.analyse(b).bridges == 0


def test_estimate_is_deterministic():
    parts = []
    for i in range(12):
        parts += list(_wire(f"W{i}", f"N{i}", 5 + 7 * i, 8, 90 - 3 * i, 80))
    b = _board(*parts)
    a = globalroute.analyse(b)
    c = globalroute.analyse(b)
    assert (a.bridges, a.conflicts, a.overflow, a.peak) == \
           (c.bridges, c.conflicts, c.overflow, c.peak)


def test_board_with_no_outline_does_not_crash():
    """A board with no Edge.Cuts reads back as a zero-size outline (seen on a
    real project). The estimate must degrade, not raise, or the annealer dies on
    a board the user merely forgot to draw an edge on."""
    b = Board(0.0, 0.0, 0.0, 0.0)
    b.components = {c.ref: c for c in _wire("W", "N", 1, 1, 2, 2)}
    est = globalroute.analyse(b)
    assert est.segments == 1
    assert est.bridges == 0
    assert est.grid.nx == 1 and est.grid.ny == 1


def test_empty_board_analyses_to_nothing():
    est = globalroute.analyse(_board())
    assert (est.bridges, est.conflicts, est.segments) == (0, 0, 0)


def test_pressure_at_is_zero_outside_the_board():
    g = globalroute.congestion(_board(*_wire("A", "N", 5, 5, 5, 95)), cell_mm=10.0)
    assert g.pressure_at(-50.0, -50.0) == 0.0
    assert g.pressure_at(5.0, 50.0) > 0.0


def test_analyse_is_fast_enough_for_the_inner_loop():
    """The middle tier only earns its place if it runs thousands of times per
    anneal. 40 parts, ~26 nets -- the shape of the real test board."""
    parts = []
    for i in range(20):
        parts += list(_wire(f"W{i}", f"N{i}",
                            5 + 4.5 * i, 10 + (i % 5) * 3,
                            95 - 4.0 * i, 85 - (i % 7) * 4))
    b = _board(*parts)
    globalroute.analyse(b)                      # warm any lazy import
    t0 = time.perf_counter()
    runs = 20
    for _ in range(runs):
        globalroute.analyse(b)
    per_call_ms = (time.perf_counter() - t0) * 1000.0 / runs
    assert per_call_ms < 30.0, f"{per_call_ms:.1f} ms per analyse()"
