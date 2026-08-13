"""Seed a placement from a planar embedding instead of from forces.

The force-directed seed (``forcedirected.py``) minimises a spring energy, which
is a proxy for wirelength and says nothing about whether the result can be
routed on one layer. When the netlist is planar -- and by the census in
`docs/NIGHT-LOG.md`, 14 of 16 real boards are -- a strictly better target
exists: a drawing with **no crossings at all**. This module finds one and hands
it to the annealer as the starting point.

**Method.** Tutte's barycentric embedding. Pin the vertices of one face to a
convex polygon, then let every other vertex settle at the average of its
neighbours. On a 3-connected planar graph that provably yields a straight-line
drawing with no crossings; on the sparser graphs real netlists produce it is a
heuristic, so the result is *checked* rather than assumed (``crossings_of``),
and callers can fall back if it does not come out clean.

Two details do the real work:

* **The graph is the component/net incidence graph** from ``planarity`` -- parts
  and nets both become vertices. Net vertices are exactly the junction points
  the copper needs, so relaxing them alongside the parts places the junctions
  too. They are discarded at the end, but their positions are what keep the
  parts untangled.
* **The boundary is the largest face of the embedding.** Any face may be chosen
  as the outer one; taking the largest puts the most vertices on the perimeter,
  which is both the least cramped and a good match for a board whose connectors
  belong on the edge anyway.

Disconnected netlists are laid out per connected piece and packed side by side,
because a barycentric relaxation says nothing about where two unrelated
subcircuits should sit relative to each other.

Pure Python, no pcbnew, deterministic: every iteration order is sorted, so the
same board always yields the same seed.
"""
from __future__ import annotations

import math

from . import planarity
from .model import Board

# Barycentric relaxation is a linear solve done by Gauss-Seidel. It converges
# geometrically; 400 sweeps settles the 130-part board to well under a
# micrometre, and the whole thing still costs a few milliseconds.
DEFAULT_SWEEPS = 400
_TOL = 1e-9


def _blocks_and_faces(nodes, edges):
    """A face of the largest biconnected block, to pin as the outer boundary.

    Only a biconnected block has well-defined faces. Netlist graphs are usually
    a big block with trees hanging off it, so the block carries the topology
    that matters and the pendants follow wherever the relaxation puts them.
    """
    verts, e = planarity._normalise(nodes, edges)
    if not e:
        return None
    adj = planarity._adjacency(verts, e)
    blocks = planarity._biconnected_edge_sets(verts, adj)
    if not blocks:
        return None
    best = max(blocks, key=len)
    if len(best) < 3:
        return None
    faces = planarity._embed_block(best)
    if not faces:
        return None
    return max(faces, key=len)


def _layout_component(nodes, adj, sweeps):
    """Barycentric positions in the unit disk for one connected piece."""
    nodes = sorted(nodes, key=planarity._rank)
    if len(nodes) == 1:
        return {nodes[0]: (0.0, 0.0)}
    sub_edges = []
    nodeset = set(nodes)
    for v in nodes:
        for w in adj[v]:
            if w in nodeset and planarity._rank(v) < planarity._rank(w):
                sub_edges.append((v, w))

    boundary = _blocks_and_faces(nodes, sub_edges)
    if boundary is None or len(boundary) < 3:
        # No cycle to pin: a tree. Pin its leaves instead, which spreads it out
        # rather than collapsing it onto its centroid.
        leaves = [v for v in nodes if len(adj[v] & nodeset) <= 1]
        boundary = leaves if len(leaves) >= 3 else nodes[:max(3, len(nodes))]
    # Deduplicate while keeping the cyclic order the embedding gave us.
    seen, ring = set(), []
    for v in boundary:
        if v not in seen:
            seen.add(v)
            ring.append(v)

    pos = {}
    n = len(ring)
    for i, v in enumerate(ring):
        a = 2.0 * math.pi * i / n
        pos[v] = (math.cos(a), math.sin(a))
    free = [v for v in nodes if v not in seen]
    for v in free:
        pos[v] = (0.0, 0.0)
    if not free:
        return pos

    for _ in range(sweeps):
        shift = 0.0
        for v in free:
            nb = [w for w in sorted(adj[v] & nodeset, key=planarity._rank)]
            if not nb:
                continue
            sx = sum(pos[w][0] for w in nb) / len(nb)
            sy = sum(pos[w][1] for w in nb) / len(nb)
            shift = max(shift, abs(sx - pos[v][0]), abs(sy - pos[v][1]))
            pos[v] = (sx, sy)
        if shift < _TOL:
            break
    return pos


def _pack(layouts):
    """Place each connected piece's unit disk side by side in a rough grid.

    A barycentric layout is only meaningful within one connected piece; nothing
    relates two unconnected subcircuits, so they are simply tiled in a stable
    order rather than allowed to overlap at the origin.
    """
    if len(layouts) == 1:
        return layouts[0]
    cols = max(1, int(math.ceil(math.sqrt(len(layouts)))))
    out = {}
    for i, pos in enumerate(layouts):
        cx, cy = (i % cols) * 2.4, (i // cols) * 2.4
        for v, (x, y) in pos.items():
            out[v] = (x + cx, y + cy)
    return out


def graph_positions(board: Board, planes: set[str] | None = None,
                    sweeps: int = DEFAULT_SWEEPS) -> dict[str, tuple[float, float]]:
    """Crossing-free relative positions for every part, in arbitrary units.

    Returns component refs only; the net junction vertices are dropped once they
    have done their job of holding the parts apart.
    """
    nodes, edges = planarity.netlist_graph(board, planes)
    if not edges:
        return {}
    verts, e = planarity._normalise(nodes, edges)
    adj = planarity._adjacency(verts, e)
    layouts = [_layout_component(comp, adj, sweeps)
               for comp in planarity._components(verts, adj)]
    merged = _pack(layouts)
    return {v[1]: xy for v, xy in merged.items() if v[0] == "c"}


def seed(board: Board, planes: set[str] | None = None,
         sweeps: int = DEFAULT_SWEEPS) -> int:
    """Move every free part onto its planar-embedding position. Returns the count.

    Positions are scaled to fill the outline while keeping every footprint fully
    inside it -- the scale is chosen against part *extents*, not centres, or a
    large part near the edge would hang over it. Locked parts stay put, and
    parts the netlist says nothing about (no nets, or only poured ones) are
    tiled into the middle rather than left at whatever the file had.
    """
    free = [c for c in board.components.values() if not c.locked]
    if not free:
        return 0
    rel = graph_positions(board, planes, sweeps)
    placed = [c for c in free if c.ref in rel]
    if not placed:
        # Nothing the netlist constrains -- every net is poured or single-ended.
        # Still a placement job: spread them out legally rather than leaving
        # them wherever the file happened to have them (often 0,0, off-board).
        _spread(free, board)
        return 0

    xs = [rel[c.ref][0] for c in placed]
    ys = [rel[c.ref][1] for c in placed]
    spanx = max(xs) - min(xs)
    spany = max(ys) - min(ys)
    half_w = max(c.eff_w for c in placed) / 2.0
    half_h = max(c.eff_h for c in placed) / 2.0
    # Usable centre-box: the outline inset by the biggest half-footprint, so no
    # part can end up hanging over the edge.
    avail_w = max(1e-6, board.width - 2 * half_w)
    avail_h = max(1e-6, board.height - 2 * half_h)
    sx = avail_w / spanx if spanx > _TOL else 0.0
    sy = avail_h / spany if spany > _TOL else 0.0
    s = min(sx, sy) if (sx > 0 and sy > 0) else max(sx, sy)
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0

    for c in placed:
        rx, ry = rel[c.ref]
        c.x = board.cx + (rx - cx) * s
        c.y = board.cy + (ry - cy) * s
        _clamp(c, board)

    # Parts the graph never mentioned still need to be somewhere legal.
    _spread([c for c in free if c.ref not in rel], board)
    return len(placed)


def _spread(parts, board: Board):
    """Lay parts out in a stable row across the board, clamped inside it."""
    for i, c in enumerate(sorted(parts, key=lambda c: c.ref)):
        c.x = board.x0 + (i + 1) * board.width / (len(parts) + 1)
        c.y = board.cy
        _clamp(c, board)


def _clamp(c, board: Board):
    c.x = min(max(c.x, board.x0 + c.eff_w / 2), board.x1 - c.eff_w / 2)
    c.y = min(max(c.y, board.y0 + c.eff_h / 2), board.y1 - c.eff_h / 2)


def crossings_of(board: Board, planes: set[str] | None = None) -> int:
    """Straight-line crossings between the drawn net trees.

    The embedding is a heuristic on graphs that are not 3-connected, so callers
    that care should check rather than trust: 0 means this seed really is a
    single-sided drawing.
    """
    from . import globalroute
    return len(globalroute.conflicts(globalroute.net_segments(board, planes)))
