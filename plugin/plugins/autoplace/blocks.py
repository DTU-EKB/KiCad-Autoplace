"""Functional-block detection (M3), dependency-free.

Groups components that are densely interconnected by *signal* nets into blocks,
so the engine can keep each functional group cohesive instead of smearing it
across the board. This is the automatic replacement for the hand-drawn
``REGION = {...}`` / per-board grouping the DTU scripts rely on.

Algorithm: weighted label propagation on the component graph (edge weight = number
of shared signal nets). Power/ground and high-fanout nets are ignored -- they
connect everything and would collapse the whole board into one block. Pure Python,
deterministic (sorted iteration, lexicographic tie-break).

A future revision can seed labels from ``.kicad_sch`` sheet hierarchy for an even
cleaner split; the net-connectivity version already works on any board.
"""
from __future__ import annotations

from collections import defaultdict

from .metrics import _is_power
from .model import Board

# Nets touching more than this many pads are treated as buses/rails and skipped
# for clustering (they imply "everything connects to everything").
_FANOUT_LIMIT = 6


def detect_blocks(board: Board, max_iters: int = 30) -> dict[str, str]:
    """Return ref -> block-id. Also writes ``comp.block`` on the board."""
    adj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for net, members in board.nets().items():
        if _is_power(net) or len(members) > _FANOUT_LIMIT:
            continue
        refs = sorted({r for r, _ in members})
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                adj[a][b] += 1
                adj[b][a] += 1

    label = {ref: ref for ref in board.components}
    refs = sorted(board.components)
    for _ in range(max_iters):
        changed = False
        for ref in refs:
            nbrs = adj.get(ref)
            if not nbrs:
                continue
            tally: dict[str, int] = defaultdict(int)
            for nb, w in nbrs.items():
                tally[label[nb]] += w
            # highest weight wins; ties broken by smallest label for determinism
            best = min((-w, lbl) for lbl, w in tally.items())[1]
            if best != label[ref]:
                label[ref] = best
                changed = True
        if not changed:
            break

    # Re-key labels to compact b0, b1, ... ordered by size then name.
    groups: dict[str, list[str]] = defaultdict(list)
    for ref, lbl in label.items():
        groups[lbl].append(ref)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    remap = {old: f"b{i}" for i, (old, _) in enumerate(ordered)}

    out = {}
    for ref, lbl in label.items():
        bid = remap[lbl]
        out[ref] = bid
        board.components[ref].block = bid
    return out


def block_centroids(board: Board) -> dict[str, tuple[float, float]]:
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for c in board.components.values():
        a = acc[c.block]
        a[0] += c.x
        a[1] += c.y
        a[2] += 1
    return {b: (a[0] / a[2], a[1] / a[2]) for b, a in acc.items()}
