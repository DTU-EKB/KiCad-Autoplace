"""Deterministic candidate ranking for the multi-seed gallery (pure-Python).

Ranking lives here, NOT in ``anneal._quality`` (that split is load-bearing --
see BUILD_SPEC.md:368-379). Two levels:

1. ``pre_rank``  -- order ALL candidates by cheap proxies (no routing).
2. ``final_order`` -- once the top finalists are routed, float them to the top by
   measured routed-%, leaving the rest in pre-rank order.

**Routability first, aesthetics as tie-breakers.** This order was set by
measurement on 2026-08-13, not by preference. 76 placements across four boards
(subxo, buck_v2, c2000_feedback, motor_power) were routed for real and scored
against the wire bridges each placement forces. The previous key --
``(overlaps, sheet_spread, pinch_fraction, decap_proximity, hpwl_mm, seed)`` --
ranked at Spearman **0.01** against that outcome: no better than shuffling. It
failed for a structural reason rather than a weighting one. A lexicographic key
is decided entirely by the first term that discriminates, and ``pinch_fraction``
and ``decap_proximity`` are near-continuous, so one of them almost always
decided the whole order before ``hpwl_mm`` was ever compared -- while neither
predicts routability (rho 0.05 and **-0.18**, the latter pointing the wrong way).

Ranking on ``crossings`` and ``hpwl_mm`` instead scores rho **0.63**, and on the
same four boards it cuts the bridges the user hand-solders on the top-ranked
placement from **21 to 6**. The two are combined as *fractional ranks* rather
than raw values so that neither wins merely by having the larger numbers --
crossings is a count in the tens, HPWL is millimetres in the hundreds.

The aesthetic and electrical terms are kept, one level down: they express real
intent (tight decoupling, clean per-sheet spread) and they still separate
candidates that are equally routable. They just no longer overrule routing.

Every key element is rounded so cross-machine float noise cannot flip the order,
and ``seed`` is the final element so the order is total (no nondeterministic
ties).
"""
from __future__ import annotations


def _fractional_ranks(values: list[float]) -> list[float]:
    """Ranks with ties sharing their mean rank. Scale-free by construction."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = mean
        i = j + 1
    return out


def tiebreak_key(cand: dict) -> tuple:
    """Placement-quality tie-breakers, applied only among equally routable
    candidates. Lower is better on every element."""
    return (
        round(cand.get("sheet_spread_score", 0.0), 3),      # clean per-sheet spread
        round(cand.get("pinch_fraction", 0.0), 3),          # fewer routing pinch points
        round(cand.get("decap_proximity", 0.0) * 2) / 2,    # decap closeness, 0.5mm buckets
        cand["seed"],                                       # total order
    )


def candidate_key(cand: dict) -> tuple:
    """Per-candidate key WITHOUT population context.

    Kept for callers that need to order a single candidate against a fixed
    reference. It cannot express the crossings/HPWL rank-sum, which needs the
    whole population, so ``pre_rank`` is what the gallery uses.
    """
    return (cand["overlaps"], round(cand.get("hpwl_mm", 0.0), 2)) + tiebreak_key(cand)


def pre_rank(candidates: list[dict]) -> list[dict]:
    """All candidates, best first.

    ``overlaps`` first as a hard legality gate -- a layout with overlapping
    courtyards is not a placement, whatever else it scores. Then routability, as
    the summed fractional ranks of ``crossings`` and ``hpwl_mm``. Then the
    quality tie-breakers, then the seed.

    A candidate dict without ``crossings`` (an older caller) degrades cleanly:
    every candidate ties on that term and the order falls back to HPWL, which on
    its own still ranks at rho 0.57.
    """
    if len(candidates) < 2:
        return list(candidates)
    cross = _fractional_ranks([c.get("crossings", 0) for c in candidates])
    hpwl = _fractional_ranks([round(c.get("hpwl_mm", 0.0), 2) for c in candidates])
    routability = [cross[i] + hpwl[i] for i in range(len(candidates))]
    order = sorted(
        range(len(candidates)),
        key=lambda i: (candidates[i]["overlaps"], routability[i],
                       tiebreak_key(candidates[i])),
    )
    return [candidates[i] for i in order]


def final_order(candidates: list[dict], routed: dict) -> list[dict]:
    """Routed finalists first (by -routed_pct, then pre-rank position); the rest
    keep pre-rank order below them. ``routed`` maps seed -> routed_pct.

    A real routed percentage always outranks a prediction -- that is the whole
    point of routing the finalists -- so this layer is unchanged.
    """
    pre = pre_rank(candidates)
    position = {c["seed"]: i for i, c in enumerate(pre)}
    finalists = [c for c in pre if c["seed"] in routed]
    rest = [c for c in pre if c["seed"] not in routed]
    finalists.sort(key=lambda c: (-routed[c["seed"]], position[c["seed"]]))
    return finalists + rest
