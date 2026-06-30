"""Force-directed global placement (M2).

Connectivity = attractive springs, courtyard overlap = repulsion, connectors get
pulled to the nearest board edge. Translation only (orientation is fixed in M2;
rotation/SA is M4). Deterministic given a seeded RNG.

This is the principled replacement for the refdes-alphabetical row packing in the
DTU ``pcb_build.py`` fallback: parts that share nets are pulled together instead
of being sorted by reference designator.
"""
from __future__ import annotations

import math

from . import geom
from .model import Board, Component


def _clamp_to_board(c: Component, board: Board, margin: float):
    geom.clamp_center(c, board, margin)


def seed_positions(board: Board, rng, margin: float = 1.0):
    """Initial spread for free components: jittered grid inside the outline."""
    free = [c for c in board.free()]
    if not free:
        return
    n = len(free)
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, int(math.ceil(n / cols)))
    usable_w = max(1.0, board.width - 2 * margin)
    usable_h = max(1.0, board.height - 2 * margin)
    for i, c in enumerate(free):
        gx = i % cols
        gy = i // cols
        c.x = board.x0 + margin + (gx + 0.5) * usable_w / cols
        c.y = board.y0 + margin + (gy + 0.5) * usable_h / rows
        c.x += (rng.random() - 0.5) * 2.0
        c.y += (rng.random() - 0.5) * 2.0
        _clamp_to_board(c, board, margin)


def _nearest_edge_target(c: Component, board: Board):
    """Point on the nearest board edge for connector edge-affinity."""
    dl, dr = c.x - board.x0, board.x1 - c.x
    dt, db = c.y - board.y0, board.y1 - c.y
    m = min(dl, dr, dt, db)
    if m == dl:
        return board.x0 + c.w / 2 + 1.0, c.y
    if m == dr:
        return board.x1 - c.w / 2 - 1.0, c.y
    if m == dt:
        return c.x, board.y0 + c.h / 2 + 1.0
    return c.x, board.y1 - c.h / 2 - 1.0


def run(board: Board, rng, *, iters: int = 400, k_spring: float = 0.04,
        k_repel: float = 0.9, k_edge: float = 0.08, margin: float = 1.0,
        cooling: float = 0.985):
    """Iterate the force model in place. Returns the board for chaining."""
    comps = list(board.components.values())
    nets = board.nets()
    free_set = {c.ref for c in board.free()}
    step = 1.0

    for _ in range(iters):
        fx = {c.ref: 0.0 for c in comps}
        fy = {c.ref: 0.0 for c in comps}

        # --- attractive: pull net members toward the net's pad centroid ---
        for net, members in nets.items():
            if len(members) < 2:
                continue
            pts = []
            for ref, pi in members:
                c = board.components[ref]
                pts.append((ref, *c.pad_world(c.pads[pi])))
            cxm = sum(p[1] for p in pts) / len(pts)
            cym = sum(p[2] for p in pts) / len(pts)
            # 1/degree so fat power nets don't dominate / collapse the layout
            w = k_spring / math.sqrt(len(members))
            for ref, px, py in pts:
                fx[ref] += (cxm - px) * w
                fy[ref] += (cym - py) * w

        # --- repulsive: push apart overlapping / close courtyards ---
        for i in range(len(comps)):
            a = comps[i]
            for j in range(i + 1, len(comps)):
                b = comps[j]
                dx = a.x - b.x
                dy = a.y - b.y
                min_x = (a.eff_w + b.eff_w) / 2 + margin
                min_y = (a.eff_h + b.eff_h) / 2 + margin
                ox = min_x - abs(dx)
                oy = min_y - abs(dy)
                if ox > 0 and oy > 0:                  # boxes (near-)overlap
                    if abs(dx) < 1e-6:
                        dx = (rng.random() - 0.5) * 0.1
                    if abs(dy) < 1e-6:
                        dy = (rng.random() - 0.5) * 0.1
                    push = k_repel
                    if ox < oy:                        # resolve along smaller axis
                        f = math.copysign(push * ox, dx)
                        fx[a.ref] += f
                        fx[b.ref] -= f
                    else:
                        f = math.copysign(push * oy, dy)
                        fy[a.ref] += f
                        fy[b.ref] -= f

        # --- connector edge-affinity ---
        for c in comps:
            if c.is_connector and c.ref in free_set:
                tx, ty = _nearest_edge_target(c, board)
                fx[c.ref] += (tx - c.x) * k_edge
                fy[c.ref] += (ty - c.y) * k_edge

        # --- integrate (locked parts never move) ---
        for c in comps:
            if c.ref not in free_set:
                continue
            c.x += max(-3.0, min(3.0, fx[c.ref])) * step
            c.y += max(-3.0, min(3.0, fy[c.ref])) * step
            _clamp_to_board(c, board, margin)

        step *= cooling
        if step < 0.05:
            break
    return board
