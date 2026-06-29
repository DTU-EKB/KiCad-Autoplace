import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import serialize                              # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402


def test_board_to_dict_shape():
    b = Board(0, 0, 50, 40)
    b.components = {
        "J1": Component("J1", 4, 4, x=10, y=20, is_connector=True, block="b0",
                        pads=[Pad("1", "SIG", 1.0, 0.0)]),
    }
    d = serialize.board_to_dict(b)
    assert d["outline"] == {"x0": 0, "y0": 0, "x1": 50, "y1": 40}
    assert len(d["footprints"]) == 1
    fp = d["footprints"][0]
    assert fp["ref"] == "J1"
    assert fp["is_connector_guess"] is True
    assert fp["block"] == "b0"
    assert fp["pads"] == [{"net": "SIG", "ox": 1.0, "oy": 0.0}]


def test_board_to_dict_uses_effective_dims_for_rotation():
    b = Board(0, 0, 50, 40)
    c = Component("U1", 10, 4, x=10, y=20, rot=90)
    b.components = {"U1": c}
    fp = serialize.board_to_dict(b)["footprints"][0]
    assert fp["w"] == 4 and fp["h"] == 10        # eff dims at rot=90
