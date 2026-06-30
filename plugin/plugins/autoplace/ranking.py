"""Deterministic candidate ranking for the multi-seed gallery (pure-Python).

Ranking lives here, NOT in ``anneal._quality`` (that split is load-bearing --
see BUILD_SPEC.md:368-379). Two levels:

1. ``pre_rank``  -- order ALL candidates by cheap proxies (no routing).
2. ``final_order`` -- once the top finalists are routed, float them to the top by
   measured routed-%, leaving the rest in pre-rank order.

Every key element is rounded so cross-machine float noise cannot flip the order,
and ``seed`` is the final element so the order is total (no nondeterministic
ties).
"""
from __future__ import annotations


def candidate_key(cand: dict) -> tuple:
    """Lexicographic pre-rank key; lower is better on every component."""
    return (
        cand["overlaps"],                          # legal layouts win outright
        round(cand["sheet_spread_score"], 3),      # clean per-sheet spread
        round(cand["pinch_fraction"], 3),          # fewer routing pinch points
        round(cand.get("decap_proximity", 0.0) * 2) / 2,  # decap closeness, 0.5mm buckets
        round(cand["hpwl_mm"], 2),                 # wirelength is the final metric
        cand["seed"],                              # total order
    )


def pre_rank(candidates: list[dict]) -> list[dict]:
    """All candidates, best first, by ``candidate_key``."""
    return sorted(candidates, key=candidate_key)


def final_order(candidates: list[dict], routed: dict) -> list[dict]:
    """Routed finalists first (by -routed_pct, then pre-rank key); the rest keep
    pre-rank order below them. ``routed`` maps seed -> routed_pct."""
    pre = pre_rank(candidates)
    finalists = [c for c in pre if c["seed"] in routed]
    rest = [c for c in pre if c["seed"] not in routed]
    finalists.sort(key=lambda c: (-routed[c["seed"]], candidate_key(c)))
    return finalists + rest
