"""Pick each net's tree to dodge other nets, not to be short.

``globalroute`` reduces every net to a minimum spanning tree over its pads. That
is one arbitrary choice out of many: a net with k pads has k^(k-2) labelled
spanning trees (Cayley) and the router may realise ANY of them -- the copper
only has to connect the pads, it does not have to take the shortest path
between them. The MST minimises length. Nothing about it minimises crossings,
and crossings are what cost money here: on a single-sided board each one that
survives becomes a hand-soldered wire bridge.

A person re-trees by reflex ("run it round the other way") and the placer has
never been allowed to. This module does it explicitly, and the structure of the
problem makes it exact rather than heuristic in the part that matters:

**Per net, the optimum is an MST under a different weight.** Hold every other
net fixed. Segments of one net never conflict with each other (same copper), so
a tree's crossing count is a plain SUM over its edges of that edge's crossings
against the fixed segments -- no interaction between the choices. An additive
edge cost over spanning trees is exactly what a minimum spanning tree
minimises, so re-treeing one net optimally is one Prim run under
``cross_mm * crossings(e) + length(e)``. No enumeration of trees is needed, at
any pin count.

**Across nets it is coordinate descent.** Re-tree the net involved in most
crossings, then the next, repeating while anything improves. Each step lowers
the global cost by construction, so it terminates, and it lands in a local
optimum of the joint problem (the joint problem is NP-hard -- it contains
minimum-crossing-number drawings -- so a local optimum is the honest target).

**It cannot come out worse than the baseline.** Starting from the MST, total
length is already at its minimum, so any accepted move paid for its extra
copper with a crossing: total crossings are non-increasing versus
``globalroute.net_segments`` and total length is non-decreasing, both provably
and both checked in the test suite. That is what makes it safe to swap in.

Same segment model, same ``Segment`` type and same skip rules as
``globalroute``, so the output is a drop-in replacement anywhere
``net_segments`` is consumed -- ``conflicts``, ``min_bridges`` and
``congestion`` all work on it unchanged.

Pure Python, no ``pcbnew``, deterministic (every choice breaks ties on sorted
order -- byte-identical output across processes and across KiCad's Python and
3.13, which the eval harness checks).

**Cost.** 1-13 ms on the 17-58 part boards, 53 ms on the 131-part system board:
roughly quadratic in segment count, because every candidate edge is tested
against every other net's segments. That is a candidate-scoring tier, not an
inner-loop tier -- ``globalroute``'s MST costs 0.2-0.4 ms on the same boards and
remains the right thing to call 10^5 times per anneal.

**What it is worth** (``tools/eval_nettopo.py``, 14 boards, 68 placements): 31%
fewer predicted crossings, 19% fewer predicted bridges, 4.6% more copper, and
not one placement where either count came out worse. The effect shrinks with
board size -- 41% on the ten small boards, 33% on the 58-part motor_power, 20%
on the 131-part system board -- because a crowded board offers fewer free ways
round.

But a LOWER prediction is not automatically a BETTER one, and that distinction
decides where this belongs. Scored against real FreeRouting runs
(``eval_nettopo.py --truth``, 18 routed placements over two boards) the re-treed
estimate is much closer to the bridges actually needed: mean absolute error
3.5 -> 2.3 on mppt_buck and 5.1 -> 3.4 on buck_v2, and the crossing count's
over-prediction roughly halves (6.7 -> 3.3, 10.4 -> 5.8). It also RANKS
placements at least as well on both boards (Spearman 0.33 -> 0.36 and
0.21 -> 0.65), but 18 routed samples cannot call that significant (bootstrap
P(better) 0.48 and 0.84) and both predictors pick the same placement anyway
(identical top-1 regret). So: a better number for certain, a better ranking on
the evidence but not yet a proven one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import globalroute
from .globalroute import Segment
from .model import Board

# What one crossing is worth in millimetres of extra track: the exchange rate a
# detour is judged against, and the whole tuning surface of the module. Swept
# over 6 boards x 6 seeds with tools/eval_nettopo.py -- mean crossings / bridges
# / wirelength against the MST baseline:
#     2 mm  -13.2%  -6.4%  +0.4%
#     5 mm  -21.7% -10.4%  +1.2%
#    10 mm  -26.8% -14.9%  +2.5%
#    25 mm  -29.6% -16.9%  +4.2%
#    50 mm  -30.0% -17.4%  +4.9%
#   100 mm  -30.4% -17.4%  +5.2%   (saturated: 400 mm is identical)
# 25 mm is the knee -- 97% of the reachable bridge reduction for 80% of the
# copper the unbounded setting spends. It is also roughly the physical truth on
# this process: a wire bridge is a hand-soldered link worth far more than 25 mm
# of milled track, but a net dragged clear across the board to dodge one
# crossing just collides with something else, and the sweep says the extra
# licence buys nothing.
CROSS_MM = 25.0

# Coordinate-descent passes. Convergence is detected -- the loop stops as soon
# as no net improves -- so this only caps a pathological board. All 36 measured
# placements converged in 1 to 4 passes, and raising the cap to 30 changed no
# result on any of them.
MAX_PASSES = 6

# Above this pin count the candidate edge set is thinned from all k(k-1)/2 pairs
# to each pad's nearest neighbours, because the full set is also k(k-1)/2
# crossing tests against every other net on the board. Union with the MST edges
# keeps the candidate graph connected -- and keeps the baseline answer inside the
# search, which is what makes even the thinned result no worse than the MST.
# 12 is the widest signal net in the 6-board corpus (motor_power's +5v and
# +15V2, once the pour is excluded), so every board measured here takes the
# exact all-pairs path and the thinning only guards boards bigger than the
# corpus. Sparse enough to matter, since a 30-pad rail is 435 pairs.
SPARSE_ABOVE = 12
NEAREST_K = 6

# Improvement threshold (mm). One KiCad internal unit is 1 nm = 1e-6 mm, so a
# gain below this cannot exist on the real board and accepting it would only
# risk the descent oscillating on float noise.
_EPS = 1e-6


@dataclass(frozen=True)
class Retree:
    """What re-treeing bought on one placement, against the MST it replaced."""
    segments: tuple[Segment, ...]
    passes: int              # coordinate-descent passes actually run
    changed: tuple[str, ...]  # nets whose tree differs from their MST
    conflicts_mst: int
    conflicts: int
    bridges_mst: int         # -1 when retree(bridges=False) skipped the solve
    bridges: int             # -1 likewise: 0 would read as "none needed"
    wirelength_mst: float
    wirelength: float


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _proper_cross(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    """Do AB and CD share any point?

    Identical predicate to ``globalroute._crosses``, on raw coordinates instead
    of ``Segment`` objects: the inner loop evaluates tens of thousands of
    candidate edges per call and cannot afford to build a frozen dataclass for
    each one. ``test_crossing_predicate_agrees_with_globalroute`` pins the two
    together, because a module that optimised a slightly different crossing rule
    from the one it is scored by would be worse than useless.

    The collinear branches are not pedantry. A zero determinant means an
    endpoint lies exactly on the other segment -- one net's copper running
    through another net's pad, which is a short. Parts sit on a 2.54 mm grid and
    get aligned, so it happens routinely; and testing only for a *proper*
    crossing makes the answer depend on which way round the pair is written,
    which was measured at 4.6% of grid-aligned pairs.
    """
    d1 = (ay - cy) * (dx - cx) - (dy - cy) * (ax - cx)
    d2 = (by - cy) * (dx - cx) - (dy - cy) * (bx - cx)
    d3 = (cy - ay) * (bx - ax) - (by - ay) * (cx - ax)
    d4 = (dy - ay) * (bx - ax) - (by - ay) * (dx - ax)
    if (((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0
            and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0):
        return True
    if d1 == 0 and min(cx, dx) <= ax <= max(cx, dx) and min(cy, dy) <= ay <= max(cy, dy):
        return True
    if d2 == 0 and min(cx, dx) <= bx <= max(cx, dx) and min(cy, dy) <= by <= max(cy, dy):
        return True
    if d3 == 0 and min(ax, bx) <= cx <= max(ax, bx) and min(ay, by) <= cy <= max(ay, by):
        return True
    if d4 == 0 and min(ax, bx) <= dx <= max(ax, bx) and min(ay, by) <= dy <= max(ay, by):
        return True
    return False


def _pad(ax, ay, bx, by):
    """A segment plus its bounding box, the form the inner loop wants.

    Four float comparisons reject the overwhelming majority of obstacle pairs on
    a real board; the crossing predicate costs eight multiplies, so pre-storing
    the box is the difference between this module fitting in the placer's budget
    and not.
    """
    return (ax, ay, bx, by,
            ax if ax < bx else bx, ax if ax > bx else bx,
            ay if ay < by else by, ay if ay > by else by)


def _cross_count(ax, ay, bx, by, obstacles) -> int:
    """How many of ``obstacles`` this candidate edge properly crosses.

    Obstacles sharing an endpoint are skipped, exactly as
    ``globalroute.conflicts`` does: two nets meeting at a pad location touch,
    they do not cross.
    """
    xlo = ax if ax < bx else bx
    xhi = ax if ax > bx else bx
    ylo = ay if ay < by else by
    yhi = ay if ay > by else by
    n = 0
    for cx, cy, dx, dy, oxlo, oxhi, oylo, oyhi in obstacles:
        if oxlo > xhi or oxhi < xlo or oylo > yhi or oyhi < ylo:
            continue
        if (cx == ax and cy == ay) or (cx == bx and cy == by) or \
           (dx == ax and dy == ay) or (dx == bx and dy == by):
            continue
        if _proper_cross(ax, ay, bx, by, cx, cy, dx, dy):
            n += 1
    return n


# --------------------------------------------------------------------------
# one net, everything else held fixed
# --------------------------------------------------------------------------

def _candidate_pairs(pts) -> list[tuple[int, int]]:
    """Edges the tree may be built from, sorted.

    All pairs on a normal net. On a wide net (a power rail with dozens of pads)
    only each pad's ``NEAREST_K`` neighbours, unioned with the MST edges so the
    graph stays connected -- and so the search still contains the baseline
    answer, which is what guarantees it can never do worse.
    """
    n = len(pts)
    if n <= SPARSE_ABOVE:
        return [(i, j) for i in range(n) for j in range(i + 1, n)]
    keep = set()
    for i in range(n):
        xi, yi = pts[i]
        near = sorted(((xi - pts[j][0]) ** 2 + (yi - pts[j][1]) ** 2, j)
                      for j in range(n) if j != i)
        for _d, j in near[:NEAREST_K]:
            keep.add((i, j) if i < j else (j, i))
    for i, j in globalroute._mst_pairs(pts):
        keep.add((i, j) if i < j else (j, i))
    return sorted(keep)


def _weight(pts, i, j, obstacles, cross_mm) -> float:
    """Cost of connecting pads ``i`` and ``j``: detour length plus crossings.

    The endpoints are ordered by index first, and that is not cosmetic. The
    cross products that decide a crossing are not bit-identical when the two
    ends are swapped, so on the degenerate geometry a real board is full of --
    a pad sitting exactly on another net's segment, which is what a part grid
    produces -- ``w(i, j)`` and ``w(j, i)`` could differ by a whole crossing.
    The descent then compared a tree against itself, found an 'improvement',
    and never converged (seen on mppt_buck: one net re-treed to the tree it
    already had on every pass).
    """
    if j < i:
        i, j = j, i
    ax, ay = pts[i]
    bx, by = pts[j]
    w = math.hypot(bx - ax, by - ay)
    if cross_mm:
        w += cross_mm * _cross_count(ax, ay, bx, by, obstacles)
    return w


def _prim(n: int, adj: list[dict[int, float]]) -> tuple[list[tuple[int, int]], float]:
    """Prim over an explicit adjacency -> (edges, total weight).

    Deliberately the same shape as ``globalroute._mst_pairs``: start at index 0,
    scan candidates in ascending index and take strictly smaller, so ties break
    on the lowest index and a zero-crossing board reproduces the MST edge for
    edge. Without that the module would churn the baseline for nothing.
    """
    used = [False] * n
    used[0] = True
    best_d = [float("inf")] * n
    best_p = [-1] * n
    for j, w in adj[0].items():
        best_d[j], best_p[j] = w, 0
    out: list[tuple[int, int]] = []
    total = 0.0
    for _ in range(n - 1):
        k, kd = -1, float("inf")
        for j in range(n):
            if not used[j] and best_d[j] < kd:
                kd, k = best_d[j], j
        if k < 0:
            break                      # candidate graph disconnected: cannot happen
        used[k] = True
        out.append((best_p[k], k))
        total += kd
        for j, w in adj[k].items():
            if not used[j] and w < best_d[j]:
                best_d[j], best_p[j] = w, k
    return out, total


def _solve_net(pts, obstacles, cross_mm) -> tuple[list[tuple[int, int]], float]:
    n = len(pts)
    if n < 2:
        return [], 0.0
    if n == 2:
        return [(0, 1)], _weight(pts, 0, 1, obstacles, cross_mm)
    adj: list[dict[int, float]] = [{} for _ in range(n)]
    for i, j in _candidate_pairs(pts):
        w = _weight(pts, i, j, obstacles, cross_mm)
        adj[i][j] = w
        adj[j][i] = w
    return _prim(n, adj)


def min_cross_tree(pts, obstacles, *, cross_mm: float = CROSS_MM) -> list[tuple[int, int]]:
    """Spanning tree of ``pts`` minimising ``cross_mm * crossings + length``.

    ``obstacles`` are foreign segments as ``(ax, ay, bx, by)``. Exact for this
    net given those obstacles -- the cost is additive over tree edges, so the
    minimum spanning tree under it is the minimum-cost tree, full stop.
    """
    obs = [_pad(*o) for o in obstacles]
    return _solve_net(list(pts), obs, cross_mm)[0]


# --------------------------------------------------------------------------
# the whole board
# --------------------------------------------------------------------------

def net_points(board: Board, planes: set[str] | None = None):
    """(net, pad points) for every net that needs copper, in sorted order.

    Same skip rules as ``globalroute.net_segments`` -- poured nets and nets with
    fewer than two pads -- so the two modules always describe the same routing
    problem and their numbers can be compared directly.
    """
    skip = globalroute.plane_nets(board) if planes is None else set(planes)
    members = board.nets()
    out = []
    for net in sorted(members):
        if net in skip:
            continue
        pts = []
        for ref, pi in sorted(members[net]):
            c = board.components[ref]
            pts.append(c.pad_world(c.pads[pi]))
        if len(pts) >= 2:
            out.append((net, pts))
    return out


def _involvement(order: list[str], segs: dict[str, list]) -> dict[str, int]:
    """Crossings each net is currently involved in.

    Two uses, and only the second one earns its keep. It orders the rip-up
    worst-net-first, which is the textbook move but measured worth nothing here
    (540 crossings against 542 for plain alphabetical order over 36 real
    placements). What it does buy is the right to SKIP every net that crosses
    nothing, and that pays for the sweep several times over: 7.5 ms worst case
    with it, 9.6 ms without.

    Uses the padded obstacle form -- bounding box first, predicate second --
    which is why this is here rather than a call to ``globalroute.conflicts``.
    """
    hits = {n: 0 for n in order}
    for a in range(len(order)):
        na = order[a]
        for s in segs[na]:
            ax, ay, bx, by, xlo, xhi, ylo, yhi = s
            for b in range(a + 1, len(order)):
                nb = order[b]
                for cx, cy, dx, dy, oxlo, oxhi, oylo, oyhi in segs[nb]:
                    if oxlo > xhi or oxhi < xlo or oylo > yhi or oyhi < ylo:
                        continue
                    if (cx == ax and cy == ay) or (cx == bx and cy == by) or \
                       (dx == ax and dy == ay) or (dx == bx and dy == by):
                        continue
                    if _proper_cross(ax, ay, bx, by, cx, cy, dx, dy):
                        hits[na] += 1
                        hits[nb] += 1
    return hits


def _canon(tree) -> tuple[tuple[int, int], ...]:
    """A tree as a comparable edge SET: Prim orients edges the way it grew them,
    so the same tree can come back written differently."""
    return tuple(sorted((i, j) if i < j else (j, i) for i, j in tree))


def _trees(board: Board, planes, cross_mm: float, max_passes: int):
    """Coordinate descent from the MST. Returns (points, trees, passes, changed)."""
    pointsets = net_points(board, planes)
    order = [net for net, _ in pointsets]
    pts_of = {net: pts for net, pts in pointsets}
    # Baseline: exactly the trees globalroute would have used, so "changed" and
    # every before/after number below are measured against the module in use.
    trees = {net: globalroute._mst_pairs(pts_of[net]) for net in order}
    segs = {net: [_pad(pts_of[net][i][0], pts_of[net][i][1],
                       pts_of[net][j][0], pts_of[net][j][1])
                  for i, j in trees[net]] for net in order}
    baseline = dict(trees)

    # Only nets with 3+ pads have a choice at all: a 2-pad net's spanning tree is
    # its single edge, so re-treeing it is a no-op by definition. Between 9% and
    # 50% of the nets on the corpus boards drop out of the loop this way (subxo:
    # 13 of 26; current_sense: 1 of 11).
    movable = [net for net in order if len(pts_of[net]) > 2]
    passes = 0
    if movable and cross_mm > 0.0:
        for _ in range(max_passes):
            passes += 1
            hits = _involvement(order, segs)
            improved = False
            for net in sorted(movable, key=lambda n: (-hits[n], n)):
                if not hits[net]:
                    # Nothing to dodge. All a re-solve could win back is length
                    # this net gave up dodging an obstacle that has since moved,
                    # and over 36 real placements dropping this guard changed no
                    # crossing, no bridge and not one millimetre of copper.
                    continue
                pts = pts_of[net]
                obstacles = [s for other in order if other != net
                             for s in segs[other]]
                cur = sum(_weight(pts, i, j, obstacles, cross_mm)
                          for i, j in trees[net])
                tree, cost = _solve_net(pts, obstacles, cross_mm)
                # Both conditions, not just the cost: a move that leaves the
                # tree where it was cannot be an improvement whatever the
                # arithmetic says, and refusing it is what makes the descent
                # provably terminate rather than merely usually terminate.
                if cost < cur - _EPS and _canon(tree) != _canon(trees[net]):
                    trees[net] = tree
                    segs[net] = [_pad(pts[i][0], pts[i][1], pts[j][0], pts[j][1])
                                 for i, j in tree]
                    improved = True
            if not improved:
                break

    changed = tuple(net for net in order
                    if _canon(trees[net]) != _canon(baseline[net]))
    return pts_of, order, trees, passes, changed


def _to_segments(order, pts_of, trees) -> list[Segment]:
    out = []
    for net in order:
        pts = pts_of[net]
        for i, j in trees[net]:
            out.append(Segment(net, pts[i][0], pts[i][1], pts[j][0], pts[j][1]))
    return out


def net_segments(board: Board, planes: set[str] | None = None, *,
                 cross_mm: float = CROSS_MM,
                 max_passes: int = MAX_PASSES) -> list[Segment]:
    """Crossing-minimal spanning-tree edges for every net that needs copper.

    Drop-in replacement for ``globalroute.net_segments``: same type, same skip
    rules, same sorted-by-net order. ``cross_mm=0`` returns the MST itself.
    """
    pts_of, order, trees, _passes, _changed = _trees(board, planes, cross_mm,
                                                     max_passes)
    return _to_segments(order, pts_of, trees)


def retree(board: Board, planes: set[str] | None = None, *,
           cross_mm: float = CROSS_MM, max_passes: int = MAX_PASSES,
           bridges: bool = True) -> Retree:
    """Re-tree the board and report what it bought against the MST baseline.

    Both halves are measured with the SAME ``globalroute`` code that scores a
    placement today, so the deltas are directly comparable with everything else
    the tool reports. ``bridges=False`` skips the two vertex-cover solves when
    only crossings are wanted.
    """
    pts_of, order, trees, passes, changed = _trees(board, planes, cross_mm,
                                                   max_passes)
    chosen = _to_segments(order, pts_of, trees)
    base = _to_segments(order, pts_of,
                        {net: globalroute._mst_pairs(pts_of[net]) for net in order})
    c_base = globalroute.conflicts(base)
    c_new = globalroute.conflicts(chosen)
    return Retree(
        segments=tuple(chosen),
        passes=passes,
        changed=changed,
        conflicts_mst=len(c_base),
        conflicts=len(c_new),
        bridges_mst=globalroute.min_bridges(len(base), c_base) if bridges else -1,
        bridges=globalroute.min_bridges(len(chosen), c_new) if bridges else -1,
        wirelength_mst=round(sum(s.length for s in base), 3),
        wirelength=round(sum(s.length for s in chosen), 3),
    )
