"""Route-driven refinement: place -> route -> re-anneal congested spots -> repeat.

``keep_best_loop`` is the pure policy (keep-best + patience), testable with
stubbed callables. ``refine`` wires it to the real router, congestion parser, and
annealer (needs pcbnew + FreeRouting; exercised by cli.py refine).
"""
from __future__ import annotations

import copy


def keep_best_loop(initial, route_eval, step, *, budget, patience, margin,
                   progress=None):
    """Iterate: route the best, refine, re-route, keep only real improvements.

    route_eval(model) -> (pct, field);  step(model, field) -> candidate model.
    Returns {"best", "best_pct", "iterations", "history"}.
    """
    best = initial
    best_pct, field = route_eval(best)
    history = [best_pct]
    if progress is not None:
        progress(0, best_pct, best_pct)
    fails = 0
    it = 0
    while it < budget and best_pct < 100.0 and fails < patience:
        it += 1
        cand = step(best, field)
        pct, cfield = route_eval(cand)
        history.append(pct)
        if pct > best_pct + margin:
            best, best_pct, field, fails = cand, pct, cfield, 0
        else:
            fails += 1
        if progress is not None:
            progress(it, pct, best_pct)
    return {"best": best, "best_pct": best_pct, "iterations": it,
            "history": history}


def refine(board, pcb, *, jar, work_pcb, passes=20, seed=0, budget=8, patience=3,
           margin_conns=1, cell_mm=5.0, progress=None):
    """pcbnew-wired loop. Mutates `board` to the best placement found.

    Each evaluation writes the candidate placement to ``work_pcb`` and routes
    that file with a FRESH load (``routing.route_once`` reloads it) -- a board
    cannot be reused across routes after ImportSpecctraSES on KiCad 10. Ensure
    ``work_pcb``'s ``.kicad_pro`` exists so net-class widths are correct.

    Inlines the keep-best/patience policy (rather than calling keep_best_loop)
    so the connection-count margin can be derived from the first route's `total`
    and the initial placement is routed only once.
    """
    import pcbnew

    from . import anneal as anneal_mod
    from . import congestion as cong_mod
    from . import kicad_io
    from . import routing

    state = {"total": 1}

    def route_eval(model):
        kicad_io.apply_to_board(model, pcb)
        pcbnew.SaveBoard(work_pcb, pcb)
        r = routing.route_once(work_pcb, jar, passes)
        state["total"] = r["total"]
        field = cong_mod.parse(r["ses_path"], model, cell_mm=cell_mm)
        return r["pct"], field

    def step(model, field):
        cand = copy.deepcopy(model)
        anneal_mod.anneal(cand, seed=seed, margin=0.8, congestion=field)
        return cand

    best = copy.deepcopy(board)
    best_pct, field = route_eval(best)
    margin_pct = 100.0 * margin_conns / max(1, state["total"])
    history = [best_pct]
    if progress is not None:
        progress(0, best_pct, best_pct)
    fails = 0
    it = 0
    while it < budget and best_pct < 100.0 and fails < patience:
        it += 1
        cand = step(best, field)
        pct, cfield = route_eval(cand)
        history.append(pct)
        if pct > best_pct + margin_pct:
            best, best_pct, field, fails = cand, pct, cfield, 0
        else:
            fails += 1
        if progress is not None:
            progress(it, pct, best_pct)

    board.components = best.components       # write the winner back to the caller
    return {"best": board, "best_pct": best_pct, "iterations": it,
            "history": history, "total": state["total"]}
