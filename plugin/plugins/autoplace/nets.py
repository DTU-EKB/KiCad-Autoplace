"""Net-name + net-role helpers (pure-Python, no pcbnew)."""
from __future__ import annotations

import re

# Net leaf names treated as ground references.
_GROUND_LEAVES = {"GND", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EARTH"}
# Explicit power-rail leaf names (beyond the +N / -N numeric pattern).
_POWER_LEAVES = {"VCC", "VDD", "VBAT", "VIN", "VOUT", "VBUS", "VMOT", "VDDA", "VCCA", "VSS"}
_POWER_RE = re.compile(r"^[+-]\d")                       # +15V2, +5V_PWR, -15V
_SENSE_RE = re.compile(r"SENSE|ISNS|ISEN|VSEN|FB|FEEDBACK|VREF|ADC")


def is_gnd_name(name: str) -> bool:
    """True if a net's leaf segment is exactly GND.

    KiCad prefixes a sheet path, so the ground net reads ``/GND`` or
    ``/Power/GND``; match the last path segment case-insensitively. Distinct
    grounds like ``AGND`` / ``DGND`` / ``GND_MCU`` are intentionally NOT matched.
    """
    return name.rsplit("/", 1)[-1].upper() == "GND"


def _leaf(name: str) -> str:
    return name.rsplit("/", 1)[-1].upper()


def classify_net(board, net: str) -> str:
    """Coarse electrical role of a net: GROUND | POWER | SENSE | SIGNAL | NC.

    Pure, deterministic, heuristic (no pcbnew). First match wins:
      NC      unconnected (name 'unconnected-...' or every member pad no_connect)
      GROUND  is_gnd_name OR leaf in a ground set (GND/AGND/DGND/PGND/...)
      POWER   any member pad pin_type carries power_in/power_out, OR a power-rail name
      SENSE   feedback / sense / ADC / VREF name
      SIGNAL  everything else (switch nodes, gate drives, auto-named nets)

    The name sets/regexes are tunable heuristics, not exhaustive. ``VSS`` is
    treated as POWER (a rail), not GROUND; ``SW``/``GATE`` are SIGNAL (the
    aggressor/victim axis is a separate Phase 3 concern).
    """
    members = board.nets().get(net, [])
    pin_types = []
    for ref, idx in members:
        comp = board.components.get(ref)
        if comp is not None and 0 <= idx < len(comp.pads):
            pin_types.append(comp.pads[idx].pin_type or "")

    if net.startswith("unconnected-"):
        return "NC"
    if pin_types and all("no_connect" in pt for pt in pin_types):
        return "NC"

    leaf = _leaf(net)
    if is_gnd_name(net) or leaf in _GROUND_LEAVES:
        return "GROUND"
    if any("power_in" in pt or "power_out" in pt for pt in pin_types):
        return "POWER"
    if _POWER_RE.match(leaf) or leaf in _POWER_LEAVES or "VCC" in leaf or "VDD" in leaf:
        return "POWER"
    if _SENSE_RE.search(leaf):
        return "SENSE"
    return "SIGNAL"
