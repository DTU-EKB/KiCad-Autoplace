"""Generate several placement candidates from distinct RNG seeds.

Pure-Python (no pcbnew): given one loaded ``Board`` model, run the placement
engine once per seed on an independent deep copy and yield a compact candidate
dict per seed -- board geometry plus the metrics the gallery shows. ``cli.py
place-multi`` is the thin pcbnew/stdout wrapper around this; the app renders the
yielded dicts as preview cards and commits the chosen seed via the normal
single-seed ``place`` path (deterministic, so preview == saved).

``parallel=True`` fans the seeds out over worker processes: each candidate is a
pure function of (model, seed) -- placement is hashseed-independent and shares
no state -- so the results are byte-identical to the serial path, only faster
(the gallery's wall time drops from sum to max of the placements). Candidates
then arrive in completion order; consumers key on ``seed``, not order. Any pool
failure falls back to placing the remaining seeds serially.
"""
from __future__ import annotations

import copy
import os
from typing import Iterator

from . import engine, metrics, serialize
from .model import Board


def _place_one(model: Board, seed: int, strategy: str, connectors,
               margin: float, track: float, aesthetic: bool) -> dict:
    """Place one seed on its own copy of ``model``; never raises.

    Module-level (not a closure) so ``ProcessPoolExecutor`` can pickle it by
    reference; a failure comes back as a ``candidate-error`` dict either way.
    """
    board = copy.deepcopy(model)
    try:
        report = engine.place(board, seed=seed, strategy=strategy,
                              connectors=connectors, margin=margin, track=track,
                              aesthetic=aesthetic)
    except Exception as exc:                      # one bad seed must not kill the gallery
        return {"type": "candidate-error", "seed": seed, "error": str(exc)}
    after = report["after"]
    return {
        "type": "candidate",
        "seed": seed,
        "hpwl_mm": after["hpwl_mm"],
        "crossings": after["crossings"],
        "overlaps": report["overlaps_remaining"],
        "hpwl_delta_pct": report["hpwl_delta_pct"],
        "sheet_spread_score": metrics.sheet_spread_score(board),
        "pinch_fraction": metrics.pinch_fraction(board, margin, track),
        "whitespace_connectivity": metrics.whitespace_connectivity(board),
        "decap_proximity": metrics.decap_proximity(board),
        "board": serialize.board_to_dict(board),
    }


def run_candidates(model: Board, count: int, *, strategy: str = "auto",
                   connectors: list[str] | None = None,
                   margin: float = 0.8, track: float = 1.0,
                   aesthetic: bool = True, parallel: bool = False) -> Iterator[dict]:
    """Yield one candidate dict for each seed in ``0..count-1``.

    Each seed places a fresh deep copy of ``model`` (``engine.place`` mutates
    positions in place). ``margin`` is the placement spacing (the fabrication
    clearance) so previews match the committed board. Serial yields seeds in
    order; ``parallel=True`` yields in completion order (same dicts).
    """
    done: set[int] = set()
    if parallel and count > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            workers = min(count, max(1, (os.cpu_count() or 4) - 1))
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_place_one, model, seed, strategy, connectors,
                                    margin, track, aesthetic): seed
                        for seed in range(count)}
                for fut in as_completed(futs):
                    seed = futs[fut]
                    try:
                        cand = fut.result()
                    except Exception as exc:      # worker died (not a placement error)
                        cand = {"type": "candidate-error", "seed": seed,
                                "error": str(exc)}
                    done.add(seed)
                    yield cand
            return
        except Exception:
            # Pool could not start (frozen/sandboxed env): finish serially below,
            # skipping any seeds already yielded.
            pass
    for seed in range(count):
        if seed in done:
            continue
        yield _place_one(model, seed, strategy, connectors, margin, track, aesthetic)
