"""Escape-aware feasibility: pads are obstacles, and some footprints are illegal.

Two things the point-model planarity census cannot see, both pure geometry:

* **Part A.** A footprint whose own pad-to-pad gap is under the process
  clearance fails DRC wherever it is placed. No routing decision saves it.
* **Part B.** A component is not a point. Its pads are a ring, and the gaps
  between them are walls where no track fits and doors where one does. A net
  landing on pad 3 of a header has to leave on the side it arrived.

  py -3.13 -m pytest tests/test_escape.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import escape, padblock, planarity          # noqa: E402
from autoplace.model import Board, Component, Pad          # noqa: E402

# The CNC profile these boards are milled on. Spelled out rather than imported
# so the tests pin the numbers the module must produce instead of agreeing with
# it by construction: a 1.0 mm track between 0.85 mm clearances.
TRACK = 1.0
CLEARANCE = 0.85

# A 2.54 mm header with 2.0 mm lands: 0.54 mm of copper between pins. This is
# the measured case that produced 28 DRC clearance errors on a board whose
# routing was otherwise perfect, and it is the reason Part A exists.
HEADER_PITCH = 2.54
HEADER_LAND = 2.0


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _part(ref, offsets, x=50.0, y=50.0, rot=0, nets=None):
    pads = [Pad(str(i + 1), (nets[i] if nets else ""), ox, oy)
            for i, (ox, oy) in enumerate(offsets)]
    xs = [o[0] for o in offsets] or [0.0]
    ys = [o[1] for o in offsets] or [0.0]
    return Component(ref=ref, w=max(xs) - min(xs) + 2.0, h=max(ys) - min(ys) + 2.0,
                     x=x, y=y, rot=rot, pads=pads)


def _row(ref, pitch, nets, **kw):
    """``len(nets)`` pads in a horizontal row at ``pitch`` mm."""
    span = pitch * (len(nets) - 1)
    return _part(ref, [(-span / 2 + k * pitch, 0.0) for k in range(len(nets))],
                 nets=nets, **kw)


def _dip8(ref="U1", pitch=2.54, rows=7.62, nets=None, **kw):
    """DIP-8: two columns of four, pads 0-3 one row and 4-7 the other."""
    span = pitch * 3
    offs = [(-rows / 2, -span / 2 + k * pitch) for k in range(4)]
    offs += [(rows / 2, span / 2 - k * pitch) for k in range(4)]
    return _part(ref, offs, nets=nets, **kw)


def _board(*parts, w=300.0, h=300.0):
    b = Board(0.0, 0.0, w, h)
    b.components = {p.ref: p for p in parts}
    return b


def _lands(comp, diameter):
    return {(comp.ref, i): escape.Land(diameter / 2, diameter / 2)
            for i in range(len(comp.pads))}


def _ring_board(order=("N1", "N3", "N2", "N4"), n_rings=1):
    """A four-pin header whose nets are wired into a ring in ``order``.

    With ``order`` = N1,N3,N2,N4 the ring asks the header to present the one
    cyclic order a four-pad wall cannot: the middle two pins would have to swap
    sides of the row. Contract the header to a point and the demand vanishes.
    """
    parts = []
    for r in range(n_rings):
        tag = "" if r == 0 else str(r)
        nets = [f"{n}{tag}" for n in ("N1", "N2", "N3", "N4")]
        parts.append(_row(f"J{r + 1}", 2.54, nets, x=50.0, y=50.0 + 90.0 * r))
        seq = [f"{n}{tag}" for n in order]
        for k in range(4):
            parts.append(_row(f"R{r + 1}_{k}", 2.54, [seq[k], seq[(k + 1) % 4]],
                              x=20.0 + 20.0 * k, y=100.0 + 90.0 * r))
    return _board(*parts)


# --------------------------------------------------------------------------
# Part A: the land geometry
# --------------------------------------------------------------------------

def test_two_round_lands_agree_with_padblock():
    """The nominal round-land case must give padblock's number exactly.

    ``padblock.clear_gap`` is centre distance less one pad diameter. Anything
    else here would mean the two modules disagree about what a gap is.
    """
    la = escape.Land(1.0, 1.0)
    assert escape.pad_gap(0.0, 0.0, la, 5.0, 0.0, la) == padblock.clear_gap(
        0.0, 0.0, 5.0, 0.0, pad_mm=2.0)


def test_round_land_gap_is_direction_independent():
    la = escape.Land(1.0, 1.0)
    straight = escape.pad_gap(0.0, 0.0, la, 5.0, 0.0, la)
    diagonal = escape.pad_gap(0.0, 0.0, la, 3.0, 4.0, la)      # also 5 mm apart
    assert abs(straight - diagonal) < 1e-9


def test_oblong_land_is_measured_along_the_line_to_its_neighbour():
    """A DIP LongPad is 2.4 mm across and 1.6 mm tall; which number applies
    depends entirely on which way the neighbour lies."""
    la = escape.Land(1.2, 0.8)
    assert abs(escape.pad_gap(0.0, 0.0, la, 2.54, 0.0, la) - (2.54 - 2.4)) < 1e-9
    assert abs(escape.pad_gap(0.0, 0.0, la, 0.0, 2.54, la) - (2.54 - 1.6)) < 1e-9


def test_rectangular_land_reaches_further_than_the_circle_inside_it():
    """A square pad's corner sticks out past the circle of the same width, so
    treating every land as round would over-state a diagonal gap."""
    rect = escape.Land(1.0, 1.0, oval=False)
    circ = escape.Land(1.0, 1.0)
    assert (escape.pad_gap(0.0, 0.0, rect, 6.0, 8.0, rect)
            < escape.pad_gap(0.0, 0.0, circ, 6.0, 8.0, circ))


def test_overlapping_lands_report_a_negative_gap():
    """Reported honestly rather than clamped: "these two pads are touching" is
    a different fault from "no room for a track" and needs a different fix."""
    la = escape.Land(1.0, 1.0)
    assert escape.pad_gap(0.0, 0.0, la, 1.5, 0.0, la) < 0.0


# --------------------------------------------------------------------------
# Part A: which footprints are illegal
# --------------------------------------------------------------------------

def test_header_at_2p54_with_2mm_lands_is_below_cnc_clearance():
    """The finding this check exists for: 0.54 mm of copper where 0.85 mm is
    the process minimum, so every adjacent pin pair is a DRC error."""
    j1 = _row("J1", HEADER_PITCH, ["A", "B", "C", "D"])
    clashes = escape.footprint_clashes(j1, clearance=CLEARANCE,
                                       lands=_lands(j1, HEADER_LAND))
    assert {(c.pad_a, c.pad_b) for c in clashes} == {("1", "2"), ("2", "3"),
                                                     ("3", "4")}
    first = clashes[0]
    assert abs(first.gap - (HEADER_PITCH - HEADER_LAND)) < 1e-9
    assert first.required == CLEARANCE
    assert first.illegal is True


def test_dip_longpads_clear_the_cnc_clearance():
    """The DIP-8 actually on these boards has 2.4 x 1.6 mm pads at 2.54 mm, so
    the pin-to-pin gap is 0.94 mm -- legal, and it must not be flagged."""
    u1 = _dip8()
    lands = {("U1", i): escape.Land(1.2, 0.8, oval=False) for i in range(8)}
    assert escape.footprint_clashes(u1, clearance=CLEARANCE, lands=lands) == []


def test_every_offending_pair_is_reported_not_only_adjacent_ones():
    """DRC checks all pads against all pads. A row so tight that pin 1 also
    clashes with pin 3 has to report both, or the count under-states the fix."""
    j1 = _row("J1", 1.0, ["A", "B", "C"])          # 2 mm lands, 1 mm pitch
    clashes = escape.footprint_clashes(j1, clearance=CLEARANCE,
                                       lands=_lands(j1, 2.0))
    assert {(c.pad_a, c.pad_b) for c in clashes} == {("1", "2"), ("2", "3"),
                                                     ("1", "3")}


def test_same_net_pads_are_reported_but_marked_legal():
    """Two pads on one net are joined by copper, so KiCad raises no clearance
    error. Reported anyway, tagged, because dropping them silently would hide a
    footprint whose lands genuinely overlap."""
    j1 = _row("J1", HEADER_PITCH, ["A", "A"])
    clashes = escape.footprint_clashes(j1, clearance=CLEARANCE,
                                       lands=_lands(j1, HEADER_LAND))
    assert len(clashes) == 1
    assert clashes[0].same_net is True
    assert clashes[0].illegal is False


def test_board_audit_lists_only_the_offenders():
    j1 = _row("J1", HEADER_PITCH, ["A", "B", "C"])
    r1 = _row("R1", 7.62, ["A", "B"], x=150.0)
    lands = dict(_lands(j1, HEADER_LAND))
    lands.update(_lands(r1, 1.6))
    bad = escape.board_clashes(_board(j1, r1), clearance=CLEARANCE, lands=lands)
    assert set(bad) == {"J1"}
    assert len(bad["J1"]) == 2                     # 1-2 and 2-3; 1-3 is 3.08 mm


def test_same_net_pairs_are_excluded_from_the_board_audit_by_default():
    j1 = _row("J1", HEADER_PITCH, ["A", "A"])
    lands = _lands(j1, HEADER_LAND)
    assert escape.board_clashes(_board(j1), clearance=CLEARANCE, lands=lands) == {}
    assert set(escape.board_clashes(_board(j1), clearance=CLEARANCE, lands=lands,
                                    same_net=True)) == {"J1"}


def test_rotation_moves_a_clash_but_cannot_open_it():
    """A rigid transform cannot change a footprint's internal geometry, so the
    verdict has to be identical at every orientation -- otherwise the placer
    could 'fix' an illegal footprint by turning it.

    Round lands, which is the nominal fallback and what most THT pads are. An
    oblong land turns with its part, and ``Land`` is stated in board axes, so
    for those it is the caller's mapping that has to be rebuilt -- see ``Land``.
    """
    lands = {("J1", i): escape.Land(1.0, 1.0) for i in range(4)}
    base = escape.footprint_clashes(_row("J1", HEADER_PITCH, ["A", "B", "C", "D"]),
                                    clearance=CLEARANCE, lands=lands)
    for rot in (90, 180, 270):
        turned = _row("J1", HEADER_PITCH, ["A", "B", "C", "D"], rot=rot, x=7.0,
                      y=3.0)
        got = escape.footprint_clashes(turned, clearance=CLEARANCE, lands=lands)
        assert [(c.pad_a, c.pad_b, round(c.gap, 9)) for c in got] == \
               [(c.pad_a, c.pad_b, round(c.gap, 9)) for c in base]


def test_missing_land_falls_back_to_the_nominal_pad():
    """``model.Pad`` carries no land size, so an audit run without real pad
    geometry has to fall back to padblock's nominal 2.0 mm land rather than
    silently report a clean board."""
    j1 = _row("J1", HEADER_PITCH, ["A", "B"])
    clashes = escape.footprint_clashes(j1, clearance=CLEARANCE)
    assert len(clashes) == 1
    assert abs(clashes[0].gap - (HEADER_PITCH - padblock.PAD_MM)) < 1e-9


def test_the_audit_comes_back_as_a_preflight_row():
    """``preflight.evaluate`` returns ``{key, label, status, detail}`` rows and
    the desktop app renders them before a run. Matching that shape is what makes
    wiring this in a one-line change rather than a new code path."""
    clean = escape.preflight_row(_board(_row("R1", 7.62, ["A", "B"])),
                                 clearance=CLEARANCE)
    assert set(clean) == {"key", "label", "status", "detail"}
    assert clean["status"] == "ok"

    j1 = _row("J1", HEADER_PITCH, ["A", "B", "C"])
    bad = escape.preflight_row(_board(j1), clearance=CLEARANCE,
                               lands=_lands(j1, HEADER_LAND))
    assert bad["status"] == "warn"
    assert "J1" in bad["detail"]


# --------------------------------------------------------------------------
# Part B: the escape graph
# --------------------------------------------------------------------------

def test_a_node_per_pad_not_per_part():
    """The whole point of the model: a header is four places copper can attach,
    not one."""
    nets = ["A", "B", "C", "D"]
    g = escape.escape_graph(_board(_row("J1", 2.54, nets),
                                   _row("R1", 2.54, nets, x=150.0)),
                            planes=set(), track=TRACK, clearance=CLEARANCE)
    assert g.pads == 8
    assert g.nets == 4


def test_only_blocked_gaps_become_walls():
    """2.54 mm pitch leaves 0.54 mm: a wall. 7.62 mm leaves 5.62: a road."""
    g = escape.escape_graph(_board(_row("J1", 2.54, ["A", "B"]),
                                   _row("R1", 7.62, ["A", "B"], x=150.0)),
                            planes=set(), track=TRACK, clearance=CLEARANCE)
    assert g.walls == frozenset({escape.wall(("J1", 0), ("J1", 1))})


def test_the_corridor_down_the_middle_of_a_dip_stays_open():
    """7.62 mm between the pin rows is 5.62 mm of clear copper -- two tracks
    fit. Walling it would be the classic way to make this model useless: a
    track under a DIP is the standard single-sided escape."""
    g = escape.escape_graph(_board(_dip8()), planes=set(), track=TRACK,
                            clearance=CLEARANCE)
    for i in (0, 1, 2, 4, 5, 6):
        assert escape.wall(("U1", i), ("U1", i + 1)) in g.walls
    for a, b in ((0, 7), (1, 6), (2, 5), (3, 4)):
        assert escape.wall(("U1", a), ("U1", b)) not in g.walls


def test_solid_barriers_close_the_corridor():
    """The pessimistic bracket: treat the whole footprint as an obstacle. Kept
    so the two readings of a part -- 'walls where no track fits' and 'you cannot
    route through a part at all' -- can be measured against each other."""
    g = escape.escape_graph(_board(_dip8()), planes=set(), barriers="solid",
                            track=TRACK, clearance=CLEARANCE)
    assert escape.wall(("U1", 3), ("U1", 4)) in g.walls


def test_walls_are_exactly_padblocks_blocked_pairs():
    """One source of truth for 'no track fits here'. If these ever diverge, the
    feasibility model and the placement cost are arguing about the same board."""
    u1 = _dip8()
    g = escape.escape_graph(_board(u1), planes=set(), track=TRACK,
                            clearance=CLEARANCE)
    assert g.walls == frozenset(
        escape.wall(("U1", i), ("U1", j))
        for i, j in padblock.component_blocked_pairs(u1, track=TRACK,
                                                     clearance=CLEARANCE))


def test_net_edges_attach_at_the_pad_that_carries_the_net():
    g = escape.escape_graph(_board(_row("J1", 2.54, ["A", "B", "A"]),
                                   _row("R1", 2.54, ["A", "B"], x=150.0)),
                            planes=set(), track=TRACK, clearance=CLEARANCE)
    assert g.has_edge(escape.pad_node("J1", 0), escape.net_node("A"))
    assert g.has_edge(escape.pad_node("J1", 2), escape.net_node("A"))
    assert not g.has_edge(escape.pad_node("J1", 1), escape.net_node("A"))


def test_plane_nets_are_excluded():
    """A filled pour connects its pads for free, exactly as in the point model."""
    g = escape.escape_graph(_board(_row("J1", 2.54, ["GND", "B"]),
                                   _row("R1", 2.54, ["GND", "B"], x=150.0)),
                            planes={"GND"}, track=TRACK, clearance=CLEARANCE)
    assert g.nets == 1
    assert escape.net_node("GND") not in g.nodes


def test_a_net_on_one_part_only_is_dropped():
    """Nothing to route: both ends are the same footprint's copper."""
    g = escape.escape_graph(_board(_row("J1", 2.54, ["A", "A"])), planes=set(),
                            track=TRACK, clearance=CLEARANCE)
    assert g.nets == 0


# --------------------------------------------------------------------------
# Part B: what the pad model says that the point model cannot
# --------------------------------------------------------------------------

def test_a_wall_forces_the_order_its_nets_leave_in():
    """The load-bearing case.

    J1 is a four-pin header: one wall, so its nets leave along the row in pad
    order. Four resistors wire those nets into the ring 1-3-2-4, which is the
    one cyclic order a four-pad wall cannot present. The point model contracts
    J1 to a vertex, where every cyclic order is free, and calls it planar.
    """
    board = _ring_board()
    assert planarity.forced_bridges(board, planes=set())["planar"] is True
    assert escape.escape_bridges(board, planes=set(), track=TRACK,
                                 clearance=CLEARANCE)["planar"] is False


def test_the_same_ring_in_pad_order_is_fine():
    """Control for the case above: rewire the ring to follow the header's own
    pad order and the board is planar in both models. Without this the previous
    test would pass for any model that simply reports 'header, therefore no'."""
    board = _ring_board(order=("N1", "N2", "N3", "N4"))
    assert escape.escape_bridges(board, planes=set(), track=TRACK,
                                 clearance=CLEARANCE)["planar"] is True


def test_an_open_gap_decouples_the_nets_a_part_joins():
    """The other direction, and the reason the pad model is not simply stricter.

    A 10.16 mm resistor has 8 mm of clear copper between its lands, so a track
    walks straight between them. The two nets it joins are then topologically
    independent -- the point model's link between them is an artefact of
    contracting the part to a point.
    """
    g = escape.escape_graph(_board(_row("R1", 10.16, ["A", "B"]),
                                   _row("R2", 10.16, ["A", "B"], x=150.0)),
                            planes=set(), track=TRACK, clearance=CLEARANCE)
    assert g.walls == frozenset()
    assert not _reaches(g, escape.net_node("A"), escape.net_node("B"))


def _reaches(g, src, dst):
    adj = {}
    for a, b in g.edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, stack = {src}, [src]
    while stack:
        v = stack.pop()
        if v == dst:
            return True
        for w in adj.get(v, ()):
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return False


# --------------------------------------------------------------------------
# Part B: the bridge search
# --------------------------------------------------------------------------

def test_a_wall_is_never_offered_as_a_bridge():
    """A bridge is a wire soldered over the board; it crosses a wall for free.
    A wall is geometry and cannot be cut, so the search must only ever delete
    net connections -- otherwise it reports a 'fix' nobody can perform.
    """
    r = escape.escape_bridges(_ring_board(), planes=set(), track=TRACK,
                              clearance=CLEARANCE)
    assert r["bridges"] >= 1
    assert len(r["cut"]) == r["bridges"]
    for ref, pad, net in r["cut"]:
        assert ref == "J1" or ref.startswith("R")
        assert net.startswith("N")


def test_a_planar_board_forces_no_bridges():
    r = escape.escape_bridges(_board(_row("R1", 2.54, ["A", "B"]),
                                     _row("R2", 2.54, ["B", "C"], x=150.0)),
                              planes=set(), track=TRACK, clearance=CLEARANCE)
    assert r["planar"] is True
    assert r["bridges"] == 0
    assert r["cut"] == []
    assert r["capped"] is False


def test_point_model_non_planarity_survives_into_the_solid_model():
    """An invariant, not a coincidence: contracting each part's pad adjacency
    graph turns the solid model back into the point model, so the point model
    is a minor of it and cannot be the harder read of the two."""
    parts = []
    for k in range(3):                                  # K3,3 in the point model
        parts.append(_row(f"U{k}", 2.54, ["A", "B", "C"], x=40.0 + 60.0 * k,
                          y=40.0))
        parts.append(_row(f"V{k}", 2.54, ["A", "B", "C"], x=40.0 + 60.0 * k,
                          y=200.0))
    board = _board(*parts)
    assert planarity.forced_bridges(board, planes=set())["planar"] is False
    assert escape.escape_bridges(board, planes=set(), barriers="solid",
                                 track=TRACK, clearance=CLEARANCE,
                                 )["planar"] is False


def test_the_search_budget_is_a_probe_count_not_a_clock():
    """Determinism is a requirement here -- this number goes in a report and is
    compared across boards. A wall-clock cut-off would give a different answer
    on a busy machine, so the cap counts planarity probes instead. Cut short,
    the model still returns a cut that works; it just stops claiming it is the
    smallest one.
    """
    board = _ring_board(n_rings=2)                      # two independent rings
    tight = escape.escape_bridges(board, planes=set(), track=TRACK,
                                  clearance=CLEARANCE, max_probes=0)
    assert tight["capped"] is True
    assert tight["planar"] is False
    assert tight["bridges"] >= 2
    assert len(tight["cut"]) == tight["bridges"]


def test_repeated_runs_and_reordered_parts_agree():
    """Two identical boards whose component dicts were built in a different
    order must produce identical results, or the comparison table is reporting
    insertion order."""
    board = _ring_board()
    flipped = _board(*reversed(list(board.components.values())))
    kw = dict(planes=set(), track=TRACK, clearance=CLEARANCE)
    first = escape.escape_bridges(board, **kw)
    assert first == escape.escape_bridges(board, **kw)
    assert first == escape.escape_bridges(flipped, **kw)


# --------------------------------------------------------------------------
# the comparison the whole exercise exists to produce
# --------------------------------------------------------------------------

def test_compare_reports_the_point_model_beside_the_pad_models():
    got = escape.compare(_ring_board(), planes=set(), track=TRACK,
                         clearance=CLEARANCE)
    assert set(got) == {"point", "blocked", "solid", "placed"}
    assert got["point"]["bridges"] == 0
    assert got["blocked"]["bridges"] >= 1
    # ``solid`` only ever adds walls to ``blocked``, and deleting a cut that
    # planarises the bigger graph also planarises the smaller one, so it can
    # never come out as the easier board.
    assert got["solid"]["bridges"] >= got["blocked"]["bridges"]
