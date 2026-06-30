"""Top-level placement engine.

Pipeline: detect blocks -> seed -> force-directed global -> SA refine
(translation, rotation, and swap moves) -> legalize.

``place`` mutates the board in place and returns a report dict comparing the
before/after metrics -- the numbers that go into ``run.json`` and the HTML
report (spec section 8).
"""
from __future__ import annotations

import random

from . import (anneal, blocks, edge as edge_mod, floorplan as floorplan_mod, forcedirected,
               legalize as legal_mod, metrics)
from .model import Board


def place(board: Board, *, seed: int = 0, grid: float = 0.5, margin: float = 0.8,
          iters: int = 400, sa_steps: int | None = None,
          strategy: str = "auto", progress=None,
          connectors: list[str] | None = None) -> dict:
    """strategy: 'auto' (force-directed seed, floorplan only via cohesion),
    'floorplan' (force the region floorplan seed), 'compact' (force-directed).

    ``progress`` is an optional callback ``progress(stage: str, frac: float)``
    invoked at pipeline milestones (frac in 0..1) so a host UI can show a live
    bar. It never affects the placement result."""
    def _report(stage, frac):
        if progress is not None:
            progress(stage, max(0.0, min(1.0, frac)))

    before = metrics.summary(board)
    _report("analyze", 0.05)

    # An explicit connector set (from the app's sidecar) overrides the
    # refdes/footprint auto-guess: exactly these refs are connectors.
    if connectors is not None:
        conn_set = set(connectors)
        for c in board.components.values():
            c.is_connector = c.ref in conn_set

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
    # Routability (validated with FreeRouting on the 131-part system board) is
    # driven by SPREAD, not raw wirelength: the HPWL-minimal compact layout routed
    # only 76% while spreading each hierarchical sheet into its own region routed
    # 87%. So hierarchical boards seed from the region floorplan and use a strong
    # block-cohesion term to keep each sheet grouped; flat single-sheet boards
    # keep the force-directed seed (which a rigid floorplan was shown to hurt).
    hierarchical = floorplan_mod.is_hierarchical(board)
    use_floorplan = strategy == "floorplan" or (strategy == "auto" and hierarchical)
    if use_floorplan:
        floorplan_mod.floorplan(board, rng, margin=margin)
    else:
        forcedirected.seed_positions(board, rng, margin=margin)
        forcedirected.run(board, rng, iters=fd_iters, margin=margin)
    _report("seed", 0.15)

    if connectors:
        edge_mod.assign_edges(board, connectors, margin=margin)

    if sa_steps:
        # anneal reports 0..1 over its loop; map it onto the 0.15..0.92 band.
        anneal.anneal(board, seed=seed, steps=sa_steps, margin=margin,
                      channel_scale=channel_scale,
                      cohesion_scale=2.5 if use_floorplan else 1.0,
                      progress=lambda f: _report("anneal", 0.15 + 0.77 * f))
    remaining = legal_mod.legalize(board, grid=grid, margin=margin)
    _report("legalize", 0.96)

    after = metrics.summary(board)
    _report("done", 1.0)
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
