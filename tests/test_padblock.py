"""Headless tests for pad-gap blocking: which gaps no track can pass through.

Pure python -- no pcbnew, no Java. This analysis runs inside the placement loop,
so the last test here is a budget, not a nicety.

  py -3.13 -m pytest tests/test_padblock.py -q
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import padblock                           # noqa: E402
from autoplace.model import Board, Component, Pad        # noqa: E402

# The CNC profile the boards are milled on: 1.0 mm track between 0.85 mm
# clearances, so a track needs 2.7 mm of clear copper to pass. Spelled out here
# rather than imported so the tests pin the number the module is supposed to
# produce instead of agreeing with it by construction.
CHANNEL = 1.0 + 2 * 0.85


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _part(ref, offsets, x=50.0, y=50.0, rot=0, nets=None):
    """A component whose pads sit at the given offsets from its centre."""
    pads = [Pad(str(i + 1), (nets[i] if nets else ""), ox, oy)
            for i, (ox, oy) in enumerate(offsets)]
    xs = [o[0] for o in offsets] or [0.0]
    ys = [o[1] for o in offsets] or [0.0]
    return Component(ref=ref, w=max(xs) - min(xs) + 2.0, h=max(ys) - min(ys) + 2.0,
                     x=x, y=y, rot=rot, pads=pads)


def _inline(ref, pitch, n, **kw):
    """``n`` pads in a row at ``pitch`` mm, centred on the component."""
    span = pitch * (n - 1)
    return _part(ref, [(-span / 2 + k * pitch, 0.0) for k in range(n)], **kw)


def _dip8(ref="U1", pitch=2.54, rows=7.62, **kw):
    """A DIP-8: two columns of four pads, 2.54 mm pitch, 7.62 mm between rows.

    Pads 0-3 are one row, 4-7 the other -- the numbering that makes the two pin
    rows contiguous index runs, so a run assertion reads clearly.
    """
    span = pitch * 3
    offs = [(-rows / 2, -span / 2 + k * pitch) for k in range(4)]
    offs += [(rows / 2, span / 2 - k * pitch) for k in range(4)]
    return _part(ref, offs, **kw)


def _board(*parts, w=100.0, h=100.0):
    b = Board(0.0, 0.0, w, h)
    b.components = {p.ref: p for p in parts}
    return b


# --------------------------------------------------------------------------
# the geometry: one clear gap against one channel width
# --------------------------------------------------------------------------

def test_clear_gap_is_centre_distance_less_one_pad_diameter():
    """Half a land is consumed at each end, so two 2 mm lands 5 mm apart leave
    3 mm of copper -- not 5."""
    assert padblock.clear_gap(0.0, 0.0, 5.0, 0.0, pad_mm=2.0) == 5.0 - 2.0


def test_overlapping_lands_report_a_negative_gap():
    """Reported honestly rather than clamped at zero: a caller can then tell
    'no room for a track' from 'these two pads are shorted'."""
    assert padblock.clear_gap(0.0, 0.0, 1.2, 0.0, pad_mm=2.0) < 0.0


def test_clear_gap_is_measured_diagonally_not_per_axis():
    assert abs(padblock.clear_gap(0.0, 0.0, 3.0, 4.0, pad_mm=2.0) - 3.0) < 1e-12


def test_a_track_needs_its_width_plus_a_clearance_on_each_side():
    assert padblock.track_fits(CHANNEL, track=1.0, clearance=0.85)
    assert not padblock.track_fits(CHANNEL - 0.01, track=1.0, clearance=0.85)


def test_a_finer_process_fits_through_a_gap_a_coarse_one_cannot():
    """The same copper is passable or not depending on the fabrication profile,
    which is why track/clearance are parameters and not constants."""
    assert not padblock.track_fits(1.0, track=1.0, clearance=0.85)
    assert padblock.track_fits(1.0, track=0.4, clearance=0.25)


# --------------------------------------------------------------------------
# one footprint: which of its own gaps are impassable
# --------------------------------------------------------------------------

def test_a_dip_pin_row_blocks_every_gap_between_its_pins():
    """The motivating case. 2.54 mm pitch minus a ~2 mm land leaves ~0.5 mm,
    against the 2.7 mm a track needs, so nothing gets between two DIP pins and
    the pin row is a solid wall."""
    pairs = padblock.component_blocked_pairs(_dip8())
    assert (0, 1) in pairs and (1, 2) in pairs and (2, 3) in pairs
    assert (4, 5) in pairs and (5, 6) in pairs and (6, 7) in pairs
    assert len(pairs) == 6


def test_the_channel_down_the_middle_of_a_dip_stays_open():
    """7.62 mm between the rows leaves 5.6 mm of copper -- two tracks' worth.
    A model that called the whole footprint solid would forbid the one route
    every hand-drawn single-sided board uses."""
    pairs = padblock.component_blocked_pairs(_dip8())
    facing = {(0, 7), (1, 6), (2, 5), (3, 4)}          # pads across the body
    assert not (facing & set(pairs))


def test_a_wide_pitch_two_pad_part_blocks_nothing():
    # 10.16 mm pitch (a 1/2 W resistor) leaves 8.2 mm: two tracks fit side by side.
    assert padblock.component_blocked_pairs(_inline("R1", 10.16, 2)) == []


def test_a_tight_two_pad_part_blocks_its_own_gap():
    assert padblock.component_blocked_pairs(_inline("R1", 2.54, 2)) == [(0, 1)]


def test_the_gap_that_counts_is_the_one_to_the_nearest_pad():
    """Three pads in a row at 1.2 mm: the 0-2 gap is tight too, but pad 1 is
    standing in it. Reporting it would count one obstruction twice and tell the
    caller to open a gap that is not the one in the way."""
    pairs = padblock.component_blocked_pairs(_inline("J1", 1.2, 3))
    assert pairs == [(0, 1), (1, 2)]


def test_a_pad_grid_reports_its_sides_and_not_its_diagonals():
    """Four pads in a rectangle: 4 blocked gaps, not 6.

    The diagonal gap is tight too, but crossing it means crossing a side first,
    so reporting it would count one obstruction twice. This is the exact tie the
    tolerance exists for -- on a rectangle the two orthogonal pads sit precisely
    *on* the adjacency circle of the diagonal, and which side of it float
    arithmetic lands on depends on the pitch. Swept over a spread of pitches
    because a single grid can resolve correctly by luck: at 2.0 x 2.0 it does,
    at 2.0 x 2.54 and 3.0 x 3.0 it does not.
    """
    for a, b in ((2.0, 2.0), (2.0, 2.54), (2.54, 1.27), (2.54, 2.54),
                 (3.0, 3.0), (1.27, 1.27), (2.0, 3.5)):
        grid = _part("U2", [(0.0, 0.0), (a, 0.0), (0.0, b), (a, b)])
        assert padblock.component_blocked_pairs(grid) == \
            [(0, 1), (0, 2), (1, 3), (2, 3)], f"{a} x {b} grid"


def test_blocked_pairs_do_not_change_when_the_part_moves():
    """A footprint's internal gaps are a property of the footprint. If they
    drifted with position the annealer would chase noise."""
    here = padblock.component_blocked_pairs(_dip8(x=10.0, y=10.0))
    there = padblock.component_blocked_pairs(_dip8(x=71.3, y=44.9))
    assert here == there


def test_blocked_pairs_do_not_change_when_the_part_rotates():
    """Rotation is rigid, so it moves the wall without opening it. Uses the
    world pad positions, so this also proves the rotation is actually applied."""
    base = padblock.component_blocked_pairs(_dip8())
    for rot in (90, 180, 270):
        assert padblock.component_blocked_pairs(_dip8(rot=rot)) == base


def test_rotation_is_actually_applied_to_the_pad_positions():
    """Guards the test above from passing for the wrong reason: a 90 deg part
    must present its pads on the other axis."""
    part = _inline("R1", 2.54, 2, rot=90)
    (ax, ay), (bx, by) = (part.pad_world(p) for p in part.pads)
    assert abs(ay - by) > 1.0 and abs(ax - bx) < 1e-9


def test_a_bigger_land_closes_a_gap_a_smaller_one_leaves_open():
    """Pad size is an approximation this module cannot read off the model, so it
    is a parameter -- and it has to actually move the answer, or passing the
    real land diameter would be pointless."""
    part = _inline("J1", 4.0, 2)                 # 4 mm between centres
    assert padblock.component_blocked_pairs(part, pad_mm=0.6) == []      # 3.4 mm clear
    assert padblock.component_blocked_pairs(part, pad_mm=2.0) == [(0, 1)]  # 2.0 mm clear


def test_a_finer_process_opens_a_gap_the_cnc_profile_blocks():
    part = _inline("J1", 4.0, 2)                 # 2.0 mm of clear copper
    assert padblock.component_blocked_pairs(part) == [(0, 1)]
    assert padblock.component_blocked_pairs(part, track=0.25, clearance=0.2) == []


def test_a_single_pad_part_has_no_gaps():
    assert padblock.component_blocked_pairs(_part("TP1", [(0.0, 0.0)])) == []


def test_a_padless_part_has_no_gaps():
    assert padblock.component_blocked_pairs(_part("H1", [])) == []


# --------------------------------------------------------------------------
# wall or not: summarising a footprint
# --------------------------------------------------------------------------

def test_a_dip_reports_one_wall_per_pin_row():
    """Two rows joined by nothing: the open middle channel keeps them separate,
    so this is two walls with a road between them, not one solid block."""
    bar = padblock.component_barrier(_dip8())
    assert bar.is_wall
    assert bar.runs == ((0, 1, 2, 3), (4, 5, 6, 7))


def test_a_wall_spans_the_outer_edge_of_its_end_pads():
    """What the router has to detour around is the copper, not the centres: a
    four-pad row at 2.54 is 3 pitches plus half a land at each end."""
    bar = padblock.component_barrier(_dip8())
    assert abs(bar.span - (3 * 2.54 + 2.0)) < 1e-9


def test_a_two_pad_pinch_is_not_a_wall():
    """One blocked gap with a pad at each end is stepped around for almost
    nothing. Calling it a wall would flag half the passives on the board."""
    bar = padblock.component_barrier(_inline("R1", 2.54, 2))
    assert bar.blocked == ((0, 1),)
    assert not bar.is_wall
    assert bar.passable


def test_three_chained_pads_are_a_wall():
    bar = padblock.component_barrier(_inline("J1", 2.54, 3))
    assert bar.is_wall
    assert bar.longest_run == (0, 1, 2)


def test_a_coarse_footprint_has_no_barrier_at_all():
    bar = padblock.component_barrier(_inline("R1", 10.16, 2))
    assert bar.blocked == () and bar.runs == ()
    assert bar.span == 0.0
    assert bar.passable and not bar.is_wall


def test_a_barrier_knows_the_part_it_describes():
    bar = padblock.component_barrier(_dip8(ref="U7"))
    assert bar.ref == "U7"
    assert bar.pads == 8


def test_a_long_header_is_a_longer_wall_than_a_short_one():
    """``span`` is the number that ranks walls: both are impassable, one costs
    far more to go around."""
    short = padblock.component_barrier(_inline("J1", 2.54, 4))
    long = padblock.component_barrier(_inline("J2", 2.54, 20))
    assert short.is_wall and long.is_wall
    assert long.span > short.span


# --------------------------------------------------------------------------
# the whole board, including the gaps the placer creates
# --------------------------------------------------------------------------

def test_every_component_contributes_its_own_blocked_gaps():
    b = _board(_dip8("U1", x=20.0, y=20.0), _dip8("U2", x=70.0, y=70.0))
    gaps = padblock.board_blocked_gaps(b)
    assert len([g for g in gaps if g.ref_a == "U1"]) == 6
    assert len([g for g in gaps if g.ref_a == "U2"]) == 6
    assert all(g.internal for g in gaps)


def test_two_parts_placed_too_close_pinch_a_gap_between_them():
    """The gap the placer actually controls. A pad of R1 and a pad of R2 2 mm
    apart is exactly as impassable as two pins of a DIP."""
    b = _board(_inline("R1", 10.16, 2, x=40.0, y=50.0),
               _inline("R2", 10.16, 2, x=54.0, y=50.0))   # 1.84 mm of clear copper
    cross = [g for g in padblock.board_blocked_gaps(b) if not g.internal]
    assert len(cross) == 1
    assert (cross[0].ref_a, cross[0].pad_a, cross[0].ref_b, cross[0].pad_b) == \
           ("R1", 1, "R2", 0)


def test_moving_the_parts_apart_opens_the_gap_between_them():
    def cross_gaps(dx):
        b = _board(_inline("R1", 10.16, 2, x=40.0, y=50.0),
                   _inline("R2", 10.16, 2, x=40.0 + dx, y=50.0))
        return [g for g in padblock.board_blocked_gaps(b) if not g.internal]
    assert cross_gaps(6.0) != []           # 2.16 mm of clear copper: too tight
    assert cross_gaps(16.0) == []          # 3.84 mm: a track fits


def test_a_blocked_gap_reports_where_it_is():
    """The midpoint is where the router would have had to squeeze through, so a
    caller can rank, plot or re-place around it."""
    b = _board(_inline("R1", 4.0, 2, x=50.0, y=60.0))
    g = padblock.board_blocked_gaps(b)[0]
    assert (g.x, g.y) == (50.0, 60.0)
    assert abs(g.gap - 2.0) < 1e-9


def test_pads_on_the_same_net_still_block_everything_else():
    """Copper joining two pads of one net does not help a *different* net get
    past them, so a shared net must not excuse the gap."""
    b = _board(_part("R1", [(-2.0, 0.0), (2.0, 0.0)], nets=["/VCC", "/VCC"]))
    assert len(padblock.board_blocked_gaps(b)) == 1


def test_a_neighbours_pad_can_take_over_a_gap():
    """The board view asks which gaps are impassable *in this design*, so a pad
    of another part standing between two pads counts as the obstruction: the
    tight gaps become the two halves, not the original pair.

    The footprint view deliberately answers differently -- it describes the
    footprint alone, which is what makes a wall classification a property of the
    part instead of of wherever it happens to sit this iteration.
    """
    a = _part("U1", [(-2.0, 0.0), (2.0, 0.0)], x=50.0, y=50.0)
    b = _part("U2", [(0.0, 0.0)], x=50.0, y=51.5)          # inside the U1 gap
    pairs = {(g.ref_a, g.pad_a, g.ref_b, g.pad_b)
             for g in padblock.board_blocked_gaps(_board(a, b))}
    assert pairs == {("U1", 0, "U2", 0), ("U1", 1, "U2", 0)}
    assert padblock.component_blocked_pairs(a) == [(0, 1)]


def test_an_empty_board_has_no_gaps_and_no_barriers():
    assert padblock.board_blocked_gaps(_board()) == []
    assert padblock.board_barriers(_board()) == {}


def test_board_barriers_lists_only_the_parts_that_obstruct():
    b = _board(_dip8("U1", x=20.0, y=20.0),
               _inline("R1", 10.16, 2, x=70.0, y=70.0))
    bars = padblock.board_barriers(b)
    assert list(bars) == ["U1"]
    assert bars["U1"].is_wall


def test_board_gaps_are_canonically_ordered_and_repeatable():
    """Deterministic output is a hard requirement: this feeds a cost the
    annealer compares between candidates, and an order that depended on dict
    iteration would make two identical placements score differently."""
    b = _board(_dip8("U3", x=30.0, y=30.0), _dip8("U1", x=34.0, y=44.0),
               _inline("J2", 2.54, 6, x=32.0, y=37.0))
    first = padblock.board_blocked_gaps(b)
    second = padblock.board_blocked_gaps(b)
    keys = [(g.ref_a, g.pad_a, g.ref_b, g.pad_b) for g in first]
    assert keys == [(g.ref_a, g.pad_a, g.ref_b, g.pad_b) for g in second]
    assert keys == sorted(keys)


# --------------------------------------------------------------------------
# the spatial hash must not change the answer, only the cost
# --------------------------------------------------------------------------

def _naive_gaps(board, *, track=1.0, clearance=0.85, pad_mm=padblock.PAD_MM):
    """Reference implementation: every pad pair against every other pad.

    Same rules, no bucketing, no pruning -- O(N^3) and obviously correct, which
    is the point. The fast path is only allowed to be faster.
    """
    pts = []
    for ref in sorted(board.components):
        c = board.components[ref]
        for i, p in enumerate(c.pads):
            x, y = c.pad_world(p)
            pts.append((ref, i, x, y))
    channel = track + 2 * clearance
    out = []
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            ax, ay, bx, by = pts[a][2], pts[a][3], pts[b][2], pts[b][3]
            d = math.hypot(bx - ax, by - ay)
            if d - pad_mm >= channel:
                continue
            mx, my = (ax + bx) / 2, (ay + by) / 2
            if any(math.hypot(pts[c][2] - mx, pts[c][3] - my) <= d / 2
                   for c in range(len(pts)) if c not in (a, b)):
                continue
            out.append((pts[a][0], pts[a][1], pts[b][0], pts[b][1]))
    return sorted(out)


def _crowded_board(seed):
    """A deliberately over-packed board: parts on top of each other, so tight
    gaps and third-pad-in-the-way cases are everywhere."""
    rng = random.Random(seed)
    parts = []
    for k in range(24):
        n = rng.choice((2, 2, 3, 4))
        pitch = rng.choice((2.0, 2.54, 3.5, 5.0))
        parts.append(_inline(f"P{k:02d}", pitch, n,
                             x=rng.uniform(10.0, 30.0), y=rng.uniform(10.0, 30.0),
                             rot=rng.choice((0, 90, 180, 270))))
    return _board(*parts)


def test_the_bucketed_scan_matches_the_brute_force_reference():
    for seed in range(12):
        b = _crowded_board(seed)
        fast = [(g.ref_a, g.pad_a, g.ref_b, g.pad_b)
                for g in padblock.board_blocked_gaps(b)]
        assert fast == _naive_gaps(b), f"seed={seed}"


def test_the_crowded_boards_actually_exercise_the_analysis():
    """Guards the cross-check above from passing on two empty lists."""
    assert len(padblock.board_blocked_gaps(_crowded_board(0))) > 10


def test_every_reported_gap_fails_the_fit_test_it_was_measured_against():
    """The two halves of the module have to agree exactly: a gap reported as
    blocked must not pass ``track_fits``. Rounding the reported width would be
    enough to break this on a gap sitting on the threshold."""
    for g in padblock.board_blocked_gaps(_crowded_board(3)):
        assert not padblock.track_fits(g.gap, track=1.0, clearance=0.85)


def test_board_and_footprint_views_agree_when_nothing_is_nearby():
    """With the parts well separated, no foreign pad can stand in any gap, so
    the two views have to produce exactly the same internal pairs."""
    b = _board(_dip8("U1", x=15.0, y=15.0), _dip8("U2", x=85.0, y=85.0),
               _inline("J1", 2.54, 5, x=15.0, y=85.0))
    for ref, comp in b.components.items():
        pairs = [(g.pad_a, g.pad_b) for g in padblock.board_blocked_gaps(b)
                 if g.internal and g.ref_a == ref]
        assert pairs == padblock.component_blocked_pairs(comp)


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------

def test_a_board_scan_is_fast_enough_for_the_inner_loop():
    """40 parts including four ICs -- the shape of the real test board. This is
    only useful to a placer if it survives being called thousands of times."""
    parts = [_dip8(f"U{k}", x=20.0 + 18.0 * k, y=25.0) for k in range(4)]
    for k in range(36):
        parts.append(_inline(f"R{k}", 2.54 if k % 3 else 5.08, 2,
                             x=8.0 + 7.0 * (k % 13), y=45.0 + 6.0 * (k // 13)))
    b = _board(*parts)
    padblock.board_blocked_gaps(b)                  # warm any lazy import
    t0 = time.perf_counter()
    runs = 20
    for _ in range(runs):
        padblock.board_blocked_gaps(b)
    per_call_ms = (time.perf_counter() - t0) * 1000.0 / runs
    # Measured at ~0.5 ms for these 104 pads; the budget is 10x that, which
    # leaves room for a slow machine without letting a quadratic regression in.
    assert per_call_ms < 5.0, f"{per_call_ms:.1f} ms per board_blocked_gaps()"
