"""The only module that imports ``pcbnew``.

Builds a plain :class:`~autoplace.model.Board` from a KiCad board and writes a
computed placement back. Keeping all ``pcbnew`` access here lets the engine and
metrics run headless under any Python for tests and CI.

M2 translates components only (orientation unchanged), so each footprint is moved
by ``new_centre - old_centre`` -- exact, and it sidesteps the bbox-vs-pad1 anchor
quirk the DTU ``pcb_build.py`` has to fight.
"""
from __future__ import annotations

import os
import shutil

import pcbnew

from . import footprints, nets
from .model import Board, Component, Pad

_CONNECTOR_HINTS = ("Connector", "TerminalBlock", "PinHeader", "Screw")


def _mm(v) -> float:
    return pcbnew.ToMM(v)


def _safe(getter, default=""):
    """Call a pcbnew getter, returning ``default`` on any failure / None."""
    try:
        v = getter()
    except Exception:
        return default
    return v if v is not None else default


def _is_connector(fp, fpid: str) -> bool:
    ref = fp.GetReference()
    if ref and ref[0] == "J":
        return True
    return any(h in fpid for h in _CONNECTOR_HINTS)


def build_model(pcb: "pcbnew.BOARD") -> Board:
    """Build a plain Board model from a live pcbnew board (no disk I/O).

    Used by both ``load_board`` (file path) and the Action Plugin (the board
    already open in the editor).
    """
    edge = pcb.GetBoardEdgesBoundingBox()
    board = Board(
        x0=_mm(edge.GetLeft()), y0=_mm(edge.GetTop()),
        x1=_mm(edge.GetRight()), y1=_mm(edge.GetBottom()),
    )
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        bb = fp.GetBoundingBox(False)               # geometry, no text
        cx, cy = _mm(bb.GetCenter().x), _mm(bb.GetCenter().y)
        fpid = _safe(fp.GetFPIDAsString)
        comp = Component(
            ref=ref,
            w=_mm(bb.GetWidth()), h=_mm(bb.GetHeight()),
            x=cx, y=cy,
            locked=fp.IsLocked(),
            is_connector=_is_connector(fp, fpid),
            sheet=_safe(fp.GetSheetname),
            value=_safe(fp.GetValue),
            fpid=fpid,
            height=footprints.height_mm(fpid),
        )
        for pad in fp.Pads():
            pp = pad.GetPosition()
            comp.pads.append(Pad(
                name=pad.GetNumber(),
                net=pad.GetNetname() or "",
                ox=_mm(pp.x) - cx,                   # offset from bbox centre
                oy=_mm(pp.y) - cy,
                pin_type=_safe(pad.GetPinType),
                pin_function=_safe(pad.GetPinFunction),
            ))
        board.components[ref] = comp
    return board


def load_board(path: str) -> tuple[Board, "pcbnew.BOARD"]:
    """Return (model Board, live pcbnew board). Keep the live board to write back."""
    pcb = pcbnew.LoadBoard(path)
    if pcb is None:
        raise RuntimeError(
            f"KiCad could not load {path!r}. The file is likely saved in a newer "
            f"KiCad format than this pcbnew ({pcbnew.GetBuildVersion()}). Open it in "
            f"the matching KiCad version, or run with that version's Python."
        )
    return build_model(pcb), pcb


def copy_project(src_pcb_path: str, dst_pcb_path: str) -> bool:
    """Copy the source board's ``.kicad_pro`` next to the output board.

    Net-class rules (track width, clearance) live in the project file, and
    ``ExportSpecctraDSN`` reads widths from it -- without the project file every
    net falls back to KiCad's 0.2 mm default, so FreeRouting would route
    un-manufacturable hair-thin traces instead of the 1.0 mm fiber-laser tracks.
    """
    src_pro = os.path.splitext(src_pcb_path)[0] + ".kicad_pro"
    dst_pro = os.path.splitext(dst_pcb_path)[0] + ".kicad_pro"
    if os.path.exists(src_pro) and os.path.abspath(src_pro) != os.path.abspath(dst_pro):
        shutil.copyfile(src_pro, dst_pro)
        return True
    return False


def unrouted_count(pcb: "pcbnew.BOARD") -> int:
    """Number of unrouted ratsnest connections (0 == fully routed)."""
    pcb.BuildConnectivity()
    conn = pcb.GetConnectivity()
    try:
        return conn.GetUnconnectedCount(True)      # KiCad 8/9/10 signature
    except TypeError:
        return conn.GetUnconnectedCount()


def apply_to_board(board: Board, pcb: "pcbnew.BOARD"):
    """Apply each footprint's computed rotation + centre to a live board (no save).

    Rotation is applied about the footprint's bounding-box centre first (so the
    model's rotated pad offsets match the board exactly -- verified against
    pcbnew's Rotate(+deg): (x, y) -> (y, -x)), then the part is translated so its
    bbox centre lands on the computed position. Locked footprints are never moved.
    """
    by_ref = {fp.GetReference(): fp for fp in pcb.GetFootprints()}
    for ref, comp in board.components.items():
        fp = by_ref.get(ref)
        if fp is None or fp.IsLocked():
            continue
        if comp.rot:
            centre = fp.GetBoundingBox(False).GetCenter()
            fp.Rotate(centre, pcbnew.EDA_ANGLE(comp.rot, pcbnew.DEGREES_T))
        cur = fp.GetBoundingBox(False).GetCenter()
        dx = pcbnew.FromMM(comp.x) - cur.x
        dy = pcbnew.FromMM(comp.y) - cur.y
        if dx or dy:
            fp.Move(pcbnew.VECTOR2I(int(dx), int(dy)))


def find_gnd_net(pcb: "pcbnew.BOARD"):
    """The board's ground net, matched by leaf name so ``/GND`` counts.

    KiCad prefixes a sheet path (``/GND``, ``/Power/GND``); match the last path
    segment case-insensitively. Returns the NETINFO_ITEM or None.
    """
    for code in range(pcb.GetNetCount()):
        net = pcb.FindNet(code)
        if net is not None and nets.is_gnd_name(net.GetNetname()):
            return net
    return None


def force_gnd_zones(pcb: "pcbnew.BOARD") -> dict:
    """Fill copper pours so the router treats them as planes; ground net-less ones.

    An unfilled (or net-less) copper pour does not connect its net, so FreeRouting
    routes that net as traces -- the reason ground gets routed. Filling each
    B.Cu/F.Cu pour on its own net makes the router see the net as already
    connected and skip it. Pours that already carry a net keep it (so deliberate
    +24V / GND / HEATER_RET planes are NOT clobbered together); only net-less
    pours -- the laser flow's zones -- are assigned to the ground net. Zones on a
    disabled copper layer (e.g. B.Cu after single-sided ``SetCopperLayerCount(1)``)
    are skipped.

    Grounded pours also get thermal-relief pad connection when they ship with
    ``connect_pads no``: a pour whose fill never touches its pads connects
    nothing -- KiCad counts every GND pad as unrouted ratsnest (deflating any
    routed-% built on it) and the etched plane is physically floating. Zones
    that already carry a net keep the designer's pad-connection setting.
    """
    gnd = find_gnd_net(pcb)
    enabled = pcb.GetEnabledLayers()
    filled, grounded = [], []
    for i in range(pcb.GetAreaCount()):
        z = pcb.GetArea(i)
        layers = [name for name, lid in (("B.Cu", pcbnew.B_Cu), ("F.Cu", pcbnew.F_Cu))
                  if z.IsOnLayer(lid) and enabled.Contains(lid)]
        if not layers:
            continue
        if z.GetNetCode() == 0 and gnd is not None:
            z.SetNet(gnd)
            if z.GetPadConnection() == pcbnew.ZONE_CONNECTION_NONE:
                z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            grounded.extend(layers)
        filled.extend(layers)
    if filled:
        pcbnew.ZONE_FILLER(pcb).Fill(pcb.Zones())
    return {"filled": filled, "grounded": grounded}


def apply_placement(board: Board, pcb: "pcbnew.BOARD", out_path: str):
    """Apply the placement to ``pcb`` and save to ``out_path`` (CLI / bench path)."""
    apply_to_board(board, pcb)
    force_gnd_zones(pcb)
    pcbnew.SaveBoard(out_path, pcb)


def tracks_to_dicts(pcb: "pcbnew.BOARD") -> list:
    """Serialize the board's routing (segments, vias, arcs) for the app canvas.

    mm coordinates in board space (same space as the model's footprint centres).
    Arcs are flattened to straight start->end segments -- good enough for a
    preview thumbnail (FreeRouting output is segments + vias anyway). Only works
    on a freshly loaded board: ``GetTracks()`` is not iterable on a board that
    just did ``ImportSpecctraSES`` (KiCad 10 SWIG quirk), so load from disk first.
    """
    out = []
    for t in pcb.GetTracks():
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            out.append({"kind": "via",
                        "x": pcbnew.ToMM(p.x), "y": pcbnew.ToMM(p.y),
                        "d": pcbnew.ToMM(t.GetWidth())})
        else:                                   # PCB_TRACK / PCB_ARC
            s, e = t.GetStart(), t.GetEnd()
            out.append({"kind": "seg",
                        "layer": pcb.GetLayerName(t.GetLayer()),
                        "x1": pcbnew.ToMM(s.x), "y1": pcbnew.ToMM(s.y),
                        "x2": pcbnew.ToMM(e.x), "y2": pcbnew.ToMM(e.y),
                        "w": pcbnew.ToMM(t.GetWidth())})
    return out
