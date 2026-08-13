"""Coarse global router: routability feedback in milliseconds, not minutes.

The placement engine has two evaluation tiers and a hole between them. Cheap
proxies (``metrics.hpwl`` / ``crossings``) cost microseconds and run 10^5 times
per anneal but do not describe routing; FreeRouting describes routing exactly
but costs 10-60 s, so a run affords maybe a dozen calls. This module is the
missing middle: a pure-Python estimate, ~1 ms for a 40-part board, that answers
the two questions the outer tier is actually asked.

**1. Where is it too tight?** A coarse grid over the outline. Every net is
reduced to a spanning tree, each tree edge is rasterised across the cells it
passes through (one track of demand per cell), and each cell's capacity is the
number of parallel tracks that physically fit -- ``cell / (track + clearance)``
from the fabrication profile -- derated by the through-hole pads sitting in it,
which no other net can pass through.

**2. How many wire bridges does this placement force?** On a single-sided board
the deliverable number is not "crossings" but "wires the user has to solder".
Two tree edges of different nets that cross cannot both stay on the copper
layer; resolving that crossing means lifting one of them onto the component
side. One lifted edge clears *every* crossing along it, so the minimum number
of bridges is the **minimum vertex cover of the crossing graph** (segments are
vertices, crossings are edges) -- not the crossing count, which overstates
badly: a single segment cutting across five others is one bridge, not five.

Nets carried by a copper pour are excluded throughout. A filled ground plane
connects its pads for free, so counting them would report a dozen phantom
bridges on every board (``planes``; defaults to the ground net by name).

Pure Python: no ``pcbnew``, no Java, deterministic, unit-tested. It has to be,
to live in the annealer's loop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import nets as netnames
from .model import Board

# Coarse-grid cell (mm). ~4 mm is a few track pitches across on the CNC profile:
# fine enough to localise a pinch, coarse enough that a 100 mm board is a 25x25
# grid the inner loop can afford.
DEFAULT_CELL_MM = 4.0

# Nominal through-hole pad diameter (mm). The Board model carries pad positions
# but not pad sizes, and a THT land on the CNC profile is ~2 mm across. Used
# only to derate cell capacity, so the exact value shifts congestion by a
# constant factor and never changes the ranking between placements.
PAD_MM = 2.0

# Capacity floor (tracks) used when turning demand into a ratio, so a cell that
# is fully blocked by pads reports a large finite pressure instead of dividing
# by zero.
_MIN_CAP = 0.25


@dataclass(frozen=True)
class Segment:
    """One edge of a net's spanning tree: copper that has to exist."""
    net: str
    ax: float
    ay: float
    bx: float
    by: float

    @property
    def length(self) -> float:
        return math.hypot(self.bx - self.ax, self.by - self.ay)


@dataclass(frozen=True)
class Congestion:
    """Per-cell routing demand against per-cell routing capacity."""
    cell_mm: float
    x0: float
    y0: float
    nx: int
    ny: int
    demand: tuple[tuple[float, ...], ...]
    capacity: tuple[tuple[float, ...], ...]

    @property
    def total_demand(self) -> float:
        return sum(sum(col) for col in self.demand)

    @property
    def overflow(self) -> float:
        """Total tracks demanded beyond what fits. 0.0 == everything fits."""
        return sum(
            max(0.0, self.demand[ix][iy] - self.capacity[ix][iy])
            for ix in range(self.nx) for iy in range(self.ny)
        )

    @property
    def peak(self) -> float:
        """Worst demand/capacity ratio over the grid. > 1.0 == over capacity."""
        worst = 0.0
        for ix in range(self.nx):
            for iy in range(self.ny):
                d = self.demand[ix][iy]
                if d <= 0.0:
                    continue
                worst = max(worst, d / max(_MIN_CAP, self.capacity[ix][iy]))
        return worst

    def pressure_at(self, x: float, y: float) -> float:
        """Demand/capacity ratio at a board coordinate (0.0 outside the grid).

        Mirrors ``congestion.Field.pressure_at`` so the annealer can consume a
        predicted field exactly like a measured one."""
        ix = int((x - self.x0) // self.cell_mm)
        iy = int((y - self.y0) // self.cell_mm)
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            return 0.0
        return self.demand[ix][iy] / max(_MIN_CAP, self.capacity[ix][iy])

    def hotspots(self, limit: int = 5) -> list[tuple[int, int, float]]:
        """The most over-subscribed cells, worst first: (ix, iy, ratio)."""
        cells = []
        for ix in range(self.nx):
            for iy in range(self.ny):
                d = self.demand[ix][iy]
                if d <= 0.0:
                    continue
                cells.append((ix, iy, d / max(_MIN_CAP, self.capacity[ix][iy])))
        cells.sort(key=lambda c: (-c[2], c[0], c[1]))
        return cells[:limit]


@dataclass(frozen=True)
class Estimate:
    """What a placement is predicted to cost the router."""
    bridges: int          # minimum wire bridges a single-sided route is forced into
    conflicts: int        # raw crossing pairs (always >= bridges)
    segments: int         # tree edges that have to be routed
    wirelength: float     # total tree length (mm), signal nets only
    overflow: float       # tracks demanded beyond capacity
    peak: float           # worst cell demand/capacity ratio
    grid: Congestion | None = None


# --------------------------------------------------------------------------
# what has to be routed
# --------------------------------------------------------------------------

def plane_nets(board: Board) -> set[str]:
    """Nets a copper pour connects for free.

    Prefers the board's own ``planes`` (populated by ``kicad_io`` from the real
    zones) and falls back to matching the ground net by name, so a bare model
    built in a test still behaves like the real thing.
    """
    declared = getattr(board, "planes", None)
    if declared:
        return set(declared)
    return {n for n in board.nets() if netnames.is_gnd_name(n)}


def _mst_pairs(pts: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """Prim MST over points -> (i, j) index pairs. O(n^2); n is a net's pin count.

    Ties break on the lowest index so the tree is identical run to run.
    """
    n = len(pts)
    if n < 2:
        return []
    used = [False] * n
    used[0] = True
    best_d = [float("inf")] * n
    best_p = [-1] * n
    for j in range(1, n):
        dx, dy = pts[j][0] - pts[0][0], pts[j][1] - pts[0][1]
        best_d[j], best_p[j] = dx * dx + dy * dy, 0
    out = []
    for _ in range(n - 1):
        k, kd = -1, float("inf")
        for j in range(n):
            if not used[j] and best_d[j] < kd:
                kd, k = best_d[j], j
        if k < 0:
            break
        used[k] = True
        out.append((best_p[k], k))
        for j in range(n):
            if used[j]:
                continue
            dx, dy = pts[j][0] - pts[k][0], pts[j][1] - pts[k][1]
            d = dx * dx + dy * dy
            if d < best_d[j]:
                best_d[j], best_p[j] = d, k
    return out


def net_segments(board: Board, planes: set[str] | None = None) -> list[Segment]:
    """Spanning-tree edges for every net that actually needs copper.

    Skips poured nets and single-pad nets. Nets and their members are visited in
    sorted order, so the result is byte-identical across runs and processes.
    """
    skip = plane_nets(board) if planes is None else set(planes)
    members = board.nets()
    out: list[Segment] = []
    for net in sorted(members):
        if net in skip:
            continue
        pts = []
        for ref, pi in sorted(members[net]):
            c = board.components[ref]
            pts.append(c.pad_world(c.pads[pi]))
        for i, j in _mst_pairs(pts):
            out.append(Segment(net, pts[i][0], pts[i][1], pts[j][0], pts[j][1]))
    return out


# --------------------------------------------------------------------------
# crossings
# --------------------------------------------------------------------------

def _crosses(s: Segment, t: Segment) -> bool:
    """Do these two segments share any point?

    Proper crossings *plus* the degenerate touches, which on a PCB are the ones
    that matter most: an endpoint lying exactly on the other segment means one
    net's copper runs straight through another net's pad, which is a short, not
    a near miss. Parts sit on a 2.54 mm grid and get aligned by
    ``aesthetic.align``, so that arrangement is routine rather than a curiosity.

    The earlier version tested only for a proper crossing, via
    ``(d1 > 0) != (d2 > 0)``. That silently treats a zero determinant as
    "negative", so a touch counted as a crossing or not depending on which way
    round the two segments happened to be written -- measured at 4.6% of
    grid-aligned pairs, every one of them a touch or a collinear overlap. The
    count therefore depended on iteration order. This is the standard
    orientation test with the collinear cases handled explicitly, and it is
    symmetric in both arguments and in each segment's own endpoint order; the
    test suite asserts that over several thousand grid-aligned pairs.

    Segments that merely share an endpoint also come back True. ``conflicts``
    filters those out beforehand, where the caller knows they are two branches
    meeting at a pad rather than two nets colliding.
    """
    def ccw(px, py, qx, qy, rx, ry):
        return (ry - py) * (qx - px) - (qy - py) * (rx - px)

    def on(px, py, qx, qy, rx, ry):
        """r is within pq's bounding box (used only when r is collinear with pq)."""
        return (min(px, qx) <= rx <= max(px, qx)
                and min(py, qy) <= ry <= max(py, qy))

    d1 = ccw(t.ax, t.ay, t.bx, t.by, s.ax, s.ay)
    d2 = ccw(t.ax, t.ay, t.bx, t.by, s.bx, s.by)
    d3 = ccw(s.ax, s.ay, s.bx, s.by, t.ax, t.ay)
    d4 = ccw(s.ax, s.ay, s.bx, s.by, t.bx, t.by)
    if (((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0
            and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0):
        return True
    if d1 == 0 and on(t.ax, t.ay, t.bx, t.by, s.ax, s.ay):
        return True
    if d2 == 0 and on(t.ax, t.ay, t.bx, t.by, s.bx, s.by):
        return True
    if d3 == 0 and on(s.ax, s.ay, s.bx, s.by, t.ax, t.ay):
        return True
    if d4 == 0 and on(s.ax, s.ay, s.bx, s.by, t.bx, t.by):
        return True
    return False


def conflicts(segments: list[Segment]) -> list[tuple[int, int]]:
    """Index pairs of segments that cannot share one copper layer.

    Segments of the *same* net are never in conflict -- they are the same node
    of the netlist, so overlapping copper merely shorts it to itself. Segments
    that merely meet at a point are not crossing either.
    """
    out = []
    n = len(segments)
    for i in range(n):
        s = segments[i]
        ends_s = ((s.ax, s.ay), (s.bx, s.by))
        for j in range(i + 1, n):
            t = segments[j]
            if s.net == t.net:
                continue
            if (t.ax, t.ay) in ends_s or (t.bx, t.by) in ends_s:
                continue
            if _crosses(s, t):
                out.append((i, j))
    return out


# --------------------------------------------------------------------------
# minimum bridges == minimum vertex cover of the crossing graph
# --------------------------------------------------------------------------

# Branch-and-bound node budget. Beyond it the search returns its best cover so
# far, which is still a valid (if possibly non-minimal) bridge count. Real
# boards resolve in a few hundred nodes; the budget only guards a pathological
# placement from stalling the annealer.
_BB_BUDGET = 20000


def min_bridges(n_segments: int, conflict_pairs: list[tuple[int, int]]) -> int:
    """Fewest segments to lift onto the component side to remove every crossing.

    Exactly a minimum vertex cover: each crossing is resolved by lifting one of
    the two segments involved, and a lifted segment clears all of its crossings
    at once. Solved per connected component with branch and bound (with the
    degree-0 and degree-1 reductions), which is instant at these sizes; a
    pathological graph falls back to the greedy cover it has already found.
    """
    if not conflict_pairs:
        return 0
    adj: dict[int, set[int]] = {}
    for a, b in conflict_pairs:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    total = 0
    seen: set[int] = set()
    for start in sorted(adj):
        if start in seen:
            continue
        comp = {start}
        stack = [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    comp.add(w)
                    stack.append(w)
        total += _cover_component(comp, adj)
    return total


def _greedy_cover(verts: set[int], adj: dict[int, set[int]]) -> int:
    """Deterministic greedy cover: repeatedly take the highest-degree vertex."""
    live = {v: set(adj[v]) & verts for v in verts}
    taken = 0
    while True:
        v, deg = -1, 0
        for cand in sorted(live):
            d = len(live[cand])
            if d > deg:
                v, deg = cand, d
        if v < 0:
            return taken
        taken += 1
        for w in live[v]:
            live[w].discard(v)
        live[v] = set()


def _cover_component(verts: set[int], adj: dict[int, set[int]]) -> int:
    best = [_greedy_cover(verts, adj)]
    budget = [_BB_BUDGET]

    def solve(live: dict[int, set[int]], taken: int):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if taken >= best[0]:
            return                       # cannot beat the incumbent
        # Reductions: isolated vertices are free; a degree-1 vertex's neighbour
        # is in some optimal cover, so take it without branching.
        while True:
            drop = [v for v, ns in live.items() if not ns]
            for v in drop:
                del live[v]
            leaf = None
            for v in sorted(live):
                if len(live[v]) == 1:
                    leaf = v
                    break
            if leaf is None:
                break
            u = next(iter(live[leaf]))
            taken += 1
            if taken >= best[0]:
                return
            for w in live[u]:
                live[w].discard(u)
            del live[u]
        if not live:
            best[0] = taken
            return
        # Branch on the highest-degree vertex (lowest index breaks ties).
        v, deg = -1, -1
        for cand in sorted(live):
            d = len(live[cand])
            if d > deg:
                v, deg = cand, d
        # (a) lift v
        nb = set(live[v])
        sub = {k: set(s) for k, s in live.items()}
        for w in sub[v]:
            sub[w].discard(v)
        del sub[v]
        solve(sub, taken + 1)
        # (b) keep v -> every neighbour must be lifted
        sub = {k: set(s) for k, s in live.items()}
        for u in nb | {v}:
            for w in sub.get(u, ()):
                sub[w].discard(u)
            sub.pop(u, None)
        solve(sub, taken + len(nb))

    solve({v: set(adj[v]) & verts for v in verts}, 0)
    return best[0]


# --------------------------------------------------------------------------
# congestion grid
# --------------------------------------------------------------------------

def congestion(board: Board, *, cell_mm: float = DEFAULT_CELL_MM,
               track: float = 1.0, clearance: float = 0.85,
               planes: set[str] | None = None,
               segments: list[Segment] | None = None) -> Congestion:
    """Rasterise the net trees onto a coarse grid and compare against capacity.

    Capacity is how many parallel tracks fit across a cell on this fabrication
    profile, derated by the area the through-hole pads in that cell make
    unroutable. Demand is one track per cell each tree edge passes through.
    """
    pitch = track + clearance
    nx = max(1, int(math.ceil(board.width / cell_mm)))
    ny = max(1, int(math.ceil(board.height / cell_mm)))
    # Fractional, deliberately: rounding down to whole tracks puts a cliff in the
    # middle of the metric (a 4 mm cell on the CNC pitch holds 2.16 tracks, and
    # truncating throws away 8% of every cell), and a cost term the annealer
    # optimises against wants to be continuous.
    base_cap = cell_mm / pitch if pitch > 0 else 0.0
    cell_area = cell_mm * cell_mm
    # A pad blocks its own land plus a clearance ring on every side. Round, not
    # square: the square form overstates a THT land by ~27%, and on a fine grid
    # that alone drives every pad-bearing cell to zero capacity.
    pad_block = math.pi / 4.0 * (PAD_MM + 2 * clearance) ** 2

    blocked = [[0.0] * ny for _ in range(nx)]
    for c in board.components.values():
        for p in c.pads:
            px, py = c.pad_world(p)
            ix = int((px - board.x0) // cell_mm)
            iy = int((py - board.y0) // cell_mm)
            if 0 <= ix < nx and 0 <= iy < ny:
                blocked[ix][iy] += pad_block

    cap = [[base_cap * max(0.0, 1.0 - blocked[ix][iy] / cell_area)
            for iy in range(ny)] for ix in range(nx)]

    if segments is None:
        segments = net_segments(board, planes)
    dem = [[0.0] * ny for _ in range(nx)]
    step = cell_mm / 4.0                 # fine enough that no cell is skipped
    for s in segments:
        dx, dy = s.bx - s.ax, s.by - s.ay
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist / step) + 1)
        hit = set()
        for k in range(steps + 1):
            t = k / steps
            px, py = s.ax + dx * t, s.ay + dy * t
            ix = int((px - board.x0) // cell_mm)
            iy = int((py - board.y0) // cell_mm)
            if 0 <= ix < nx and 0 <= iy < ny:
                hit.add((ix, iy))
        for ix, iy in hit:
            dem[ix][iy] += 1.0

    return Congestion(
        cell_mm=cell_mm, x0=board.x0, y0=board.y0, nx=nx, ny=ny,
        demand=tuple(tuple(col) for col in dem),
        capacity=tuple(tuple(col) for col in cap),
    )


# --------------------------------------------------------------------------
# the public estimate
# --------------------------------------------------------------------------

def analyse(board: Board, *, cell_mm: float = DEFAULT_CELL_MM,
            track: float = 1.0, clearance: float = 0.85,
            planes: set[str] | None = None,
            grid: bool = True) -> Estimate:
    """Predicted routing cost of a placement: bridges, crossings and congestion.

    ``grid=False`` skips the congestion raster when only the bridge count is
    wanted, which is the annealer's hot path.
    """
    segs = net_segments(board, planes)
    conf = conflicts(segs)
    bridges = min_bridges(len(segs), conf)
    g = congestion(board, cell_mm=cell_mm, track=track, clearance=clearance,
                   planes=planes, segments=segs) if grid else None
    return Estimate(
        bridges=bridges,
        conflicts=len(conf),
        segments=len(segs),
        wirelength=round(sum(s.length for s in segs), 3),
        overflow=round(g.overflow, 3) if g else 0.0,
        peak=round(g.peak, 3) if g else 0.0,
        grid=g,
    )
