"""Electrical-aware structural detectors (pure-Python, no pcbnew).

These power the Phase 2 placement terms. Each is a deterministic structural query
over the Phase-1-enriched model. They use ``nets.classify_net`` for role detection,
NOT ``metrics._is_power`` -- the two are intentionally separate (classify_net tags a
net's role; _is_power drives HPWL exclusion). Full unification is deferred.
"""
from __future__ import annotations

import math

from . import nets


def decoupling_pairs(board) -> dict:
    """Pair each decoupling cap to the IC power pin it should hug.

    A decoupling cap = a 2-pad component whose two nets classify as one POWER and
    one GROUND. Its target = the nearest component with > 2 pads that also has a pad
    on the same POWER rail (cap rail-pad -> candidate rail-pad distance; ``ref``
    tiebreak). Caps whose rail has no such multi-pad part are skipped.

    Returns ``{cap_ref: (cap_rail_pad_idx, ic_ref, ic_rail_pad_idx)}`` on current
    positions. Deterministic; no RNG.
    """
    comps = board.components
    # POWER rail net -> [(ic_ref, pad_idx)] for pads of >2-pad parts on that rail
    rail_ic_pads: dict[str, list] = {}
    for ref in sorted(comps):
        c = comps[ref]
        if len(c.pads) <= 2:
            continue
        for i, p in enumerate(c.pads):
            if p.net and nets.classify_net(board, p.net) == "POWER":
                rail_ic_pads.setdefault(p.net, []).append((ref, i))

    out = {}
    for ref in sorted(comps):
        c = comps[ref]
        if len(c.pads) != 2:
            continue
        roles = [nets.classify_net(board, p.net) if p.net else "NC" for p in c.pads]
        if set(roles) != {"POWER", "GROUND"}:
            continue
        rail_idx = 0 if roles[0] == "POWER" else 1
        cands = rail_ic_pads.get(c.pads[rail_idx].net, [])
        if not cands:
            continue
        cx, cy = c.pad_world(c.pads[rail_idx])
        best = None  # (dist, ic_ref, ic_idx)
        for ic_ref, ic_idx in cands:
            ic = comps[ic_ref]
            ix, iy = ic.pad_world(ic.pads[ic_idx])
            d = math.hypot(ix - cx, iy - cy)
            if best is None or d < best[0] or (d == best[0] and ic_ref < best[1]):
                best = (d, ic_ref, ic_idx)
        out[ref] = (rail_idx, best[1], best[2])
    return out
