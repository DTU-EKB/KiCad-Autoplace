"""Drive a board to 100% connectivity: re-place -> grow -> bridge.

``completion.py`` decides *what* to do next; this module does it, wired to the
real annealer, the real router and a live pcbnew board. Keeping the two apart is
what makes the policy testable without FreeRouting installed.

The contract this implements, in the order the escalation is tried:

1. **re-place** -- move parts inside the outline the user gave us. Free and
   reversible, so it is always tried first and repeated while it keeps helping.
2. **grow** -- only once placement has stopped paying off, and only in bounded
   steps up to ``max_growth_mm``. The board coming back bigger is a real cost to
   the user, so it is never silent: the growth is in the result.
3. **bridge** -- close the residual connections as wire links on the component
   side (``routing.route_stage2_bridges``). Single-sided boards are rarely
   planar, so this is the step that actually reaches 100%; the bridges are
   counted and reported, never hidden.

Anything still unrouted after all three is reported as such. The one thing this
must never do is present a partial board as finished.
"""
from __future__ import annotations

import copy
import os

from . import completion


def complete(board, pcb, *, jar, work_pcb, passes=20, seed=0, sides=1,
             place_budget=3, max_growth_mm=completion.DEFAULT_MAX_GROWTH_MM,
             growth_step_mm=completion.DEFAULT_GROWTH_STEP_MM,
             allow_growth=True, allow_bridges=True, place_margin=0.85,
             progress=None) -> dict:
    """Escalate until the board is fully connected or the ladder is exhausted.

    Mutates ``board`` to the winning placement. Returns the summary from
    ``completion.summarise`` plus the path of the finished board.
    """
    import pcbnew

    from . import anneal as anneal_mod
    from . import congestion as cong_mod
    from . import kicad_io
    from . import outline as outline_mod
    from . import routing
    from . import unrouted as unrouted_mod

    history: list[completion.Attempt] = []
    state = completion.CompletionState(pct=0.0, missing=1)
    best_model = copy.deepcopy(board)
    best_pcb_path = None
    growth_total = 0.0

    def emit(stage, msg):
        if progress is not None:
            progress(stage, msg)

    totals = {"n": 1}

    def route(model):
        """Place `model` on the live board, route it, return (pct, missing, path, field)."""
        kicad_io.apply_to_board(model, pcb)
        pcbnew.SaveBoard(work_pcb, pcb)
        r = routing.route_once(work_pcb, jar, passes, sides=sides)
        totals["n"] = max(1, r["total"])
        field = cong_mod.parse(r["ses_path"], model, cell_mm=5.0)
        # DRC is the authority: it refills zones before checking, and it can name
        # WHICH connections are missing, which the escalation needs. The ratsnest
        # is the fallback when kicad-cli is absent.
        #
        # A previous version of this comment claimed DRC "routinely finds fewer
        # missing connections than the ratsnest", and the completion metric was
        # switched over on that basis. That was not DRC being smarter -- it was
        # ``unrouted.parse_drc_report`` silently dropping every gap whose endpoint
        # was a Track or a Zone rather than a pad, which is most of them on a
        # routed board. Re-measured across 16 placements once the parser was
        # fixed, the two agree EXACTLY (4-13 missing each). Until that fix the
        # tool reported boards with up to 13 missing connections as 100% complete.
        # So: if these two ever diverge again, something is broken -- do not
        # assume the smaller number is the smarter one.
        missing = r["unrouted"]
        ratsnest = r["unrouted"]
        try:
            missing = len(unrouted_mod.analyse(r["routed_pcb"])["missing"])
        except RuntimeError:
            pass
        if missing != ratsnest:
            emit("route", f"NOTE: DRC says {missing} missing, ratsnest says "
                          f"{ratsnest} -- these should agree")
        pct = 100.0 * (totals["n"] - missing) / totals["n"]
        return pct, missing, r["routed_pcb"], field

    def pct_of(missing):
        """Completion for a board we did not re-route through route_once."""
        return 100.0 * (totals["n"] - missing) / totals["n"]

    pct, missing, path, field = route(best_model)
    best_pct, best_pcb_path = pct, path
    history.append(completion.Attempt("route", pct, missing))
    emit("route", f"{pct:.1f}% ({missing} missing)")
    state = completion.CompletionState(pct=pct, missing=missing, history=history)

    while True:
        action = completion.next_action(
            state, place_budget=place_budget, max_growth_mm=max_growth_mm,
            growth_step_mm=growth_step_mm, allow_growth=allow_growth,
            allow_bridges=allow_bridges)

        if action in ("done", "stop"):
            break

        if action == "place":
            cand = copy.deepcopy(best_model)
            anneal_mod.anneal(cand, seed=seed + state.place_attempts + 1,
                              margin=place_margin, congestion=field)
            pct, missing, path, cfield = route(cand)
            improved = missing < state.missing
            if improved:
                best_model, best_pct, best_pcb_path, field = cand, pct, path, cfield
            history.append(completion.Attempt("place", pct, missing))
            emit("place", f"{pct:.1f}% ({missing} missing)"
                 + ("" if improved else " -- no better, escalating"))
            state = completion.CompletionState(
                pct=best_pct, missing=min(missing, state.missing),
                growth_mm=growth_total,
                place_attempts=state.place_attempts + 1,
                grow_attempts=state.grow_attempts,
                improved_last=improved, history=history)
            continue

        if action == "grow":
            step = completion.growth_needed(state.missing, step_mm=growth_step_mm)
            info = outline_mod.grow_board(pcb, step)
            growth_total += step
            pcbnew.SaveBoard(work_pcb, pcb)
            emit("grow", f"outline {info['size_before']} -> {info['size_after']} mm")
            cand = copy.deepcopy(best_model)
            anneal_mod.anneal(cand, seed=seed + 101, margin=place_margin,
                              congestion=field)
            pct, missing, path, cfield = route(cand)
            improved = missing < state.missing
            if improved:
                best_model, best_pct, best_pcb_path, field = cand, pct, path, cfield
            if not improved:
                # Give the millimetres back: a larger board is a real cost and we
                # did not buy anything with it.
                outline_mod.grow_board(pcb, -step)
                growth_total -= step
                pcbnew.SaveBoard(work_pcb, pcb)
                emit("grow", f"{pct:.1f}% ({missing} missing) -- no better, reverted")
            else:
                emit("grow", f"{pct:.1f}% ({missing} missing)")
            history.append(completion.Attempt("grow", pct, missing,
                                              growth_mm=growth_total))
            state = completion.CompletionState(
                pct=best_pct, missing=min(missing, state.missing),
                growth_mm=growth_total, place_attempts=place_budget,
                grow_attempts=state.grow_attempts + 1, improved_last=improved,
                history=history)
            continue

        if action == "bridge":
            r = routing.route_stage2_bridges(best_pcb_path, jar, passes)
            history.append(completion.Attempt(
                "bridge", pct_of(r["unrouted"]), r["unrouted"],
                bridges=r["bridges"], growth_mm=growth_total,
                note=f"{r['vias']} vias"))
            emit("bridge", f"{r['bridges']} bridges, {r['vias']} vias, "
                           f"{r['unrouted']} still missing")
            best_pcb_path = r["routed_pcb"]
            if r["unrouted"] > 0 and allow_bridges:
                # Locking stage-1 copper can wall off the last few connections:
                # the router cannot rip up a track that is in the way. Final
                # fallback -- route the whole board unrestricted on both layers.
                # It costs more top copper, so it is only ever a last resort, and
                # the extra bridges are reported like any other.
                r2 = routing.route_once(work_pcb, jar, passes, sides=2)
                if r2["unrouted"] < r["unrouted"]:
                    best_pcb_path = r2["routed_pcb"]
                    history.append(completion.Attempt(
                        "bridge", r2["pct"], r2["unrouted"],
                        bridges=r["bridges"], growth_mm=growth_total,
                        note="unrestricted two-layer fallback"))
                    emit("bridge", f"unrestricted fallback: {r2['pct']:.1f}% "
                                   f"({r2['unrouted']} missing)")
            state = completion.CompletionState(
                pct=history[-1].pct, missing=r["unrouted"], bridges=r["bridges"],
                growth_mm=growth_total, place_attempts=place_budget,
                improved_last=False, history=history)
            break

    board.components = best_model.components
    out = completion.summarise(history)

    # Report the bridges against the floor the netlist forces. Bridges above
    # that floor are the placer's doing, not the circuit's, and until this was
    # measurable the tool had no way to tell the two apart -- so every bridge
    # looked equally unavoidable and there was nothing to aim at.
    from . import planarity
    try:
        forced = planarity.forced_bridges(board)
        out["forced_bridges"] = forced["bridges"]
        out["single_sided_possible"] = forced["planar"]
        out["avoidable_bridges"] = max(0, out.get("bridges", 0) - forced["bridges"])
    except Exception:                       # never let analysis break a finished board
        out["forced_bridges"] = None

    out["routed_pcb"] = best_pcb_path
    out["growth_mm"] = growth_total
    out["board_size"] = None
    if best_pcb_path and os.path.exists(best_pcb_path):
        out["board_size"] = _board_size(best_pcb_path)
    return out


def _board_size(pcb_path):
    import pcbnew
    b = pcbnew.LoadBoard(pcb_path)
    bb = b.GetBoardEdgesBoundingBox()
    return (round(pcbnew.ToMM(bb.GetWidth()), 2), round(pcbnew.ToMM(bb.GetHeight()), 2))
