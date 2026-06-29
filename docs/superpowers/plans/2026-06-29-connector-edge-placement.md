# Connector graphical selection + edge placement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user graphically flag connector footprints in the Electron app, then have the engine pin those connectors to the board edge nearest the circuitry they feed (sliding along the edge during optimization), giving the rest of the placement a stable frame.

**Architecture:** Pure-Python engine changes (`edge.py`, `anneal.py`, `legalize.py`, `engine.py`, `serialize.py`) keep `pcbnew` isolated in `kicad_io`. A new `cli.py dump` subcommand emits board geometry as JSON; `cli.py place` reads a sidecar connector list. The Electron app gains a clickable SVG board canvas (picker + result viewer) wired through new IPC handlers.

**Tech Stack:** Python 3 (engine, pure stdlib), pytest (tests, run on system python), Electron/Node (app), vanilla JS + inline SVG (renderer).

## Global Constraints

- Engine core stays free of `pcbnew`; only `kicad_io.py` imports it. New modules `edge.py`, `serialize.py` are pure-Python.
- Unit tests run under system `python` (3.11/3.13 + pytest), NOT KiCad's python: `python -m pytest tests/`.
- KiCad-python-only steps (anything importing `pcbnew`) use `"C:/Program Files/KiCad/10.0/bin/python.exe"`.
- The system board for manual smoke tests: `C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb`.
- Coordinates are millimetres. Edge codes are exactly the strings `"L"`, `"R"`, `"T"`, `"B"`; `""` means "not edge-constrained".
- Connector selection persists in `<board-stem>.autoplace.json` = `{"connectors": ["REF", ...]}`. Never modify the `.kicad_pcb`.
- Do not modify the existing SA quality-selection logic (`anneal._quality` and `run`'s sampling) except where a step explicitly says so.

---

### Task 1: `edge.py` — connector→edge assignment (pure-Python)

**Files:**
- Modify: `plugin/plugins/autoplace/model.py` (add `edge` field to `Component`)
- Create: `plugin/plugins/autoplace/edge.py`
- Test: `tests/test_edge.py`

**Interfaces:**
- Consumes: `Board`, `Component` from `autoplace.model`; `_is_power` from `autoplace.metrics`.
- Produces:
  - `Component.edge: str` (default `""`).
  - `edge.nearest_edge(board, x, y) -> str` — returns `"L"|"R"|"T"|"B"`.
  - `edge.pin_to_edge(c, board, margin=0.8) -> None` — sets the perpendicular coordinate so `c`'s courtyard sits against `c.edge` (no-op if `c.edge==""`).
  - `edge.assign_edges(board, connectors, margin=0.8) -> None` — for each ref in `connectors` that exists and is not locked: set `c.is_connector=True`, set `c.edge`, orient (`rot` 90 for L/R, 0 for T/B), place on the edge at its net-partner centroid projection, then de-collide connectors sharing an edge.

- [ ] **Step 1: Add the `edge` field to `Component`**

In `plugin/plugins/autoplace/model.py`, inside the `Component` dataclass, add the field right after `block`:

```python
    sheet: str = ""                # hierarchical schematic sheet path
    block: str = ""
    edge: str = ""                 # "" free; "L"/"R"/"T"/"B" pinned to that edge
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_edge.py`:

```python
"""Headless tests for connector edge assignment. No pcbnew. Pure Python."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import edge                                   # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402


def _conn(ref, x, y, net, w=4.0, h=4.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y,
                     pads=[Pad("1", net, 0.0, 0.0)])


def _part(ref, x, y, net):
    return Component(ref=ref, w=4.0, h=2.0, x=x, y=y,
                     pads=[Pad("1", net, 0.0, 0.0)])


def test_connector_assigned_to_edge_nearest_its_partners():
    # J1 wired to P1 which sits on the right side -> J1 belongs on edge R.
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": _conn("J1", 50, 30, "SIG"),
        "P1": _part("P1", 92, 30, "SIG"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge == "R"


def test_connector_lands_on_its_edge_line():
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": _conn("J1", 50, 30, "SIG"),
        "P1": _part("P1", 92, 30, "SIG"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    c = b.components["J1"]
    # right edge: right side of courtyard within one margin of the outline edge
    assert abs(c.right - b.x1) <= 0.8 + 1e-6


def test_connectors_on_same_edge_do_not_overlap():
    b = Board(0, 0, 60, 100)
    # two connectors both pulled left
    b.components = {
        "J1": _conn("J1", 30, 40, "A"),
        "J2": _conn("J2", 30, 44, "B"),
        "PA": _part("PA", 4, 40, "A"),
        "PB": _part("PB", 4, 44, "B"),
    }
    edge.assign_edges(b, ["J1", "J2"], margin=0.8)
    a, c = b.components["J1"], b.components["J2"]
    assert a.edge == "L" and c.edge == "L"
    gap = abs(a.y - c.y) - (a.eff_h + c.eff_h) / 2
    assert gap >= -1e-6


def test_connector_with_no_signal_partners_still_gets_an_edge():
    b = Board(0, 0, 100, 60)
    b.components = {"J1": _conn("J1", 10, 30, "")}   # empty net == unconnected
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge in ("L", "R", "T", "B")


def test_locked_connector_is_left_alone():
    b = Board(0, 0, 100, 60)
    b.components = {"J1": _conn("J1", 50, 30, "SIG")}
    b.components["J1"].locked = True
    edge.assign_edges(b, ["J1"], margin=0.8)
    assert b.components["J1"].edge == ""          # untouched
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_edge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoplace.edge'`.

- [ ] **Step 4: Implement `edge.py`**

Create `plugin/plugins/autoplace/edge.py`:

```python
"""Connector edge assignment (pure-Python, no pcbnew).

A connector flagged by the user is pinned to the board edge nearest the
circuitry it feeds, then slides ALONG that edge during annealing (see
``anneal.py``). This module computes the edge and the on-edge position; the
annealer keeps it there via ``pin_to_edge``.
"""
from __future__ import annotations

from .metrics import _is_power
from .model import Board, Component

EDGES = ("L", "R", "T", "B")


def nearest_edge(board: Board, x: float, y: float) -> str:
    """The board edge ('L'/'R'/'T'/'B') closest to point (x, y)."""
    dists = {
        "L": x - board.x0,
        "R": board.x1 - x,
        "T": y - board.y0,
        "B": board.y1 - y,
    }
    return min(dists, key=dists.get)


def pin_to_edge(c: Component, board: Board, margin: float = 0.8) -> None:
    """Set the perpendicular coordinate so c's courtyard sits against c.edge."""
    if c.edge == "L":
        c.x = board.x0 + margin + c.eff_w / 2
    elif c.edge == "R":
        c.x = board.x1 - margin - c.eff_w / 2
    elif c.edge == "T":
        c.y = board.y0 + margin + c.eff_h / 2
    elif c.edge == "B":
        c.y = board.y1 - margin - c.eff_h / 2


def _partner_centroid(board: Board, c: Component) -> tuple[float, float]:
    """Centroid of pad positions on OTHER comps sharing c's signal nets."""
    my_nets = {p.net for p in c.pads if p.net and not _is_power(p.net)}
    pts = []
    for other in board.components.values():
        if other is c:
            continue
        for p in other.pads:
            if p.net in my_nets:
                pts.append(other.pad_world(p))
    if not pts:
        return c.x, c.y
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def _along(c: Component) -> float:
    """The coordinate that varies along c's edge (y on L/R, x on T/B)."""
    return c.y if c.edge in ("L", "R") else c.x


def _set_along(c: Component, v: float) -> None:
    if c.edge in ("L", "R"):
        c.y = v
    else:
        c.x = v


def _clamp_along(c: Component, board: Board, margin: float) -> None:
    if c.edge in ("L", "R"):
        lo, hi = board.y0 + margin + c.eff_h / 2, board.y1 - margin - c.eff_h / 2
    else:
        lo, hi = board.x0 + margin + c.eff_w / 2, board.x1 - margin - c.eff_w / 2
    _set_along(c, min(max(_along(c), lo), hi))


def _span(c: Component) -> float:
    """c's extent along its edge."""
    return c.eff_h if c.edge in ("L", "R") else c.eff_w


def assign_edges(board: Board, connectors, margin: float = 0.8) -> None:
    """Pin each given connector to the edge nearest its net partners."""
    conns = [board.components[r] for r in connectors
             if r in board.components and not board.components[r].locked]
    for c in conns:
        c.is_connector = True
        cx, cy = _partner_centroid(board, c)
        c.edge = nearest_edge(board, cx, cy)
        c.rot = 90 if c.edge in ("L", "R") else 0
        _set_along(c, cy if c.edge in ("L", "R") else cx)
        pin_to_edge(c, board, margin)
        _clamp_along(c, board, margin)
    # de-collide connectors sharing an edge: sort along the edge, push apart
    for e in EDGES:
        group = sorted((c for c in conns if c.edge == e), key=_along)
        for i in range(1, len(group)):
            prev, cur = group[i - 1], group[i]
            need = (_span(prev) + _span(cur)) / 2 + margin
            if _along(cur) - _along(prev) < need:
                _set_along(cur, _along(prev) + need)
        for c in group:
            _clamp_along(c, board, margin)
            pin_to_edge(c, board, margin)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_edge.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add plugin/plugins/autoplace/model.py plugin/plugins/autoplace/edge.py tests/test_edge.py
git commit -m "feat(engine): connector->edge assignment (edge.py) + Component.edge field"
```

---

### Task 2: Annealer slides edge connectors along their edge

**Files:**
- Modify: `plugin/plugins/autoplace/anneal.py`
- Test: `tests/test_engine.py` (add one test)

**Interfaces:**
- Consumes: `edge.pin_to_edge` from `autoplace.edge`; `Component.edge`.
- Produces: annealer behavior — a component with `edge != ""` only ever moves along its edge line (perpendicular coordinate stays pinned); rotate/swap never touch edge connectors.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
def test_edge_connector_stays_on_its_edge_through_anneal():
    from autoplace import anneal, edge
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": Component("J1", 4, 4, x=50, y=30,
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "R1": _two_pin("R1", 20, 20, "SIG", "N1"),
        "R2": _two_pin("R2", 80, 40, "N1", "N2"),
        "R3": _two_pin("R3", 60, 10, "N2", "GND"),
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    j = b.components["J1"]
    assert j.edge in ("L", "R", "T", "B")
    pinned_axis = j.x if j.edge in ("L", "R") else j.y
    anneal.anneal(b, seed=0, steps=3000, margin=0.8)
    j = b.components["J1"]
    moved_axis = j.x if j.edge in ("L", "R") else j.y
    assert abs(moved_axis - pinned_axis) <= 1e-6   # never left the edge line
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_edge_connector_stays_on_its_edge_through_anneal -v`
Expected: FAIL — the connector drifts off the edge line (nudge moves both axes).

- [ ] **Step 3: Import edge helper and precompute the movable list**

In `plugin/plugins/autoplace/anneal.py`, change the import block near the top:

```python
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power
from .model import Board
```

In `Annealer.__init__`, right after `self.free = [c for c in self.comps if not c.locked]`, add:

```python
        # parts the rotate/swap moves may touch (edge connectors are excluded:
        # they keep their assigned orientation and only slide along their edge)
        self.movable = [c for c in self.free if not c.edge]
```

- [ ] **Step 4: Make nudge edge-aware**

Replace `Annealer._try_nudge` with:

```python
    def _try_nudge(self, T):
        c = self.rng.choice(self.free)
        ox, oy = c.x, c.y
        before = self.local_cost((c,))
        amp = max(1.0, T)
        if c.edge:
            d = (self.rng.random() - 0.5) * 2 * amp
            if c.edge in ("L", "R"):
                c.y += d
            else:
                c.x += d
            pin_to_edge(c, self.board, self.margin)
        else:
            c.x += (self.rng.random() - 0.5) * 2 * amp
            c.y += (self.rng.random() - 0.5) * 2 * amp
        self._clamp(c)
        after = self.local_cost((c,))
        return self._accept(after - before, T, lambda: self._revert1(c, ox, oy))
```

- [ ] **Step 5: Exclude edge connectors from rotate and swap**

In `_try_rotate`, replace `c = self.rng.choice(self.free)` with:

```python
        if not self.movable:
            return None
        c = self.rng.choice(self.movable)
```

In `_try_swap`, replace the opening `if len(self.free) < 2: return None` and the sample line:

```python
    def _try_swap(self):
        if len(self.movable) < 2:
            return None
        a, b = self.rng.sample(self.movable, 2)
```

(Leave the rest of `_try_swap` unchanged.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (all tests, including the new one and the existing `test_anneal_returns_best_quality_not_lowest_cost`).

- [ ] **Step 7: Commit**

```bash
git add plugin/plugins/autoplace/anneal.py tests/test_engine.py
git commit -m "feat(engine): edge connectors slide along their edge during anneal"
```

---

### Task 3: Legalizer keeps edge connectors on their edge

**Files:**
- Modify: `plugin/plugins/autoplace/legalize.py`
- Test: `tests/test_engine.py` (add one test)

**Interfaces:**
- Consumes: `Component.edge`.
- Produces: `legalize` / `push_apart` treat edge connectors as fixed obstacles (like locked) — they are never pushed off their edge and not grid-snapped off it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
def test_legalize_keeps_edge_connector_on_edge():
    from autoplace import edge, legalize
    b = Board(0, 0, 100, 60)
    b.components = {
        "J1": Component("J1", 4, 4, x=50, y=30,
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "R1": _two_pin("R1", 90, 30, "SIG", "N1"),   # pulls J1 to edge R
    }
    edge.assign_edges(b, ["J1"], margin=0.8)
    x_before = b.components["J1"].x
    legalize.legalize(b, grid=0.5, margin=0.8)
    assert abs(b.components["J1"].x - x_before) <= 1e-6
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_legalize_keeps_edge_connector_on_edge -v`
Expected: FAIL — legalize snaps/pushes the connector off its edge x.

- [ ] **Step 3: Exclude edge connectors from the legalizer's free set**

In `plugin/plugins/autoplace/legalize.py`, in `push_apart`, change:

```python
    free = {c.ref for c in board.free()}
```
to:
```python
    # edge connectors are fixed obstacles here: they were already placed on the
    # edge and slid along it during annealing; legalize must not move them off.
    free = {c.ref for c in board.free() if not c.edge}
```

In `legalize`, change the snap loop:

```python
    for c in board.free():
        if c.edge:
            continue
        c.x = _snap(c.x, grid)
        c.y = _snap(c.y, grid)
        _clamp(c, board, margin)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/legalize.py tests/test_engine.py
git commit -m "feat(engine): legalize treats edge connectors as fixed"
```

---

### Task 4: `engine.place` accepts an explicit connector set

**Files:**
- Modify: `plugin/plugins/autoplace/engine.py`
- Test: `tests/test_engine.py` (add one test)

**Interfaces:**
- Consumes: `edge.assign_edges`.
- Produces: `engine.place(board, *, seed=0, grid=0.5, margin=0.8, iters=400, sa_steps=None, strategy="auto", progress=None, connectors=None)`. When `connectors` is a list, it is the authoritative connector set (overrides the `is_connector` auto-guess); those parts are pinned to edges. `None` keeps today's behavior.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
def test_place_pins_explicit_connectors_to_edges():
    # hierarchical board so the floorplan path runs; J1 wired into block A
    b = Board(0, 0, 120, 80)
    b.components = {
        "J1": Component("J1", 4, 4, x=60, y=40, sheet="/A/",
                        pads=[Pad("1", "SIG", 0.0, 0.0)]),
        "A1": _two_pin("A1", 20, 20, "SIG", "a1"),
        "A2": _two_pin("A2", 24, 20, "a1", "a2"),
        "B1": _two_pin("B1", 100, 60, "b1", "b2"),
        "B2": _two_pin("B2", 96, 60, "b2", "b3"),
    }
    b.components["A1"].sheet = b.components["A2"].sheet = "/A/"
    b.components["B1"].sheet = b.components["B2"].sheet = "/B/"
    engine.place(b, seed=0, connectors=["J1"])
    j = b.components["J1"]
    assert j.edge in ("L", "R", "T", "B")
    # courtyard sits against its edge within one margin
    on_edge = (
        abs(j.left - b.x0) <= 0.8 + 1e-6 or abs(j.right - b.x1) <= 0.8 + 1e-6 or
        abs(j.top - b.y0) <= 0.8 + 1e-6 or abs(j.bottom - b.y1) <= 0.8 + 1e-6
    )
    assert on_edge
    assert metrics.overlaps(b) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_place_pins_explicit_connectors_to_edges -v`
Expected: FAIL — `place()` got an unexpected keyword argument `connectors`.

- [ ] **Step 3: Add the parameter and wire in edge assignment**

In `plugin/plugins/autoplace/engine.py`, add `edge as edge_mod` to the import:

```python
from . import (anneal, blocks, edge as edge_mod, floorplan as floorplan_mod,
               forcedirected, legalize as legal_mod, metrics)
```

Change the `place` signature to add `connectors=None`:

```python
def place(board: Board, *, seed: int = 0, grid: float = 0.5, margin: float = 0.8,
          iters: int = 400, sa_steps: int | None = None,
          strategy: str = "auto", progress=None,
          connectors: list[str] | None = None) -> dict:
```

Immediately after `before = metrics.summary(board)` and its `_report("analyze", 0.05)`, add the override:

```python
    # An explicit connector set (from the app's sidecar) overrides the
    # refdes/footprint auto-guess: exactly these refs are connectors.
    if connectors is not None:
        conn_set = set(connectors)
        for c in board.components.values():
            c.is_connector = c.ref in conn_set
```

Then, right after the seed block (after the `_report("seed", 0.15)` line), before the `if sa_steps:` block, add:

```python
    if connectors:
        edge_mod.assign_edges(board, connectors, margin=margin)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/engine.py tests/test_engine.py
git commit -m "feat(engine): engine.place accepts explicit connectors, pins them to edges"
```

---

### Task 5: `serialize.py` + `cli.py dump` + sidecar read in `cli.py place`

**Files:**
- Create: `plugin/plugins/autoplace/serialize.py`
- Modify: `cli.py`
- Test: `tests/test_serialize.py`

**Interfaces:**
- Consumes: `Board` model; `engine.place(..., connectors=...)`.
- Produces:
  - `serialize.board_to_dict(board) -> dict` with keys `outline` and `footprints` (each footprint: `ref,x,y,w,h,rot,block,sheet,is_connector_guess,locked,pads`).
  - `cli.py dump <board>` prints that dict as one JSON line.
  - `cli.py place` reads `<stem>.autoplace.json` and passes its `connectors` to `engine.place`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_serialize.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import serialize                              # noqa: E402
from autoplace.model import Board, Component, Pad            # noqa: E402


def test_board_to_dict_shape():
    b = Board(0, 0, 50, 40)
    b.components = {
        "J1": Component("J1", 4, 4, x=10, y=20, is_connector=True, block="b0",
                        pads=[Pad("1", "SIG", 1.0, 0.0)]),
    }
    d = serialize.board_to_dict(b)
    assert d["outline"] == {"x0": 0, "y0": 0, "x1": 50, "y1": 40}
    assert len(d["footprints"]) == 1
    fp = d["footprints"][0]
    assert fp["ref"] == "J1"
    assert fp["is_connector_guess"] is True
    assert fp["block"] == "b0"
    assert fp["pads"] == [{"net": "SIG", "ox": 1.0, "oy": 0.0}]


def test_board_to_dict_uses_effective_dims_for_rotation():
    b = Board(0, 0, 50, 40)
    c = Component("U1", 10, 4, x=10, y=20, rot=90)
    b.components = {"U1": c}
    fp = serialize.board_to_dict(b)["footprints"][0]
    assert fp["w"] == 4 and fp["h"] == 10        # eff dims at rot=90
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_serialize.py -v`
Expected: FAIL — `No module named 'autoplace.serialize'`.

- [ ] **Step 3: Implement `serialize.py`**

Create `plugin/plugins/autoplace/serialize.py`:

```python
"""Serialize a Board model to a plain dict (pure-Python, no pcbnew).

Used by ``cli.py dump`` to feed the Electron app's board canvas. Uses effective
dimensions so a rotated footprint's box matches what KiCad shows.
"""
from __future__ import annotations

from .model import Board


def board_to_dict(board: Board) -> dict:
    return {
        "outline": {"x0": board.x0, "y0": board.y0,
                    "x1": board.x1, "y1": board.y1},
        "footprints": [
            {
                "ref": c.ref,
                "x": c.x, "y": c.y,
                "w": c.eff_w, "h": c.eff_h,
                "rot": c.rot,
                "block": c.block,
                "sheet": c.sheet,
                "edge": c.edge,
                "is_connector_guess": c.is_connector,
                "locked": c.locked,
                "pads": [{"net": p.net, "ox": p.ox, "oy": p.oy} for p in c.pads],
            }
            for c in board.components.values()
        ],
    }
```

- [ ] **Step 4: Run the serialize tests to verify they pass**

Run: `python -m pytest tests/test_serialize.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add `dump` subcommand and sidecar read to `cli.py`**

In `cli.py`, add a helper and the `cmd_dump` function, and read the sidecar in `cmd_place`.

Add after the imports (`from autoplace import engine, kicad_io`):

```python
def _read_connectors(in_path):
    """Read the connector ref list from <stem>.autoplace.json, or None."""
    side = os.path.splitext(in_path)[0] + ".autoplace.json"
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                return json.load(f).get("connectors")
        except Exception:
            return None
    return None
```

In `cmd_place`, change the `engine.place(...)` call to pass connectors:

```python
    connectors = _read_connectors(in_path)
    report = engine.place(model, seed=seed, strategy=strategy,
                          connectors=connectors, progress=progress)
```

Add the `cmd_dump` function:

```python
def cmd_dump(args):
    """Emit board geometry as one JSON line for the desktop canvas."""
    from autoplace import blocks, serialize
    model, _ = kicad_io.load_board(args[0])
    blocks.detect_blocks(model)
    sys.stdout.write(json.dumps(serialize.board_to_dict(model)) + "\n")
    return 0
```

Update `main` to register and accept `dump`:

```python
def main(argv):
    if len(argv) < 2 or argv[1] not in ("place", "metrics", "dump"):
        print(__doc__)
        return 2
    return {"place": cmd_place, "metrics": cmd_metrics,
            "dump": cmd_dump}[argv[1]](argv[2:])
```

- [ ] **Step 6: Run the full unit suite (no regressions)**

Run: `python -m pytest tests/ -v`
Expected: PASS (all).

- [ ] **Step 7: Manual smoke — `dump` under KiCad python**

Run:
```bash
"/c/Program Files/KiCad/10.0/bin/python.exe" cli.py dump "C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb" | python -c "import sys,json; d=json.load(sys.stdin); print('footprints', len(d['footprints']), 'outline', d['outline'])"
```
Expected: prints `footprints 131 outline {...}` with non-empty outline.

- [ ] **Step 8: Commit**

```bash
git add plugin/plugins/autoplace/serialize.py cli.py tests/test_serialize.py
git commit -m "feat(cli): add dump subcommand and sidecar connector read"
```

---

### Task 6: App IPC — dump board, load/save connectors

**Files:**
- Modify: `app/main.js`
- Modify: `app/preload.js`

**Interfaces:**
- Consumes: `cli.py dump` (Task 5), sidecar path convention.
- Produces (on `window.api`):
  - `dumpBoard({python, board}) -> {ok, geometry}|{ok:false, error}`
  - `loadConnectors({board}) -> string[]|null`
  - `saveConnectors({board, connectors}) -> boolean`

- [ ] **Step 1: Add the IPC handlers in `main.js`**

In `app/main.js`, add a sidecar helper near `CLI_PY`:

```javascript
function sidecarPath(board) {
  return board.replace(/\.kicad_pcb$/i, "") + ".autoplace.json";
}
```

Add a `dumpBoard` function next to `runPlace`:

```javascript
function dumpBoard(python, board) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    let proc;
    try {
      proc = spawn(python, [CLI_PY, "dump", board], { cwd: REPO_ROOT });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    let out = "";
    let err = "";
    proc.stdout.on("data", (d) => (out += d.toString()));
    proc.stderr.on("data", (d) => (err += d.toString()));
    proc.on("error", (e) => resolve({ ok: false, error: e.message }));
    proc.on("close", (code) => {
      if (code !== 0) {
        return resolve({ ok: false, error: err.trim() || `dump exited ${code}` });
      }
      try {
        resolve({ ok: true, geometry: JSON.parse(out) });
      } catch (e) {
        resolve({ ok: false, error: "bad dump JSON: " + e.message });
      }
    });
  });
}
```

In `registerIpc`, add the three handlers (next to `ipcMain.handle("run-place", ...)`):

```javascript
  ipcMain.handle("dump-board", (_e, { python, board }) =>
    dumpBoard(python, board)
  );

  ipcMain.handle("load-connectors", (_e, { board }) => {
    const p = sidecarPath(board);
    try {
      if (fs.existsSync(p)) {
        return JSON.parse(fs.readFileSync(p, "utf8")).connectors || null;
      }
    } catch {
      /* fall through */
    }
    return null;
  });

  ipcMain.handle("save-connectors", (_e, { board, connectors }) => {
    try {
      fs.writeFileSync(sidecarPath(board), JSON.stringify({ connectors }, null, 2));
      return true;
    } catch {
      return false;
    }
  });
```

- [ ] **Step 2: Expose them in `preload.js`**

In `app/preload.js`, add to the `exposeInMainWorld("api", { ... })` object:

```javascript
  dumpBoard: (opts) => ipcRenderer.invoke("dump-board", opts),
  loadConnectors: (opts) => ipcRenderer.invoke("load-connectors", opts),
  saveConnectors: (opts) => ipcRenderer.invoke("save-connectors", opts),
```

- [ ] **Step 3: Manual verification — IPC reachable**

Launch the app (deps already installed): from `app/`, run `npm start`. Open DevTools (the app passes `--dev` via `npm run dev`, or run `npm run dev`). In the console:
```javascript
await window.api.saveConnectors({board: "X:/tmp/foo.kicad_pcb", connectors: ["J1"]});
await window.api.loadConnectors({board: "X:/tmp/foo.kicad_pcb"});
```
Expected: `saveConnectors` returns `true`; `loadConnectors` returns `["J1"]`. Delete `X:/tmp/foo.autoplace.json` afterward.

- [ ] **Step 4: Commit**

```bash
git add app/main.js app/preload.js
git commit -m "feat(app): IPC for board dump and connector sidecar load/save"
```

---

### Task 7: App renderer — clickable board canvas (picker + result viewer)

**Files:**
- Modify: `app/renderer/index.html`
- Modify: `app/renderer/renderer.js`
- Modify: `app/renderer/styles.css`

**Interfaces:**
- Consumes: `window.api.dumpBoard`, `loadConnectors`, `saveConnectors` (Task 6); geometry shape from `serialize.board_to_dict` (Task 5).
- Produces: a board canvas that renders footprints, lets the user click to toggle connectors (saved to the sidecar), and re-renders the result after a run.

- [ ] **Step 1: Add the board-view section to `index.html`**

In `app/renderer/index.html`, insert this `<section>` between the setup card (`</section>` after `progressWrap`) and the results card:

```html
      <!-- Board view -->
      <section id="boardView" class="card board-view" hidden>
        <div class="results-head">
          <h2>Board</h2>
          <span class="muted">click parts to mark them as connectors (placed on edges)</span>
        </div>
        <div id="boardCanvas" class="board-canvas"></div>
        <div class="board-foot">
          <span id="connCount" class="muted">0 connectors</span>
          <span id="boardMode" class="muted"></span>
        </div>
      </section>
```

- [ ] **Step 2: Add canvas styles to `styles.css`**

Append to `app/renderer/styles.css`:

```css
.board-canvas { width: 100%; overflow: auto; background: #0b0d12; border-radius: 8px; }
.board-canvas svg { display: block; width: 100%; height: auto; }
.board-canvas .fp { cursor: pointer; }
.board-canvas .fp rect { stroke-width: 0.4; }
.board-canvas .fp.conn rect { stroke: #ffd166; stroke-width: 1.2; }
.board-canvas .fp text { font: 2px sans-serif; fill: #cfd3dc; pointer-events: none; }
.board-foot { display: flex; justify-content: space-between; margin-top: 8px; }
```

- [ ] **Step 3: Add rendering + interaction to `renderer.js`**

In `app/renderer/renderer.js`, extend the `state` object:

```javascript
const state = {
  python: null,
  board: null,
  running: false,
  geometry: null,
  connectors: new Set(),
};
```

Add the block-color palette and render function (place near the top, after `const $ = ...`):

```javascript
const BLOCK_COLORS = [
  "#2a78d6", "#1baf7a", "#eda100", "#4a3aa7", "#e87ba4",
  "#e34948", "#199e70", "#d95926", "#9085e9", "#888781",
];
function blockColor(block, blockList) {
  const i = blockList.indexOf(block);
  return BLOCK_COLORS[(i < 0 ? 0 : i) % BLOCK_COLORS.length];
}

function renderBoard(geom) {
  const host = $("boardCanvas");
  const o = geom.outline;
  const W = o.x1 - o.x0;
  const H = o.y1 - o.y0;
  const blockList = [...new Set(geom.footprints.map((f) => f.block))].sort();
  const parts = geom.footprints
    .map((f) => {
      const x = f.x - f.w / 2 - o.x0;
      const y = f.y - f.h / 2 - o.y0;
      const conn = state.connectors.has(f.ref);
      const col = blockColor(f.block, blockList);
      return (
        `<g class="fp${conn ? " conn" : ""}" data-ref="${f.ref}">` +
        `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(f.w, 0.5).toFixed(2)}" ` +
        `height="${Math.max(f.h, 0.5).toFixed(2)}" fill="${col}" fill-opacity="0.5" stroke="${col}"/>` +
        `<text x="${(x + 0.3).toFixed(2)}" y="${(y + 2).toFixed(2)}">${f.ref}</text>` +
        `</g>`
      );
    })
    .join("");
  host.innerHTML =
    `<svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">` +
    `<rect x="0" y="0" width="${W.toFixed(1)}" height="${H.toFixed(1)}" fill="none" stroke="#333"/>` +
    parts +
    `</svg>`;
  host.querySelectorAll(".fp").forEach((g) => {
    g.addEventListener("click", () => toggleConnector(g.dataset.ref));
  });
  updateConnCount();
}

function updateConnCount() {
  $("connCount").textContent = `${state.connectors.size} connectors`;
}

async function toggleConnector(ref) {
  if (state.connectors.has(ref)) state.connectors.delete(ref);
  else state.connectors.add(ref);
  await window.api.saveConnectors({
    board: state.board,
    connectors: [...state.connectors],
  });
  renderBoard(state.geometry);
}

async function loadBoardView() {
  if (!state.python || !state.board) return;
  $("boardMode").textContent = "loading…";
  const res = await window.api.dumpBoard({
    python: state.python,
    board: state.board,
  });
  if (!res.ok) {
    $("boardMode").textContent = "could not render board";
    appendLog("dump error: " + res.error);
    return;
  }
  state.geometry = res.geometry;
  const saved = await window.api.loadConnectors({ board: state.board });
  state.connectors = new Set(
    saved ||
      res.geometry.footprints
        .filter((f) => f.is_connector_guess)
        .map((f) => f.ref)
  );
  $("boardView").hidden = false;
  $("boardMode").textContent = "before placement";
  renderBoard(state.geometry);
}
```

Wire `loadBoardView` into the board picker — in `pickBoard`, after `refreshRunEnabled();` add:

```javascript
  loadBoardView();
```

After a successful run, show the result on the canvas. In `run()`, inside `if (res.ok) { ... }`, after `showResults(res.report, res.output);` add:

```javascript
    const dump = await window.api.dumpBoard({
      python: state.python,
      board: res.output,
    });
    if (dump.ok) {
      state.geometry = dump.geometry;
      $("boardMode").textContent = "after placement";
      renderBoard(state.geometry);
    }
```

Finally, in `init()`, after the dev-board branch sets `state.board`, add `loadBoardView();` so a preloaded dev board renders too:

```javascript
  if (dev && dev.board) {
    state.board = dev.board;
    $("boardPath").textContent = dev.board;
    $("boardPath").classList.remove("muted");
    refreshRunEnabled();
    loadBoardView();
    if (dev.autorun && state.python) run();
  }
```

- [ ] **Step 4: Manual verification — full flow on the system board**

From `app/`:
```bash
AUTOPLACE_DEV_BOARD="C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb" npm run dev
```
Verify, in order:
1. The board canvas renders all 131 footprints with refdes labels, colored by block.
2. Auto-detected connectors (refdes `J*`, test points) start highlighted (yellow outline); the count matches.
3. Clicking a part toggles its highlight and updates the count; a `system.autoplace.json` appears next to the board.
4. Click "Run AutoPlacement"; after it finishes, the canvas switches to "after placement" and the flagged connectors sit on the board edges.
5. Restart the app on the same board — the connector selection is restored from the sidecar.

- [ ] **Step 5: Commit**

```bash
git add app/renderer/index.html app/renderer/renderer.js app/renderer/styles.css
git commit -m "feat(app): clickable board canvas for connector selection and result view"
```

---

## Self-Review

**Spec coverage:**
- Board view in app → Task 7. Click-to-toggle connector → Task 7. Persistence sidecar → Tasks 5 (read), 6 (load/save), 7 (use). Connectors on edge nearest circuitry → Task 1. Slide along edge → Task 2. Legalize respects edge → Task 3. Engine override of auto-guess → Task 4. `cli.py dump` geometry → Task 5. Canvas as result viewer → Task 7. Error handling (dump failure surfaced, missing sidecar → auto-guess, no-net connector → nearest edge by position) → Tasks 1, 5, 6, 7. All spec sections covered.
- Out of scope per spec (decap-near-IC, rotation/alignment of interior, traces on canvas) → intentionally absent.

**Placeholder scan:** No TBD/TODO; every code step has full code; every test step has full assertions.

**Type consistency:** `Component.edge` (str) defined in Task 1, consumed in Tasks 2/3/4/5/7. `edge.assign_edges(board, connectors, margin)` defined Task 1, called Tasks 2-test/3-test/4. `edge.pin_to_edge` defined Task 1, used Task 2. `serialize.board_to_dict` defined Task 5, consumed Task 7 (geometry shape: `outline`, `footprints[].{ref,x,y,w,h,rot,block,sheet,edge,is_connector_guess,locked,pads}`). `window.api.dumpBoard/loadConnectors/saveConnectors` defined Task 6, used Task 7. `engine.place(..., connectors=)` defined Task 4, called Task 5. Consistent throughout.
