"""Block floorplanning for hierarchical boards (M3/M4b).

Only used when the board has real functional blocks (hierarchical sheets, e.g. the
system board's Motor Power / MPPT / Boost / ... sub-circuits). It arranges those
blocks into compact, connectivity-ordered regions and seeds each part inside its
region, so the SA pass refines *within* a sensible global structure instead of
trying to discover the whole floorplan from a random scatter.

Crucially this is gated on hierarchy: flat single-sheet boards keep the plain
force-directed seed, which an earlier "floorplan everything" attempt was shown to
hurt (rigid rows wrecked the mid boards).
"""
from __future__ import annotations

import math
from collections import defaultdict

from .metrics import _is_power
from .model import Board

_FANOUT_LIMIT = 6
_DENSITY = 0.5            # region area = member courtyard area / this


def _members(board: Board):
    m: dict[str, list[str]] = defaultdict(list)
    for ref, c in board.components.items():
        m[c.block].append(ref)
    return m


def _block_adj(board: Board):
    adj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for net, mem in board.nets().items():
        if _is_power(net) or len(mem) > _FANOUT_LIMIT:
            continue
        blks = sorted({board.components[r].block for r, _ in mem})
        for i, a in enumerate(blks):
            for b in blks[i + 1:]:
                adj[a][b] += 1
                adj[b][a] += 1
    return adj


def _order_chain(members, adj):
    remaining = set(members)
    if not remaining:
        return []
    order = [max(sorted(remaining), key=lambda b: len(members[b]))]
    remaining.discard(order[0])
    while remaining:
        nxt = max(sorted(remaining),
                  key=lambda b: sum(adj[b].get(o, 0) for o in order))
        order.append(nxt)
        remaining.discard(nxt)
    return order


def floorplan(board: Board, rng, *, margin: float = 0.8):
    members = _members(board)
    order = _order_chain(members, _block_adj(board))
    if not order:
        return

    aspect = board.width / max(board.height, 1.0)
    # region box per block, proportioned to the board aspect
    region = {}
    for blk in order:
        area = sum(board.components[r].w * board.components[r].h
                   for r in members[blk]) / _DENSITY
        h = math.sqrt(max(area, 16.0) / max(aspect, 0.1))
        w = area / h if h else math.sqrt(area)
        region[blk] = [w, h]

    # shelf-pack regions left->right, wrapping rows, within the outline width
    avail_w = board.width - 2 * margin
    x = 0.0
    y = 0.0
    row_h = 0.0
    pos = {}
    for blk in order:
        w, h = region[blk]
        if x + w > avail_w and row_h > 0:
            x = 0.0
            y += row_h
            row_h = 0.0
        pos[blk] = (x, y)
        x += w
        row_h = max(row_h, h)
    total_w = max((pos[b][0] + region[b][0] for b in order), default=avail_w)
    total_h = y + row_h

    # scale the whole arrangement to fit the usable area, then centre it
    sx = avail_w / total_w if total_w > 0 else 1.0
    sy = (board.height - 2 * margin) / total_h if total_h > 0 else 1.0
    s = min(sx, sy, 1.0) if min(sx, sy) < 1.0 else min(sx, sy)
    used_w, used_h = total_w * s, total_h * s
    ox = board.x0 + (board.width - used_w) / 2
    oy = board.y0 + (board.height - used_h) / 2

    for blk in order:
        bx, by = pos[blk]
        w, h = region[blk]
        rx0 = ox + bx * s
        ry0 = oy + by * s
        rw, rh = w * s, h * s
        for ref in members[blk]:
            c = board.components[ref]
            if c.locked:
                continue
            c.x = rx0 + rw * (0.2 + 0.6 * rng.random())
            c.y = ry0 + rh * (0.2 + 0.6 * rng.random())
            hw, hh = c.eff_w / 2, c.eff_h / 2
            c.x = min(max(c.x, board.x0 + hw + margin), board.x1 - hw - margin)
            c.y = min(max(c.y, board.y0 + hh + margin), board.y1 - hh - margin)


def is_hierarchical(board: Board) -> bool:
    subs = {c.sheet for c in board.components.values()
            if c.sheet and c.sheet != "/"}
    return len(subs) >= 2
