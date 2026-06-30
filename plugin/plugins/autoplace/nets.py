"""Net-name helpers (pure-Python, no pcbnew)."""
from __future__ import annotations


def is_gnd_name(name: str) -> bool:
    """True if a net's leaf segment is exactly GND.

    KiCad prefixes a sheet path, so the ground net reads ``/GND`` or
    ``/Power/GND``; match the last path segment case-insensitively. Distinct
    grounds like ``AGND`` / ``DGND`` / ``GND_MCU`` are intentionally NOT matched.
    """
    return name.rsplit("/", 1)[-1].upper() == "GND"
