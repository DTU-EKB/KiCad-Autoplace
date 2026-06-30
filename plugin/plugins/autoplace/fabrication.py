"""Fabrication profiles: clearance / track width per manufacturing method.

Pure-Python (no pcbnew). A profile drives the placement margin and the copper
rules written into the *output* project file:

- net classes (``net_settings.classes[*]``): ``clearance`` + ``track_width``,
  read by FreeRouting via DSN export,
- DRC rules (``board.design_settings.rules``): ``min_clearance`` +
  ``min_track_width``, read by KiCad's DRC,

so routing and DRC both match the chosen fabrication. The input board is never
modified -- ``cli.py`` applies this only to the copied output ``.kicad_pro``.
"""
from __future__ import annotations

import json
import os

# clearance / track width in millimetres
PROFILES = {
    "laser": {"clearance": 0.8, "track": 1.0},   # fiber laser (xTool)
    "cnc": {"clearance": 0.85, "track": 1.0},     # CNC mill, 0.8 mm endmill
}


def _profile(fab: str) -> dict:
    try:
        return PROFILES[fab]
    except KeyError:
        raise ValueError(
            f"unknown fabrication {fab!r}; expected one of {sorted(PROFILES)}")


def margin_for(fab: str) -> float:
    """Placement margin (mm) for a fabrication: its copper clearance."""
    return _profile(fab)["clearance"]


def track_for(fab: str) -> float:
    """Track width (mm) for a fabrication profile."""
    return _profile(fab)["track"]


def apply_to_project(pro_path: str, fab: str) -> bool:
    """Write a fabrication's clearance/track into a ``.kicad_pro`` JSON.

    Returns True if the file existed and was updated, False if it is missing.
    Raises ValueError on an unknown fabrication. Leaves unrelated keys intact.
    """
    prof = _profile(fab)
    if not os.path.exists(pro_path):
        return False
    with open(pro_path, encoding="utf-8") as f:
        data = json.load(f)

    for cls in data.get("net_settings", {}).get("classes", []):
        cls["clearance"] = prof["clearance"]
        cls["track_width"] = prof["track"]

    rules = (data.setdefault("board", {})
             .setdefault("design_settings", {})
             .setdefault("rules", {}))
    rules["min_clearance"] = prof["clearance"]
    rules["min_track_width"] = prof["track"]

    with open(pro_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True
