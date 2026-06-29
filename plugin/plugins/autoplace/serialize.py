"""Serialize a Board model to a plain dict (pure-Python, no pcbnew).

Used by ``cli.py dump`` to feed the Electron app's board canvas. Uses effective
dimensions so a rotated footprint's box matches what KiCad shows.
"""
from __future__ import annotations

from .model import Board


def board_to_dict(board: Board) -> dict:
    return {
        "outline": {"x0": board.x0, "y0": board.y0,
                    "x1": board.x1, "y1": board.y1},
        "footprints": [
            {
                "ref": c.ref,
                "x": c.x, "y": c.y,
                "w": c.eff_w, "h": c.eff_h,
                "rot": c.rot,
                "block": c.block,
                "sheet": c.sheet,
                "edge": c.edge,
                "is_connector_guess": c.is_connector,
                "locked": c.locked,
                "pads": [{"net": p.net, "ox": p.ox, "oy": p.oy} for p in c.pads],
            }
            for c in board.components.values()
        ],
    }
