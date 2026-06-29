"""Headless tests for fabrication profiles. No pcbnew required.

  python -m pytest tests/test_fabrication.py
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import fabrication                      # noqa: E402


def test_margin_per_profile():
    assert fabrication.margin_for("laser") == 0.8
    assert fabrication.margin_for("cnc") == 0.85


def test_margin_unknown_raises():
    with pytest.raises(ValueError):
        fabrication.margin_for("bogus")


def _project():
    return {
        "net_settings": {
            "classes": [
                {"name": "Default", "clearance": 0.2, "track_width": 0.25,
                 "via_diameter": 0.8},
                {"name": "Power", "clearance": 0.3, "track_width": 0.5},
            ]
        },
        "board": {
            "design_settings": {
                "rules": {"min_clearance": 0.2, "min_track_width": 0.25,
                          "min_hole_to_hole": 0.5},
            }
        },
    }


@pytest.mark.parametrize("fab,clr", [("laser", 0.8), ("cnc", 0.85)])
def test_apply_to_project_sets_all_fields(tmp_path, fab, clr):
    p = tmp_path / "b.kicad_pro"
    p.write_text(json.dumps(_project()), encoding="utf-8")

    assert fabrication.apply_to_project(str(p), fab) is True

    d = json.loads(p.read_text(encoding="utf-8"))
    for c in d["net_settings"]["classes"]:
        assert c["clearance"] == clr
        assert c["track_width"] == 1.0
        # unrelated keys survive
    assert d["net_settings"]["classes"][0]["via_diameter"] == 0.8
    rules = d["board"]["design_settings"]["rules"]
    assert rules["min_clearance"] == clr
    assert rules["min_track_width"] == 1.0
    assert rules["min_hole_to_hole"] == 0.5        # untouched


def test_apply_to_project_missing_file(tmp_path):
    p = tmp_path / "nope.kicad_pro"
    assert fabrication.apply_to_project(str(p), "cnc") is False


def test_apply_to_project_unknown_fab(tmp_path):
    p = tmp_path / "b.kicad_pro"
    p.write_text(json.dumps(_project()), encoding="utf-8")
    with pytest.raises(ValueError):
        fabrication.apply_to_project(str(p), "bogus")
