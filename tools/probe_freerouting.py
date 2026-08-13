#!/usr/bin/env python
"""Is FreeRouting the binding constraint on single-sided boards, or is placement?

The engine gives up on single-sided boards when FreeRouting leaves connections
unrouted, and the user's complaint is that a human can often find a single-sided
layout FreeRouting declares impossible. Two very different diagnoses follow:

* **placement** -- FreeRouting is fine, the placer hands it un-routable geometry;
  fix the placer.
* **the router** -- FreeRouting cannot find single-sided routings that demonstrably
  exist; then a narrow single-layer maze router has to be written.

This tool decides between them with a controlled experiment. It generates small
synthetic boards whose single-sided routability is **proved by construction**: for
every board it also emits a *reference* board with the complete routing already
drawn on one copper layer, and checks that reference with KiCad's own DRC (0
missing connections, 0 clearance errors). A board only enters the experiment once
its reference passes -- so "FreeRouting failed" can never be confused with "no
solution exists".

The same boards, stripped of copper, are then handed to FreeRouting under a sweep
of its command-line settings (``-mp`` passes, ``-us`` board-update strategy,
``-is`` item selection, ``-oit`` optimisation threshold) and scored with the same
DRC-based metric the engine uses (``unrouted.analyse``).

Usage (needs KiCad's bundled python -- this imports pcbnew)::

    & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/probe_freerouting.py \\
        gen    --out DIR
    ... verify   --dir DIR          # DRC the references; proves solutions exist
    ... sweep    --dir DIR --json OUT.json [--passes 1,5,10,30,100] [--variants ...]
    ... report   --json OUT.json

Traps handled (see docs/HANDOFF-routability-driven-placement.md): BOM-free DSN,
FreeRouting 1.9.0 only, ``--refill-zones`` on every DRC, ``GetTracks()`` is not
iterable after ``ImportSpecctraSES`` so boards are re-loaded, and zone fills are
invalidated by a layer move so pours are refilled before saving.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "plugin", "plugins"))

import pcbnew                                                    # noqa: E402

from autoplace import strip as strip_mod                         # noqa: E402
from autoplace import unrouted as unrouted_mod                   # noqa: E402
from autoplace.kicad_io import force_gnd_zones, unrouted_count   # noqa: E402
from autoplace.routing import _flip_to_bottom                    # noqa: E402

DEFAULT_JAR = os.path.expanduser("~/.freerouting/freerouting-1.9.0.jar")

# CNC fabrication profile (autoplace.fabrication.PROFILES["cnc"]) -- the process
# these boards are actually made with, and the one that makes single-sided hard.
TRACK_MM = 1.0
CLEAR_MM = 0.85
PAD_MM = 2.0
DRILL_MM = 0.9

# Centre-to-centre distance a track must keep from a foreign pad, and from a
# foreign track. Every generated reference routing is built to beat these, and
# then checked by DRC rather than trusted.
PAD_KEEPOUT = TRACK_MM / 2 + CLEAR_MM + PAD_MM / 2      # 2.35 mm
TRACK_KEEPOUT = TRACK_MM + CLEAR_MM                      # 1.85 mm


def mm(v: float) -> int:
    return pcbnew.FromMM(float(v))


# --------------------------------------------------------------------------
# board construction
# --------------------------------------------------------------------------
class Builder:
    """Assemble a .kicad_pcb from parts, nets, an outline and reference copper."""

    def __init__(self, name: str):
        self.name = name
        self.pcb = pcbnew.BOARD()
        self.pcb.SetCopperLayerCount(2)
        self._nets: dict[str, "pcbnew.NETINFO_ITEM"] = {}
        self._pads: dict[tuple[str, str], "pcbnew.PAD"] = {}
        self.ref_tracks: list[tuple[str, list[tuple[float, float]]]] = []

    # -- netlist ----------------------------------------------------------
    def net(self, name: str):
        if name not in self._nets:
            n = pcbnew.NETINFO_ITEM(self.pcb, name)
            self.pcb.Add(n)
            self._nets[name] = n
        return self._nets[name]

    def part(self, ref: str, pads: list[tuple[float, float]], origin=(0.0, 0.0),
             pad_mm: float = PAD_MM, drill_mm: float = DRILL_MM):
        """A through-hole part: ``pads`` are (x, y) offsets in mm from ``origin``.

        ``pad_mm`` matters more than it looks: a stock 2.54 mm header has 2.0 mm
        pads, i.e. a 0.54 mm pad-to-pad gap, which **fails the 0.85 mm CNC
        clearance rule before a single track is drawn**. Tight-pitch parts here
        use smaller pads so the board is legal and the experiment measures
        routing, not footprint choice.
        """
        fp = pcbnew.FOOTPRINT(self.pcb)
        fp.SetFPID(pcbnew.LIB_ID("probe", "TH%d_%d" % (len(pads), round(pad_mm * 100))))
        fp.SetPosition(pcbnew.VECTOR2I(mm(origin[0]), mm(origin[1])))
        fp.SetReference(ref)
        fp.SetValue(ref)
        for i, (dx, dy) in enumerate(pads, start=1):
            p = pcbnew.PAD(fp)
            p.SetNumber(str(i))
            p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
            p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            p.SetSize(pcbnew.VECTOR2I(mm(pad_mm), mm(pad_mm)))
            p.SetDrillSize(pcbnew.VECTOR2I(mm(drill_mm), mm(drill_mm)))
            p.SetLayerSet(p.PTHMask())
            p.SetFPRelativePosition(pcbnew.VECTOR2I(mm(dx), mm(dy)))
            p.SetPosition(pcbnew.VECTOR2I(mm(origin[0] + dx), mm(origin[1] + dy)))
            fp.Add(p)
            self._pads[(ref, str(i))] = p
        self.pcb.Add(fp)
        return fp

    def pad_xy(self, ref: str, pad: str) -> tuple[float, float]:
        p = self._pads[(ref, str(pad))].GetPosition()
        return (pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))

    def connect(self, net_name: str, *pads: tuple[str, str]):
        n = self.net(net_name)
        for ref, pad in pads:
            self._pads[(ref, str(pad))].SetNet(n)

    # -- geometry ---------------------------------------------------------
    def outline(self, x0, y0, x1, y1):
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                     ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            s = pcbnew.PCB_SHAPE(self.pcb)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
            s.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.pcb.Add(s)
        self.box = (x0, y0, x1, y1)

    def pour(self, net_name: str, inset: float = 0.5):
        """Full-board copper pour on ``net_name`` (the real boards all have one)."""
        x0, y0, x1, y1 = self.box
        z = pcbnew.ZONE(self.pcb)
        z.SetLayer(pcbnew.F_Cu)
        z.SetNet(self.net(net_name))
        z.SetIsFilled(False)
        z.SetLocalClearance(mm(CLEAR_MM))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        pts = [(x0 + inset, y0 + inset), (x1 - inset, y0 + inset),
               (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)]
        outline = z.Outline()
        outline.NewOutline()
        for x, y in pts:
            outline.Append(mm(x), mm(y))
        z.HatchBorder()
        self.pcb.Add(z)

    def wire(self, net_name: str, points: list[tuple[float, float]]):
        """Record one polyline of the *reference* routing (emitted on F.Cu only)."""
        self.ref_tracks.append((net_name, points))

    # -- output -----------------------------------------------------------
    def save(self, path: str, with_reference: bool):
        if with_reference:
            for net_name, pts in self.ref_tracks:
                n = self.net(net_name)
                for a, b in zip(pts, pts[1:]):
                    t = pcbnew.PCB_TRACK(self.pcb)
                    t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
                    t.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
                    t.SetWidth(mm(TRACK_MM))
                    t.SetLayer(pcbnew.F_Cu)
                    t.SetNet(n)
                    self.pcb.Add(t)
        ds = self.pcb.GetDesignSettings()
        try:
            ds.SetCustomTrackWidth(mm(TRACK_MM))
            ds.SetTrackWidth(mm(TRACK_MM))
        except AttributeError:
            pass
        self.pcb.BuildConnectivity()
        pcbnew.SaveBoard(path, self.pcb)
        _write_project(path)


_PRO_TEMPLATE = {
    "board": {"design_settings": {"rules": {
        "min_clearance": CLEAR_MM, "min_track_width": TRACK_MM,
        "min_copper_edge_clearance": 0.3, "min_hole_clearance": 0.25,
        "min_through_hole_diameter": 0.3, "min_via_annular_width": 0.1,
        "min_via_diameter": 0.5}}},
    "net_settings": {"classes": [{
        "name": "Default", "clearance": CLEAR_MM, "track_width": TRACK_MM,
        "via_diameter": 1.8, "via_drill": 0.8, "microvia_diameter": 0.3,
        "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_width": 0.2,
        "diff_pair_via_gap": 0.25, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
        "priority": 2147483648, "schematic_color": "rgba(0, 0, 0, 0.000)",
        "wire_width": 6, "bus_width": 12}], "meta": {"version": 4}},
    "meta": {"filename": "x.kicad_pro", "version": 3},
}


def _write_project(pcb_path: str):
    pro = os.path.splitext(pcb_path)[0] + ".kicad_pro"
    data = json.loads(json.dumps(_PRO_TEMPLATE))
    data["meta"]["filename"] = os.path.basename(pro)
    with open(pro, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# --------------------------------------------------------------------------
# the board family -- every design ships the single-sided solution with it
# --------------------------------------------------------------------------
def _pour_variant(b: Builder, refs: list[str], gnd_pad: str):
    b.connect("GND", *[(r, gnd_pad) for r in refs])
    b.pour("GND")


def build_chain(n: int, pour: bool = False) -> Builder:
    """N two-pin parts in a row, each tied to the next. Straight 7.6 mm hops.

    The floor of the experiment: if this fails, nothing else matters.
    """
    b = Builder("chain%d%s" % (n, "_gnd" if pour else ""))
    pitch, step, y = 7.62, 15.24, 20.0
    pads = [(0, 0), (pitch, 0)] + ([(pitch / 2, 7.62)] if pour else [])
    for k in range(n):
        b.part("R%d" % (k + 1), pads, origin=(10 + step * k, y))
    for k in range(n - 1):
        b.connect("N%d" % k, ("R%d" % (k + 1), 2), ("R%d" % (k + 2), 1))
        b.wire("N%d" % k, [b.pad_xy("R%d" % (k + 1), 2),
                           b.pad_xy("R%d" % (k + 2), 1)])
    b.outline(0, 0, 20 + step * n, 45 if pour else 40)
    if pour:
        _pour_variant(b, ["R%d" % (k + 1) for k in range(n)], "3")
    return b


def build_serpentine(n: int) -> Builder:
    """The chain folded into rows, so the routing has to turn corners and run
    back between the rows instead of straight down a single line."""
    b = Builder("serp%d" % n)
    pitch, step, rowh, per_row = 7.62, 15.24, 20.0, 4
    order = []
    for k in range(n):
        row, col = divmod(k, per_row)
        if row % 2:
            col = per_row - 1 - col
        b.part("R%d" % (k + 1), [(0, 0), (pitch, 0)],
               origin=(10 + step * col, 15 + rowh * row))
        order.append((k, row))
    for k in range(n - 1):
        ra, rb = "R%d" % (k + 1), "R%d" % (k + 2)
        same_row = order[k][1] == order[k + 1][1]
        forward = order[k][1] % 2 == 0
        a = b.pad_xy(ra, 2 if forward else 1)
        c = b.pad_xy(rb, 1 if (order[k + 1][1] % 2 == 0) else 2)
        b.connect("N%d" % k, (ra, 2 if forward else 1),
                  (rb, 1 if (order[k + 1][1] % 2 == 0) else 2))
        if same_row:
            b.wire("N%d" % k, [a, c])
        else:                                # drop to the gap between rows
            midy = (a[1] + c[1]) / 2
            b.wire("N%d" % k, [a, (a[0], midy), (c[0], midy), c])
    b.outline(0, 0, 20 + step * per_row, 25 + rowh * ((n - 1) // per_row + 1))
    return b


def build_nested(n: int, pour: bool = False, tight: bool = False) -> Builder:
    """2N pads in a line, wired in nested pairs (1-2N, 2-2N-1, ...).

    Single-sided-routable only as concentric arcs -- the router has to plan the
    whole set together, which is exactly the global reasoning a human does by
    hand. The reference draws the arcs, so a solution provably exists.

    ``tight`` packs the arcs at 1.9 mm, a hair over the 1.85 mm two-track minimum,
    and shrinks the outline to match: the reference is then very nearly the *only*
    solution, which is the regime real boards are in.
    """
    b = Builder("nested%d%s%s" % (n, "_gnd" if pour else "", "_t" if tight else ""))
    pitch, y0, depth0, dstep = 5.08, 18.0, 6.0, (1.9 if tight else 2.6)
    pads = [(i * pitch, 0) for i in range(2 * n)]
    b.part("J1", pads, origin=(12, y0))
    for i in range(n):
        left, right = i + 1, 2 * n - i
        depth = depth0 + (n - 1 - i) * dstep
        a = b.pad_xy("J1", left)
        c = b.pad_xy("J1", right)
        b.connect("S%d" % i, ("J1", left), ("J1", right))
        b.wire("S%d" % i, [a, (a[0], y0 + depth), (c[0], y0 + depth), c])
    margin = 3.0 if tight else 12.0
    width = pitch * (2 * n - 1) + 2 * margin + 12
    b.outline(0, 0, width, y0 + depth0 + n * dstep + margin)
    if pour:
        b.part("J2", [(0, 0), (0, 6)], origin=(6, 8))
        b.connect("GND", ("J2", 1), ("J2", 2))
        b.pour("GND")
    return b


def build_wheel(n: int) -> Builder:
    """Hub in the middle, N radial parts, plus a ring joining neighbours.

    A wheel graph: planar, but the ring has to be drawn *outside* every part and
    the spokes *inside*, so a router that treats nets one at a time can trap
    itself.
    """
    b = Builder("wheel%d" % n)
    cx = cy = 60.0
    r_hub, r_in, r_out = 9.0, 22.0, 32.0
    hub_pads = [(r_hub * math.cos(2 * math.pi * i / n),
                 r_hub * math.sin(2 * math.pi * i / n)) for i in range(n)]
    b.part("U1", hub_pads, origin=(cx, cy))
    for i in range(n):
        a = 2 * math.pi * i / n
        b.part("R%d" % (i + 1),
               [(r_in * math.cos(a), r_in * math.sin(a)),
                (r_out * math.cos(a), r_out * math.sin(a))], origin=(cx, cy))
        b.connect("SP%d" % i, ("U1", i + 1), ("R%d" % (i + 1), 1))
        b.wire("SP%d" % i, [b.pad_xy("U1", i + 1), b.pad_xy("R%d" % (i + 1), 1)])
    r_ring = r_out + 6.0
    for i in range(n):
        j = (i + 1) % n
        b.connect("RG%d" % i, ("R%d" % (i + 1), 2), ("R%d" % (j + 1), 2))
        pts = [b.pad_xy("R%d" % (i + 1), 2)]
        for t in range(1, 8):                 # arc outside every part
            a = 2 * math.pi * (i + t / 8.0) / n
            pts.append((cx + r_ring * math.cos(a), cy + r_ring * math.sin(a)))
        pts.append(b.pad_xy("R%d" % (j + 1), 2))
        b.wire("RG%d" % i, pts)
    b.outline(0, 0, 2 * cx, 2 * cy)
    return b


def build_detour(n: int) -> Builder:
    """A wall of foreign pads between source and target, open at one end.

    Straight-line placement metrics say these nets are short; the only legal
    single-sided route is the long way round. Tests whether the router will
    accept a big detour rather than declare failure.
    """
    b = Builder("detour%d" % n)
    b.part("J1", [(0, i * 7.62) for i in range(n)], origin=(15, 20))
    b.part("J2", [(0, i * 7.62) for i in range(n)], origin=(85, 20))
    wall_y0, wall_n = 20.0, n * 3
    b.part("W1", [(0, i * 2.54) for i in range(wall_n)], origin=(50, wall_y0))
    b.connect("WALL", *[("W1", i + 1) for i in range(wall_n)])
    gap_y = wall_y0 - 12.0
    for i in range(n):
        b.connect("D%d" % i, ("J1", i + 1), ("J2", i + 1))
        a, c = b.pad_xy("J1", i + 1), b.pad_xy("J2", i + 1)
        lane = gap_y - 3.0 - i * 2.6
        b.wire("D%d" % i, [a, (a[0] - 4 - i * 2.6, a[1]),
                           (a[0] - 4 - i * 2.6, lane), (c[0] + 4 + i * 2.6, lane),
                           (c[0] + 4 + i * 2.6, c[1]), c])
    b.wire("WALL", [b.pad_xy("W1", 1), b.pad_xy("W1", wall_n)])
    b.outline(0, 0, 110, 30 + n * 7.62)
    return b


def build_bus(n: int, pour: bool = False) -> Builder:
    """Two 2.54 mm headers face to face, wired straight across.

    Header pitch is the tightest geometry these boards ever see: at track 1.0 /
    clearance 0.85 two parallel tracks need 1.85 mm and a track clears a foreign
    pad at 2.35 mm, so 2.54 mm pitch fits -- but only just.
    """
    b = Builder("bus%d%s" % (n, "_gnd" if pour else ""))
    pitch, pad = 2.54, 1.4
    b.part("J1", [(0, i * pitch) for i in range(n)], origin=(15, 20), pad_mm=pad)
    b.part("J2", [(0, i * pitch) for i in range(n)], origin=(55, 20), pad_mm=pad)
    for i in range(n):
        b.connect("B%d" % i, ("J1", i + 1), ("J2", i + 1))
        b.wire("B%d" % i, [b.pad_xy("J1", i + 1), b.pad_xy("J2", i + 1)])
    b.outline(0, 0, 70, 40 + n * pitch)
    if pour:
        b.part("J3", [(0, 0), (0, 6), (40, 0), (40, 6)], origin=(15, 8))
        b.connect("GND", ("J3", 1), ("J3", 2), ("J3", 3), ("J3", 4))
        b.wire("GND", [b.pad_xy("J3", 1), b.pad_xy("J3", 2)])
        b.wire("GND", [b.pad_xy("J3", 3), b.pad_xy("J3", 4)])
        b.wire("GND", [b.pad_xy("J3", 1), b.pad_xy("J3", 3)])
        b.pour("GND")
    return b


def build_revbus(n: int, pour: bool = False, tight: bool = False,
                 zone: bool = True) -> Builder:
    """Two headers wired in *reverse* order -- the classic "needs a jumper" look.

    Straight across, every net crosses every other. The single-sided solution is
    to take all of them the long way round the top as nested U's, which is what a
    person draws by hand and what a net-at-a-time router never discovers. The
    reference below is exactly that drawing, so a solution provably exists.
    """
    tag = ("_gnd" if zone else "_gndpart") if pour else ""
    b = Builder("revbus%d%s%s" % (n, tag, "_t" if tight else ""))
    pitch, x1, x2, y0 = 5.08, 45.0, 85.0, 60.0
    step = 1.9 if tight else 3.0
    b.part("J1", [(0, i * pitch) for i in range(n)], origin=(x1, y0))
    b.part("J2", [(0, i * pitch) for i in range(n)], origin=(x2, y0))
    for i in range(n):
        left, right = i + 1, n - i
        b.connect("R%d" % i, ("J1", left), ("J2", right))
        a, c = b.pad_xy("J1", left), b.pad_xy("J2", right)
        col_l = x1 - 4.0 - i * step           # nested columns on the left
        lane = y0 - 6.0 - i * step            # nested lanes across the top
        col_r = x2 + 4.0 + i * step           # nested columns on the right
        b.wire("R%d" % i, [a, (col_l, a[1]), (col_l, lane), (col_r, lane),
                           (col_r, c[1]), c])
    edge = 3.0 if tight else 12.0
    b.outline(x1 - 4 - n * step - edge, y0 - 6 - n * step - edge,
              x2 + 4 + n * step + edge, y0 + (n - 1) * pitch + edge)
    if pour:
        b.part("J3", [(0, 0), (0, 4)], origin=(x1 - 4 - n * step - edge + 2,
                                               y0 + (n - 1) * pitch + edge - 6))
        b.connect("GND", ("J3", 1), ("J3", 2))
        b.wire("GND", [b.pad_xy("J3", 1), b.pad_xy("J3", 2)])
        if zone:
            b.pour("GND")
    return b


def build_taps(nets: int, parts: int, pour: bool = False) -> Builder:
    """A bus: ``parts`` headers stacked in a column, net j taking pad j of each.

    Every other board here has two-pad nets; real boards are full of 3-to-8-pad
    nets, whose routing is a Steiner tree rather than a wire, and that is a
    different (harder) problem for a router. The single-sided solution is the
    obvious one -- ``nets`` parallel vertical lanes -- and the reference draws it.
    """
    b = Builder("taps%dx%d%s" % (nets, parts, "_gnd" if pour else ""))
    pitch, rowgap, pad = 2.54, 14.0, 1.4
    x0, y0 = 20.0, 20.0
    for r in range(parts):
        b.part("J%d" % (r + 1), [(i * pitch, 0) for i in range(nets)],
               origin=(x0, y0 + r * rowgap), pad_mm=pad)
    for j in range(nets):
        b.connect("T%d" % j, *[("J%d" % (r + 1), j + 1) for r in range(parts)])
        x = x0 + j * pitch
        b.wire("T%d" % j, [(x, y0), (x, y0 + (parts - 1) * rowgap)])
    b.outline(0, 0, x0 + pitch * nets + 20, y0 + rowgap * parts + 6)
    if pour:
        b.part("G1", [(0, 0), (0, 6)], origin=(8, y0))
        b.connect("GND", ("G1", 1), ("G1", 2))
        b.wire("GND", [b.pad_xy("G1", 1), b.pad_xy("G1", 2)])
        b.pour("GND")
    return b


def build_book(k: int, tight: bool = False) -> Builder:
    """4k pads in a row; the odd-position nets nest, the even-position nets nest,
    and the two families interleave with each other.

    The sharpest test in the set. No single-sided routing exists that keeps all
    nets on one side of the row: the router has to send one family *below* and the
    other *above*, i.e. make a global two-page decision before laying any copper.
    A net-at-a-time router that commits the first two arcs to the same side traps
    itself and reports the board impossible -- which is exactly the complaint
    being investigated. The reference draws the two-page solution, so a
    single-sided routing provably exists.
    """
    b = Builder("book%d%s" % (k, "_t" if tight else ""))
    pitch, y0 = 5.08, 60.0
    step = 1.9 if tight else 2.8
    margin = 3.0 if tight else 10.0
    n = 4 * k
    b.part("J1", [(i * pitch, 0) for i in range(n)], origin=(20, y0))
    odd = [i for i in range(n) if i % 2 == 0]        # 0-based: pads 1,3,5,...
    even = [i for i in range(n) if i % 2 == 1]
    for fam, idxs, sign in (("A", odd, +1), ("B", even, -1)):
        m = len(idxs)
        for i in range(m // 2):
            lo, hi = idxs[i] + 1, idxs[m - 1 - i] + 1
            net = "%s%d" % (fam, i)
            b.connect(net, ("J1", lo), ("J1", hi))
            a, c = b.pad_xy("J1", lo), b.pad_xy("J1", hi)
            off = sign * (4.0 + (m // 2 - 1 - i) * step)
            b.wire(net, [a, (a[0], y0 + off), (c[0], y0 + off), c])
    reach = 4.0 + (k - 1) * step + margin
    b.outline(20 - margin - 4, y0 - reach, 20 + pitch * (n - 1) + margin + 4,
              y0 + reach)
    return b


def build_cross2() -> Builder:
    """NEGATIVE CONTROL. Two nets crossing in a box too tight to escape.

    Four pads at the corners of a square, wired as the two diagonals. The board
    edge sits 2.0 mm outside each pad centre, and squeezing a 1.0 mm track past a
    2.0 mm pad needs 0.3 (edge) + 0.5 (half track) + 0.85 (clearance) + 1.0 (pad
    radius) = 2.65 mm -- so neither diagonal can be taken around the other's
    endpoints, and no single-sided routing exists. The harness has to call this
    one a failure, or its successes mean nothing.

    (The first version of this control used a 4 mm margin and FreeRouting
    correctly routed it -- the escape route was real. Negative controls need
    checking as hard as the positives.)
    """
    b = Builder("cross2")
    s, m = 8.0, 2.0
    b.part("J1", [(0, 0), (s, s), (s, 0), (0, s)], origin=(m + 2, m + 2))
    b.connect("A", ("J1", 1), ("J1", 2))       # one diagonal
    b.connect("B", ("J1", 3), ("J1", 4))       # the other
    b.outline(2, 2, s + m + 4, s + m + 4)
    return b


BOARDS = {
    "chain6": lambda: build_chain(6),
    "chain12": lambda: build_chain(12),
    "chain8_gnd": lambda: build_chain(8, pour=True),
    "serp8": lambda: build_serpentine(8),
    "serp12": lambda: build_serpentine(12),
    "nested4": lambda: build_nested(4),
    "nested6": lambda: build_nested(6),
    "nested10": lambda: build_nested(10),
    "nested6_gnd": lambda: build_nested(6, pour=True),
    "wheel5": lambda: build_wheel(5),
    "wheel8": lambda: build_wheel(8),
    "wheel12": lambda: build_wheel(12),
    "detour3": lambda: build_detour(3),
    "detour5": lambda: build_detour(5),
    "bus8": lambda: build_bus(8),
    "bus8_gnd": lambda: build_bus(8, pour=True),
    "revbus4": lambda: build_revbus(4),
    "revbus6": lambda: build_revbus(6),
    "revbus4_gnd": lambda: build_revbus(4, pour=True),
    "revbus6_gnd": lambda: build_revbus(6, pour=True),
    "revbus10": lambda: build_revbus(10),
    "revbus10_gnd": lambda: build_revbus(10, pour=True),
    # same board as revbus*_gnd minus only the copper pour: isolates whether the
    # failure comes from the exported plane or from the extra GND part.
    "revbus6_gndpart": lambda: build_revbus(6, pour=True, zone=False),
    "revbus10_gndpart": lambda: build_revbus(10, pour=True, zone=False),
    "revbus10_t_gnd": lambda: build_revbus(10, pour=True, tight=True),
    "nested10_gnd": lambda: build_nested(10, pour=True),
    "nested6_t": lambda: build_nested(6, tight=True),
    "nested10_t": lambda: build_nested(10, tight=True),
    "revbus6_t": lambda: build_revbus(6, tight=True),
    "revbus10_t": lambda: build_revbus(10, tight=True),
    "book2": lambda: build_book(2),
    "book3": lambda: build_book(3),
    "book5": lambda: build_book(5),
    "book3_t": lambda: build_book(3, tight=True),
    "taps6x4": lambda: build_taps(6, 4),
    "taps8x6": lambda: build_taps(8, 6),
    "taps8x6_gnd": lambda: build_taps(8, 6, pour=True),
    "cross2": build_cross2,
}


# --------------------------------------------------------------------------
# routing + measurement
# --------------------------------------------------------------------------
def route_single_sided(pcb_path: str, jar: str, passes: int,
                       extra: list[str] | None = None, stem: str | None = None,
                       timeout: int = 900) -> dict:
    """``routing.route_once(..., sides=1)`` with extra FreeRouting flags allowed.

    Kept byte-for-byte equivalent to the engine's own single-sided path (strip,
    one copper layer, pour onto F.Cu, fill, export, route, import, refill, flip
    to B.Cu, measure the flipped board) so its numbers describe the engine, not
    this script.
    """
    with open(pcb_path, encoding="utf-8") as f:
        stripped, _ = strip_mod.strip_tracks(f.read())
    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(stripped)
    board = pcbnew.LoadBoard(pcb_path)
    if stem is None:
        stem = os.path.splitext(pcb_path)[0]
    board.SetCopperLayerCount(1)
    for i in range(board.GetAreaCount()):
        z = board.GetArea(i)
        if z.IsOnLayer(pcbnew.B_Cu):
            z.SetLayer(pcbnew.F_Cu)
    force_gnd_zones(board)
    total = unrouted_count(board)
    dsn, ses = stem + ".dsn", stem + ".ses"
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise RuntimeError("DSN export failed for " + pcb_path)
    if os.path.exists(ses):
        os.remove(ses)
    cmd = ["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes)]
    cmd += list(extra or [])
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    dt = time.time() - t0
    if not os.path.exists(ses) or os.path.getsize(ses) == 0:
        tail = (proc.stdout or "")[-600:] + (proc.stderr or "")[-400:]
        return {"total": total, "error": "no SES (exit %s)" % proc.returncode,
                "tail": tail, "seconds": round(dt, 1)}
    pcbnew.ImportSpecctraSES(board, ses)
    force_gnd_zones(board)
    routed_pcb = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(routed_pcb, board)
    _write_project(routed_pcb)
    _flip_to_bottom(routed_pcb)
    fresh = pcbnew.LoadBoard(routed_pcb)
    ratsnest = unrouted_count(fresh)
    # FreeRouting's own verdict, so its opinion can be compared with DRC's.
    say = [ln.strip() for ln in (proc.stdout or "").splitlines()
           if "incomplete" in ln.lower() or "unrouted" in ln.lower()
           or "violation" in ln.lower()]
    return {"total": total, "ratsnest_missing": ratsnest, "seconds": round(dt, 1),
            "routed_pcb": routed_pcb, "cmd": " ".join(cmd[3:]),
            "freerouting_says": say[-3:]}


def drc_missing(pcb_path: str, cli: str | None = None) -> dict:
    """DRC-based truth: how many connections are missing, and clearance errors."""
    res = unrouted_mod.analyse(pcb_path, cli=cli)
    return {"missing": len(res["missing"]), "by_net": res["by_net"],
            "clearance": len(res["clearance"])}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_gen(args):
    os.makedirs(args.out, exist_ok=True)
    made = []
    for name, fn in BOARDS.items():
        if args.only and name not in args.only.split(","):
            continue
        b = fn()
        ref = os.path.join(args.out, name + "_ref.kicad_pcb")
        b.save(ref, with_reference=True)
        b2 = fn()                                     # fresh: no reference copper
        bare = os.path.join(args.out, name + ".kicad_pcb")
        b2.save(bare, with_reference=False)
        made.append(name)
        print("generated %-14s %s" % (name, bare))
    print("\n%d boards in %s" % (len(made), args.out))


def cmd_verify(args):
    """DRC the reference boards: this is what makes 'a solution exists' a fact."""
    rows = []
    for name in sorted(BOARDS):
        ref = os.path.join(args.dir, name + "_ref.kicad_pcb")
        if not os.path.exists(ref):
            continue
        d = drc_missing(ref, args.kicad_cli)
        bare = os.path.join(args.dir, name + ".kicad_pcb")
        nets = drc_missing(bare, args.kicad_cli)["missing"] if os.path.exists(bare) else -1
        ok = d["missing"] == 0 and d["clearance"] == 0
        rows.append({"board": name, "ref_missing": d["missing"],
                     "ref_clearance": d["clearance"], "connections": nets,
                     "single_sided_proved": ok})
        print("%-14s connections=%-3d ref missing=%-3d clearance=%-3d  %s"
              % (name, nets, d["missing"], d["clearance"],
                 "PROVED routable" if ok else "reference NOT clean"))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    return rows


def cmd_sweep(args):
    jar = args.jar
    passes = [int(p) for p in args.passes.split(",")]
    variants = [("mp", [])]
    if args.variants:
        for v in args.variants.split(","):
            variants.append((v, _variant_args(v)))
    boards = [n for n in BOARDS if not args.only or n in args.only.split(",")]
    work = os.path.join(args.dir, "_work")
    os.makedirs(work, exist_ok=True)
    out = []
    for name in boards:
        src = os.path.join(args.dir, name + ".kicad_pcb")
        if not os.path.exists(src):
            continue
        for vname, vargs in variants:
            for p in passes:
                if vname != "mp" and p != args.variant_passes:
                    continue
                stem = os.path.join(work, "%s_%s_p%d" % (name, vname, p))
                pcb = stem + ".kicad_pcb"
                shutil.copy(src, pcb)
                _write_project(pcb)
                try:
                    r = route_single_sided(pcb, jar, p, vargs, stem=stem,
                                           timeout=args.timeout)
                except Exception as exc:                     # noqa: BLE001
                    r = {"error": repr(exc)}
                row = {"board": name, "variant": vname, "passes": p}
                row.update(r)
                if "routed_pcb" in r:
                    try:
                        row.update(drc_missing(r["routed_pcb"], args.kicad_cli))
                    except Exception as exc:                 # noqa: BLE001
                        row["drc_error"] = repr(exc)
                out.append(row)
                print("%-14s %-6s mp=%-4d  missing=%-4s clearance=%-4s %5.1fs %s"
                      % (name, vname, p, row.get("missing", "ERR"),
                         row.get("clearance", "-"), row.get("seconds", 0),
                         row.get("error", "")))
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2)
    print("\nwrote %s (%d rows)" % (args.json, len(out)))


def _variant_args(v: str) -> list[str]:
    """FreeRouting 1.9.0 flags worth sweeping (read out of StartupOptions)."""
    table = {
        "global": ["-us", "global"],       # board update strategy GLOBAL_OPTIMAL
        "hybrid": ["-us", "hybrid", "-hr", "1:1"],
        "seq": ["-is", "seq"],             # item selection SEQUENTIAL
        "rand": ["-is", "rand"],           # item selection RANDOM
        "oit0": ["-oit", "0"],             # never stop improving
        "mt1": ["-mt", "1"],               # single-threaded
        "mt4": ["-mt", "4"],
        "ignore_nc": ["-inc"],             # ignore net classes
    }
    if v not in table:
        raise SystemExit("unknown variant %r; known: %s" % (v, ",".join(table)))
    return table[v]


def cmd_report(args):
    rows = []
    for path in args.json.split(","):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                rows.extend(json.load(f))
    proved = {}
    if args.verify and os.path.exists(args.verify):
        with open(args.verify, encoding="utf-8") as f:
            proved = {r["board"]: r for r in json.load(f)}

    by = {}
    for r in rows:
        by.setdefault((r["board"], r["variant"]), []).append(r)
    print("%-14s %-8s %-6s %-44s %s"
          % ("board", "variant", "proved", "missing by -mp passes", "best"))
    for (board, variant), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["passes"])
        cells = " ".join("%d:%s" % (r["passes"], r.get("missing", "E")) for r in rs)
        best = min((r.get("missing", 999) for r in rs), default=999)
        p = proved.get(board, {}).get("single_sided_proved")
        print("%-14s %-8s %-6s %-44s %s"
              % (board, variant, {True: "yes", False: "NO"}.get(p, "?"), cells, best))

    # the headline: on boards where a single-sided routing PROVABLY exists, how
    # often does FreeRouting find one, and does turning up -mp ever rescue it?
    base = [r for r in rows if r["variant"] == "mp"
            and proved.get(r["board"], {}).get("single_sided_proved")]
    per_board = {}
    for r in base:
        per_board.setdefault(r["board"], {})[r["passes"]] = r.get("missing", 999)
    if per_board:
        n = len(per_board)
        solved_any = sum(1 for v in per_board.values() if min(v.values()) == 0)
        print("\nboards with a proved single-sided solution: %d" % n)
        print("  FreeRouting closed it at some -mp:  %d/%d" % (solved_any, n))
        secs = {}
        for r in base:
            secs.setdefault(r["passes"], []).append(r.get("seconds", 0))
        for p in sorted({p for v in per_board.values() for p in v}):
            ok = sum(1 for v in per_board.values() if v.get(p) == 0)
            have = sum(1 for v in per_board.values() if p in v)
            t = secs.get(p, [0])
            print("  -mp %-4d closed %2d/%-2d (%3.0f%%)   mean %4.1f s  max %4.1f s"
                  % (p, ok, have, 100.0 * ok / max(have, 1),
                     sum(t) / len(t), max(t)))
        rescued = [b for b, v in per_board.items()
                   if v and min(v) in v and v[min(v)] > 0 and min(v.values()) == 0]
        print("  more passes turned a failure into a success on: %s"
              % (", ".join(rescued) if rescued else "NO BOARD"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"routes": rows, "reference_proof": list(proved.values())},
                      f, indent=2)
        print("\nmerged raw data -> %s" % args.out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="write the synthetic boards + reference routings")
    g.add_argument("--out", required=True)
    g.add_argument("--only")
    g.set_defaults(func=cmd_gen)

    v = sub.add_parser("verify", help="DRC the references: prove a solution exists")
    v.add_argument("--dir", required=True)
    v.add_argument("--json")
    v.add_argument("--kicad-cli")
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("sweep", help="route every board at every setting")
    s.add_argument("--dir", required=True)
    s.add_argument("--json", required=True)
    s.add_argument("--jar", default=DEFAULT_JAR)
    s.add_argument("--passes", default="1,5,10,30,100")
    s.add_argument("--variants", default="")
    s.add_argument("--variant-passes", type=int, default=10)
    s.add_argument("--only")
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--kicad-cli")
    s.set_defaults(func=cmd_sweep)

    r = sub.add_parser("report", help="summarise one or more sweep jsons")
    r.add_argument("--json", required=True, help="comma-separated sweep json paths")
    r.add_argument("--verify", help="verify json, to mark which boards are proved")
    r.add_argument("--out", help="write the merged raw data here")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
