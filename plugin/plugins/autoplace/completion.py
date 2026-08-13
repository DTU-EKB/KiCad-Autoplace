"""Escalation policy: what to do when a board does not route to 100%.

Today the pipeline has exactly one response to an incomplete route -- re-anneal
and try again (``refine.refine``) -- and one outcome when that fails: hand back a
board with unrouted connections and let the user finish by hand. A senior
engineer does not stop there. They escalate, in a deliberate order, cheapest and
least invasive first:

  1. **re-place**        keep the board as specified, move parts
  2. **grow the board**  only if placement cannot be made routable inside the
                         given outline -- and only as far as needed
  3. **bridge**          accept the residual crossings and build them as wire
                         bridges / jumpers on the component side

That order matters. Growing the board before exhausting placement wastes stock
and can violate an enclosure the user has already committed to; bridging before
trying to grow adds hand-soldered wires that a slightly larger board would not
have needed. Single-sided boards essentially always need *some* bridges -- an
arbitrary netlist is rarely planar -- so the goal is not "zero bridges", it is
**100% connectivity with as few bridges as possible, every one of them
deliberate and reported**.

This module is the pure policy: it decides the next action from the current
state. The pcbnew/FreeRouting wiring lives in the caller, so the decision logic
stays unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A board is only allowed to grow this far past the outline the user gave us,
# and only in whole steps, so "it needed 2 mm" never silently becomes "it took
# whatever it liked".
DEFAULT_GROWTH_STEP_MM = 5.0
DEFAULT_MAX_GROWTH_MM = 20.0


@dataclass
class Attempt:
    """One route evaluation in the ladder."""
    strategy: str                 # "place" | "grow" | "bridge"
    pct: float
    missing: int
    bridges: int = 0
    growth_mm: float = 0.0
    note: str = ""


@dataclass
class CompletionState:
    """Everything the policy needs to choose the next move."""
    pct: float
    missing: int
    bridges: int = 0
    growth_mm: float = 0.0
    place_attempts: int = 0
    grow_attempts: int = 0
    improved_last: bool = True
    history: list[Attempt] = field(default_factory=list)


def next_action(state: CompletionState, *, place_budget: int = 3,
                max_growth_mm: float = DEFAULT_MAX_GROWTH_MM,
                growth_step_mm: float = DEFAULT_GROWTH_STEP_MM,
                allow_growth: bool = True,
                allow_bridges: bool = True) -> str:
    """Return the next escalation step: 'done' | 'place' | 'grow' | 'bridge' | 'stop'.

    'stop' means the ladder is exhausted with connections still missing -- the
    caller must report that honestly rather than presenting a partial board as
    finished.
    """
    if state.missing <= 0:
        return "done"
    # Keep re-placing while it is still buying improvements and we have budget:
    # placement is free, reversible, and costs the user nothing.
    if state.place_attempts < place_budget and state.improved_last:
        return "place"
    # Growing keeps the board single-sided and bridge-free, so prefer it over
    # bridges -- but only within the cap the caller allowed.
    if allow_growth and state.growth_mm + growth_step_mm <= max_growth_mm:
        return "grow"
    # Residual crossings are genuinely non-planar: build them as bridges.
    if allow_bridges:
        return "bridge"
    return "stop"


def growth_needed(missing: int, *, step_mm: float = DEFAULT_GROWTH_STEP_MM) -> float:
    """How much to grow for this many stubborn connections.

    Deliberately coarse: one step for a handful of failures, two for a lot. A
    board that is 30 connections short is not going to be rescued by 5 mm.
    """
    if missing <= 0:
        return 0.0
    return step_mm if missing <= 8 else 2 * step_mm


def summarise(history: list[Attempt]) -> dict:
    """Report the ladder honestly: where it ended and what it cost."""
    if not history:
        return {"pct": 0.0, "missing": 0, "bridges": 0, "growth_mm": 0.0,
                "complete": False, "steps": []}
    last = history[-1]
    return {
        "pct": last.pct,
        "missing": last.missing,
        "bridges": last.bridges,
        "growth_mm": last.growth_mm,
        "complete": last.missing == 0,
        "steps": [f"{a.strategy}: {a.pct:.1f}% ({a.missing} missing"
                  + (f", {a.bridges} bridges" if a.bridges else "")
                  + (f", +{a.growth_mm:g}mm" if a.growth_mm else "") + ")"
                  for a in history],
    }
