import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import serialize                              # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402


def test_board_to_dict_shape():
    b = Board(0, 0, 50, 40)
    b.components = {
        "J1": Component("J1", 4, 4, x=10, y=20, is_connector=True, block="b0",
                        value="CONN_2x1", fpid="Connector:PinHeader_2x1",
                        pads=[Pad("1", "SIG", 1.0, 0.0,
                                  pin_type="input", pin_function="RX")]),
    }
    d = serialize.board_to_dict(b)
    assert d["outline"] == {"x0": 0, "y0": 0, "x1": 50, "y1": 40}
    assert len(d["footprints"]) == 1
    fp = d["footprints"][0]
    assert fp["ref"] == "J1"
    assert fp["is_connector_guess"] is True
    assert fp["block"] == "b0"
    assert fp["value"] == "CONN_2x1"
    assert fp["fpid"] == "Connector:PinHeader_2x1"
    assert fp["pads"] == [{"net": "SIG", "ox": 1.0, "oy": 0.0,
                           "pin_type": "input", "pin_function": "RX"}]


def test_board_to_dict_uses_effective_dims_for_rotation():
    b = Board(0, 0, 50, 40)
    c = Component("U1", 10, 4, x=10, y=20, rot=90)
    b.components = {"U1": c}
    fp = serialize.board_to_dict(b)["footprints"][0]
    assert fp["w"] == 4 and fp["h"] == 10        # eff dims at rot=90


# --- round trip ------------------------------------------------------------
# A serialized placement has to come back as a Board so routability can be
# re-scored offline: routing a placement for real costs 10-60 s, so the
# validation set is routed once and replayed against the predictor many times.

def _sample_board():
    b = Board(1.5, 2.5, 51.5, 42.5, edge_keepout=0.75)
    b.planes = {"/GND"}
    b.components = {
        "J1": Component("J1", 4, 6, x=10, y=20, rot=90, is_connector=True,
                        block="b0", sheet="/io/", edge="L", locked=True,
                        value="CONN", fpid="Connector:PinHeader", height=9.0,
                        pads=[Pad("1", "SIG", 1.0, -2.0,
                                  pin_type="input", pin_function="RX"),
                              Pad("2", "/GND", -1.0, 2.0)]),
        "U1": Component("U1", 10, 4, x=30, y=25,
                        pads=[Pad("1", "SIG", 0.0, 1.0)]),
    }
    return b


def test_board_from_dict_round_trips_geometry():
    b = _sample_board()
    r = serialize.board_from_dict(serialize.board_to_dict(b))
    assert (r.x0, r.y0, r.x1, r.y1) == (b.x0, b.y0, b.x1, b.y1)
    assert set(r.components) == set(b.components)
    for ref, c in b.components.items():
        g = r.components[ref]
        assert (g.x, g.y, g.rot) == (c.x, c.y, c.rot)
        assert (g.w, g.h) == (c.w, c.h)          # baseline dims, not effective
        assert (g.eff_w, g.eff_h) == (c.eff_w, c.eff_h)


def test_board_from_dict_round_trips_pad_world_positions():
    """The predictor scores pad geometry, so a round trip that shifted a rotated
    part's pads would silently invalidate every replayed placement."""
    b = _sample_board()
    r = serialize.board_from_dict(serialize.board_to_dict(b))
    for ref, c in b.components.items():
        g = r.components[ref]
        assert [g.pad_world(p) for p in g.pads] == [c.pad_world(p) for p in c.pads]
        assert [p.net for p in g.pads] == [p.net for p in c.pads]


def test_board_from_dict_round_trips_attributes():
    b = _sample_board()
    r = serialize.board_from_dict(serialize.board_to_dict(b))
    j = r.components["J1"]
    assert j.locked is True and j.is_connector is True
    assert (j.block, j.sheet, j.edge) == ("b0", "/io/", "L")
    assert (j.value, j.fpid) == ("CONN", "Connector:PinHeader")
    assert r.components["J1"].pads[0].pin_type == "input"


def test_planes_round_trip():
    """Which nets a pour connects decides what the global router must route."""
    b = _sample_board()
    assert serialize.board_to_dict(b)["planes"] == ["/GND"]
    assert serialize.board_from_dict(serialize.board_to_dict(b)).planes == {"/GND"}


def test_board_planes_defaults_to_empty():
    assert Board(0, 0, 10, 10).planes == set()
