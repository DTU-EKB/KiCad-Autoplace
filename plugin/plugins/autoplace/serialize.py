"""Serialize a Board model to a plain dict, and back (pure-Python, no pcbnew).

Used by ``cli.py dump`` to feed the Electron app's board canvas. Uses effective
dimensions so a rotated footprint's box matches what KiCad shows.

``board_from_dict`` is the inverse. It exists so a placement can be scored
offline without KiCad in the loop: routing one for real costs 10-60 s, so the
validation set for the global router is routed once and replayed against the
predictor as often as tuning needs. It reverses the effective-dimension
transform, recovering the baseline ``w``/``h`` from ``rot``.
"""
from __future__ import annotations

from .model import Board, Component, Pad


def board_to_dict(board: Board) -> dict:
    return {
        "outline": {"x0": board.x0, "y0": board.y0,
                    "x1": board.x1, "y1": board.y1},
        "edge_keepout": board.edge_keepout,
        "planes": sorted(board.planes),
        "footprints": [
            {
                "ref": c.ref,
                "x": c.x, "y": c.y,
                "w": c.eff_w, "h": c.eff_h,
                "rot": c.rot,
                "block": c.block,
                "sheet": c.sheet,
                "edge": c.edge,
                "value": c.value,
                "fpid": c.fpid,
                "is_connector_guess": c.is_connector,
                "locked": c.locked,
                "height": c.height,
                "pads": [{"net": p.net, "ox": p.ox, "oy": p.oy,
                          "pin_type": p.pin_type, "pin_function": p.pin_function}
                         for p in c.pads],
            }
            for c in board.components.values()
        ],
    }


def board_from_dict(d: dict) -> Board:
    """Rebuild a Board from ``board_to_dict`` output.

    ``w``/``h`` come back as *effective* (post-rotation) dimensions, so they are
    swapped back to the baseline whenever ``rot`` is 90 or 270 -- otherwise a
    rotated part would grow and shrink on every round trip. Pad offsets are
    stored unrotated, so they are restored verbatim and ``pad_world`` reproduces
    the original coordinates exactly.
    """
    o = d["outline"]
    board = Board(o["x0"], o["y0"], o["x1"], o["y1"],
                  edge_keepout=d.get("edge_keepout", 0.0),
                  planes=set(d.get("planes", ())))
    for fp in d.get("footprints", []):
        rot = int(fp.get("rot", 0))
        w, h = fp["w"], fp["h"]
        if rot in (90, 270):
            w, h = h, w                       # undo the effective-dims swap
        board.components[fp["ref"]] = Component(
            ref=fp["ref"], w=w, h=h, x=fp["x"], y=fp["y"], rot=rot,
            locked=fp.get("locked", False),
            is_connector=fp.get("is_connector_guess", False),
            sheet=fp.get("sheet", ""), block=fp.get("block", ""),
            edge=fp.get("edge", ""), value=fp.get("value", ""),
            fpid=fp.get("fpid", ""), height=fp.get("height", 4.0),
            pads=[Pad(name=str(i + 1), net=p.get("net", ""),
                      ox=p["ox"], oy=p["oy"],
                      pin_type=p.get("pin_type", ""),
                      pin_function=p.get("pin_function", ""))
                  for i, p in enumerate(fp.get("pads", []))],
        )
    return board
