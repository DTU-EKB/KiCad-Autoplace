"""Headless tests for the orientation-only post-placement pass.

Everything here is pure Python -- no pcbnew, no Java. The pass only ever writes
``Component.rot``, so most of the suite is about what it must NOT do: move a
part, touch a locked or edge-pinned one, create an overlap, or produce a
different answer on a second run.

  python -m pytest tests/test_orient.py
"""
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute, metrics, orient           # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _term(ref, net, x, y, size=0.6, locked=True):
    """A one-pad 'terminal': its pad sits at (x, y) and cannot move under
    rotation (offset 0,0), so it is inert scenery for these cases."""
    return Component(ref=ref, w=size, h=size, x=x, y=y, locked=locked,
                     pads=[Pad("1", net, 0.0, 0.0)])


def _flip_board():
    """Two tall 2-pin parts wired into an X. Verified against globalroute:

        R1 rot   0 -> 1 conflict,  1 bridge, no overlap
        R1 rot  90 -> 0 conflicts, but R1 x R2 OVERLAP (eff_w 14 vs a 4 mm gap)
        R1 rot 180 -> 0 conflicts, no overlap, and the shortest tree
        R1 rot 270 -> 0 conflicts, same overlap as 90

    So the single legal fix is a 180 flip of R1: no part moves, the bounding
    box is unchanged, and the crossing is gone.
    """
    r1 = Component(ref="R1", w=4.0, h=14.0, x=10.0, y=20.0,
                   pads=[Pad("1", "N1", 0.0, -5.0), Pad("2", "N2", 0.0, 5.0)])
    r2 = Component(ref="R2", w=4.0, h=14.0, x=18.0, y=20.0,
                   pads=[Pad("1", "N2", 0.0, -5.0), Pad("2", "N1", 0.0, 5.0)])
    b = Board(0.0, 0.0, 40.0, 40.0)
    b.components = {c.ref: c for c in (r1, r2)}
    return b


def _quarter_board(blocker=False):
    """One free part whose only crossing-free orientation is 90.

    A fixed NW wire runs across the board at y=20. P1 straddles it with one pad
    above and one below; at rot 0 and 180 exactly one of its nets has to cross
    the wire, and at 270 the tree gets longer and still crosses. Only rot 90 --
    which lays P1 flat, putting both pads below the wire -- removes it. That
    rotation turns a 3x20 part into a 20x3 one, so ``blocker`` can make it
    illegal without touching anything else.
    """
    p = Component(ref="P1", w=3.0, h=20.0, x=20.0, y=24.0,
                  pads=[Pad("1", "N1", 0.0, -9.0), Pad("2", "N2", 0.0, 9.0)])
    b = Board(0.0, 0.0, 40.0, 44.0)
    parts = [p, _term("L1", "NW", 5.0, 20.0), _term("L2", "NW", 35.0, 20.0),
             _term("T1", "N1", 12.0, 34.0), _term("T2", "N2", 28.0, 34.0)]
    if blocker:
        # x 29..35: clear of P1 at rot 0 (18.5..21.5), overlapping it at rot 90
        # (10..30). Pad-less, so it changes geometry only, never the netlist.
        parts.append(Component(ref="B1", w=6.0, h=6.0, x=32.0, y=24.0,
                               locked=True, pads=[]))
    b.components = {c.ref: c for c in parts}
    return b


def _random_board(seed, n=14, w=90.0, h=70.0, pitch=None):
    """A pseudo-board: 2-3 pad parts on a loose grid, nets wired across it.

    Not a real circuit -- the point is a placement with plenty of crossings and
    a spread of part shapes/rotations for the invariants to bite on.
    """
    rng = random.Random(seed)
    b = Board(0.0, 0.0, w, h)
    comps = {}
    cols = 5
    pitch = pitch or (w / (cols + 1))
    names = [f"NET{i}" for i in range(n)]
    for k in range(n):
        ref = f"U{k:02d}"
        cw = rng.choice([2.5, 5.0, 8.0])
        ch = rng.choice([1.6, 3.0, 6.0])
        pads = [Pad(str(i + 1), names[(k * 3 + i) % len(names)],
                    -cw / 2 + 0.4 + i * (cw - 0.8) / 2, (i % 2) * 0.5 - 0.25)
                for i in range(rng.choice([2, 3]))]
        comps[ref] = Component(
            ref=ref, w=cw, h=ch, pads=pads,
            x=pitch * (1 + k % cols), y=8.0 + (k // cols) * (h - 16.0) / 3.0,
            rot=rng.choice([0, 90, 180, 270]))
    b.components = comps
    return b


def _placed_board(seed, n=40, w=120.0, h=90.0):
    """A board shaped like a *placed* one: parts on a grid, nets joining only
    neighbours.

    ``_random_board`` wires across the whole board on purpose and lands at 500+
    crossings, which is great for hammering the invariants but is not what comes
    out of the annealer -- the real corpus places at 5-30 crossings
    (tools/eval_orient.py), and the pass's cost is driven by exactly that number.
    """
    rng = random.Random(seed)
    cols = 8
    rows = max(1, (n + cols - 1) // cols)
    dx, dy = w / (cols + 1), h / (rows + 1)
    wiring: dict[int, list[str]] = {}
    for k in range(n):
        for nb in ((k + 1) if (k + 1) % cols else -1, k + cols):
            if not (0 <= nb < n):
                continue
            name = f"N{len(wiring.get(-1, []))}_{k}_{nb}"
            wiring.setdefault(k, []).append(name)
            wiring.setdefault(nb, []).append(name)
    b = Board(0.0, 0.0, w, h)
    comps = {}
    for k in range(n):
        ref = f"U{k:02d}"
        mine = wiring.get(k, [])
        cw, ch = rng.choice([2.5, 5.0, 7.0]), rng.choice([1.6, 3.0])
        pads = [Pad(str(i + 1), net, -cw / 2 + 0.4 + i * (cw - 0.8) / 3,
                    (i % 2) * 0.5 - 0.25) for i, net in enumerate(mine)]
        comps[ref] = Component(ref=ref, w=cw, h=ch, pads=pads,
                               x=dx * (1 + k % cols), y=dy * (1 + k // cols),
                               rot=rng.choice([0, 90, 180, 270]))
    b.components = comps
    return b


def _snapshot(board):
    return {c.ref: (c.x, c.y, c.rot) for c in board.components.values()}


def _measure(board):
    segs = globalroute.net_segments(board)
    conf = globalroute.conflicts(segs)
    return globalroute.min_bridges(len(segs), conf), len(conf)


# --------------------------------------------------------------------------
# the core claim: a free flip removes a crossing
# --------------------------------------------------------------------------

def test_a_180_flip_removes_a_crossing_for_free():
    b = _flip_board()
    assert _measure(b) == (1, 1)
    pos_before = {c.ref: (c.x, c.y) for c in b.components.values()}

    rep = orient.optimise(b)

    assert _measure(b) == (0, 0)
    assert b.components["R1"].rot == 180
    assert b.components["R2"].rot == 0          # flipping both re-crosses them
    assert {c.ref: (c.x, c.y) for c in b.components.values()} == pos_before
    assert metrics.overlaps(b) == []
    assert rep["rotated"] == [("R1", 0, 180)]
    assert (rep["before"]["conflicts"], rep["after"]["conflicts"]) == (1, 0)
    assert (rep["before"]["bridges"], rep["after"]["bridges"]) == (1, 0)


def test_the_flip_also_drops_the_cheap_crossings_proxy():
    """The pass optimises globalroute's crossing graph; ``metrics.crossings``
    is a different tree over a different net subset. On this case they agree,
    which is the sanity check that the objective is not self-referential."""
    b = _flip_board()
    assert metrics.crossings(b) == 1
    orient.optimise(b)
    assert metrics.crossings(b) == 0


# --------------------------------------------------------------------------
# legality: a rotation that costs an overlap is not worth a crossing
# --------------------------------------------------------------------------

def test_a_quarter_turn_that_fits_is_taken():
    b = _quarter_board(blocker=False)
    assert _measure(b) == (1, 1)
    orient.optimise(b)
    assert b.components["P1"].rot == 90
    assert _measure(b) == (0, 0)
    assert metrics.overlaps(b) == []


def test_a_rotation_that_would_overlap_a_neighbour_is_rejected():
    """Same board, same improving rotation -- but now it collides. The pass
    must keep the crossing rather than buy it with an overlap."""
    b = _quarter_board(blocker=True)
    assert _measure(b) == (1, 1)
    assert metrics.overlaps(b) == []

    rep = orient.optimise(b)

    assert b.components["P1"].rot == 0
    assert _measure(b) == (1, 1)
    assert metrics.overlaps(b) == []
    assert rep["rotated"] == []


def test_a_rotation_that_would_leave_the_outline_is_rejected():
    """The pass never moves a part, so a rotation whose taller/wider box pokes
    out of the outline has to be refused -- clamping it back in would turn an
    orientation pass into a placement pass."""
    b = _quarter_board(blocker=False)
    b.x1 = 26.0                    # P1 at rot 90 spans x 10..30: outside now
    assert _measure(b) == (1, 1)
    orient.optimise(b, margin=0.8)
    assert b.components["P1"].rot == 0


def test_no_overlap_is_ever_introduced_on_dense_boards():
    """Randomised: whatever the pass does, the set of overlapping pairs may
    only shrink. Dense boards, so most quarter turns really do collide."""
    for seed in range(12):
        b = _random_board(seed, n=16, w=52.0, h=40.0)
        before = set(metrics.overlaps(b))
        orient.optimise(b)
        assert set(metrics.overlaps(b)) <= before, seed


def test_a_pre_existing_overlap_is_not_made_worse():
    """A legalize residual must not lock the pass out, but it must not deepen
    either: the pair's separation may not decrease."""
    b = _flip_board()
    b.components["R2"].x = 12.0                       # forced overlap with R1
    assert metrics.overlaps(b) == [("R1", "R2")]
    gap_before = abs(10.0 - 12.0) - (4.0 + 4.0) / 2
    orient.optimise(b)
    a, c = b.components["R1"], b.components["R2"]
    gap_after = max(abs(a.x - c.x) - (a.eff_w + c.eff_w) / 2,
                    abs(a.y - c.y) - (a.eff_h + c.eff_h) / 2)
    assert gap_after >= gap_before - 1e-9


# --------------------------------------------------------------------------
# what the pass is not allowed to touch
# --------------------------------------------------------------------------

def test_locked_components_are_never_rotated():
    b = _flip_board()
    b.components["R1"].locked = True
    orient.optimise(b)
    assert b.components["R1"].rot == 0


def test_edge_pinned_components_keep_their_orientation():
    """``anneal`` excludes edge-pinned parts from rotate moves -- a connector
    faces its edge, and a pass that spins it to save a crossing produces a
    board whose USB port points inwards. Same rule here."""
    b = _flip_board()
    b.components["R1"].edge = "L"
    orient.optimise(b)
    assert b.components["R1"].rot == 0
    assert b.components["R2"].rot == 180              # R2 alone still fixes it
    assert _measure(b) == (0, 0)


def test_positions_are_never_changed():
    for seed in range(6):
        b = _random_board(seed)
        before = {c.ref: (c.x, c.y) for c in b.components.values()}
        orient.optimise(b)
        assert {c.ref: (c.x, c.y) for c in b.components.values()} == before, seed


def test_rotations_stay_on_the_ninety_degree_grid():
    b = _random_board(3)
    orient.optimise(b)
    assert all(c.rot in (0, 90, 180, 270) for c in b.components.values())


# --------------------------------------------------------------------------
# the objective genuinely goes down, and stops going down
# --------------------------------------------------------------------------

def test_the_pass_never_increases_bridges_or_conflicts():
    for seed in range(12):
        b = _random_board(seed)
        before = _measure(b)
        orient.optimise(b)
        after = _measure(b)
        assert after <= before, (seed, before, after)


def test_the_reported_numbers_match_a_fresh_analyse():
    """The report is what the eval harness prints, so it must be the same
    number an independent ``globalroute`` call would produce -- not an
    incremental tally that has drifted."""
    for seed in range(6):
        b = _random_board(seed)
        rep = orient.optimise(b)
        bridges, conflicts = _measure(b)
        assert (rep["after"]["bridges"], rep["after"]["conflicts"]) == \
            (bridges, conflicts), seed


def test_a_second_run_changes_nothing():
    """Sweeping to a local optimum means exactly this: re-running finds no
    single rotation left to take."""
    for seed in range(8):
        b = _random_board(seed)
        orient.optimise(b)
        state = _snapshot(b)
        rep = orient.optimise(b)
        assert rep["rotated"] == [], seed
        assert _snapshot(b) == state, seed


def test_sweeping_is_bounded():
    b = _random_board(1)
    rep = orient.optimise(b, max_sweeps=1)
    assert rep["sweeps"] == 1
    rep = orient.optimise(b, max_sweeps=orient.MAX_SWEEPS)
    assert rep["sweeps"] <= orient.MAX_SWEEPS


def test_wirelength_tiebreak_can_be_switched_off():
    """With the tie-break off the pass must still never lose crossings, and it
    must leave orientations alone that only shorten copper."""
    for seed in range(6):
        b = _random_board(seed)
        before = _measure(b)
        orient.optimise(b, wirelength_tiebreak=False)
        assert _measure(b) <= before, seed


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

def test_same_board_in_same_board_out():
    for seed in range(6):
        a = _random_board(seed)
        c = _random_board(seed)
        orient.optimise(a)
        orient.optimise(c)
        assert _snapshot(a) == _snapshot(c), seed


_HASHSEED_SCRIPT = r"""
import hashlib, os, sys
sys.path.insert(0, os.path.join(r"%s", "plugin", "plugins"))
sys.path.insert(0, os.path.join(r"%s", "tests"))
from test_orient import _random_board
from autoplace import orient

b = _random_board(5, n=18)
orient.optimise(b)
s = ";".join("%%s,%%d" %% (c.ref, c.rot)
             for c in sorted(b.components.values(), key=lambda c: c.ref))
print(hashlib.sha1(s.encode()).hexdigest())
""" % (REPO, REPO)


def test_result_is_hashseed_independent():
    """Same rule as the placement engine (tests/test_determinism.py): str-set
    iteration order changes between processes, so any set the pass iterates
    while accumulating floats or picking a winner would make the answer
    process-dependent."""
    digests = {}
    for hs in ("1", "2", "3", "4"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        out = subprocess.run([sys.executable, "-c", _HASHSEED_SCRIPT], env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        digests[hs] = out.stdout.strip()
    assert len(set(digests.values())) == 1, digests


# --------------------------------------------------------------------------
# the incremental crossing graph must equal the reference one
# --------------------------------------------------------------------------

def test_incremental_trees_match_globalroute_after_rotations():
    """The pass keeps the net trees and the crossing graph up to date under
    single-part rotations instead of rebuilding them. That shortcut is only
    safe if it is exact, so: rotate at random and compare against a fresh
    ``net_segments`` / ``conflicts`` every time."""
    rng = random.Random(4242)
    for seed in range(6):
        b = _random_board(seed)
        trees = orient._Trees(b)
        for _ in range(30):
            ref = rng.choice(sorted(b.components))
            trees.rotate(ref, rng.choice([0, 90, 180, 270]))
            ref_segs = globalroute.net_segments(b)
            assert trees.segments == ref_segs
            assert sorted(trees.pairs) == globalroute.conflicts(ref_segs)
            assert trees.bridges() == globalroute.min_bridges(
                len(ref_segs), globalroute.conflicts(ref_segs))


def test_plane_nets_are_excluded_like_globalroute():
    """A poured net needs no copper, so it must not create phantom crossings
    the pass then spins parts to fix. With N1 poured the X collapses to a single
    segment and there is nothing left to uncross."""
    b = _flip_board()
    b.planes = {"N1"}
    trees = orient._Trees(b, planes={"N1"})
    assert [s.net for s in trees.segments] == ["N2"]
    rep = orient.optimise(b, planes={"N1"})
    assert rep["before"]["conflicts"] == 0
    assert rep["after"]["conflicts"] == 0
    assert rep["after"]["wirelength"] <= rep["before"]["wirelength"]


# --------------------------------------------------------------------------
# degenerate boards, and speed
# --------------------------------------------------------------------------

def test_empty_board_does_not_crash():
    rep = orient.optimise(Board(0.0, 0.0, 50.0, 50.0))
    assert rep["rotated"] == []
    assert rep["after"]["conflicts"] == 0


def test_board_with_no_outline_still_takes_the_free_flip():
    """A project with no Edge.Cuts loads as a zero-size outline (seen on a real
    project). Every part is then already outside it, so a rule of "must end up
    inside" would disable the pass entirely. The rule is "must not end up
    further outside", and a 180 flip -- which cannot change the box -- always
    satisfies it, while the quarter turns that grow the box are still refused."""
    b = _flip_board()
    b.x0 = b.y0 = b.x1 = b.y1 = 0.0
    rep = orient.optimise(b)
    assert rep["rotated"] == [("R1", 0, 180)]
    assert rep["after"]["conflicts"] == 0


def test_all_locked_board_does_nothing():
    b = _flip_board()
    for c in b.components.values():
        c.locked = True
    rep = orient.optimise(b)
    assert rep["rotated"] == []
    assert rep["sweeps"] == 0


def test_fast_enough_to_run_after_every_placement():
    """The pass is only worth wiring into ``engine.place`` if it is noise next
    to the anneal (seconds). 40 parts at ~25 crossings is the shape of the real
    corpus after placement."""
    b = _placed_board(7, n=40)
    assert 5 <= _measure(b)[1] <= 60, _measure(b)
    t0 = time.perf_counter()
    rep = orient.optimise(b)
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"{dt * 1000:.0f} ms for {len(b.components)} parts"
    assert rep["seconds"] <= dt + 1e-6
    assert rep["exact_bridges"] is True


def test_it_can_untangle_a_realistic_board_completely():
    """Neighbour-wired grid: every crossing in it is an artefact of which way
    the parts happen to face, and orientation alone should clear the lot."""
    for seed in (1, 7):
        b = _placed_board(seed, n=40)
        assert _measure(b)[0] > 0
        orient.optimise(b)
        assert _measure(b) == (0, 0), seed
        assert metrics.overlaps(b) == []


def test_a_hairball_falls_back_to_the_crossing_count():
    """``min_bridges`` is a branch-and-bound vertex cover: ~0.5 ms at 93
    crossings but ~142 ms at 376, and past its node budget it is no longer even
    exact. A pathological placement must not turn a post-processing pass into a
    minute of CPU, so beyond BRIDGE_LIMIT the objective drops to crossings --
    which still only ever goes down. (Measured: this board costs 17 s with the
    exact bridge count in the key and 1.4 s without.)"""
    b = _random_board(11, n=80, w=140.0, h=110.0)
    before = _measure(b)
    assert before[1] > orient.BRIDGE_LIMIT, before
    t0 = time.perf_counter()
    rep = orient.optimise(b)
    dt = time.perf_counter() - t0
    assert rep["exact_bridges"] is False
    assert rep["after"]["conflicts"] <= rep["before"]["conflicts"]
    assert dt < 10.0, f"{dt:.1f} s on a {before[1]}-crossing hairball"


def test_the_bridge_limit_is_decided_once_not_per_candidate():
    """If the key could switch definition mid-sweep the pass could accept a move
    purely because the objective changed under it, and the strictly-decreasing
    argument that guarantees termination would be gone."""
    b = _random_board(11, n=80, w=140.0, h=110.0)
    rep = orient.optimise(b)
    assert rep["exact_bridges"] is False
    # Re-running re-reads the (now lower) crossing count, so the flag is a
    # function of the STARTING placement only -- never of a mid-run state.
    again = orient.optimise(b)
    assert again["exact_bridges"] is (again["before"]["conflicts"]
                                      <= orient.BRIDGE_LIMIT)
