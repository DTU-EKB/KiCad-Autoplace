"""Footprint-class -> nominal THT body height (mm), pure-Python (no pcbnew).

Coarse by design: only the tall/short distinction matters for DFM spacing
(keeping small parts clear of tall parts so a rework tool fits). Unknown
footprints fall back to a low height, so a mis-classified part simply gets no
clearance halo rather than a spurious one.
"""
from __future__ import annotations

import re

_CP_DIA = re.compile(r"CP_RADIAL_D(\d+(?:\.\d+)?)")


def height_mm(fpid: str) -> float:
    f = fpid.upper()
    if "TO-220" in f or "TO-247" in f or "TO-126" in f:
        return 18.0                              # vertical power transistor
    if "CP_RADIAL" in f:                          # electrolytic, height ~ diameter + leads
        m = _CP_DIA.search(f)
        return (float(m.group(1)) + 4.0) if m else 12.0
    if "TOROID" in f or "INDUCTOR" in f:
        return 22.0
    if "POTENTIOMETER" in f or "3296W" in f:
        return 10.0
    if "TERMINALBLOCK" in f or "BORNIER" in f:
        return 11.0
    if "PINHEADER" in f and "VERTICAL" in f:
        return 9.0
    if "SW_DIP" in f or "SWITCH" in f:
        return 6.0
    if "C_DISC" in f:
        return 6.0
    if "DIP-" in f:
        return 5.0
    if "HORIZONTAL" in f:                         # axial R/D lying flat
        return 3.0
    return 4.0                                    # low-profile default
