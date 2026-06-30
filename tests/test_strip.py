"""Headless tests for textual track stripping. No pcbnew required.

  python -m pytest tests/test_strip.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import strip                             # noqa: E402


BOARD = """(kicad_pcb
  (footprint "R_0805"
    (at 10 10)
    (pad "1" smd (net 3 "GND"))
  )
  (segment (start 1 1) (end 2 2) (width 1) (layer "B.Cu") (net 3))
  (via (at 5 5) (size 0.8) (layers "F.Cu" "B.Cu") (net 3))
  (arc (start 1 1) (mid 1.5 1.2) (end 2 2) (layer "B.Cu") (net 3))
  (zone (net 3) (net_name "GND") (layer "B.Cu"))
)"""


def test_removes_tracks_keeps_structure():
    out, removed = strip.strip_tracks(BOARD)
    assert removed == 3                        # 1 segment + 1 via + 1 arc
    assert "(segment" not in out
    assert "(via" not in out
    assert "(arc" not in out
    # footprint, pad, and zone survive
    assert "(footprint" in out
    assert '(pad "1"' in out
    assert "(zone" in out


def test_keeps_nets_section():
    # a (net ...) declaration is not a track and must survive
    txt = '(kicad_pcb\n  (net 3 "GND")\n  (segment (start 0 0) (end 1 1) (net 3))\n)'
    out, removed = strip.strip_tracks(txt)
    assert removed == 1
    assert '(net 3 "GND")' in out
    assert "(segment" not in out


def test_handles_parens_in_quoted_strings():
    txt = '(kicad_pcb\n  (gr_text "a (b) c" (at 0 0))\n  (via (at 1 1) (net 0))\n)'
    out, removed = strip.strip_tracks(txt)
    assert removed == 1
    assert '(gr_text "a (b) c"' in out         # quoted parens didn't confuse matching


def test_no_tracks_is_noop():
    txt = '(kicad_pcb\n  (footprint "X")\n)'
    out, removed = strip.strip_tracks(txt)
    assert removed == 0
    assert out == txt
