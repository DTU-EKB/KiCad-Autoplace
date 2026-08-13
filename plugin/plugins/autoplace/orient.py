"""Orientation-only cleanup: untangle a placement by turning parts, not moving them.

Which way a part faces decides which of its pads end up near which neighbours.
Flipping a 2-pin resistor by 180 degrees swaps its ends, and on a single-sided
board that can uncross two nets *for free*: nothing moves, the bounding box is
bit-identical, wirelength does not have to be paid, and no other part is
disturbed. The annealer already proposes rotations, but it judges them with the
general search cost (HPWL + overlap + channel + cohesion), which contains no
crossing term at all -- so it has no direct pressure to untangle. This pass
supplies exactly that pressure, and nothing else.

**The objective is lexicographic: (bridges, crossings, tree length).**

* ``globalroute.min_bridges`` is the deliverable number -- the wires the user
  has to solder -- so it must lead. Alone it is a poor search signal: it is a
  minimum vertex cover, so it is coarse and flat, and most rotations that
  genuinely help do so by removing a crossing without removing a whole bridge.
  A pass driven by bridges alone stops on the first plateau.
* ``globalroute.conflicts`` is the fine-grained gradient under it. Alone it
  mis-ranks in the classic way: it will happily trade one bridge for three
  crossings, because it cannot see that one lifted segment clears all of them.
* Lexicographic gets both properties: never accept a move that costs a bridge,
  and among the many bridge-neutral moves prefer the one with fewer crossings.

Tree length is the third key, never the first: two orientations that are equal
on crossings are not equal to the router, and picking between them by rotation
order would be arbitrary. Measured (``wirelength_tiebreak=False`` turns it off,
which is how the two were compared on identical placements): with the tie-break
the routed tree lands at -0.7% to +2.1% per board, without it at +0.7% to +4.9%
-- better on all six -- while the bridge count comes out the same or better on
five of six. So it is on by default.

**Measured on the corpus** (tools/eval_orient.py, 6 boards x 6 seeds, cnc
profile, placements straight out of ``engine.place``):

    board            free   bridges       crossings       HPWL     pass
    buck_v2            31   8.00->5.83    10.3->7.3      +5.7%     179 ms
    c2000_feedback     47   9.50->7.00    10.7->7.8      +2.0%     250 ms
    mppt_buck          20   4.83->3.00     2.7->2.7     +10.6%      63 ms
    current_sense      17   5.33->4.00     6.0->3.7      +4.4%      29 ms
    motor_power        58  20.33->16.00   25.5->20.2     +6.6%    1070 ms
    subxo              32  14.17->11.33   15.0->12.8     +5.0%     248 ms

33 of 36 seeds improved, none regressed, no overlap was ever introduced and no
part ever moved. The whole pass is 1.7% of placement time. The one real cost is
that +5% mean ``metrics.hpwl``: the *routed tree* is neutral, but half-perimeter
over signal nets is not, because turning a part genuinely moves its pads. That
is the trade -- ~22% fewer wires to hand-solder for ~5% more copper -- and on a
single-sided board it is the right way round.

**Legality without moving anything.** A quarter turn swaps ``eff_w`` / ``eff_h``,
so it can push a part into a neighbour or out of the outline. The annealer
handles that by re-clamping the centre; this pass must not, because a clamp is a
*placement* change and would break the property that makes the pass safe to run
after ``legalize``. So an illegal rotation is refused, not repaired. The test is
local (O(n) per candidate) and phrased as "never worse": a pair must end up at
least ``margin`` apart, unless it already was closer than that, in which case it
merely may not get closer. A legalize residual therefore does not lock the pass
out, and no new overlap can appear. A 180 flip never changes the box, so it
always passes both tests -- the free move stays free.

**Cost.** Naively every candidate rebuilds every net tree and re-tests every
segment pair: O(S^2) per candidate, 3N candidates per sweep. Rotating one part
only changes the nets that part sits on, and a net's tree always has (pins - 1)
edges whatever the rotation -- so the segment list can be patched in place at
fixed indices and only the touched rows of the crossing graph recomputed
(``_Trees``). That is the difference between a pass that runs after every
placement and one that does not: 22 ms to 2.1 s per placement (median 0.21 s)
against 4-42 s of annealing. Profiled, 95% of what is left is ``min_bridges``
itself, not the bookkeeping -- and ``BRIDGE_LIMIT`` bounds that.

Deterministic by construction: parts are visited in sorted-ref order, rotations
are tried in a fixed order, ties keep the incumbent, and no float is accumulated
over a set. Pure Python -- no pcbnew, no numpy.
"""
from __future__ import annotations

import time

from . import globalroute
from .globalroute import Segment
from .model import Board

# The four orientations the model allows, in the order candidates are tried.
# Fixed, so a tie between two equally good rotations always resolves the same
# way; ``0`` first means an incumbent-equal candidate never displaces it.
ROTATIONS = (0, 90, 180, 270)

# Sweep cap. Every accepted rotation strictly decreases a lexicographic key that
# is bounded below, so the pass terminates on its own; this is only insurance
# against a pathological board. Measured on the DTU corpus (tools/eval_orient.py,
# 36 placements): every one reached its local optimum in 2-4 sweeps, counting the
# final no-change sweep that proves it, so 8 is well clear of what real boards use.
MAX_SWEEPS = 8

# Above this many crossings the exact bridge count stops being affordable, and
# stops being exact anyway. ``min_bridges`` is a branch-and-bound minimum vertex
# cover; measured on random conflict graphs of the shape a tangled placement
# produces, one call costs 0.03 ms at 21 crossings, 0.5 ms at 93, 4 ms at 183,
# then 142 ms at 376 -- past ~200 it saturates its own ``_BB_BUDGET`` and returns
# the greedy cover. A placement that tangled needs re-placing, not re-orienting,
# so beyond the limit the pass ranks on the crossing count alone. The decision is
# taken ONCE from the starting placement, never per candidate, so the objective
# cannot change mid-run and the "every accepted move strictly decreases the key"
# termination argument survives. Real placements land at 5-30 crossings
# (tools/eval_orient.py), so on the corpus this never fires.
BRIDGE_LIMIT = 200

# Tolerance (mm) on the wirelength tie-break. Below this a "shorter" tree is
# float noise from recomputing the same MST, and acting on it would let the pass
# rotate parts back and forth forever instead of converging.
_WL_EPS = 1e-6

# Tolerance (mm) on the clearance comparisons, for the same reason: a rotation
# that reproduces a gap to the last ulp must not read as having shrunk it.
_GEO_EPS = 1e-9


# --------------------------------------------------------------------------
# incremental net trees + crossing graph
# --------------------------------------------------------------------------

class _Trees:
    """``globalroute``'s net trees and crossing graph, maintained under rotation.

    Holds exactly what ``net_segments`` / ``conflicts`` would return for the
    board's current state -- the test suite asserts that equality after random
    rotations, because an incremental structure that has drifted from the
    reference would make the whole pass optimise a number nobody else computes.
    """

    def __init__(self, board: Board, planes: set[str] | None = None):
        skip = globalroute.plane_nets(board) if planes is None else set(planes)
        members = board.nets()
        self.board = board
        self._members: dict[str, list[tuple[str, int]]] = {}
        self._slice: dict[str, tuple[int, int]] = {}
        self.segments: list[Segment] = []
        self.comp_nets: dict[str, list[str]] = {}
        # Sorted-net order, single-pad nets dropped: byte-identical to what
        # net_segments produces, so the segment indices mean the same thing.
        for net in sorted(members):
            if net in skip:
                continue
            mem = sorted(members[net])
            if len(mem) < 2:
                continue
            self._members[net] = mem
            start = len(self.segments)
            self.segments.extend(self._build(net))
            self._slice[net] = (start, len(self.segments))
            for ref in sorted({r for r, _ in mem}):
                self.comp_nets.setdefault(ref, []).append(net)
        self.pairs: set[tuple[int, int]] = set()
        self._refresh(range(len(self.segments)))

    def _build(self, net: str) -> list[Segment]:
        pts = []
        for ref, pi in self._members[net]:
            c = self.board.components[ref]
            pts.append(c.pad_world(c.pads[pi]))
        # Same MST routine as net_segments, deliberately: a second spanning-tree
        # implementation would drift and the pass would optimise a tree the
        # router estimate never sees.
        return [Segment(net, pts[i][0], pts[i][1], pts[j][0], pts[j][1])
                for i, j in globalroute._mst_pairs(pts)]

    def rotate(self, ref: str, rot: int) -> None:
        """Set one component's rotation and bring the trees/graph back in sync."""
        c = self.board.components[ref]
        if c.rot == rot:
            return
        c.rot = rot
        dirty: list[int] = []
        for net in self.comp_nets.get(ref, ()):
            s0, s1 = self._slice[net]
            # A net's tree has (pins - 1) edges regardless of where the pins
            # are, so the slice length is invariant and the indices -- which the
            # crossing graph keys on -- stay valid.
            self.segments[s0:s1] = self._build(net)
            dirty.extend(range(s0, s1))
        self._refresh(dirty)

    def _conflict(self, i: int, j: int) -> bool:
        """Mirrors ``globalroute.conflicts``: same net and shared endpoints are
        not conflicts, everything else is a strict proper crossing."""
        s, t = self.segments[i], self.segments[j]
        if s.net == t.net:
            return False
        ends_s = ((s.ax, s.ay), (s.bx, s.by))
        if (t.ax, t.ay) in ends_s or (t.bx, t.by) in ends_s:
            return False
        return globalroute._crosses(s, t)

    def _refresh(self, dirty) -> None:
        """Recompute only the crossing-graph rows touching moved segments."""
        d = set(dirty)
        if not d:
            return
        self.pairs = {p for p in self.pairs if p[0] not in d and p[1] not in d}
        n = len(self.segments)
        for i in sorted(d):
            for j in range(n):
                if j == i or (j in d and j < i):
                    continue          # dirty/dirty pairs are tested at the lower index
                if self._conflict(i, j):
                    self.pairs.add((i, j) if i < j else (j, i))

    def bridges(self) -> int:
        return globalroute.min_bridges(len(self.segments), sorted(self.pairs))

    def wirelength(self) -> float:
        # Summed in index order, not set order: float addition is not
        # associative, and a hash-order sum would make the tie-break -- and so
        # the final layout -- differ between processes.
        return sum(s.length for s in self.segments)

    def key(self, wirelength_tiebreak: bool,
            exact_bridges: bool = True) -> tuple[int, int, float]:
        return (self.bridges() if exact_bridges else 0, len(self.pairs),
                self.wirelength() if wirelength_tiebreak else 0.0)

    def stats(self) -> dict:
        return {"bridges": self.bridges(), "conflicts": len(self.pairs),
                "wirelength": round(self.wirelength(), 3)}


# --------------------------------------------------------------------------
# legality of a rotation, without moving anything
# --------------------------------------------------------------------------

def _pair_slack(a, b) -> float:
    """Clear gap (mm) between two courtyards; negative when they overlap.

    ``max`` of the two axis gaps is exactly ``metrics.overlaps``' test: boxes
    intersect precisely when both axis gaps are negative.
    """
    return max(abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2,
               abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2)


def _edge_slack(c, board: Board, margin: float) -> float:
    """How deep the courtyard sits inside the legal inset; negative when outside.

    Same inset ``geom.clamp_center`` enforces (``margin + edge_keepout``), so a
    rotation this accepts is one the placement pipeline would also accept.
    """
    inset = margin + board.edge_keepout
    return min(c.left - (board.x0 + inset), (board.x1 - inset) - c.right,
               c.top - (board.y0 + inset), (board.y1 - inset) - c.bottom)


def _legal(c, others, board: Board, margin: float,
           base_pair: dict[str, float], base_edge: float) -> bool:
    """Is ``c``'s current rotation no worse, geometrically, than the one it had?

    "No worse" rather than "clear": a board that comes out of ``legalize`` with a
    residual pinch would otherwise freeze every part near it, and this pass is
    supposed to be free. A pair that had room must keep ``margin``; a pair that
    was already tight may not get tighter. Either way an overlap that did not
    exist before cannot appear.
    """
    if _edge_slack(c, board, margin) < min(0.0, base_edge) - _GEO_EPS:
        return False
    for o in others:
        if _pair_slack(c, o) < min(margin, base_pair[o.ref]) - _GEO_EPS:
            return False
    return True


def _better(cand: tuple[int, int, float], best: tuple[int, int, float]) -> bool:
    """Strict lexicographic improvement, with a tolerance on the float key."""
    if cand[0] != best[0]:
        return cand[0] < best[0]
    if cand[1] != best[1]:
        return cand[1] < best[1]
    return cand[2] < best[2] - _WL_EPS


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------

def optimise(board: Board, *, margin: float = 0.8, max_sweeps: int = MAX_SWEEPS,
             planes: set[str] | None = None,
             wirelength_tiebreak: bool = True) -> dict:
    """Sweep every free part's orientation to a local optimum for crossings.

    Mutates ``board`` in place (only ``Component.rot`` -- never ``x``/``y``) and
    returns a report: before/after bridges, crossings and tree length, the parts
    that turned, and the cost of getting there.

    Locked parts are untouched, and so are edge-pinned ones: ``anneal`` excludes
    them from its rotate move because a connector faces its edge, and a pass that
    spins one to save a crossing produces a board whose connector points inwards.
    """
    t0 = time.perf_counter()
    trees = _Trees(board, planes)
    before = trees.stats()
    exact = len(trees.pairs) <= BRIDGE_LIMIT

    parts = sorted(board.components.values(), key=lambda c: c.ref)
    movable = [c for c in parts if not c.locked and not c.edge]
    start_rot = {c.ref: c.rot for c in movable}

    sweeps = 0
    evaluated = 0
    while movable and sweeps < max_sweeps:
        sweeps += 1
        changed = False
        for c in movable:
            others = [o for o in parts if o is not c]
            # Baselines are re-taken per part per sweep: a neighbour may have
            # turned since, which moves the gap this rotation is judged against.
            base_edge = _edge_slack(c, board, margin)
            base_pair = {o.ref: _pair_slack(c, o) for o in others}
            cur = c.rot
            best_rot, best_key = cur, trees.key(wirelength_tiebreak, exact)
            for r in ROTATIONS:
                if r == cur:
                    continue
                trees.rotate(c.ref, r)
                evaluated += 1
                if _legal(c, others, board, margin, base_pair, base_edge):
                    k = trees.key(wirelength_tiebreak, exact)
                    if _better(k, best_key):
                        best_key, best_rot = k, r
            trees.rotate(c.ref, best_rot)
            if best_rot != cur:
                changed = True
        if not changed:
            break                    # local optimum: no single rotation helps

    rotated = [(ref, start_rot[ref], board.components[ref].rot)
               for ref in sorted(start_rot)
               if board.components[ref].rot != start_rot[ref]]
    return {
        "before": before,
        "after": trees.stats(),
        "rotated": rotated,
        "sweeps": sweeps,
        "evaluated": evaluated,
        "exact_bridges": exact,
        "seconds": round(time.perf_counter() - t0, 4),
    }
