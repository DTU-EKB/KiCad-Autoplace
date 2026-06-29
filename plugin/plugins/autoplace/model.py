"""Plain-Python placement data model.

Deliberately free of any ``pcbnew`` dependency so the placement engine can be
unit-tested and run headless. ``kicad_io`` is the only module that touches
``pcbnew`` and it builds / writes back these objects.

Coordinates are millimetres. A component is represented by its bounding-box
*centre* plus pad offsets relative to that centre, captured at the footprint's
current orientation. The M2 engine only translates components (no rotation), so
those offsets stay constant and HPWL is exact.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pad:
    name: str
    net: str          # "" when unconnected
    ox: float         # offset from component centre (mm), current orientation
    oy: float


@dataclass
class Component:
    ref: str
    w: float                       # bbox width (mm) at the captured (rot=0) baseline
    h: float                       # bbox height (mm)
    pads: list[Pad] = field(default_factory=list)
    x: float = 0.0                 # bbox-centre position (mm)
    y: float = 0.0
    rot: int = 0                   # extra rotation vs baseline: 0/90/180/270
    locked: bool = False
    is_connector: bool = False
    sheet: str = ""                # hierarchical schematic sheet path
    block: str = ""
    edge: str = ""                 # "" free; "L"/"R"/"T"/"B" pinned to that edge

    def pad_world(self, pad: Pad) -> tuple[float, float]:
        # Matches pcbnew Rotate(centre, +deg): (x, y) -> (y, -x) per 90 deg.
        ox, oy = pad.ox, pad.oy
        r = self.rot
        if r == 90:
            ox, oy = oy, -ox
        elif r == 180:
            ox, oy = -ox, -oy
        elif r == 270:
            ox, oy = -oy, ox
        return (self.x + ox, self.y + oy)

    @property
    def eff_w(self) -> float:
        return self.h if self.rot in (90, 270) else self.w

    @property
    def eff_h(self) -> float:
        return self.w if self.rot in (90, 270) else self.h

    @property
    def left(self) -> float:
        return self.x - self.eff_w / 2

    @property
    def right(self) -> float:
        return self.x + self.eff_w / 2

    @property
    def top(self) -> float:
        return self.y - self.eff_h / 2

    @property
    def bottom(self) -> float:
        return self.y + self.eff_h / 2


@dataclass
class Board:
    x0: float
    y0: float
    x1: float
    y1: float
    components: dict[str, Component] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def free(self) -> list[Component]:
        """Components the engine is allowed to move."""
        return [c for c in self.components.values() if not c.locked]

    def nets(self) -> dict[str, list[tuple[str, int]]]:
        """net name -> list of (component ref, pad index). Skips empty nets."""
        out: dict[str, list[tuple[str, int]]] = {}
        for c in self.components.values():
            for i, p in enumerate(c.pads):
                if p.net:
                    out.setdefault(p.net, []).append((c.ref, i))
        return out
