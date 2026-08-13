"""Which pad-to-pad gaps no track can pass through.

On the single-sided through-hole boards this engine targets, the copper is on the
bottom and the parts are on top. A track may therefore run *under* a component
body -- the body is not on the copper layer at all -- but it can never cross a
pad, because a THT land is a hole in the copper with a clearance ring around it.
The pads, not the courtyards, are the real obstacles, and that is why a placement
which looks roomy measured bounding-box to bounding-box can still be unroutable.

The rule is a single number. Two pads let a track between them only if the clear
copper separating their lands is at least ``track + 2 * clearance`` -- the track
itself plus one clearance to the land on either side. On the CNC profile
(1.0 mm track, 0.85 mm clearance) that is 2.7 mm. A DIP at 2.54 mm pitch with
~2 mm lands leaves ~0.5 mm, so *nothing* can be routed between two adjacent DIP
pins: the pin row is one solid wall the router has to go around, and a placement
that assumes otherwise fails at the routing stage, several minutes later, with no
explanation of why.

Two views on the same geometry, and they deliberately disagree:

* :func:`component_blocked_pairs` / :func:`component_barrier` look at one
  footprint alone. That makes "this part is a wall" a property of the part, so it
  means the same thing wherever the annealer has currently put it.
* :func:`board_blocked_gaps` looks at the assembled design, where a neighbouring
  part's pad is just as much of an obstruction as one of your own. It is the view
  that catches the gaps the *placer* creates, which are also the only ones the
  placer can open again by moving something.

Net membership is irrelevant throughout. Two pads on the same net are joined by
copper, but that copper does not help a *different* net get past them, and it is
the other net that is trying to squeeze through.

Pure Python, deterministic, no ``pcbnew``: this is meant to be affordable inside
the placement loop, so the whole-board scan is bucketed rather than quadratic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .metrics import channel_width
from .model import Board, Component

# Nominal through-hole pad diameter (mm). ``model.Pad`` carries a position and a
# net but no land size -- ``kicad_io`` captures what the placer is allowed to
# move, not what the footprint editor drew -- so every land is approximated as a
# round hole of this diameter. 2.0 mm is a typical KiCad THT land over a
# 0.8-1.0 mm drill: a DIP pin is ~1.8 mm, a 5 mm-pitch terminal block ~2.4 mm. It
# is deliberately the same figure ``globalroute.PAD_MM`` derates cell capacity
# with, so the two analyses agree on how much copper a pad eats.
#
# The approximation is the module's one real weakness and it is worth being
# precise about. Every reported gap is wrong by however much the true land
# differs from this nominal one -- on the common THT footprints that is bounded
# by about +-0.4 mm, and it is the *same* error for every candidate placement, so
# it shifts the pass/fail line slightly but never re-ranks two placements against
# each other. What it means in practice: this module localises and ranks tight
# gaps, it is not a DRC, and a gap within ~0.5 mm of the channel width should be
# treated as "check it" rather than "it fits". Every entry point takes ``pad_mm``
# as a parameter, so a caller that does know the real land diameter should pass
# it and get an exact answer.
PAD_MM = 2.0

# Pads a run of blocked gaps has to chain together before the footprint counts as
# a wall rather than a pinch. Two pads (one blocked gap) is a pinch: there is a
# pad at each end and the router steps around either of them for almost nothing.
# Three pads is a continuous barrier two pitches long, which is the point where
# going around stops being free -- and it is exactly the shape a DIP pin row or a
# pin header presents. Deliberately a pad count rather than a millimetre
# threshold, so it means the same thing on a 2.54 mm DIP and a 5.08 mm terminal
# block; ``Barrier.span`` is there for callers that want to rank walls by how far
# the detour actually is.
WALL_MIN_PADS = 3

# Tolerance (mm) for the two boundary cases, both of which are exact geometric
# ties rather than measurement noise: a gap that fits the channel to the micron
# still fits, and a pad sitting exactly *on* the adjacency circle (the diagonal
# of any rectangular pad grid puts one there, see ``_blocking``) still counts as
# standing in the way. 1e-9 mm is a picometre: far below any real board
# dimension, far above float64 round-off on board-sized coordinates (~1e-13 mm).
_EPS_MM = 1e-9


# --------------------------------------------------------------------------
# the geometry: one gap against one channel width
# --------------------------------------------------------------------------

def clear_gap(ax: float, ay: float, bx: float, by: float,
              pad_mm: float = PAD_MM) -> float:
    """Clear copper (mm) between two pad lands, given their centres.

    Centre distance minus one pad diameter -- half a land is consumed at each
    end. Negative when the lands overlap; reported honestly instead of clamped
    at zero so a caller can distinguish "no room for a track" from "these two
    pads are touching", which is a different problem with a different fix.
    """
    return math.hypot(bx - ax, by - ay) - pad_mm


def track_fits(gap: float, *, track: float = 1.0, clearance: float = 0.85) -> bool:
    """Can a track pass through a clear gap this wide?

    The channel is ``track + 2 * clearance``: the track plus one clearance to the
    land on each side. Shares :func:`metrics.channel_width` with the placement
    term and the pinch metric so all three agree on what a channel is. A gap that
    fits to within ``_EPS_MM`` counts as fitting -- exactly-fits is a tie the
    geometry produces on purpose (a footprint drawn to the process minimum), not
    a rounding artefact.
    """
    return gap >= channel_width(clearance, track) - _EPS_MM


# --------------------------------------------------------------------------
# the core: adjacent pad pairs that are too tight to route between
# --------------------------------------------------------------------------

def _blocking(pts: list[tuple[float, float]], *, pad_mm: float, channel: float,
              near: list[list[int]] | None = None) -> list[tuple[int, int, float]]:
    """``(i, j, gap)`` for every pad pair that is adjacent *and* too tight.

    Two filters, cheapest first.

    **Too tight.** A pair whose centres are more than ``pad_mm + channel`` apart
    always fits a track, so it is rejected on a squared distance with no square
    root and never reaches the adjacency test. On the CNC profile that cutoff is
    4.7 mm -- under two DIP pitches -- so nearly every pair on a real board dies
    on this line, which is what makes the whole analysis affordable in a loop.

    **Adjacent.** The gap between two pads is only *their* gap if no third pad is
    standing in it; the gap from a DIP's pin 1 to its pin 3 is not a channel, it
    is pin 2. The test used is the Gabriel condition -- no other pad centre
    inside the circle that has AB as its diameter -- chosen because that circle
    *is* the region a track squeezing between A and B has to cross, so the test
    asks the routing question directly rather than approximating it with a
    k-nearest-neighbour rule that would need an arbitrary k. It is also stable:
    the Gabriel graph is a subgraph of the Delaunay triangulation, so a footprint
    contributes O(n) gaps, not O(n^2), and adding a distant pad cannot change
    which nearby pads are adjacent.

    A pad exactly *on* the circle counts as inside (``_EPS_MM``). That is the
    case that decides a rectangular pad grid: for the diagonal of any such grid
    the two orthogonal pads land exactly on the circle, and treating them as
    obstructions keeps the four sides and drops the diagonals -- which is right,
    since crossing the diagonal means crossing a side first, and reporting both
    would count one obstruction twice.

    Adjacency is judged on pad *centres* while the gap width uses the pad radius.
    Deliberate: they answer different questions. Adjacency is topological ("whose
    gap is this?") and pad radius does not change the answer for any realistic
    land; the gap is metric ("how wide is it?") and pad radius is the whole
    point. Folding the radius into the adjacency test would also close the one
    channel that matters most -- the corridor down the middle of a DIP, whose
    end pads sit just outside the circle of the pads facing each other across it.

    ``near[i]`` optionally restricts both the partner and the witness search to a
    pre-bucketed neighbourhood of pad ``i``. It must contain every pad within
    ``pad_mm + channel`` of ``i``: that is sufficient because a witness inside
    the circle is no further from A than B is, and B is inside the cutoff or the
    pair was already rejected. It must also be symmetric (``j in near[i]`` iff
    ``i in near[j]``) or a pair would be visited zero times instead of once.
    """
    reach = pad_mm + channel
    reach2 = reach * reach
    fits = channel - _EPS_MM
    n = len(pts)
    out: list[tuple[int, int, float]] = []
    for i in range(n):
        ax, ay = pts[i]
        cand = range(n) if near is None else near[i]
        for j in cand:
            if j <= i:
                continue
            bx, by = pts[j]
            dx, dy = bx - ax, by - ay
            d2 = dx * dx + dy * dy
            if d2 >= reach2:
                continue                      # a track fits: not a gap at all
            d = math.sqrt(d2)
            gap = d - pad_mm
            if gap >= fits:
                continue
            mx, my = ax + dx / 2.0, ay + dy / 2.0
            r2 = (d / 2.0 + _EPS_MM) ** 2
            for k in cand:
                if k == i or k == j:
                    continue
                cx, cy = pts[k]
                ux, uy = cx - mx, cy - my
                if ux * ux + uy * uy <= r2:
                    break                     # a third pad is standing in it
            else:
                out.append((i, j, gap))
    out.sort()
    return out


# --------------------------------------------------------------------------
# one footprint
# --------------------------------------------------------------------------

def component_blocked_pairs(comp: Component, *, track: float = 1.0,
                            clearance: float = 0.85,
                            pad_mm: float = PAD_MM) -> list[tuple[int, int]]:
    """Pad-index pairs of one footprint with no room for a track between them.

    Ascending ``(i, j)`` with ``i < j``, so the result is a set in content and a
    deterministic sequence in form. Judged on world pad positions, so the part's
    rotation is honoured -- though as a rigid transform it can only move the
    wall, never open it, and the answer is invariant under both rotation and
    translation. Only *adjacent* pairs appear; see :func:`_blocking`.

    This is the footprint on its own. Neighbouring parts are not consulted even
    when one of their pads is sitting in the gap, because the point of this view
    is to classify the part -- the same footprint has to come out the same way on
    every iteration of the anneal. Use :func:`board_blocked_gaps` for what the
    assembled design actually presents to the router.
    """
    pts = [comp.pad_world(p) for p in comp.pads]
    channel = channel_width(clearance, track)
    return [(i, j) for i, j, _ in _blocking(pts, pad_mm=pad_mm, channel=channel)]


# --------------------------------------------------------------------------
# wall or not
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Barrier:
    """How much of a wall one footprint's pad field is.

    ``runs`` are the groups of pads chained together by blocked gaps -- one run
    is one continuous barrier -- longest first, then longest-spanning, then by
    lowest pad index. A DIP comes back as two runs, one per pin row, because the
    open corridor between the rows keeps them apart; that is the useful answer,
    since it says the part is two walls with a road between them rather than one
    solid block.
    """
    ref: str
    pads: int                                   # pad count of the footprint
    blocked: tuple[tuple[int, int], ...]        # impassable adjacent pad pairs
    runs: tuple[tuple[int, ...], ...]           # pads joined by those gaps
    span: float                                 # longest barrier's outer extent (mm)

    @property
    def is_wall(self) -> bool:
        """Does this footprint present a continuous barrier (``WALL_MIN_PADS``)?"""
        return bool(self.runs) and len(self.runs[0]) >= WALL_MIN_PADS

    @property
    def passable(self) -> bool:
        """No continuous barrier: at worst a pinch the router steps around.

        Not a promise that every gap is open -- a two-pad part with one blocked
        gap is passable in this sense and still has a gap in ``blocked``. It
        means the obstruction is a single pad pair with a way past at either end,
        which is a detour of a few millimetres rather than a wall.
        """
        return not self.is_wall

    @property
    def longest_run(self) -> tuple[int, ...]:
        """The pads of the longest continuous barrier; empty when there is none."""
        return self.runs[0] if self.runs else ()


def _runs(pairs: list[tuple[int, int]], pts: list[tuple[float, float]],
          pad_mm: float) -> tuple[tuple[tuple[int, ...], ...], float]:
    """Connected runs of pads joined by blocked gaps, plus the longest span.

    A run's span is its widest pad-to-pad distance plus one pad diameter: what
    the router has to detour around is the copper, so the outer edge of the end
    lands is the honest extent, not their centres. The max over runs is reported
    because a caller ranking walls cares about the worst one.
    """
    adj: dict[int, set[int]] = {}
    for i, j in pairs:
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    found: list[tuple[tuple[int, ...], float]] = []
    seen: set[int] = set()
    for start in sorted(adj):
        if start in seen:
            continue
        group = {start}
        stack = [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    group.add(w)
                    stack.append(w)
        run = tuple(sorted(group))
        extent = max(math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
                     for a in run for b in run) + pad_mm
        found.append((run, extent))
    found.sort(key=lambda r: (-len(r[0]), -r[1], r[0]))
    return tuple(r for r, _ in found), (max(e for _, e in found) if found else 0.0)


def component_barrier(comp: Component, *, track: float = 1.0,
                      clearance: float = 0.85,
                      pad_mm: float = PAD_MM) -> Barrier:
    """Summarise one footprint as wall-like or passable.

    Everything is derived from the blocked gaps, so a part the process can route
    between is trivially passable and costs almost nothing to classify.
    """
    pts = [comp.pad_world(p) for p in comp.pads]
    pairs = component_blocked_pairs(comp, track=track, clearance=clearance,
                                    pad_mm=pad_mm)
    runs, span = _runs(pairs, pts, pad_mm)
    return Barrier(ref=comp.ref, pads=len(comp.pads), blocked=tuple(pairs),
                   runs=runs, span=round(span, 6))


# --------------------------------------------------------------------------
# the whole board
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Gap:
    """One pad-to-pad gap in the design that no track can pass through."""
    ref_a: str
    pad_a: int
    ref_b: str
    pad_b: int
    gap: float          # clear copper (mm); negative when the lands overlap
    x: float            # midpoint of the gap: where a track would have to squeeze
    y: float

    @property
    def internal(self) -> bool:
        """Both pads on one footprint.

        Worth separating from the rest: an internal gap is fixed by the footprint
        and the placer cannot open it by moving anything -- only a different
        footprint or a route around the part will do. A gap between two parts is
        the placer's own doing and can be opened by moving one of them, which
        makes it the actionable half of the report.
        """
        return self.ref_a == self.ref_b


def _pad_index(board: Board) -> list[tuple[str, int, float, float]]:
    """Every pad in the design as ``(ref, index, x, y)``, in sorted ref order.

    The order is what makes the output canonical: pads are visited by ``(ref,
    pad index)``, so taking only ``j > i`` yields each pair once with its lower
    endpoint first.
    """
    out = []
    for ref in sorted(board.components):
        c = board.components[ref]
        for i, p in enumerate(c.pads):
            x, y = c.pad_world(p)
            out.append((ref, i, x, y))
    return out


def _buckets(pts: list[tuple[float, float]], cell: float) -> list[list[int]]:
    """For each pad, the indices in its own and its eight neighbouring cells.

    A uniform grid at ``cell = pad_mm + channel``, the distance beyond which a
    gap always fits. Every pad within that distance is then guaranteed to be in
    the 3x3 block around its cell, which is what :func:`_blocking` needs of
    ``near``, and the result is symmetric because cell adjacency is. Turns the
    board scan from O(N^2) pairs into O(N) times the handful of pads that are
    actually close enough to matter -- on a real board the neighbourhood is a
    dozen pads whatever the board's size.
    """
    grid: dict[tuple[int, int], list[int]] = {}
    keys = []
    for idx, (x, y) in enumerate(pts):
        k = (int(math.floor(x / cell)), int(math.floor(y / cell)))
        keys.append(k)
        grid.setdefault(k, []).append(idx)
    near = []
    for gx, gy in keys:
        block: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                block.extend(grid.get((gx + dx, gy + dy), ()))
        block.sort()
        near.append(block)
    return near


def board_blocked_gaps(board: Board, *, track: float = 1.0,
                       clearance: float = 0.85,
                       pad_mm: float = PAD_MM) -> list[Gap]:
    """Every impassable pad-to-pad gap in the design, sorted and repeatable.

    Both kinds: the gaps a footprint carries with it and the gaps this particular
    placement creates between parts (``Gap.internal`` tells them apart). A pad of
    a neighbouring part obstructs exactly like one of your own, so it also counts
    as a third pad standing in a gap -- put a part's pad between two pads of
    another and the two halves are reported rather than the original pair, which
    is the truthful answer about where the copper is actually pinched.

    Sorted by ``(ref_a, pad_a, ref_b, pad_b)``. Determinism is a requirement, not
    a courtesy: this feeds a cost the annealer compares between candidates, and
    an order that depended on dict iteration would score two identical placements
    differently.
    """
    index = _pad_index(board)
    pts = [(x, y) for _, _, x, y in index]
    if len(pts) < 2:
        return []
    channel = channel_width(clearance, track)
    near = _buckets(pts, max(pad_mm + channel, _EPS_MM))
    out = []
    for i, j, gap in _blocking(pts, pad_mm=pad_mm, channel=channel, near=near):
        ref_a, pad_a, ax, ay = index[i]
        ref_b, pad_b, bx, by = index[j]
        # Deliberately unrounded, unlike the summary figures elsewhere in the
        # engine. ``gap`` is the quantity ``track_fits`` is applied to, and
        # rounding it -- even to a nanometre, which is a thousand times coarser
        # than the tie tolerance -- could hand a caller a number that passes the
        # very test this gap was reported for failing.
        out.append(Gap(ref_a=ref_a, pad_a=pad_a, ref_b=ref_b, pad_b=pad_b,
                       gap=gap, x=(ax + bx) / 2.0, y=(ay + by) / 2.0))
    out.sort(key=lambda g: (g.ref_a, g.pad_a, g.ref_b, g.pad_b))
    return out


def board_barriers(board: Board, *, track: float = 1.0, clearance: float = 0.85,
                   pad_mm: float = PAD_MM) -> dict[str, Barrier]:
    """Barrier summary per obstructing footprint, keyed by ref in sorted order.

    Only parts with at least one blocked gap are listed -- a board is mostly
    passives whose pads a track walks straight between, and an entry per part
    would bury the handful that actually block anything.
    """
    out: dict[str, Barrier] = {}
    for ref in sorted(board.components):
        bar = component_barrier(board.components[ref], track=track,
                                clearance=clearance, pad_mm=pad_mm)
        if bar.blocked:
            out[ref] = bar
    return out
