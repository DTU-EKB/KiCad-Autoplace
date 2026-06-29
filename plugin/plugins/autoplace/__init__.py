"""DTU-EKB KiCad Autoplace -- connectivity-aware PCB placement engine.

Headless core (no pcbnew): model, metrics, edge, forcedirected, legalize, engine.
pcbnew bridge: kicad_io (imported lazily so the core stays importable anywhere).
"""
from . import edge, engine, forcedirected, legalize, metrics, model  # noqa: F401

__version__ = "0.1.0"
