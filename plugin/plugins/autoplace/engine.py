"""Top-level placement engine.

Pipeline: detect blocks -> seed -> force-directed global -> SA refine -> legalize.
(Rotation moves remain the last open M4 item; this pass is translation + swap.)

``place`` mutates the board in place and returns a report dict comparing the
before/after metrics -- the numbers that go into ``run.json`` and the HTML
report (spec section 8).
"""
from __future__ import annotations

import random

from . import (anneal, blocks, floorplan as floorplan_mod, forcedirected,
               legalize as legal_mod, metrics)
from .model import Board


def place(board: Board, *, seed: int = 0, grid: float = 0.5, margin: float = 0.8,
          iters: int = 400, sa_steps: int | None = None,
          strategy: str = "auto") -> dict:
    """strategy: 'auto' (force-directed seed, floorplan only via cohesion),
    'floorplan' (force the region floorplan seed), 'compact' (force-directed)."""
    before = metrics.summary(board)

    block_map = blocks.detect_blocks(board)
    n_blocks = len(set(block_map.values()))

    # Scale SA effort with the number of free parts, bounded so even the 131-part
    # system board finishes in well under a minute.
    n_free = len(board.free())
    if sa_steps is None:
        sa_steps = max(3500, min(45000, n_free * 700))
    fd_iters = max(iters, n_free * 6)

    # Density-adaptive routing-channel weight: on a board packed near its area
    # limit there is no room for channels, so insisting on them only thrashes the
    # layout (the dense motor_power case). Relax the channel term as the board
    # fills up; keep it strong on roomy boards where channels aid routability.
    area = max(1.0, board.width * board.height)
    used = sum(c.eff_w * c.eff_h for c in board.components.values())
    util = used / area
    channel_scale = max(0.0, min(1.0, (0.55 - util) / 0.35))

    rng = random.Random(seed)
    # Force-directed finds an excellent global arrangement; keep it for every
    # board. On hierarchical boards the blocks are the schematic sheets, and a
    # stronger cohesion term then pulls any stray sheet members back into their
    # group -- enforcing the sheet layout *without* a rigid floorplan (a rigid
    # region seed was measured to be worse on both HPWL and crossings).
    # Routability (validated with FreeRouting on the 131-part system board) is
    # driven by SPREAD, not raw wirelength: the compact HPWL-minimal layout routed
    # 76% while spreading the sheets into regions routed 87%. So hierarchical
    # boards use the region floorplan by default.
    hierarchical = floorplan_mod.is_hierarchical(board)
    use_floorplan = strategy == "floorplan" or (strategy == "auto" and hierarchical)
    anchors = None
    if use_floorplan:
        anchors = floorplan_mod.floorplan(board, rng, margin=margin)
    else:
        forcedirected.seed_positions(board, rng, margin=margin)
        forcedirected.run(board, rng, iters=fd_iters, margin=margin)
    if sa_steps:
        anneal.anneal(board, seed=seed, steps=sa_steps, margin=margin,
                      channel_scale=channel_scale,
                      cohesion_scale=2.5 if use_floorplan else 1.0,
                      anchors=anchors)
    remaining = legal_mod.legalize(board, grid=grid, margin=margin)

    after = metrics.summary(board)
    return {
        "before": before,
        "after": after,
        "blocks": n_blocks,
        "utilization": round(util, 2),
        "channel_scale": round(channel_scale, 2),
        "hpwl_delta_pct": _pct(before["hpwl_mm"], after["hpwl_mm"]),
        "crossings_delta": after["crossings"] - before["crossings"],
        "overlaps_remaining": len(remaining),
        "seed": seed,
    }


def _pct(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return round((b - a) / a * 100.0, 1)
