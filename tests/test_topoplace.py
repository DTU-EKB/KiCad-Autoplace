"""Placement seeded from a planar embedding rather than from forces.

Once a netlist is known planar, "avoid crossings" stops being the goal and
"realise this embedding" takes over: there exists a drawing with no crossings at
all, and a barycentric (Tutte) layout finds one. These tests pin the property
that matters -- the seed really is crossing-free -- rather than any particular
coordinates.

  python -m pytest tests/test_topoplace.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute, planarity, topoplace     # noqa: E402
from autoplace.model import Board, Component, Pad           # noqa: E402


def _part(ref, *nets, w=3.0, h=3.0, locked=False, edge=""):
    return Component(ref=ref, w=w, h=h, locked=locked, edge=edge,
                     pads=[Pad(str(i + 1), n, 0.0, 0.0) for i, n in enumerate(nets)])


def _board(*parts, w=100.0, h=80.0):
    b = Board(0.0, 0.0, w, h)
    b.components = {p.ref: p for p in parts}
    return b


def _ring(n):
    """n parts in a cycle: R0-R1-...-Rn-1-R0. Planar, and its only embedding
    puts every part on one face."""
    return _board(*[_part(f"R{i}", f"N{i}", f"N{(i + 1) % n}") for i in range(n)])


def _mesh():
    """3x3 grid of parts wired to their right and lower neighbours."""
    nets = {(x, y): [] for y in range(3) for x in range(3)}
    n = 0
    for y in range(3):
        for x in range(3):
            for nb in (((x + 1, y) if x + 1 < 3 else None),
                       ((x, y + 1) if y + 1 < 3 else None)):
                if nb is None:
                    continue
                nets[(x, y)].append(f"N{n}")
                nets[nb].append(f"N{n}")
                n += 1
    return _board(*[_part(f"U{x}{y}", *ns) for (x, y), ns in sorted(nets.items())],
                  w=120.0, h=120.0)


def _crossing_pairs(board):
    """Straight-line crossings between the drawn net trees -- the thing the
    embedding is supposed to drive to zero."""
    segs = globalroute.net_segments(board)
    return globalroute.conflicts(segs)


# --------------------------------------------------------------------------
# it produces a usable placement at all
# --------------------------------------------------------------------------

def test_every_free_component_gets_a_position():
    b = _ring(6)
    topoplace.seed(b)
    assert all(isinstance(c.x, float) and isinstance(c.y, float)
               for c in b.components.values())


def test_positions_land_inside_the_outline():
    b = _ring(8)
    topoplace.seed(b)
    for c in b.components.values():
        assert b.x0 <= c.left and c.right <= b.x1, c.ref
        assert b.y0 <= c.top and c.bottom <= b.y1, c.ref


def test_components_do_not_all_collapse_onto_one_point():
    """A barycentric relaxation with no fixed boundary collapses everything to a
    single point. The boundary is what stops that, so this is the load-bearing
    property, not a nicety."""
    b = _ring(8)
    topoplace.seed(b)
    pts = {(round(c.x, 3), round(c.y, 3)) for c in b.components.values()}
    assert len(pts) == len(b.components)


def test_locked_components_are_never_moved():
    b = _board(_part("R0", "A", "B"), _part("R1", "B", "C"),
               _part("R2", "C", "A", locked=True))
    b.components["R2"].x, b.components["R2"].y = 12.5, 33.5
    topoplace.seed(b)
    assert (b.components["R2"].x, b.components["R2"].y) == (12.5, 33.5)


def test_seeding_is_deterministic():
    a, b = _ring(9), _ring(9)
    topoplace.seed(a)
    topoplace.seed(b)
    assert [(c.ref, round(c.x, 9), round(c.y, 9)) for c in a.components.values()] == \
           [(c.ref, round(c.x, 9), round(c.y, 9)) for c in b.components.values()]


def test_the_same_seed_number_reproduces_the_same_layout():
    a, b = _mesh(), _mesh()
    topoplace.seed(a, seed=3)
    topoplace.seed(b, seed=3)
    assert [(c.ref, round(c.x, 9), round(c.y, 9)) for c in a.components.values()] == \
           [(c.ref, round(c.x, 9), round(c.y, 9)) for c in b.components.values()]


def test_different_seeds_give_different_planar_drawings():
    """A planar graph has many embeddings -- any face may be the outer one, and
    each choice is a different but equally valid crossing-free layout. Without
    this the gallery's multi-seed diversity collapses: every candidate would be
    the same board, because the embedding ignores the RNG entirely."""
    layouts = set()
    for s in range(6):
        b = _mesh()
        topoplace.seed(b, seed=s)
        layouts.add(tuple(sorted((c.ref, round(c.x, 4), round(c.y, 4))
                                 for c in b.components.values())))
    assert len(layouts) >= 2


def test_every_seed_still_produces_a_legal_in_bounds_layout():
    for s in range(6):
        b = _mesh()
        topoplace.seed(b, seed=s)
        for c in b.components.values():
            assert b.x0 <= c.left and c.right <= b.x1, (s, c.ref)
            assert b.y0 <= c.top and c.bottom <= b.y1, (s, c.ref)


# --------------------------------------------------------------------------
# the point of the whole exercise: no crossings
# --------------------------------------------------------------------------

def test_a_ring_is_drawn_without_crossings():
    b = _ring(10)
    topoplace.seed(b)
    assert _crossing_pairs(b) == []


def test_a_planar_mesh_is_drawn_without_crossings():
    """A 3x3 grid of parts wired to their right and lower neighbours: planar,
    and a force-directed seed routinely tangles it."""
    parts, nets = [], {}
    for y in range(3):
        for x in range(3):
            nets[(x, y)] = []
    conn = 0
    edges = []
    for y in range(3):
        for x in range(3):
            if x + 1 < 3:
                edges.append(((x, y), (x + 1, y)))
            if y + 1 < 3:
                edges.append(((x, y), (x, y + 1)))
    for a, bb in edges:
        net = f"N{conn}"
        conn += 1
        nets[a].append(net)
        nets[bb].append(net)
    for (x, y), ns in sorted(nets.items()):
        parts.append(_part(f"U{x}{y}", *ns))
    b = _board(*parts, w=120.0, h=120.0)
    assert planarity.is_planar(*planarity.netlist_graph(b)) is True
    topoplace.seed(b)
    assert _crossing_pairs(b) == []


def test_a_nonplanar_netlist_still_produces_a_legal_seed():
    """K5 cannot be drawn without crossings, so the seed will have some. It must
    still be a usable placement rather than an exception or a NaN."""
    parts = []
    for i in range(5):
        nets = [f"N{min(i, j)}{max(i, j)}" for j in range(5) if j != i]
        parts.append(_part(f"U{i}", *nets))
    b = _board(*parts)
    topoplace.seed(b)
    for c in b.components.values():
        assert c.x == c.x and c.y == c.y          # not NaN
        assert b.x0 <= c.left and c.right <= b.x1


# --------------------------------------------------------------------------
# awkward shapes that must not crash
# --------------------------------------------------------------------------

def test_disconnected_netlist_places_both_halves():
    b = _board(_part("A0", "P", "Q"), _part("A1", "Q", "P"),
               _part("B0", "R", "S"), _part("B1", "S", "R"))
    topoplace.seed(b)
    pts = {(round(c.x, 3), round(c.y, 3)) for c in b.components.values()}
    assert len(pts) == 4


def test_parts_with_no_nets_are_still_placed_inside_the_board():
    b = _board(_part("R0", "A", "B"), _part("R1", "B", "A"), _part("M1"))
    topoplace.seed(b)
    m = b.components["M1"]
    assert b.x0 <= m.left and m.right <= b.x1
    assert b.y0 <= m.top and m.bottom <= b.y1


def test_a_single_component_board_is_centred_not_crashed():
    b = _board(_part("R0", "A", "B"))
    topoplace.seed(b)
    assert b.x0 <= b.components["R0"].left


def test_empty_board_is_a_no_op():
    b = _board()
    topoplace.seed(b)                      # must not raise
    assert b.components == {}


# --------------------------------------------------------------------------
# using it as a placement strategy
# --------------------------------------------------------------------------

def test_engine_exposes_a_topology_seeding_strategy():
    """'topo' must actually seed from the embedding, not silently fall through
    to the force-directed path -- an unknown strategy string would do that and
    still pass a mere in-bounds check."""
    from autoplace import engine
    b = _ring(8)
    engine.place(b, seed=0, strategy="topo", sa_steps=0, aesthetic=False)
    for c in b.components.values():
        assert b.x0 <= c.left and c.right <= b.x1
    direct = _ring(8)
    topoplace.seed(direct)
    engine.place(direct, seed=0, strategy="keep", sa_steps=0, aesthetic=False)
    assert [(c.ref, round(c.x, 6), round(c.y, 6)) for c in b.components.values()] == \
           [(c.ref, round(c.x, 6), round(c.y, 6)) for c in direct.components.values()]


def test_engine_keep_strategy_starts_from_the_given_positions():
    """Needed to measure a seed on its own: 'keep' must not re-seed, so whatever
    the caller placed is what the anneal starts from."""
    from autoplace import engine
    a, b = _ring(6), _ring(6)
    topoplace.seed(a)
    before = {c.ref: (c.x, c.y) for c in a.components.values()}
    engine.place(a, seed=0, strategy="keep", sa_steps=0, aesthetic=False)
    after = {c.ref: (c.x, c.y) for c in a.components.values()}
    # legalize may nudge for overlap, but nothing should be re-seeded across the
    # board: every part stays near where the embedding put it.
    assert all(abs(before[r][0] - after[r][0]) < 25.0 and
               abs(before[r][1] - after[r][1]) < 25.0 for r in before)
    engine.place(b, seed=0, strategy="keep", sa_steps=0, aesthetic=False)
    assert {c.ref for c in b.components.values()} == {c.ref for c in a.components.values()}


def test_engine_can_run_the_orientation_pass():
    """Rotating parts to uncross nets moves nothing and cannot create an
    overlap, so it belongs at the very end -- after legalize and align, where
    the positions are already final."""
    from autoplace import engine, orient
    b = _mesh()
    engine.place(b, seed=0, sa_steps=200, orient_pass=True, aesthetic=False)
    for c in b.components.values():
        assert b.x0 <= c.left and c.right <= b.x1
    # and it really ran: a second pass on the result finds nothing left to turn
    assert orient.optimise(b)["rotated"] == []


def test_engine_orientation_pass_is_off_by_default():
    from autoplace import engine
    a, b = _mesh(), _mesh()
    engine.place(a, seed=1, sa_steps=200, aesthetic=False)
    engine.place(b, seed=1, sa_steps=200, orient_pass=False, aesthetic=False)
    assert [(c.ref, c.x, c.y, c.rot) for c in a.components.values()] == \
           [(c.ref, c.x, c.y, c.rot) for c in b.components.values()]


def test_oversized_parts_are_still_kept_in_bounds():
    """Scaling has to account for footprint size, not just centre points."""
    b = _board(_part("BIG0", "A", "B", w=40.0, h=30.0),
               _part("BIG1", "B", "C", w=40.0, h=30.0),
               _part("BIG2", "C", "A", w=40.0, h=30.0), w=100.0, h=80.0)
    topoplace.seed(b)
    for c in b.components.values():
        assert c.left >= b.x0 - 1e-6 and c.right <= b.x1 + 1e-6, c.ref
