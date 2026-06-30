"""Placement quality metrics.

These are both the optimisation objective and the validation yardstick (spec
section 8). All are pure functions over a :class:`~autoplace.model.Board`.

- ``hpwl``       primary cost: total half-perimeter wirelength over pads.
- ``crossings``  single-sided-routability proxy: intersecting MST edges.
- ``overlaps``   hard constraint: count of overlapping component bounding boxes.
"""
from __future__ import annotations

import math

from .model import Board

# Power / ground nets dominate fan-out; counting them in HPWL just rewards
# squashing everything together. They are excluded from the wirelength metric
# (the router floods them anyway), but kept for crossings.
POWER_HINTS = ("GND", "VCC", "VDD", "VSS", "+5V", "+3V3", "+3.3V", "+12V",
               "+15V", "-15V", "VBAT", "AGND", "DGND", "PGND", "VIN", "VOUT")


def _is_power(net: str) -> bool:
    u = net.upper()
    return any(h.upper() in u for h in POWER_HINTS)


def net_pad_points(board: Board):
    """Yield (net, [(x, y), ...]) of pad world positions per connected net."""
    nets = board.nets()
    for net, members in nets.items():
        pts = []
        for ref, pi in members:
            c = board.components[ref]
            pts.append(c.pad_world(c.pads[pi]))
        yield net, pts


def hpwl(board: Board, include_power: bool = False) -> float:
    """Total half-perimeter wirelength (mm) over multi-pin signal nets."""
    total = 0.0
    for net, pts in net_pad_points(board):
        if len(pts) < 2:
            continue
        if not include_power and _is_power(net):
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def _mst_edges(pts):
    """Prim MST over points -> list of ((x1,y1),(x2,y2)). O(n^2), fine here."""
    n = len(pts)
    if n < 2:
        return []
    used = [False] * n
    used[0] = True
    best = [(float("inf"), -1)] * n
    for j in range(1, n):
        dx = pts[j][0] - pts[0][0]
        dy = pts[j][1] - pts[0][1]
        best[j] = (dx * dx + dy * dy, 0)
    edges = []
    for _ in range(n - 1):
        k, kd = -1, float("inf")
        for j in range(n):
            if not used[j] and best[j][0] < kd:
                kd, k = best[j][0], j
        if k < 0:
            break
        used[k] = True
        edges.append((pts[best[k][1]], pts[k]))
        for j in range(n):
            if not used[j]:
                dx = pts[j][0] - pts[k][0]
                dy = pts[j][1] - pts[k][1]
                d = dx * dx + dy * dy
                if d < best[j][0]:
                    best[j] = (d, k)
    return edges


def _seg_cross(a, b, c, d) -> bool:
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])
    d1 = ccw(c, d, a)
    d2 = ccw(c, d, b)
    d3 = ccw(a, b, c)
    d4 = ccw(a, b, d)
    return (((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)))


def crossings(board: Board, include_power: bool = False) -> int:
    """Estimated net crossings: intersecting MST edges across all signal nets.

    A low crossing count means the layout is close to planar, i.e. routable on a
    single copper layer -- the property the current refdes-grid placement lacks.
    """
    segs = []
    for net, pts in net_pad_points(board):
        if not include_power and _is_power(net):
            continue
        segs.extend(_mst_edges(pts))
    n = 0
    for i in range(len(segs)):
        a, b = segs[i]
        for j in range(i + 1, len(segs)):
            c, d = segs[j]
            if a in (c, d) or b in (c, d):
                continue          # shared endpoint, not a crossing
            if _seg_cross(a, b, c, d):
                n += 1
    return n


def overlaps(board: Board):
    """List of (refA, refB) whose bounding boxes overlap. Empty == legal."""
    comps = list(board.components.values())
    out = []
    for i in range(len(comps)):
        a = comps[i]
        for j in range(i + 1, len(comps)):
            b = comps[j]
            if (a.left < b.right and a.right > b.left and
                    a.top < b.bottom and a.bottom > b.top):
                out.append((a.ref, b.ref))
    return out


def summary(board: Board) -> dict:
    ov = overlaps(board)
    return {
        "hpwl_mm": round(hpwl(board), 2),
        "crossings": crossings(board),
        "overlaps": len(ov),
        "components": len(board.components),
    }


# Cell size (mm) for the whitespace / congestion grid -- one source of truth,
# reused by congestion.parse.
CELL_MM = 5.0

# Per-sheet fill-ratio band the floorplan targets (_DENSITY=0.5). Below SPREAD_LO
# a sheet is over-spread; above SPREAD_HI it is cramped. Both route worse, so the
# score penalises deviation outside the band.
SPREAD_LO = 0.35
SPREAD_HI = 0.6


def channel_width(margin: float, track: float) -> float:
    """Clear gap (mm) that fits one routing track between two courtyards:
    clearance + track + clearance, where ``margin`` is the copper clearance."""
    return track + 2 * margin


def sheet_spread_score(board: Board) -> float:
    """Mean per-sheet penalty for fill ratio outside ``[SPREAD_LO, SPREAD_HI]``.

    Lower is better (0.0 == every qualifying sheet sits in the target band).
    Movable parts only: locked and edge-pinned parts are excluded because they
    sit where the board forces them and would distort a sheet's bounding box.
    Fewer than two qualifying sheets (>=2 movable parts each) returns 0.0, so
    single-sheet boards rank purely on the other keys."""
    sheets: dict[str, list] = {}
    for c in board.components.values():
        if c.locked or c.edge:
            continue
        sheets.setdefault(c.sheet, []).append(c)
    penalties = []
    for parts in sheets.values():
        if len(parts) < 2:
            continue
        left = min(p.left for p in parts)
        right = max(p.right for p in parts)
        top = min(p.top for p in parts)
        bottom = max(p.bottom for p in parts)
        bbox = max(1e-6, (right - left) * (bottom - top))
        used = sum(p.eff_w * p.eff_h for p in parts)
        fill = used / bbox
        penalties.append(max(0.0, SPREAD_LO - fill) + max(0.0, fill - SPREAD_HI))
    if len(penalties) < 2:
        return 0.0
    return round(sum(penalties) / len(penalties), 4)


def pinch_fraction(board: Board, margin: float, track: float = 1.0) -> float:
    """Fraction of close (shadowing) component pairs whose gap is too tight for a
    routing channel. Lower is better. A pair 'shadows' when it nearly aligns on
    one axis (perpendicular gap < margin); it is a 'pinch' when the along-axis gap
    is non-negative but below one channel width. Mirrors the channel test in
    ``anneal._pair_penalty`` via the shared ``channel_width`` helper. Returns 0.0
    when no pairs shadow."""
    channel = channel_width(margin, track)
    comps = list(board.components.values())
    shadow = 0
    pinch = 0
    for i in range(len(comps)):
        a = comps[i]
        for j in range(i + 1, len(comps)):
            b = comps[j]
            gx = abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2
            gy = abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2
            if min(gx, gy) < margin:
                shadow += 1
                gap = max(gx, gy)
                if 0 <= gap < channel:
                    pinch += 1
    if shadow == 0:
        return 0.0
    return round(pinch / shadow, 4)


def whitespace_connectivity(board: Board, cell_mm: float = CELL_MM) -> float:
    """Largest connected empty region / total empty cells on a coarse grid over
    the outline. 1.0 == all whitespace is one connected routing sea; low == it is
    broken into isolated pockets. Higher is better. Every component (locked and
    edge-pinned included) is an obstacle, since they all block routing. Returns
    0.0 when there are no empty cells."""
    nx = max(1, int(math.ceil(board.width / cell_mm)))
    ny = max(1, int(math.ceil(board.height / cell_mm)))
    occupied = [[False] * ny for _ in range(nx)]
    for c in board.components.values():
        ix0 = max(0, int((c.left - board.x0) // cell_mm))
        ix1 = min(nx - 1, int((c.right - board.x0) // cell_mm))
        iy0 = max(0, int((c.top - board.y0) // cell_mm))
        iy1 = min(ny - 1, int((c.bottom - board.y0) // cell_mm))
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                occupied[ix][iy] = True
    total_empty = sum(1 for ix in range(nx) for iy in range(ny) if not occupied[ix][iy])
    if total_empty == 0:
        return 0.0
    seen = [[False] * ny for _ in range(nx)]
    largest = 0
    for sx in range(nx):
        for sy in range(ny):
            if occupied[sx][sy] or seen[sx][sy]:
                continue
            size = 0
            stack = [(sx, sy)]
            seen[sx][sy] = True
            while stack:
                ix, iy = stack.pop()
                size += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    jx, jy = ix + dx, iy + dy
                    if 0 <= jx < nx and 0 <= jy < ny and not occupied[jx][jy] and not seen[jx][jy]:
                        seen[jx][jy] = True
                        stack.append((jx, jy))
            largest = max(largest, size)
    return round(largest / total_empty, 4)
