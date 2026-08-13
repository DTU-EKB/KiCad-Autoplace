"""Exact planarity for netlists: can this board be single-sided at all?

Single-sided routability is, underneath the geometry, a planarity question: a
set of connections can live on one copper layer exactly when its graph can be
drawn in the plane without crossings. That is decidable exactly, so the tool can
know -- before placing anything -- whether single-sided is achievable, and if
not, how many wire bridges are *forced* no matter how well it places.

  python -m pytest tests/test_planarity.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import planarity                          # noqa: E402
from autoplace.model import Board, Component, Pad        # noqa: E402


def _complete(n):
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


def _complete_bipartite(a, b):
    return [(i, a + j) for i in range(a) for j in range(b)]


def _grid(w, h):
    idx = lambda x, y: y * w + x            # noqa: E731
    e = []
    for y in range(h):
        for x in range(w):
            if x + 1 < w:
                e.append((idx(x, y), idx(x + 1, y)))
            if y + 1 < h:
                e.append((idx(x, y), idx(x, y + 1)))
    return e


PETERSEN = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0),          # outer 5-cycle
            (5, 7), (7, 9), (9, 6), (6, 8), (8, 5),          # inner pentagram
            (0, 5), (1, 6), (2, 7), (3, 8), (4, 9)]          # spokes


# --------------------------------------------------------------------------
# the classic yes/no cases
# --------------------------------------------------------------------------

def test_small_complete_graphs():
    assert planarity.is_planar(range(4), _complete(4)) is True      # K4
    assert planarity.is_planar(range(5), _complete(5)) is False     # K5


def test_utility_graph_is_not_planar():
    """K3,3 has 9 edges on 6 vertices, under the 3V-6 = 12 bound, so a naive
    edge-count filter passes it. It needs the real algorithm."""
    assert planarity.is_planar(range(6), _complete_bipartite(3, 3)) is False


def test_k33_minus_one_edge_is_planar():
    e = _complete_bipartite(3, 3)
    assert planarity.is_planar(range(6), e[1:]) is True


def test_petersen_graph_is_not_planar():
    """15 edges on 10 vertices -- well under 3V-6 = 24. Only a genuine planarity
    test rejects this one."""
    assert planarity.is_planar(range(10), PETERSEN) is False


def test_trees_and_forests_and_empty_graphs_are_planar():
    assert planarity.is_planar([], []) is True
    assert planarity.is_planar(range(3), []) is True
    assert planarity.is_planar(range(4), [(0, 1), (1, 2), (1, 3)]) is True
    assert planarity.is_planar(range(6), [(0, 1), (1, 2), (3, 4)]) is True


def test_grid_is_planar():
    assert planarity.is_planar(range(16), _grid(4, 4)) is True


def test_disconnected_graph_is_planar_only_if_every_part_is():
    k5_a = _complete(5)
    k5_b = [(a + 5, b + 5) for a, b in _complete(5)]
    assert planarity.is_planar(range(10), k5_b) is False
    assert planarity.is_planar(range(10), k5_a + k5_b) is False


def test_a_cut_vertex_does_not_make_two_planar_halves_nonplanar():
    """Two K4s sharing one vertex: planar, but a test that forgets to split into
    biconnected components can get this wrong."""
    a = _complete(4)
    b = [(x + 3, y + 3) for x, y in _complete(4)]     # shares vertex 3
    assert planarity.is_planar(range(7), a + b) is True


# --------------------------------------------------------------------------
# things that must not change the answer
# --------------------------------------------------------------------------

def test_parallel_edges_and_self_loops_do_not_affect_planarity():
    """Two nets between the same pair of parts route side by side, and a net
    that starts and ends on one part needs no crossing. Neither can make a board
    non-planar, so neither may change the verdict."""
    e = _complete(4) + [(0, 1), (0, 1), (2, 2)]
    assert planarity.is_planar(range(4), e) is True
    assert planarity.is_planar(range(5), _complete(5) + [(0, 1), (3, 3)]) is False


def test_node_labels_need_not_be_integers():
    e = [("U1", "R1"), ("R1", "C1"), ("C1", "U1")]
    assert planarity.is_planar(["U1", "R1", "C1"], e) is True


def test_verdict_does_not_depend_on_edge_order():
    import random
    rng = random.Random(4242)
    for graph in (_complete(5), _complete_bipartite(3, 3), _grid(3, 3), PETERSEN):
        want = planarity.is_planar(range(16), graph)
        for _ in range(5):
            shuffled = list(graph)
            rng.shuffle(shuffled)
            assert planarity.is_planar(range(16), shuffled) is want


# --------------------------------------------------------------------------
# the embedding itself has to be real, not just a yes
# --------------------------------------------------------------------------

def test_planar_faces_satisfy_eulers_formula():
    """A claimed embedding is checkable: V - E + F = 2 on a connected planar
    graph. If the face list does not balance, the 'planar' verdict is worthless."""
    for e in (_complete(4), _grid(4, 4), _complete_bipartite(2, 3),
              [(0, 1), (1, 2), (2, 0)]):
        v = {x for edge in e for x in edge}          # isolated nodes have no face
        faces = planarity.planar_faces(v, e)
        assert faces is not None
        assert len(v) - len(e) + len(faces) == 2, (len(v), len(e), len(faces))


def test_planar_faces_returns_none_when_not_planar():
    assert planarity.planar_faces(range(5), _complete(5)) is None


def test_every_edge_borders_exactly_two_faces():
    faces = planarity.planar_faces(range(16), _grid(4, 4))
    seen = {}
    for f in faces:
        for i in range(len(f)):
            key = tuple(sorted((f[i], f[(i + 1) % len(f)])))
            seen[key] = seen.get(key, 0) + 1
    assert set(seen.values()) == {2}


# --------------------------------------------------------------------------
# skewness == the forced wire bridges
# --------------------------------------------------------------------------

def test_planar_graph_needs_no_bridges():
    assert planarity.skewness(range(16), _grid(4, 4)) == 0


def test_k5_and_k33_need_exactly_one_bridge():
    assert planarity.skewness(range(5), _complete(5)) == 1
    assert planarity.skewness(range(6), _complete_bipartite(3, 3)) == 1


def test_k6_needs_three_bridges():
    """K6 has 15 edges but a planar graph on 6 vertices holds at most 12, so at
    least 3 must go; the octahedron K2,2,2 shows 3 is enough."""
    assert planarity.skewness(range(6), _complete(6)) == 3


def test_two_disjoint_k5s_need_two_bridges():
    e = _complete(5) + [(a + 5, b + 5) for a, b in _complete(5)]
    assert planarity.skewness(range(10), e) == 2


def test_removing_the_skew_edges_really_leaves_a_planar_graph():
    """The count is only meaningful if the edges it names actually work."""
    for n, e in ((5, _complete(5)), (6, _complete_bipartite(3, 3)),
                 (10, PETERSEN), (6, _complete(6))):
        drop = planarity.skew_edges(range(n), e)
        keep = [x for x in e if tuple(sorted(x)) not in
                {tuple(sorted(d)) for d in drop}]
        assert planarity.is_planar(range(n), keep) is True
        assert len(drop) == planarity.skewness(range(n), e)


def test_parallel_edges_are_free_bridges():
    """A second net between the same two parts runs alongside the first."""
    e = _complete(5) + [(0, 1)] * 4
    assert planarity.skewness(range(5), e) == 1


# --------------------------------------------------------------------------
# turning a board into the graph whose planarity we care about
# --------------------------------------------------------------------------

def _part(ref, *nets):
    return Component(ref=ref, w=2.0, h=2.0,
                     pads=[Pad(str(i + 1), n, 0.0, 0.0) for i, n in enumerate(nets)])


def _board(*parts):
    b = Board(0, 0, 100, 100)
    b.components = {p.ref: p for p in parts}
    return b


def test_netlist_graph_is_the_component_net_incidence():
    """A net is a hyperedge -- it joins every part on it, and the junction is a
    real point on the copper. Modelling it as a node connected to each member
    is exact, and avoids inventing an arbitrary spanning tree whose shape would
    change the planarity answer."""
    b = _board(_part("R1", "A", "B"), _part("R2", "B", "C"), _part("R3", "C", "A"))
    nodes, edges = planarity.netlist_graph(b)
    assert set(nodes) == {("c", "R1"), ("c", "R2"), ("c", "R3"),
                          ("n", "A"), ("n", "B"), ("n", "C")}
    assert len(edges) == 6                      # each part touches two nets
    assert planarity.is_planar(nodes, edges) is True


def test_netlist_graph_drops_poured_nets():
    """A filled ground plane connects its pads for free, so ground is not an
    edge at all -- and it is usually the highest-degree net on the board, so
    leaving it in would call almost every board non-planar."""
    b = _board(_part("R1", "A", "/GND"), _part("R2", "A", "/GND"))
    b.planes = {"/GND"}
    nodes, edges = planarity.netlist_graph(b)
    assert ("n", "/GND") not in set(nodes)
    assert all("/GND" not in str(e) for e in edges)


def test_netlist_graph_ignores_single_member_nets():
    b = _board(_part("R1", "A", "B"), _part("R2", "B", "C"), _part("R3", "C", "A"),
               _part("TP1", "DANGLING"))
    nodes, _ = planarity.netlist_graph(b)
    assert ("n", "DANGLING") not in set(nodes)


def test_a_board_that_cannot_be_single_sided_is_detected():
    """Five parts each connected to all the others is K5: no placement, however
    clever, routes it on one layer. The tool should say so instead of letting
    the router discover it 60 seconds at a time."""
    parts = []
    for i in range(5):
        nets = [f"N{min(i, j)}{max(i, j)}" for j in range(5) if j != i]
        parts.append(_part(f"U{i}", *nets))
    nodes, edges = planarity.netlist_graph(_board(*parts))
    assert planarity.is_planar(nodes, edges) is False
    assert planarity.skewness(nodes, edges) == 1


def test_forced_bridges_reports_both_the_verdict_and_the_count():
    b = _board(_part("R1", "A", "B"), _part("R2", "B", "C"), _part("R3", "C", "A"))
    r = planarity.forced_bridges(b)
    assert r["planar"] is True
    assert r["bridges"] == 0
    assert r["components"] == 3 and r["nets"] == 3
