"""Headless tests for crossing-minimal net trees.

``globalroute`` reduces every net to its MINIMUM SPANNING tree, which optimises
length. A net with k pads has many spanning trees and the router is free to pick
any of them ("run it round the other way"), so the tree that crosses fewest
other nets is usually not the shortest one. ``nettopo`` picks that tree instead.

Two properties carry the module and are checked here on top of the usual
plumbing, because they are what make it safe to wire into the placer:

  * the result is always a spanning tree per net (same contract as globalroute),
  * total crossings can never come out worse than the MST baseline.

No pcbnew, no Java.

  python -m pytest tests/test_nettopo.py
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute, nettopo                # noqa: E402
from autoplace.model import Board, Component, Pad         # noqa: E402


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


def _conflicts(segs):
    return len(globalroute.conflicts(segs))


def _random_board(rng, n_nets=14, span=90.0):
    """A scatter of 2-4 pad nets: dense enough that trees really do interact."""
    parts = []
    for n in range(n_nets):
        for k in range(rng.randint(2, 4)):
            parts.append(_term(f"P{n}_{k}", f"N{n}",
                               round(rng.uniform(2.0, span), 2),
                               round(rng.uniform(2.0, span), 2)))
    return _board(*parts)


# --------------------------------------------------------------------------
# the point of the module: a different tree of the same net crosses less
# --------------------------------------------------------------------------

def test_mst_of_this_net_provably_crosses_and_another_tree_provably_does_not():
    """Hand-built witness that length-optimal != crossing-optimal.

    Net A has three pads in a wide V: A0 (0,0), A1 (10,10), A2 (20,0). Its MST
    is the two 14.14 mm arms A0-A1 and A1-A2 (the base A0-A2 is 20 mm, so Prim
    never takes it). Net B runs (2,10)-(8,2), which cuts the left arm at one
    point and comes nowhere near either the right arm or the base.

    So the MST is forced into exactly one crossing, while the tree {base, right
    arm} has none -- for 5.86 mm more copper. That trade is the whole idea.
    """
    b = _board(_term("A0", "A", 0, 0), _term("A1", "A", 10, 10),
               _term("A2", "A", 20, 0), *_wire("B", "B", 2, 10, 8, 2))

    mst = globalroute.net_segments(b)
    assert _conflicts(mst) == 1                    # the MST provably crosses

    chosen = nettopo.net_segments(b)
    assert _conflicts(chosen) == 0                 # another tree provably does not
    assert len(chosen) == len(mst)                 # still a spanning tree of each net
    # It paid for it in copper, and only what the geometry costs: 20 + 14.14
    # instead of 14.14 + 14.14.
    a_len = sum(s.length for s in chosen if s.net == "A")
    assert abs(a_len - (20.0 + math.sqrt(200.0))) < 1e-6


def test_bridge_estimate_follows_the_better_tree():
    """The deliverable number is bridges, not crossings, so it must move too."""
    b = _board(_term("A0", "A", 0, 0), _term("A1", "A", 10, 10),
               _term("A2", "A", 20, 0), *_wire("B", "B", 2, 10, 8, 2))
    mst = globalroute.net_segments(b)
    assert globalroute.min_bridges(len(mst), globalroute.conflicts(mst)) == 1
    r = nettopo.retree(b)
    assert (r.bridges_mst, r.bridges) == (1, 0)
    assert (r.conflicts_mst, r.conflicts) == (1, 0)
    assert r.changed == ("A",)


def test_a_net_that_cannot_improve_keeps_its_mst():
    """No gratuitous deviation: with nothing to dodge, the answer is the MST.

    Deviating anyway would trade wirelength for nothing, and would make the
    module's output impossible to reason about against the existing baseline."""
    # Three 3-pad nets, each in its own corner: every net has a real choice of
    # tree and no choice can ever touch another net.
    parts = []
    for n, (ox, oy) in enumerate([(0, 0), (60, 0), (0, 60)]):
        for k, (dx, dy) in enumerate([(5, 5), (15, 5), (10, 12)]):
            parts.append(_term(f"P{n}_{k}", f"N{n}", ox + dx, oy + dy))
    b = _board(*parts)
    # Sanity: this scatter must actually be crossing-free, or the test is vacuous.
    assert _conflicts(globalroute.net_segments(b)) == 0
    assert nettopo.net_segments(b) == globalroute.net_segments(b)


# --------------------------------------------------------------------------
# the guarantee: never worse than the baseline it replaces
# --------------------------------------------------------------------------

def test_crossings_never_exceed_the_mst_baseline():
    """Coordinate descent on a cost of ``cross_mm * crossings + length``, started
    from the MST, can only lower crossings: length is already minimal at the
    start, so any accepted move must have bought its extra copper with a
    crossing. Checked over random boards because the proof is only as good as
    the implementation."""
    for seed in range(25):
        b = _random_board(random.Random(seed))
        base = _conflicts(globalroute.net_segments(b))
        got = _conflicts(nettopo.net_segments(b))
        assert got <= base, f"seed {seed}: {got} > {base}"


def test_wirelength_never_drops_below_the_mst():
    """The mirror of the guarantee above: the MST is the length optimum, so a
    crossing-minimal tree can only cost more copper, never less. A result that
    claimed both would mean the trees are not spanning trees at all."""
    for seed in range(10):
        b = _random_board(random.Random(100 + seed))
        r = nettopo.retree(b)
        assert r.wirelength >= r.wirelength_mst - 1e-9


def test_every_net_still_gets_a_spanning_tree():
    """Same contract as globalroute: k pads -> k-1 edges, all pads connected.
    A cheaper 'tree' that quietly dropped a branch would be an open circuit."""
    for seed in range(15):
        b = _random_board(random.Random(200 + seed))
        by_net = {}
        for s in nettopo.net_segments(b):
            by_net.setdefault(s.net, []).append(s)
        for net, pts in nettopo.net_points(b):
            segs = by_net.get(net, [])
            assert len(segs) == len(pts) - 1
            # Union-find over the pad points: one component == connected.
            idx = {p: i for i, p in enumerate(pts)}
            parent = list(range(len(pts)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for s in segs:
                a, c = idx[(s.ax, s.ay)], idx[(s.bx, s.by)]
                parent[find(a)] = find(c)
            assert len({find(i) for i in range(len(pts))}) == 1, net


# --------------------------------------------------------------------------
# the single-net solver
# --------------------------------------------------------------------------

def test_with_no_obstacles_the_solver_returns_the_mst():
    pts = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0), (5.0, 30.0)]
    assert nettopo.min_cross_tree(pts, []) == globalroute._mst_pairs(pts)


def test_cross_mm_zero_reduces_to_the_mst():
    """The knob's floor is the current behaviour, so it can be turned off."""
    b = _board(_term("A0", "A", 0, 0), _term("A1", "A", 10, 10),
               _term("A2", "A", 20, 0), *_wire("B", "B", 2, 10, 8, 2))
    assert nettopo.net_segments(b, cross_mm=0.0) == globalroute.net_segments(b)


def test_cross_mm_sets_the_price_of_a_detour():
    """One crossing is worth ``cross_mm`` millimetres of extra track and no more.

    The witness costs 5.86 mm to dodge one crossing, so a 3 mm budget must
    refuse and a 10 mm budget must accept -- otherwise the constant is not
    actually the exchange rate it claims to be."""
    b = _board(_term("A0", "A", 0, 0), _term("A1", "A", 10, 10),
               _term("A2", "A", 20, 0), *_wire("B", "B", 2, 10, 8, 2))
    assert _conflicts(nettopo.net_segments(b, cross_mm=3.0)) == 1
    assert _conflicts(nettopo.net_segments(b, cross_mm=10.0)) == 0


def test_solver_dodges_an_obstacle_it_is_given():
    """Directly: the same three pads, with the crossing net passed as an
    obstacle rather than discovered from a board. Edges come back oriented the
    way Prim grew them, so the dodging tree is 0-2 then 2-1: the base first,
    then the far arm."""
    pts = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)]
    assert nettopo.min_cross_tree(pts, []) == [(0, 1), (1, 2)]
    assert nettopo.min_cross_tree(pts, [(2.0, 10.0, 8.0, 2.0)]) == [(0, 2), (2, 1)]


def test_solver_handles_degenerate_nets():
    assert nettopo.min_cross_tree([], []) == []
    assert nettopo.min_cross_tree([(1.0, 2.0)], []) == []
    assert nettopo.min_cross_tree([(1.0, 2.0), (3.0, 4.0)], []) == [(0, 1)]
    # Coincident pads (two parts stacked): still a tree, no division by zero.
    assert len(nettopo.min_cross_tree([(0.0, 0.0), (0.0, 0.0), (5.0, 0.0)], [])) == 2


def test_a_wide_net_is_still_spanned():
    """Above the sparsification threshold the candidate edge set is thinned to
    each pad's nearest neighbours, so the union with the MST edges is what keeps
    the result connected. If that union were dropped the tree would fall apart
    exactly on the big power nets."""
    rng = random.Random(7)
    pts = [(round(rng.uniform(0, 90), 2), round(rng.uniform(0, 90), 2))
           for _ in range(nettopo.SPARSE_ABOVE + 8)]
    tree = nettopo.min_cross_tree(pts, [(45.0, 0.0, 45.0, 90.0)])
    assert len(tree) == len(pts) - 1
    seen = {0}
    for _ in range(len(pts)):
        for i, j in tree:
            if i in seen or j in seen:
                seen |= {i, j}
    assert len(seen) == len(pts)


def test_edge_weight_does_not_depend_on_which_end_is_first():
    """A pad lattice is full of exactly-collinear triples, and the cross-product
    signs that decide a crossing are not bit-identical when the endpoints are
    swapped. If the weight of edge (i, j) can differ from (j, i), the descent
    compares its current tree against itself and 'improves' forever: measured on
    mppt_buck, one net re-treed to the tree it already had, every pass, because
    the two orientations disagreed by exactly one crossing.
    """
    rng = random.Random(31)
    for _ in range(300):
        pts = [(float(rng.randint(0, 6)), float(rng.randint(0, 6)))
               for _ in range(4)]
        obs = [tuple(float(rng.randint(0, 6)) for _ in range(4))
               for _ in range(6)]
        padded = [nettopo._pad(*o) for o in obs]
        for i in range(4):
            for j in range(i + 1, 4):
                a = nettopo._weight(pts, i, j, padded, nettopo.CROSS_MM)
                b = nettopo._weight(pts, j, i, padded, nettopo.CROSS_MM)
                assert a == b, (pts[i], pts[j], obs)


def test_descent_converges_on_a_lattice_board():
    """Termination, on the geometry that breaks it: parts on a grid. Hitting the
    pass cap is not an error, but it means the loop is still moving when it stops
    and the cap is doing the terminating -- so it must not happen on ordinary
    boards."""
    rng = random.Random(17)
    parts = []
    for n in range(12):
        for k in range(rng.randint(2, 4)):
            parts.append(_term(f"P{n}_{k}", f"N{n}",
                               float(rng.randrange(0, 60, 5)),
                               float(rng.randrange(0, 60, 5))))
    r = nettopo.retree(_board(*parts))
    assert r.passes < nettopo.MAX_PASSES, "descent did not converge"


# --------------------------------------------------------------------------
# the crossing predicate must agree with the one it is measured by
# --------------------------------------------------------------------------

def test_crossing_predicate_agrees_with_globalroute():
    """nettopo tests raw coordinates in its inner loop instead of building a
    Segment per candidate edge (it evaluates tens of thousands of them). That is
    a second copy of the predicate, so it is pinned to the original here: any
    disagreement would make the module optimise a metric nobody else measures."""
    rng = random.Random(20260813)
    for _ in range(4000):
        c = [round(rng.uniform(0, 6), 1) for _ in range(8)]
        s = globalroute.Segment("a", c[0], c[1], c[2], c[3])
        t = globalroute.Segment("b", c[4], c[5], c[6], c[7])
        assert nettopo._proper_cross(*c) == globalroute._crosses(s, t), c


# --------------------------------------------------------------------------
# same contract as globalroute.net_segments, so it is a drop-in replacement
# --------------------------------------------------------------------------

def test_plane_and_single_pad_nets_are_skipped():
    b = _board(*_wire("G", "/GND", 10, 10, 40, 40),
               *_wire("S", "/SIG", 10, 40, 40, 10),
               _term("L", "/LONELY", 80, 80))
    assert [s.net for s in nettopo.net_segments(b)] == ["/SIG"]
    b.planes = {"/SIG"}
    assert [s.net for s in nettopo.net_segments(b)] == ["/GND"]


def test_bridges_can_be_skipped():
    """The two vertex-cover solves are the expensive half of the report, and a
    caller ranking on crossings alone should not have to pay for them."""
    b = _board(_term("A0", "A", 0, 0), _term("A1", "A", 10, 10),
               _term("A2", "A", 20, 0), *_wire("B", "B", 2, 10, 8, 2))
    r = nettopo.retree(b, bridges=False)
    # -1, not 0: an uncomputed bridge count must not read as 'none needed'.
    assert (r.bridges_mst, r.bridges) == (-1, -1)
    assert (r.conflicts_mst, r.conflicts) == (1, 0)  # the rest is unaffected


def test_explicit_planes_are_honoured():
    b = _board(*_wire("P", "/V12", 10, 10, 40, 40),
               *_wire("S", "/SIG", 10, 40, 40, 10))
    assert [s.net for s in nettopo.net_segments(b, planes={"/V12"})] == ["/SIG"]


def test_empty_board_yields_nothing():
    assert nettopo.net_segments(_board()) == []
    r = nettopo.retree(_board())
    assert (r.conflicts, r.bridges, r.changed) == (0, 0, ())


def test_segments_come_back_sorted_by_net():
    b = _random_board(random.Random(3))
    nets = [s.net for s in nettopo.net_segments(b)]
    assert nets == sorted(nets)


# --------------------------------------------------------------------------
# determinism and speed -- it has to be callable from the placer's loop
# --------------------------------------------------------------------------

def test_same_board_gives_the_same_trees():
    b = _random_board(random.Random(11))
    assert nettopo.net_segments(b) == nettopo.net_segments(b)


def test_component_insertion_order_does_not_change_the_trees():
    """The placer builds its board dict in whatever order KiCad hands parts
    over. Sorting inside the module is what makes a run reproducible across
    processes, so a reversed dict must give byte-identical segments."""
    b = _random_board(random.Random(12))
    shuffled = _board(*reversed(list(b.components.values())))
    assert nettopo.net_segments(shuffled) == nettopo.net_segments(b)


def test_fast_enough_to_call_repeatedly():
    """Target: well under 50 ms on a 60-part board, so a candidate placement can
    be re-scored without the outer loop noticing."""
    rng = random.Random(5)
    parts = []
    for n in range(22):                       # ~60 pads over 22 nets
        for k in range(rng.randint(2, 4)):
            parts.append(_term(f"P{n}_{k}", f"N{n}",
                               round(rng.uniform(2, 95), 2),
                               round(rng.uniform(2, 95), 2)))
    b = _board(*parts)
    nettopo.net_segments(b)                   # warm any lazy import
    t0 = time.perf_counter()
    runs = 5
    for _ in range(runs):
        nettopo.net_segments(b)
    per_call_ms = (time.perf_counter() - t0) * 1000.0 / runs
    assert per_call_ms < 50.0, f"{per_call_ms:.1f} ms per net_segments()"
