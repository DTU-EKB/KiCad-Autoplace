"""Feasibility with the pads put back in: what a footprint costs before routing.

``planarity.forced_bridges`` contracts every component to a single point. That
is exact about the netlist and silent about geometry, and it means the census
believes copper can walk straight through a part. Two consequences, and this
module supplies both readings.

**A footprint can be illegal on its own.** A 2.54 mm pin header with 2.0 mm
lands leaves 0.54 mm of copper between pins against a 0.85 mm CNC clearance, so
every adjacent pin pair is a DRC violation -- 28 of them on one measured board
whose routing was otherwise perfect. The stock KiCad header is not much better:
1.7 mm lands at 2.54 mm pitch leave 0.840 mm, which misses the same clearance by
0.01 mm and fails just as hard. Neither the placer nor the router can do
anything about either; only a different footprint can. :func:`board_clashes`
finds them before a run starts, which is why this belongs with the pre-run
checks rather than in the placement loop.

**A component is a ring of pads, not a point.** ``padblock`` already decides
which pad-to-pad gaps no track fits through -- on the CNC profile a 1.0 mm track
between 0.85 mm clearances needs 2.7 mm of clear copper, so a 2.54 mm pin row is
a solid wall while the 7.62 mm corridor down the middle of a DIP is a road that
takes two tracks. :func:`escape_graph` builds the planarity graph with a node
per *pad* and those walls as edges, so a net landing on pin 3 of a header has to
leave on the side it arrived instead of reappearing on the far side of the row.

**Why this is a bracket and not a correction.** Splitting a part into pads
changes the model in both directions at once, and it is worth being blunt about
that:

* it *adds* constraints, because a wall of four or more pads restricts the
  cyclic order its nets may leave in -- a four-pin header can present N1,N2,N3,N4
  and N1,N2,N4,N3 but never N1,N3,N2,N4, while the contracted point vertex
  accepts all of them;
* it *removes* constraints, because two pads with an open gap between them are
  genuinely independent terminals. A 10.16 mm resistor has 8 mm of clear copper
  between its lands; a track walks straight through, and the point model's link
  between the two nets it joins is an artefact of the contraction.

So neither model dominates the other, and ``barriers`` selects which way to
lean: ``"blocked"`` is the truthful barrier set from ``padblock`` (optimistic
about a part's rigidity), ``"solid"`` walls every adjacent pad pair and so
treats a part as an obstacle you cannot route through at all (pessimistic, and
by construction it has the point model as a minor, so it can never report an
easier board than the point model does). ``"placed"`` is ``"blocked"`` plus the
gaps *between* neighbouring parts, which is the only reading that depends on
where anything sits. Run them side by side; that comparison is the point.

Pure Python, no ``pcbnew`` and no numpy, so it stays unit-testable. Real land
sizes are not in ``model.Pad`` -- ``kicad_io`` captures what the placer may move,
not what the footprint editor drew -- so Part A takes them as an optional
``lands`` mapping and falls back to ``padblock``'s nominal round land.
Deterministic throughout: every collection is sorted and the search budget
counts planarity probes rather than seconds, because a wall-clock cut-off would
give a different bridge count on a busy machine and this number goes in a table.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import padblock, planarity
from .model import Board, Component

# Track width handed to ``padblock`` when every adjacent pad pair is meant to
# come back as blocked (``barriers="solid"``). Any figure wider than a board
# does it: ``component_blocked_pairs`` reports a pair when the clear gap is
# under ``track + 2 * clearance``, so a metre-wide track with zero clearance and
# zero land diameter reduces the test to plain Gabriel adjacency. Using the same
# entry point rather than reaching into padblock's internals keeps one
# definition of "which pads are neighbours" in the codebase.
SOLID_TRACK_MM = 1e6

# Zero-padding width for a pad index inside a node label. Node labels are
# compared and sorted as plain tuples of strings -- planarity's ``_rank`` needs a
# total order across every label it sees -- and unpadded indices would sort pad
# 10 before pad 2. Four digits covers any real footprint (the widest part across
# these 15 boards is a DIP-14) and keeps the label readable in a report.
_IDX_WIDTH = 4

# Half-extents below this (mm) are treated as this instead, so a zero-size pad
# (KiCad allows one on an NPTH mounting hole) yields a finite reach rather than
# a division by zero. 1e-9 mm is a picometre: far below any real land, far above
# float64 round-off on board-sized coordinates.
_EPS_MM = 1e-9


# --------------------------------------------------------------------------
# Part A: how much copper is actually between two lands
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Land:
    """One pad's copper land as half-extents about its centre, in millimetres.

    ``oval`` says which shape the half-extents describe: an ellipse (KiCad's
    circle and oval pads) or a rectangle (rectangle, rounded rectangle,
    chamfered rectangle, trapezoid). A rounded rectangle is measured as the
    sharp one, which understates its gap by the corner radius -- conservative,
    and the alternative is carrying a corner radius through every comparison to
    move a diagonal gap by a tenth of a millimetre.

    The half-extents are in *board* axes, not the pad's own: a pad rotated 90
    degrees has them swapped by whoever built the mapping. That keeps this
    module free of orientation bookkeeping, which ``model.Component`` already
    does for pad positions -- and it puts one obligation on the caller, which is
    that a ``lands`` mapping describes a footprint at one orientation. Turn the
    part and an oblong land has to be rebuilt with it. Round lands, which is the
    fallback and most THT pads, are unaffected.
    """
    hx: float
    hy: float
    oval: bool = True


# What a land is when nobody supplied one. Deliberately the same nominal round
# 2.0 mm land ``padblock`` derates gaps with, so an audit run without real
# footprint geometry agrees with the placement-time analysis instead of
# quietly disagreeing with it by a few tenths of a millimetre.
DEFAULT_LAND = Land(padblock.PAD_MM / 2.0, padblock.PAD_MM / 2.0)


def land_reach(land: Land, ux: float, uy: float) -> float:
    """How far the land extends from its centre along the unit vector (ux, uy).

    Not the projected width of the land but the point where the ray leaves it,
    which is the quantity a gap measured along the centre line consumes. For an
    ellipse that is the radius in that direction; for a rectangle it is
    whichever side the ray hits first.

    The two agree on a circle and on a square measured along a diagonal, and
    they differ exactly where they should: a 2.4 x 1.6 mm DIP land is 1.2 mm
    deep towards its neighbour along the row and 0.8 mm deep towards the row
    above, which is the difference between a legal footprint and an illegal one
    at 2.54 mm pitch.
    """
    hx = max(abs(land.hx), _EPS_MM)
    hy = max(abs(land.hy), _EPS_MM)
    ax, ay = abs(ux), abs(uy)
    if land.oval:
        return 1.0 / math.hypot(ax / hx, ay / hy)
    return min(hx / ax if ax > 0.0 else math.inf,
               hy / ay if ay > 0.0 else math.inf)


def pad_gap(ax: float, ay: float, la: Land,
            bx: float, by: float, lb: Land) -> float:
    """Clear copper (mm) between two lands, measured along their centre line.

    Negative when the lands overlap, reported rather than clamped: "these two
    pads are touching" is a different fault from "no room for a track" and needs
    a different fix.

    Exact when the lands face each other, which is the case that decides every
    real footprint -- a pin row, a DIP pin pitch, a terminal block. On a pair
    that is diagonal and unequal it is within a few hundredths of a millimetre
    of the true separation, and it generalises ``padblock.clear_gap``: two equal
    round lands give centre distance less one pad diameter, identically.

    Coincident centres have no direction to measure along; the +x axis is used
    so the answer stays finite and the lands still report as overlapping.
    """
    dx, dy = bx - ax, by - ay
    d = math.hypot(dx, dy)
    ux, uy = (dx / d, dy / d) if d > 0.0 else (1.0, 0.0)
    return d - land_reach(la, ux, uy) - land_reach(lb, -ux, -uy)


# --------------------------------------------------------------------------
# Part A: footprints that fail DRC wherever they are placed
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PadClash:
    """Two lands of one footprint with less than the process clearance between.

    Carries the offending pair by KiCad pad *number*, because that is what the
    footprint editor shows and what the fix has to be made against.
    """
    ref: str
    pad_a: str
    pad_b: str
    gap: float                  # clear copper (mm); negative when lands overlap
    required: float             # the fabrication profile's clearance (mm)
    same_net: bool
    net_a: str
    net_b: str

    @property
    def illegal(self) -> bool:
        """Would KiCad's DRC raise a clearance error for this pair?

        Same-net copper needs no clearance -- the two lands are joined anyway --
        so those pairs are reported but not counted as errors. Two *unconnected*
        pads are not the same net: KiCad checks no-net copper against everything,
        and a pair of overlapping mounting-hole lands is a real fault.
        """
        return self.gap < self.required and not self.same_net


def footprint_clashes(comp: Component, *, clearance: float,
                      lands: dict[tuple[str, int], Land] | None = None,
                      ) -> list[PadClash]:
    """Every pad pair of one footprint with under ``clearance`` mm between them.

    All pairs, not only adjacent ones, because that is what DRC checks: a row
    tight enough that pin 1 also clashes with pin 3 has to report both or the
    count understates the repair. Footprints have a handful of pads, so the
    quadratic scan costs nothing.

    Judged on world pad positions, so the part's rotation is honoured -- and
    since a rigid transform cannot change a footprint's internal geometry the
    verdict is the same at every orientation and every position, which is the
    property that makes this a *footprint* check rather than a placement one.

    ``lands`` maps ``(ref, pad index)`` to real land geometry; anything missing
    falls back to the nominal round land, because ``model.Pad`` carries no size.
    """
    pts = [comp.pad_world(p) for p in comp.pads]
    look = lands or {}
    out = []
    for i in range(len(pts)):
        la = look.get((comp.ref, i), DEFAULT_LAND)
        for j in range(i + 1, len(pts)):
            lb = look.get((comp.ref, j), DEFAULT_LAND)
            gap = pad_gap(pts[i][0], pts[i][1], la, pts[j][0], pts[j][1], lb)
            if gap >= clearance:
                continue
            na, nb = comp.pads[i].net, comp.pads[j].net
            out.append(PadClash(ref=comp.ref, pad_a=comp.pads[i].name,
                                pad_b=comp.pads[j].name, gap=gap,
                                required=clearance,
                                same_net=bool(na) and na == nb,
                                net_a=na, net_b=nb))
    return out


def board_clashes(board: Board, *, clearance: float,
                  lands: dict[tuple[str, int], Land] | None = None,
                  same_net: bool = False) -> dict[str, list[PadClash]]:
    """Footprints on this board that fail DRC on their own geometry.

    Keyed by ref in sorted order and listing only offenders, so a clean board
    comes back empty rather than as one line per part. ``same_net=True`` keeps
    the pairs KiCad would not flag, which is what you want when hunting
    overlapping lands rather than counting DRC errors.
    """
    out: dict[str, list[PadClash]] = {}
    for ref in sorted(board.components):
        found = footprint_clashes(board.components[ref], clearance=clearance,
                                  lands=lands)
        if not same_net:
            found = [c for c in found if c.illegal]
        if found:
            out[ref] = found
    return out


def preflight_row(board: Board, *, clearance: float,
                  lands: dict[tuple[str, int], Land] | None = None) -> dict:
    """The audit as one ``preflight.evaluate``-shaped checklist row.

    ``{key, label, status, detail}`` is the contract ``cli.py preflight`` builds
    and the desktop app renders, so wiring this into the pre-run checklist is a
    one-line change instead of a new code path. It is a *warning*, not a block:
    an illegal footprint still places and still routes, it just cannot be
    manufactured, and telling the user which part to change is more useful than
    refusing to run.
    """
    bad = board_clashes(board, clearance=clearance, lands=lands)
    errors = sum(len(v) for v in bad.values())
    return {
        "key": "footprint_clearance",
        "label": "Footprint clearance",
        "status": "warn" if bad else "ok",
        "detail": (f"{len(bad)} footprint(s) below {clearance} mm — "
                   f"{errors} DRC clearance error(s) whatever the placement: "
                   + ", ".join(sorted(bad)))
        if bad else f"every footprint clears its own pads by {clearance} mm",
    }


# --------------------------------------------------------------------------
# Part B: node labels
# --------------------------------------------------------------------------

def pad_node(ref: str, index: int) -> tuple[str, str, str]:
    """Graph label for one pad of one footprint."""
    return ("p", ref, f"{index:0{_IDX_WIDTH}d}")


def net_node(name: str) -> tuple[str, str, str]:
    """Graph label for one net's junction.

    A net is a hyperedge -- it joins every pad that touches it, and on copper
    the junction is a real point -- so it gets a node of its own exactly as in
    ``planarity.netlist_graph``. Keeping that identical is what makes the two
    models comparable: the only difference between them is the pad expansion.
    """
    return ("n", name, "")


def wall(a: tuple[str, int], b: tuple[str, int]) -> tuple:
    """Canonical barrier edge between two ``(ref, pad index)`` pads."""
    u, v = pad_node(*a), pad_node(*b)
    return (u, v) if u <= v else (v, u)


def _edge(u, v) -> tuple:
    return (u, v) if u <= v else (v, u)


# --------------------------------------------------------------------------
# Part B: the graph
# --------------------------------------------------------------------------

BARRIERS = ("blocked", "solid", "placed")


@dataclass(frozen=True)
class EscapeGraph:
    """The pad-level planarity graph, with the barriers marked.

    ``walls`` is a subset of ``edges``. The distinction is not cosmetic: a net
    edge can be cut, and cutting it is what a hand-soldered wire bridge does. A
    wall is where the copper physically is, and no bridge deletes it -- so the
    bridge search must never be allowed to spend one on a wall, or it reports a
    repair nobody can perform.
    """
    nodes: tuple
    edges: tuple
    walls: frozenset
    pads: int                   # pad nodes in the graph
    nets: int                   # net nodes in the graph
    barriers: str

    def has_edge(self, u, v) -> bool:
        return _edge(u, v) in self.edges

    @property
    def net_edges(self) -> tuple:
        """The edges a wire bridge could stand in for."""
        return tuple(e for e in self.edges if e not in self.walls)


def _wall_pairs(board: Board, barriers: str, track: float, clearance: float,
                pad_mm: float) -> list[tuple[tuple[str, int], tuple[str, int]]]:
    """``((ref, pad), (ref, pad))`` for every gap no track passes through.

    ``blocked`` and ``solid`` ask each footprint on its own, so the answer
    travels with the part and means the same thing wherever it sits. ``placed``
    asks the assembled design, where a neighbour's pad obstructs exactly like
    one of your own -- the only reading that changes when something moves, and
    therefore the only one that can be opened again by moving it.
    """
    if barriers == "placed":
        return [((g.ref_a, g.pad_a), (g.ref_b, g.pad_b))
                for g in padblock.board_blocked_gaps(board, track=track,
                                                     clearance=clearance,
                                                     pad_mm=pad_mm)]
    if barriers == "solid":
        kw = dict(track=SOLID_TRACK_MM, clearance=0.0, pad_mm=0.0)
    elif barriers == "blocked":
        kw = dict(track=track, clearance=clearance, pad_mm=pad_mm)
    else:
        raise ValueError(f"unknown barriers {barriers!r}; expected one of "
                         f"{list(BARRIERS)}")
    out = []
    for ref in sorted(board.components):
        for i, j in padblock.component_blocked_pairs(board.components[ref], **kw):
            out.append(((ref, i), (ref, j)))
    return out


def escape_graph(board: Board, planes: set[str] | None = None, *,
                 barriers: str = "blocked", track: float = 1.0,
                 clearance: float = 0.85,
                 pad_mm: float = padblock.PAD_MM) -> EscapeGraph:
    """The pad-level graph whose planarity decides single-sided routability.

    One node per pad, one node per net, an edge for "this pad is on this net",
    and a wall edge for every gap no track fits through. Nets carried by a pour
    are dropped and so are nets that never leave one footprint -- both exactly
    as ``planarity.netlist_graph`` does, so the two models differ only in the
    pad expansion and a difference between them means something.

    Pads that carry no routed net and stand in no wall are left out: an isolated
    node cannot affect planarity, and including it would only inflate the size
    reported next to the verdict.
    """
    from . import globalroute
    skip = globalroute.plane_nets(board) if planes is None else set(planes)

    edges: list[tuple] = []
    walls: set[tuple] = set()
    for a, b in _wall_pairs(board, barriers, track, clearance, pad_mm):
        w = wall(a, b)
        if w[0] == w[1]:
            continue
        walls.add(w)

    members = board.nets()
    net_names = []
    for name in sorted(members):
        if name in skip:
            continue
        pads = sorted(members[name])
        if len({ref for ref, _ in pads}) < 2:
            continue                          # nothing to route: one part's copper
        net_names.append(name)
        for ref, i in pads:
            edges.append(_edge(pad_node(ref, i), net_node(name)))

    edges = sorted(set(edges) | walls)
    nodes = sorted({v for e in edges for v in e})
    return EscapeGraph(nodes=tuple(nodes), edges=tuple(edges),
                       walls=frozenset(walls),
                       pads=sum(1 for n in nodes if n[0] == "p"),
                       nets=len(net_names), barriers=barriers)


# --------------------------------------------------------------------------
# Part B: the smallest set of connections that has to become a wire bridge
# --------------------------------------------------------------------------

class _OutOfProbes(Exception):
    """The search spent its planarity budget; whatever it has is what it gets."""


class _Budget:
    """A planarity-probe allowance.

    Deliberately a probe count rather than a time limit. This number is compared
    across boards and pasted into a report, so it has to be the same on a busy
    laptop as on an idle one -- a wall-clock cut-off would silently make a slow
    machine report more forced bridges than a fast one.
    """

    def __init__(self, allowance: int):
        self.left = allowance

    def take(self):
        if self.left <= 0:
            raise _OutOfProbes
        self.left -= 1


def _planar(nodes, edges, budget: _Budget | None) -> bool:
    if budget is not None:
        budget.take()
    return planarity.is_planar(nodes, edges)


def _core(nodes, edges, removable: frozenset, budget) -> list:
    """Shrink until every removable edge left in it is load-bearing.

    The same idea as ``planarity._minimal_nonplanar`` and for the same reason --
    any planarising deletion must hit this subgraph, so the branching factor is
    a handful rather than every connection on the board -- with one change: only
    removable edges are ever stripped, so the walls stay in and the core the
    search branches over never offers one.
    """
    keep = list(edges)
    for e in list(keep):
        if e not in removable:
            continue
        trial = [x for x in keep if x != e]
        if not _planar(nodes, trial, budget):
            keep = trial
    return keep


def _greedy_cut(nodes, edges, removable: frozenset) -> list | None:
    """A cut that certainly works, found without a budget.

    Run first for two reasons: it guarantees the caller gets an *achievable*
    answer even when the exact search is cut short, and it seeds the branch and
    bound with an upper bound, which is what makes the bound prune anything at
    all. Deterministic -- the core comes back in the input edge order, which is
    sorted, and the first removable edge in it is taken.

    None when the obstruction is made of walls alone, which cannot happen while
    the wall set is a Gabriel graph (a subgraph of a Delaunay triangulation, so
    planar), but is checked rather than assumed.
    """
    cur = list(edges)
    cut = []
    while not planarity.is_planar(nodes, cur):
        pick = next((e for e in _core(nodes, cur, removable, None)
                     if e in removable), None)
        if pick is None:
            return None
        cur = [x for x in cur if x != pick]
        cut.append(pick)
    return sorted(cut)


def _minimum_cut(nodes, edges, removable: frozenset, max_bridges: int,
                 max_probes: int) -> tuple[list | None, bool]:
    """Smallest set of removable edges whose deletion leaves a planar graph.

    Returns ``(cut, capped)``. ``capped`` means the answer is an upper bound
    that works rather than a proven minimum -- either the probe allowance ran
    out or no cut of at most ``max_bridges`` edges was found. Minimum skewness
    is NP-hard, so this is branch and bound over the Kuratowski-style core, the
    same shape as ``planarity.skew_edges``.
    """
    if planarity.is_planar(nodes, edges):
        return [], False
    seed = _greedy_cut(nodes, edges, removable)
    if seed is None:
        return None, True
    if len(seed) <= 1:
        # The graph is not planar, so no cut of zero edges exists and a cut of
        # one is already minimal. Worth the special case: it is the common
        # answer and it skips the search entirely.
        return seed, False
    best = [list(seed)]
    budget = _Budget(max_probes)
    capped = False

    def search(cur, dropped):
        if len(dropped) >= len(best[0]):
            return                              # cannot beat what we have
        if _planar(nodes, cur, budget):
            best[0] = sorted(dropped)
            return
        if len(dropped) >= max_bridges:
            return                              # depth cap: reported as capped
        for e in _core(nodes, cur, removable, budget):
            if e in removable:
                search([x for x in cur if x != e], dropped + [e])

    try:
        search(list(edges), [])
    except _OutOfProbes:
        capped = True
    return best[0], capped or len(best[0]) > max_bridges


def escape_bridges(board: Board, planes: set[str] | None = None, *,
                   barriers: str = "blocked", track: float = 1.0,
                   clearance: float = 0.85, pad_mm: float = padblock.PAD_MM,
                   max_bridges: int = 6, max_probes: int = 20000) -> dict:
    """Can this board be single-sided once its pads are obstacles?

    The pad-level counterpart of ``planarity.forced_bridges``, returning the
    same shape of answer so the two can sit in one table. ``bridges`` is always
    a cut that genuinely works; ``capped`` says whether it was also proven to be
    the smallest one.

    ``walls_planar`` is reported because it is the assumption the whole search
    rests on: if the barriers alone were not drawable in the plane, no set of
    wire bridges could rescue the board and the bridge count would be
    meaningless rather than merely large.
    """
    g = escape_graph(board, planes, barriers=barriers, track=track,
                     clearance=clearance, pad_mm=pad_mm)
    removable = frozenset(g.net_edges)
    cut, capped = _minimum_cut(g.nodes, g.edges, removable, max_bridges,
                               max_probes)
    planar = cut == []
    names = {}
    for ref in sorted(board.components):
        for i, p in enumerate(board.components[ref].pads):
            names[pad_node(ref, i)] = (ref, p.name)
    listed = []
    for a, b in (cut or []):
        pad, net = (a, b) if a[0] == "p" else (b, a)
        ref, name = names.get(pad, (pad[1], pad[2]))
        listed.append((ref, name, net[1]))
    return {
        "model": f"pad/{barriers}",
        "planar": planar,
        "bridges": None if cut is None else len(cut),
        "capped": capped,
        "components": len(board.components),
        "pads": g.pads,
        "nets": g.nets,
        "walls": len(g.walls),
        "incidences": len(removable),
        "walls_planar": planarity.is_planar(g.nodes, sorted(g.walls)),
        "cut": sorted(listed),
    }


def compare(board: Board, planes: set[str] | None = None, *,
            track: float = 1.0, clearance: float = 0.85,
            pad_mm: float = padblock.PAD_MM, max_bridges: int = 6,
            max_probes: int = 20000) -> dict:
    """The optimistic point model beside every pad-level reading of one board.

    Four answers to the same question, and the spread between them is the
    result: ``point`` contracts each part to a vertex, ``blocked`` and ``solid``
    bracket what a footprint's own geometry forbids, and ``placed`` adds the
    gaps this particular arrangement of parts has closed.
    """
    out = {"point": dict(planarity.forced_bridges(board, planes,
                                                  max_bridges=max_bridges),
                         model="point")}
    for barriers in BARRIERS:
        out[barriers] = escape_bridges(board, planes, barriers=barriers,
                                       track=track, clearance=clearance,
                                       pad_mm=pad_mm, max_bridges=max_bridges,
                                       max_probes=max_probes)
    return out
