"""Top-level placement engine.

M2 pipeline: seed -> force-directed global -> legalize -> snap.
(M4 will insert block floorplanning before, and simulated-annealing refinement
plus rotation moves after.)

``place`` mutates the board in place and returns a report dict comparing the
before/after metrics -- the numbers that go into ``run.json`` and the HTML
report (spec section 8).
"""
from __future__ import annotations

import random

from . import forcedirected, legalize as legal_mod, metrics
from .model import Board


def place(board: Board, *, seed: int = 0, grid: float = 0.5,
          margin: float = 0.8, iters: int = 400) -> dict:
    before = metrics.summary(board)

    rng = random.Random(seed)
    forcedirected.seed_positions(board, rng, margin=margin)
    forcedirected.run(board, rng, iters=iters, margin=margin)
    remaining = legal_mod.legalize(board, grid=grid, margin=margin)

    after = metrics.summary(board)
    return {
        "before": before,
        "after": after,
        "hpwl_delta_pct": _pct(before["hpwl_mm"], after["hpwl_mm"]),
        "crossings_delta": after["crossings"] - before["crossings"],
        "overlaps_remaining": len(remaining),
        "seed": seed,
    }


def _pct(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return round((b - a) / a * 100.0, 1)
