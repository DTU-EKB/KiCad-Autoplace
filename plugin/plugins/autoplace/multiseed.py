"""Generate several placement candidates from distinct RNG seeds.

Pure-Python (no pcbnew): given one loaded ``Board`` model, run the placement
engine once per seed on an independent deep copy and yield a compact candidate
dict per seed -- board geometry plus the metrics the gallery shows. ``cli.py
place-multi`` is the thin pcbnew/stdout wrapper around this; the app renders the
yielded dicts as preview cards and commits the chosen seed via the normal
single-seed ``place`` path (deterministic, so preview == saved).
"""
from __future__ import annotations

import copy
from typing import Iterator

from . import engine, metrics, serialize
from .model import Board


def run_candidates(model: Board, count: int, *, strategy: str = "auto",
                   connectors: list[str] | None = None,
                   margin: float = 0.8, track: float = 1.0) -> Iterator[dict]:
    """Yield one candidate dict for each seed in ``0..count-1``.

    Each seed places a fresh deep copy of ``model`` (``engine.place`` mutates
    positions in place). ``margin`` is the placement spacing (the fabrication
    clearance) so previews match the committed board. A seed whose placement
    raises yields a ``candidate-error`` entry and does not abort the rest.
    """
    for seed in range(count):
        board = copy.deepcopy(model)
        try:
            report = engine.place(board, seed=seed, strategy=strategy,
                                  connectors=connectors, margin=margin)
        except Exception as exc:                      # one bad seed must not kill the gallery
            yield {"type": "candidate-error", "seed": seed, "error": str(exc)}
            continue
        after = report["after"]
        yield {
            "type": "candidate",
            "seed": seed,
            "hpwl_mm": after["hpwl_mm"],
            "crossings": after["crossings"],
            "overlaps": report["overlaps_remaining"],
            "hpwl_delta_pct": report["hpwl_delta_pct"],
            "sheet_spread_score": metrics.sheet_spread_score(board),
            "pinch_fraction": metrics.pinch_fraction(board, margin, track),
            "whitespace_connectivity": metrics.whitespace_connectivity(board),
            "board": serialize.board_to_dict(board),
        }
