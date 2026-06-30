"""Parse a FreeRouting .ses session into a placement-congestion field.

Pure-Python (no pcbnew). Reads routed wire polylines and vias, bins them into a
grid over the board outline, and combines track density, via clusters, and
per-net detour into a per-cell pressure. ``anneal.py`` samples this to widen
routing channels exactly where the router struggled.

SES coordinates: ``(resolution um <r>)`` => mm = coord / (r * 1000). KiCad's DSN
negates Y, so model_y = -ses_y_mm. Coordinates therefore map to the model frame
as (x/scale, -y/scale) with scale = r * 1000 (= 10000 for the usual r=10).
"""
from __future__ import annotations

import math
import re

from .metrics import _is_power, CELL_MM
from .model import Board

_RES_RE = re.compile(r"\(resolution\s+um\s+(\d+)\)")
_VIA_RE = re.compile(r'\(via\s+"[^"]*"\s+(-?\d+)\s+(-?\d+)')
_PATH_RE = re.compile(r"\(path\s+(\S+)\s+\d+\s+([-\d\s]+?)\)", re.DOTALL)
# a net block: (net NAME ... ) up to the next (net or end of network_out
_NET_RE = re.compile(r"\(net\s+(\"[^\"]+\"|\S+)(.*?)(?=\(net\s|\Z)", re.DOTALL)


class CongestionField:
    def __init__(self, x0, y0, cell_mm, nx, ny, pressure):
        self._x0, self._y0, self._cell = x0, y0, cell_mm
        self._nx, self._ny = nx, ny
        self._p = pressure                       # dict (ix, iy) -> float 0..~3
        self.empty = not pressure

    def _cell_of(self, x, y):
        ix = int((x - self._x0) // self._cell)
        iy = int((y - self._y0) // self._cell)
        if 0 <= ix < self._nx and 0 <= iy < self._ny:
            return (ix, iy)
        return None

    def pressure_at(self, x: float, y: float) -> float:
        c = self._cell_of(x, y)
        return self._p.get(c, 0.0) if c is not None else 0.0


def _scale(text: str) -> float:
    m = _RES_RE.search(text)
    return (int(m.group(1)) * 1000.0) if m else 10000.0


def _points(coord_block: str, scale: float):
    nums = [int(t) for t in coord_block.split()]
    return [(nums[i] / scale, -nums[i + 1] / scale)
            for i in range(0, len(nums) - 1, 2)]


def parse(ses_path: str, board: Board, cell_mm: float = CELL_MM) -> CongestionField:
    with open(ses_path, encoding="utf-8") as f:
        text = f.read()
    scale = _scale(text)

    nx = max(1, int(math.ceil(board.width / cell_mm)))
    ny = max(1, int(math.ceil(board.height / cell_mm)))

    density = {}   # (ix,iy) -> routed mm in cell
    vias = {}      # (ix,iy) -> count
    detour = {}    # (ix,iy) -> summed (ratio-1)

    def cell(x, y):
        ix = int((x - board.x0) // cell_mm)
        iy = int((y - board.y0) // cell_mm)
        if 0 <= ix < nx and 0 <= iy < ny:
            return (ix, iy)
        return None

    # straight pad-span (HPWL) per signal net, for detour ratio
    span = {}
    for net, members in board.nets().items():
        if _is_power(net) or len(members) < 2:
            continue
        pts = []
        for r, pi in members:
            c = board.components[r]
            pts.append(c.pad_world(c.pads[pi]))
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        span[net] = max(1.0, (max(xs) - min(xs)) + (max(ys) - min(ys)))

    # restrict to the network_out section if present (avoids matching library)
    no = text.split("(network_out", 1)
    body = no[1] if len(no) > 1 else text

    for vx, vy in _VIA_RE.findall(body):
        c = cell(int(vx) / scale, -int(vy) / scale)
        if c:
            vias[c] = vias.get(c, 0) + 1

    for nm in _NET_RE.finditer(body):
        net = nm.group(1).strip('"')
        block = nm.group(2)
        routed = 0.0
        cells_hit = set()
        for _layer, coords in _PATH_RE.findall(block):
            pts = _points(coords, scale)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                seg = math.hypot(bx - ax, by - ay)
                routed += seg
                mc = cell((ax + bx) / 2, (ay + by) / 2)
                if mc:
                    density[mc] = density.get(mc, 0.0) + seg
                    cells_hit.add(mc)
        if net in span and routed > 0:
            ratio = max(0.0, routed / span[net] - 1.0)
            for c in cells_hit:
                detour[c] = detour.get(c, 0.0) + ratio

    if not (density or vias or detour):
        return CongestionField(board.x0, board.y0, cell_mm, nx, ny, {})

    dmax = (max(density.values()) if density else 1.0) or 1.0
    vmax = (max(vias.values()) if vias else 1.0) or 1.0
    tmax = (max(detour.values()) if detour else 1.0) or 1.0
    pressure = {}
    for c in set(density) | set(vias) | set(detour):
        pressure[c] = (density.get(c, 0.0) / dmax
                       + vias.get(c, 0) / vmax
                       + detour.get(c, 0.0) / tmax)
    return CongestionField(board.x0, board.y0, cell_mm, nx, ny, pressure)
