"""Placement quality metrics.

These are both the optimisation objective and the validation yardstick (spec
section 8). All are pure functions over a :class:`~autoplace.model.Board`.

- ``hpwl``       primary cost: total half-perimeter wirelength over pads.
- ``crossings``  single-sided-routability proxy: intersecting MST edges.
- ``overlaps``   hard constraint: count of overlapping component bounding boxes.
"""
from __future__ import annotations

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
