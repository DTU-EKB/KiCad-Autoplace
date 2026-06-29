"""Pure-Python tests for SES congestion parsing. No pcbnew."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import congestion                            # noqa: E402
from autoplace.model import Board, Component, Pad           # noqa: E402

# A minimal SES in the real KiCad format: resolution um 10 (coord/10000 = mm),
# Y negated. Two dense wires + a via packed in the bottom-left model corner
# (model x~5-15mm, y~5-15mm => ses x 50000-150000, y -50000..-150000), and one
# short wire far away in the top-right.
SAMPLE_SES = """(session test
  (routes
    (resolution um 10)
    (network_out
      (net A
        (wire (path F.Cu 10000 50000 -50000 150000 -50000 150000 -150000))
        (wire (path B.Cu 10000 50000 -150000 150000 -50000))
        (via "Via[0-1]" 100000 -100000)
      )
      (net B
        (wire (path F.Cu 10000 1900000 -1900000 1910000 -1900000))
      )
    )
  )
)
"""


def _board():
    b = Board(0, 0, 200, 200)
    # net A pads near the bottom-left corner; net B pads near top-right
    b.components = {
        "A1": Component("A1", 2, 2, x=5, y=5, pads=[Pad("1", "A", 0, 0)]),
        "A2": Component("A2", 2, 2, x=15, y=15, pads=[Pad("1", "A", 0, 0)]),
        "B1": Component("B1", 2, 2, x=190, y=190, pads=[Pad("1", "B", 0, 0)]),
        "B2": Component("B2", 2, 2, x=191, y=190, pads=[Pad("1", "B", 0, 0)]),
    }
    return b


def _write(tmp_path, text):
    p = os.path.join(tmp_path, "s.ses")
    with open(p, "w") as f:
        f.write(text)
    return p


def test_parse_marks_crowded_corner_hotter(tmp_path):
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert not field.empty
    hot = field.pressure_at(10, 10)       # crowded corner (wires + via)
    cold = field.pressure_at(190, 190)    # single short wire
    assert hot > cold
    assert hot > 0.0


def test_pressure_zero_outside_and_for_empty(tmp_path):
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert field.pressure_at(1e6, 1e6) == 0.0          # far outside grid
    empty = congestion.parse(_write(tmp_path, "(session x (routes))"), _board())
    assert empty.empty
    assert empty.pressure_at(10, 10) == 0.0


def test_high_detour_net_adds_pressure(tmp_path):
    # net A routed length is large vs its straight pad span (5,5)->(15,15);
    # detour pressure should land in the corner cell.
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert field.pressure_at(10, 10) > 0.0
