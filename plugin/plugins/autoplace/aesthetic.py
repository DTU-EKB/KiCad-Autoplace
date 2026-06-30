"""Aesthetic post-pass: snap near-collinear free parts onto a shared axis.

Pure module — imports only from geom/metrics/model. No pcbnew dependency.
"""
from __future__ import annotations

from .model import Board

# Maximum distance (mm) two parts' centres may differ on an axis and still
# be snapped to a shared line; also the maximum any single part is moved.
ALIGN_TOL_MM = 1.5


def _snap(v: float, grid: float) -> float:
    """Round v to the nearest grid step."""
    return round(v / grid) * grid


def _try_move(board: Board, c, axis: str, target: float, margin: float) -> bool:
    """Attempt to set c.axis = target.

    Returns True if the move was accepted (legal + in-bounds), False otherwise.
    Reverts the coordinate if the move is rejected.
    """
    old = getattr(c, axis)
    if abs(target - old) < 1e-9:
        return False  # no-op: already at target

    setattr(c, axis, target)

    # In-bounds check: use the same inset as geom.clamp_center.
    inset = margin + board.edge_keepout
    if (c.left < board.x0 + inset or c.right > board.x1 - inset or
            c.top < board.y0 + inset or c.bottom > board.y1 - inset):
        setattr(c, axis, old)
        return False

    # Overlap check against all other components.
    for o in board.components.values():
        if o is c:
            continue
        if ((c.eff_w + o.eff_w) / 2 + margin > abs(c.x - o.x) and
                (c.eff_h + o.eff_h) / 2 + margin > abs(c.y - o.y)):
            setattr(c, axis, old)
            return False

    return True


def align(board: Board, *, grid: float = 0.5, margin: float = 0.8,
          tol: float = ALIGN_TOL_MM) -> int:
    """Snap near-collinear free parts onto a shared axis, per functional block.

    Legality-preserving (no new overlap, stays in bounds) and deterministic.
    Returns the number of parts actually moved.
    """
    # 1. Candidates: free, non-edge, non-locked.
    candidates = [
        c for c in board.free()
        if not c.edge and not c.locked
    ]

    # 2. Group by c.block; sort keys for determinism.
    groups: dict[str, list] = {}
    for c in candidates:
        groups.setdefault(c.block, []).append(c)

    moved = 0

    # 3. For each axis in order: X first, then Y.
    for axis in ("x", "y"):
        for _key, group in sorted(groups.items()):
            # Sort parts within the group by their axis coordinate, ties by ref.
            parts = sorted(group, key=lambda c: (getattr(c, axis), c.ref))

            # 3b. Greedy clustering on the sorted axis coordinates.
            if not parts:
                continue
            clusters: list[list] = []
            current = [parts[0]]
            running_mean = getattr(parts[0], axis)
            for c in parts[1:]:
                coord = getattr(c, axis)
                if abs(coord - running_mean) <= tol:
                    current.append(c)
                    # Update running mean.
                    running_mean = sum(getattr(p, axis) for p in current) / len(current)
                else:
                    clusters.append(current)
                    current = [c]
                    running_mean = coord
            clusters.append(current)

            # 3c. For each cluster of >= 2 parts, attempt to snap each to the target.
            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                coords = [getattr(c, axis) for c in cluster]
                target = _snap(sum(coords) / len(coords), grid)
                # Iterate in deterministic order (by ref).
                for c in sorted(cluster, key=lambda c: c.ref):
                    if _try_move(board, c, axis, target, margin):
                        moved += 1

    return moved
