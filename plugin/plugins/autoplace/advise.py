"""Single-sided or double-sided? Decide, explain, and never skip the attempt.

Single-sided is worth wanting: one layer to mill, no vias, no drilling through
to a second side. But plenty of boards genuinely cannot have it, and forcing the
placer to chase it on a 60-part board wastes a lot of time to arrive at a worse
layout than a two-layer route would have produced in one pass.

So the tool does three separate things and keeps them separate:

1. **Decides what is possible.** ``planarity.forced_bridges`` answers exactly,
   from the netlist alone, whether single-sided is achievable and how many wire
   bridges the circuit forces. This is a *proof*, holds for every placement, and
   costs milliseconds.
2. **Always attempts single-sided anyway.** Even when the advice says a board is
   probably too dense, the attempt is cheap relative to being wrong, and the
   result is real information rather than a prediction. ``try_single_sided_first``
   is therefore always True.
3. **Recommends, then defers.** After the attempt the user sees what was actually
   achieved -- bridges used, bridges forced, connections left -- and chooses.

The line between fact and guess is deliberate and load-bearing:

* ``single_sided_possible`` and ``forced_bridges`` are **exact**.
* ``difficulty`` is **advisory** -- a heuristic about whether *this placer, today*
  is likely to find a layout that exists. It is fitted to a small sample and it
  moves whenever the placer improves, so it is labelled ``confidence =
  "estimated"`` and it never suppresses the attempt.

Reporting a forced bridge as a placement failure is the specific confusion this
module exists to prevent. A board that forces two bridges has not been placed
badly; the circuit demands them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import planarity
from .model import Board

# Part-count bands for the difficulty estimate.
#
# Fitted to the overnight routed study, not chosen for roundness. Boards that
# were attempted and fully closed single-sided: 10, 10, 11, 11, 12, 14, 17, 20
# parts. Boards attempted that did NOT close: 31, 38, 47, 58. Nothing was
# measured between 21 and 30, which is exactly where the boundary sits, so
# EASY_PARTS is placed at the top of the measured-good range and HARD_PARTS at
# the bottom of the measured-bad one, leaving the untested gap honestly marked
# "moderate" rather than guessed at.
#
# Density was tried first and does NOT separate the two groups (drive_circuit
# closes at 1.14 pads/cm^2 while boards at half that do not), so part count is
# used on its own rather than dressed up with a pad-density term that the data
# does not support.
EASY_PARTS = 20
HARD_PARTS = 31

# Default tolerance for hand-soldered wires on a board the user wanted
# single-sided. Above this, a two-layer route is usually the better trade -- but
# it is the user's call, so it is a parameter, not a rule.
DEFAULT_MAX_BRIDGES = 4


@dataclass
class Advice:
    """What is possible (exact) and what is likely (estimated), kept apart."""
    single_sided_possible: bool     # EXACT: the netlist admits a one-layer route
    forced_bridges: int             # EXACT: bridges no placement can avoid
    difficulty: str                 # ESTIMATED: "easy" | "moderate" | "hard"
    confidence: str                 # "measured" for the exact part, else "estimated"
    recommend: str                  # "single" | "single-with-bridges" | "double"
    reasons: list[str] = field(default_factory=list)
    parts: int = 0
    nets: int = 0
    max_bridges: int = DEFAULT_MAX_BRIDGES

    # Always true. Kept as a field rather than implied, because the contract
    # that the tool tries before it judges is the whole point of the module.
    try_single_sided_first: bool = True


def _difficulty(parts: int) -> str:
    if parts <= EASY_PARTS:
        return "easy"
    if parts >= HARD_PARTS:
        return "hard"
    return "moderate"


def assess(board: Board, planes: set[str] | None = None,
           max_bridges: int = DEFAULT_MAX_BRIDGES) -> Advice:
    """Look at a board before placing it and say what to aim for.

    ``max_bridges`` is the user's tolerance for hand-soldered wires. It only
    ever moves the *recommendation*; it never changes what is possible, and it
    never stops the single-sided attempt.
    """
    f = planarity.forced_bridges(board, planes)
    parts, forced = f["components"], f["bridges"]
    diff = _difficulty(parts)
    reasons: list[str] = []

    if f["planar"]:
        reasons.append(
            f"The netlist is planar: a single-sided route with zero wire "
            f"bridges exists for some placement ({parts} parts, {f['nets']} nets).")
    else:
        reasons.append(
            f"The netlist is not planar: at least {forced} wire bridge"
            f"{'s' if forced != 1 else ''} are forced by the circuit itself, "
            f"whatever the placement.")

    if diff == "easy":
        reasons.append(
            f"{parts} parts -- in the range where every board measured so far "
            f"routed single-sided.")
    elif diff == "hard":
        reasons.append(
            f"{parts} parts -- above the size where no board measured so far "
            f"has closed single-sided. Two layers is likely the better trade.")
    else:
        reasons.append(
            f"{parts} parts -- between the sizes that were measured to work and "
            f"to fail, so the single-sided attempt is genuinely worth running.")

    # Order matters. Size is checked before bridge tolerance, so a large board
    # is not recommended "single-with-bridges" by the same advice that just said
    # two layers is the better trade -- on a board that big the bridge count is
    # not the binding problem, finding any single-sided route at all is.
    if forced > max_bridges:
        recommend = "double"
        reasons.append(
            f"{forced} forced bridges exceeds your tolerance of {max_bridges}.")
    elif diff == "hard":
        recommend = "double"
        if forced:
            reasons.append(
                f"The {forced} forced bridge{'s are' if forced != 1 else ' is'} "
                f"within your tolerance, but size is the deciding factor here.")
    elif forced > 0:
        recommend = "single-with-bridges"
        reasons.append(
            f"{forced} forced bridge{'s' if forced != 1 else ''} is within your "
            f"tolerance of {max_bridges}.")
    else:
        recommend = "single"

    reasons.append(
        "Single-sided will be attempted regardless; this is advice, not a gate.")
    return Advice(
        single_sided_possible=f["planar"], forced_bridges=forced,
        difficulty=diff, confidence="estimated", recommend=recommend,
        reasons=reasons, parts=parts, nets=f["nets"], max_bridges=max_bridges,
    )


def verdict(advice: Advice, *, closed: bool, bridges: int,
            missing: int = 0) -> dict:
    """Judge the single-sided attempt on what it actually achieved.

    ``closed`` means every connection was made. ``bridges`` is what it cost.
    The comparison that matters is against ``advice.forced_bridges``: bridges at
    the floor are the circuit's doing, bridges above it are the placer's.
    """
    floor = advice.forced_bridges
    avoidable = max(0, bridges - floor)
    reasons: list[str] = []

    if not closed:
        reasons.append(
            f"The single-sided attempt did not connect everything: {missing} "
            f"connection{'s' if missing != 1 else ''} still missing.")
        if advice.single_sided_possible:
            reasons.append(
                "A zero-bridge single-sided layout does exist for this netlist, "
                "so this is a placement or routing shortfall, not a limit of the "
                "circuit -- but two layers will finish the board now.")
        return {"keep_single_sided": False, "recommend": "double",
                "at_the_floor": False, "avoidable_bridges": avoidable,
                "forced_bridges": floor, "reasons": reasons}

    if bridges > advice.max_bridges:
        reasons.append(
            f"It closed, but needed {bridges} wire bridges -- more than the "
            f"{advice.max_bridges} you allowed.")
        if avoidable:
            reasons.append(
                f"{avoidable} of those are avoidable in principle: the circuit "
                f"only forces {floor}.")
        return {"keep_single_sided": False, "recommend": "double",
                "at_the_floor": False, "avoidable_bridges": avoidable,
                "forced_bridges": floor, "reasons": reasons}

    if avoidable == 0:
        reasons.append(
            f"Closed single-sided with {bridges} wire bridge"
            f"{'s' if bridges != 1 else ''}, which is the minimum this circuit "
            f"allows. No placement can do better.")
    else:
        reasons.append(
            f"Closed single-sided with {bridges} wire bridge"
            f"{'s' if bridges != 1 else ''}, of which {avoidable} "
            f"{'are' if avoidable != 1 else 'is'} avoidable -- the circuit only "
            f"forces {floor}.")
    return {"keep_single_sided": True, "recommend": "single",
            "at_the_floor": avoidable == 0, "avoidable_bridges": avoidable,
            "forced_bridges": floor, "reasons": reasons}
