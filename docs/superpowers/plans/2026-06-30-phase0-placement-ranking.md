# Phase 0 Placement Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HPWL-only candidate selection with a deterministic, explainable two-level ranking (cheap proxies → route the finalists), surface the new signals in the gallery, and add fab-derived spacing controls (`edge_keepout`, cross-block gutters).

**Architecture:** Pure-Python engine additions (new `geom.py`, new `ranking.py`, three new `metrics.py` proxies, a shared `channel_width` helper) feed the existing multiseed/CLI/Electron pipeline. Ranking lives in the multiseed/CLI layer, never in `anneal._quality`. Only `cli.py` route-top-2 touches `pcbnew`/FreeRouting, exactly as `refine.py` already does.

**Tech Stack:** Python 3 (engine, plain `pytest`, no `pcbnew` in tests), KiCad 10 `pcbnew` + Java FreeRouting (route step only), Electron (Node `main.js` + browser `renderer.js`).

## Global Constraints

- **Engine stays `pcbnew`-free and unit-tested on plain Python.** Only `kicad_io.py`, `routing.py`, and the new `cli.py` route step import `pcbnew`. Verbatim from spec §4.4.
- **`anneal._quality` is never modified.** No electrical/aesthetic term is added to the selection metric. Ranking lives in the multiseed/CLI layer. Verbatim from spec §4.2.
- **Defaults reproduce today's output exactly — except D4.** `edge_keepout=0.0` and the ranking fallback are byte-identical; D2 proxies are additive fields. D4 (cross-block gutter) is the one intentional layout change. Verbatim from spec §4.1.
- **Determinism per seed.** Every proxy and the ranking key are pure functions of board geometry; the ranking key is a total order (seed is the final tiebreak). Verbatim from spec §4.3.
- **Fabrication values (verbatim, `fabrication.py:20-23`):** laser `clearance=0.8, track=1.0`; cnc `clearance=0.85, track=1.0`. `margin` passed to the engine equals the profile clearance.
- **Channel math (verbatim, spec §6 D4):** `channel_width(margin, track) = track + 2*margin` (= 2.6 at laser defaults); cross-block gutter `= track + margin`, scaled by `channel_scale`.
- **Overlap barrier weight `_Weights.OVERLAP = 60.0`; `CONG_K = 3.0`; `CHANNEL = 4.0`** (`anneal.py:31-37`) — unchanged.
- **Tests run with `python -m pytest tests/`** (plain Python; in-memory `Board`/`Component`/`Pad`, no `.kicad_pcb`).
- **Commits: developer voice, no AI attribution** (repo convention).

---

### Task 1: Fix the stale rotation docstring (D5)

**Files:**
- Modify: `plugin/plugins/autoplace/engine.py:1-9`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (comment-only).

- [ ] **Step 1: Confirm the suite is green before touching anything**

Run: `python -m pytest tests/ -q`
Expected: all tests pass (baseline).

- [ ] **Step 2: Replace the stale docstring lines**

In `plugin/plugins/autoplace/engine.py`, replace lines 3-4:

```python
Pipeline: detect blocks -> seed -> force-directed global -> SA refine -> legalize.
(Rotation moves remain the last open M4 item; this pass is translation + swap.)
```

with:

```python
Pipeline: detect blocks -> seed -> force-directed global -> SA refine
(translation, rotation, and swap moves) -> legalize.
```

- [ ] **Step 3: Verify nothing broke**

Run: `python -m pytest tests/ -q`
Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add plugin/plugins/autoplace/engine.py
git commit -m "Fix stale rotation docstring in engine.py"
```

---

### Task 2: Shared clamp helper + `Board.edge_keepout` (D3)

**Files:**
- Create: `plugin/plugins/autoplace/geom.py`
- Modify: `plugin/plugins/autoplace/model.py:77-83` (add `edge_keepout` field)
- Modify: `plugin/plugins/autoplace/legalize.py:12-13,20-23` (delegate `_clamp`)
- Modify: `plugin/plugins/autoplace/forcedirected.py:15,18-21` (delegate `_clamp_to_board`)
- Modify: `plugin/plugins/autoplace/anneal.py:25-28,163-167` (delegate `_clamp`)
- Test: `tests/test_edge_keepout.py`

**Interfaces:**
- Produces: `geom.clamp_center(c: Component, board: Board, margin: float) -> None` — clamps `c.x/c.y` so its eff-bbox stays inside the outline inset by `margin + board.edge_keepout`. `Board.edge_keepout: float = 0.0`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_edge_keepout.py`:

```python
"""Headless tests for the shared clamp helper + Board.edge_keepout. No pcbnew.

  python -m pytest tests/test_edge_keepout.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import engine, geom                   # noqa: E402
from autoplace.model import Board, Component, Pad     # noqa: E402


def _two_pin(ref, x, y, neta, netb, w=2.0, h=1.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y, pads=[
        Pad("1", neta, -w / 2 + 0.2, 0.0),
        Pad("2", netb, w / 2 - 0.2, 0.0),
    ])


def _board():
    b = Board(0, 0, 60, 60)
    b.components = {
        "R1": _two_pin("R1", 5, 5, "VIN", "N1"),
        "R2": _two_pin("R2", 55, 55, "N1", "N2"),
        "R3": _two_pin("R3", 5, 55, "N2", "N3"),
        "R4": _two_pin("R4", 55, 5, "N3", "GND"),
    }
    return b


def test_clamp_center_no_keepout_matches_margin():
    b = Board(0, 0, 20, 20)                 # edge_keepout defaults to 0.0
    c = Component("C", 4, 4, x=100, y=100)
    geom.clamp_center(c, b, 0.8)
    assert c.x == 20 - 2 - 0.8              # x1 - half_w - (margin + 0)
    assert c.y == 20 - 2 - 0.8


def test_clamp_center_insets_by_keepout():
    b = Board(0, 0, 20, 20, edge_keepout=2.0)
    c = Component("C", 4, 4, x=-100, y=-100)
    geom.clamp_center(c, b, 0.8)
    assert c.x == 0 + 2 + 0.8 + 2.0         # x0 + half_w + margin + keepout
    assert c.y == 0 + 2 + 0.8 + 2.0


def test_place_respects_edge_keepout():
    b = _board()
    b.edge_keepout = 3.0
    engine.place(b, seed=0)
    for c in b.components.values():
        assert c.left >= b.x0 + 3.0 - 1e-6
        assert c.right <= b.x1 - 3.0 + 1e-6
        assert c.top >= b.y0 + 3.0 - 1e-6
        assert c.bottom <= b.y1 - 3.0 + 1e-6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_edge_keepout.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoplace.geom'` (and `Board.__init__` has no `edge_keepout`).

- [ ] **Step 3: Create the shared helper**

Create `plugin/plugins/autoplace/geom.py`:

```python
"""Shared geometry helpers (pure-Python, no pcbnew)."""
from __future__ import annotations

from .model import Board, Component


def clamp_center(c: Component, board: Board, margin: float) -> None:
    """Clamp a component centre so its effective bbox stays inside the outline,
    inset by ``margin + board.edge_keepout`` on every side. ``edge_keepout``
    defaults to 0.0, so the inset reduces to ``margin`` -- identical to the
    pre-existing per-phase clamps."""
    inset = margin + board.edge_keepout
    hw, hh = c.eff_w / 2, c.eff_h / 2
    c.x = min(max(c.x, board.x0 + hw + inset), board.x1 - hw - inset)
    c.y = min(max(c.y, board.y0 + hh + inset), board.y1 - hh - inset)
```

- [ ] **Step 4: Add the `Board.edge_keepout` field**

In `plugin/plugins/autoplace/model.py`, change the `Board` dataclass body (lines 78-83) from:

```python
@dataclass
class Board:
    x0: float
    y0: float
    x1: float
    y1: float
    components: dict[str, Component] = field(default_factory=dict)
```

to:

```python
@dataclass
class Board:
    x0: float
    y0: float
    x1: float
    y1: float
    components: dict[str, Component] = field(default_factory=dict)
    edge_keepout: float = 0.0      # extra inward inset from the outline (mm); 0 == legacy
```

- [ ] **Step 5: Delegate the three clamps to the helper**

In `plugin/plugins/autoplace/legalize.py`, change the imports (lines 12-13) from:

```python
from .model import Board, Component
from .metrics import overlaps
```

to:

```python
from . import geom
from .model import Board, Component
from .metrics import overlaps
```

and replace `_clamp` (lines 20-23) with:

```python
def _clamp(c: Component, board: Board, margin: float):
    geom.clamp_center(c, board, margin)
```

In `plugin/plugins/autoplace/forcedirected.py`, change the import (line 15) from:

```python
from .model import Board, Component
```

to:

```python
from . import geom
from .model import Board, Component
```

and replace `_clamp_to_board` (lines 18-21) with:

```python
def _clamp_to_board(c: Component, board: Board, margin: float):
    geom.clamp_center(c, board, margin)
```

In `plugin/plugins/autoplace/anneal.py`, change the imports (lines 25-28) from:

```python
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power
from .model import Board
```

to:

```python
from . import geom
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power
from .model import Board
```

and replace `Annealer._clamp` (lines 163-167) with:

```python
    def _clamp(self, c):
        geom.clamp_center(c, self.board, self.margin)
```

- [ ] **Step 6: Run the new test and the full suite**

Run: `python -m pytest tests/test_edge_keepout.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: all tests pass (default `edge_keepout=0.0` preserves today's behavior — the identity gate).

- [ ] **Step 7: Commit**

```bash
git add plugin/plugins/autoplace/geom.py plugin/plugins/autoplace/model.py plugin/plugins/autoplace/legalize.py plugin/plugins/autoplace/forcedirected.py plugin/plugins/autoplace/anneal.py tests/test_edge_keepout.py
git commit -m "Add Board.edge_keepout via one shared clamp helper"
```

---

### Task 3: Three cheap proxies + shared channel-width helper (D2 engine side)

**Files:**
- Modify: `plugin/plugins/autoplace/metrics.py:10-12` (add `import math`), add `CELL_MM`/`SPREAD_LO`/`SPREAD_HI` constants and four functions
- Modify: `plugin/plugins/autoplace/congestion.py:17,57` (default `cell_mm` from `metrics.CELL_MM`)
- Modify: `plugin/plugins/autoplace/fabrication.py:34-36` (add `track_for`)
- Modify: `plugin/plugins/autoplace/multiseed.py:15,19-21,38-46` (compute proxies, add `track` param)
- Modify: `cli.py:115-117` (pass `track`)
- Test: `tests/test_metrics_proxies.py`
- Test: `tests/test_multiseed.py:40-46` (assert new keys)

**Interfaces:**
- Produces: `metrics.channel_width(margin: float, track: float) -> float`; `metrics.sheet_spread_score(board) -> float` (lower better, 0.0 sentinel); `metrics.pinch_fraction(board, margin: float, track: float=1.0) -> float` (lower better); `metrics.whitespace_connectivity(board, cell_mm: float=CELL_MM) -> float` (higher better); `metrics.CELL_MM=5.0`; `fabrication.track_for(fab: str) -> float`.
- Consumes: `geom`/`Board` geometry only.

- [ ] **Step 1: Write the failing proxy tests**

Create `tests/test_metrics_proxies.py`:

```python
"""Headless tests for the Phase 0 cheap placement proxies. No pcbnew.

  python -m pytest tests/test_metrics_proxies.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import metrics                         # noqa: E402
from autoplace.model import Board, Component, Pad      # noqa: E402


def _part(ref, x, y, w=4.0, h=4.0, sheet="", locked=False, edge=""):
    return Component(ref=ref, w=w, h=h, x=x, y=y, sheet=sheet,
                     locked=locked, edge=edge, pads=[Pad("1", "N", 0.0, 0.0)])


def test_channel_width_is_track_plus_two_clearances():
    assert metrics.channel_width(0.8, 1.0) == 2.6     # laser default == today
    assert metrics.channel_width(0.85, 1.0) == 2.7    # cnc (the fixed value)


def test_sheet_spread_single_sheet_is_zero_sentinel():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 10, 10), "B": _part("B", 20, 20)}  # sheet ""
    # one qualifying sheet -> not enough to judge spread -> sentinel
    assert metrics.sheet_spread_score(b) == 0.0


def test_sheet_spread_excludes_locked_and_edge():
    # Two sheets, each with two movable parts spread sanely + a far locked part
    # that would wreck the bbox if counted.
    b = Board(0, 0, 100, 100)
    b.components = {
        "A1": _part("A1", 20, 20, sheet="/A/"),
        "A2": _part("A2", 30, 30, sheet="/A/"),
        "A3": _part("A3", 95, 95, sheet="/A/", locked=True),
        "B1": _part("B1", 60, 60, sheet="/B/"),
        "B2": _part("B2", 70, 70, sheet="/B/"),
        "B3": _part("B3", 5, 5, sheet="/B/", edge="L"),
    }
    score = metrics.sheet_spread_score(b)
    assert isinstance(score, float)
    assert score >= 0.0          # deterministic, defined; locked/edge ignored


def test_pinch_fraction_close_pair_is_pinched():
    b = Board(0, 0, 60, 60)
    # gap along x = 7.5 - 4 = 3.5 ... set so 0 <= gap < channel(2.6) is FALSE,
    # then bring them closer so gap < channel is TRUE.
    b.components = {"A": _part("A", 20, 20), "B": _part("B", 25.5, 20)}  # gx = 1.5
    # gx = |25.5-20| - (4+4)/2 = 5.5 - 4 = 1.5 ; gy = -4 -> shadow; gap=1.5<2.6 -> pinch
    assert metrics.pinch_fraction(b, 0.8, 1.0) == 1.0


def test_pinch_fraction_far_pair_not_pinched():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 5, 5), "B": _part("B", 55, 55)}      # far apart
    assert metrics.pinch_fraction(b, 0.8, 1.0) == 0.0                    # no shadow


def test_whitespace_connectivity_open_board_is_one():
    b = Board(0, 0, 60, 60)
    b.components = {"A": _part("A", 30, 30)}            # one small part in the middle
    # all empty cells stay 4-connected around the single obstacle
    assert metrics.whitespace_connectivity(b) == 1.0


def test_whitespace_connectivity_full_board_is_zero():
    b = Board(0, 0, 10, 10)
    b.components = {"A": _part("A", 5, 5, w=20, h=20)}  # covers the whole grid
    assert metrics.whitespace_connectivity(b) == 0.0
```

- [ ] **Step 2: Run the proxy tests to verify they fail**

Run: `python -m pytest tests/test_metrics_proxies.py -q`
Expected: FAIL — `AttributeError: module 'autoplace.metrics' has no attribute 'channel_width'`.

- [ ] **Step 3: Add `import math` and the constants/functions to `metrics.py`**

In `plugin/plugins/autoplace/metrics.py`, change the imports (lines 10-12) from:

```python
from __future__ import annotations

from .model import Board
```

to:

```python
from __future__ import annotations

import math

from .model import Board
```

Then append to the end of `plugin/plugins/autoplace/metrics.py`:

```python
# Cell size (mm) for the whitespace / congestion grid -- one source of truth,
# reused by congestion.parse.
CELL_MM = 5.0

# Per-sheet fill-ratio band the floorplan targets (_DENSITY=0.5). Below SPREAD_LO
# a sheet is over-spread; above SPREAD_HI it is cramped. Both route worse, so the
# score penalises deviation outside the band.
SPREAD_LO = 0.35
SPREAD_HI = 0.6


def channel_width(margin: float, track: float) -> float:
    """Clear gap (mm) that fits one routing track between two courtyards:
    clearance + track + clearance, where ``margin`` is the copper clearance."""
    return track + 2 * margin


def sheet_spread_score(board: Board) -> float:
    """Mean per-sheet penalty for fill ratio outside ``[SPREAD_LO, SPREAD_HI]``.

    Lower is better (0.0 == every qualifying sheet sits in the target band).
    Movable parts only: locked and edge-pinned parts are excluded because they
    sit where the board forces them and would distort a sheet's bounding box.
    Fewer than two qualifying sheets (>=2 movable parts each) returns 0.0, so
    single-sheet boards rank purely on the other keys."""
    sheets: dict[str, list[Component]] = {}
    for c in board.components.values():
        if c.locked or c.edge:
            continue
        sheets.setdefault(c.sheet, []).append(c)
    penalties = []
    for parts in sheets.values():
        if len(parts) < 2:
            continue
        left = min(p.left for p in parts)
        right = max(p.right for p in parts)
        top = min(p.top for p in parts)
        bottom = max(p.bottom for p in parts)
        bbox = max(1e-6, (right - left) * (bottom - top))
        used = sum(p.eff_w * p.eff_h for p in parts)
        fill = used / bbox
        penalties.append(max(0.0, SPREAD_LO - fill) + max(0.0, fill - SPREAD_HI))
    if len(penalties) < 2:
        return 0.0
    return round(sum(penalties) / len(penalties), 4)


def pinch_fraction(board: Board, margin: float, track: float = 1.0) -> float:
    """Fraction of close (shadowing) component pairs whose gap is too tight for a
    routing channel. Lower is better. A pair 'shadows' when it nearly aligns on
    one axis (perpendicular gap < margin); it is a 'pinch' when the along-axis gap
    is non-negative but below one channel width. Mirrors the channel test in
    ``anneal._pair_penalty`` via the shared ``channel_width`` helper. Returns 0.0
    when no pairs shadow."""
    channel = channel_width(margin, track)
    comps = list(board.components.values())
    shadow = 0
    pinch = 0
    for i in range(len(comps)):
        a = comps[i]
        for j in range(i + 1, len(comps)):
            b = comps[j]
            gx = abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2
            gy = abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2
            if min(gx, gy) < margin:
                shadow += 1
                gap = max(gx, gy)
                if 0 <= gap < channel:
                    pinch += 1
    if shadow == 0:
        return 0.0
    return round(pinch / shadow, 4)


def whitespace_connectivity(board: Board, cell_mm: float = CELL_MM) -> float:
    """Largest connected empty region / total empty cells on a coarse grid over
    the outline. 1.0 == all whitespace is one connected routing sea; low == it is
    broken into isolated pockets. Higher is better. Every component (locked and
    edge-pinned included) is an obstacle, since they all block routing. Returns
    0.0 when there are no empty cells."""
    nx = max(1, int(math.ceil(board.width / cell_mm)))
    ny = max(1, int(math.ceil(board.height / cell_mm)))
    occupied = [[False] * ny for _ in range(nx)]
    for c in board.components.values():
        ix0 = max(0, int((c.left - board.x0) // cell_mm))
        ix1 = min(nx - 1, int((c.right - board.x0) // cell_mm))
        iy0 = max(0, int((c.top - board.y0) // cell_mm))
        iy1 = min(ny - 1, int((c.bottom - board.y0) // cell_mm))
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                occupied[ix][iy] = True
    total_empty = sum(1 for ix in range(nx) for iy in range(ny) if not occupied[ix][iy])
    if total_empty == 0:
        return 0.0
    seen = [[False] * ny for _ in range(nx)]
    largest = 0
    for sx in range(nx):
        for sy in range(ny):
            if occupied[sx][sy] or seen[sx][sy]:
                continue
            size = 0
            stack = [(sx, sy)]
            seen[sx][sy] = True
            while stack:
                ix, iy = stack.pop()
                size += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    jx, jy = ix + dx, iy + dy
                    if 0 <= jx < nx and 0 <= jy < ny and not occupied[jx][jy] and not seen[jx][jy]:
                        seen[jx][jy] = True
                        stack.append((jx, jy))
            largest = max(largest, size)
    return round(largest / total_empty, 4)
```

- [ ] **Step 4: Point `congestion.py` at the shared `CELL_MM`**

In `plugin/plugins/autoplace/congestion.py`, change the import (line 17) from:

```python
from .metrics import _is_power
```

to:

```python
from .metrics import _is_power, CELL_MM
```

and change the `parse` signature (line 57) from:

```python
def parse(ses_path: str, board: Board, cell_mm: float = 5.0) -> CongestionField:
```

to:

```python
def parse(ses_path: str, board: Board, cell_mm: float = CELL_MM) -> CongestionField:
```

- [ ] **Step 5: Run the proxy + congestion tests**

Run: `python -m pytest tests/test_metrics_proxies.py tests/test_congestion.py -q`
Expected: PASS (CELL_MM is 5.0, identical to the old default).

- [ ] **Step 6: Add `fabrication.track_for`**

In `plugin/plugins/autoplace/fabrication.py`, after `margin_for` (line 36) add:

```python
def track_for(fab: str) -> float:
    """Track width (mm) for a fabrication profile."""
    return _profile(fab)["track"]
```

- [ ] **Step 7: Wire the proxies into `run_candidates` (with a `track` param)**

In `plugin/plugins/autoplace/multiseed.py`, change the import (line 15) from:

```python
from . import engine, serialize
```

to:

```python
from . import engine, metrics, serialize
```

change the signature (lines 19-21) from:

```python
def run_candidates(model: Board, count: int, *, strategy: str = "auto",
                   connectors: list[str] | None = None,
                   margin: float = 0.8) -> Iterator[dict]:
```

to:

```python
def run_candidates(model: Board, count: int, *, strategy: str = "auto",
                   connectors: list[str] | None = None,
                   margin: float = 0.8, track: float = 1.0) -> Iterator[dict]:
```

and change the success yield (lines 38-46) from:

```python
        after = report["after"]
        yield {
            "type": "candidate",
            "seed": seed,
            "hpwl_mm": after["hpwl_mm"],
            "crossings": after["crossings"],
            "overlaps": report["overlaps_remaining"],
            "hpwl_delta_pct": report["hpwl_delta_pct"],
            "board": serialize.board_to_dict(board),
        }
```

to:

```python
        after = report["after"]
        yield {
            "type": "candidate",
            "seed": seed,
            "hpwl_mm": after["hpwl_mm"],
            "crossings": after["crossings"],
            "overlaps": report["overlaps_remaining"],
            "hpwl_delta_pct": report["hpwl_delta_pct"],
            "sheet_spread_score": metrics.sheet_spread_score(board),
            "pinch_fraction": metrics.pinch_fraction(board, margin, track),
            "whitespace_connectivity": metrics.whitespace_connectivity(board),
            "board": serialize.board_to_dict(board),
        }
```

- [ ] **Step 8: Pass `track` from the CLI**

In `cli.py`, change the `run_candidates` call in `cmd_place_multi` (lines 115-117) from:

```python
    for i, cand in enumerate(multiseed.run_candidates(
            model, count, strategy=strategy, connectors=connectors,
            margin=fabrication.margin_for(fab))):
```

to:

```python
    for i, cand in enumerate(multiseed.run_candidates(
            model, count, strategy=strategy, connectors=connectors,
            margin=fabrication.margin_for(fab), track=fabrication.track_for(fab))):
```

- [ ] **Step 9: Update the multiseed field-shape test**

In `tests/test_multiseed.py`, change `test_count_and_shape` (lines 40-46) from:

```python
def test_count_and_shape():
    cands = list(multiseed.run_candidates(_board(), 6))
    assert len(cands) == 6
    for c in cands:
        assert set(c) >= {"seed", "hpwl_mm", "crossings", "overlaps",
                          "hpwl_delta_pct", "board"}
        assert c["board"]["footprints"]
```

to:

```python
def test_count_and_shape():
    cands = list(multiseed.run_candidates(_board(), 6))
    assert len(cands) == 6
    for c in cands:
        assert set(c) >= {"seed", "hpwl_mm", "crossings", "overlaps",
                          "hpwl_delta_pct", "sheet_spread_score",
                          "pinch_fraction", "whitespace_connectivity", "board"}
        assert isinstance(c["sheet_spread_score"], float)
        assert isinstance(c["pinch_fraction"], float)
        assert isinstance(c["whitespace_connectivity"], float)
        assert c["board"]["footprints"]
```

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add plugin/plugins/autoplace/metrics.py plugin/plugins/autoplace/congestion.py plugin/plugins/autoplace/fabrication.py plugin/plugins/autoplace/multiseed.py cli.py tests/test_metrics_proxies.py tests/test_multiseed.py
git commit -m "Add cheap placement proxies (spread, pinch, whitespace) to candidates"
```

---

### Task 4: Surface the proxies on the gallery cards (D2 app side)

**Files:**
- Modify: `app/renderer/renderer.js:424-431` (card metrics markup)
- Modify: `app/renderer/styles.css:709-716` (two-row metrics + chip styles)

**Interfaces:**
- Consumes: candidate dict fields `sheet_spread_score`, `pinch_fraction`, `whitespace_connectivity`, `overlaps` (from Task 3).
- Produces: nothing for later tasks (display only).

> No JS unit-test framework exists in this repo; verification is `node --check` for syntax plus a manual launch. This matches how the existing app code is validated.

- [ ] **Step 1: Update the candidate-card markup**

In `app/renderer/renderer.js`, replace the `card.innerHTML` assignment in `addCandidateCard` (lines 424-431) from:

```javascript
  card.innerHTML =
    `<div class="cand-thumb"><svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">${inner}</svg></div>` +
    `<div class="cand-meta">` +
    `<span class="cand-seed">seed ${cand.seed}</span>` +
    `<span class="badge badge-success cand-badge" hidden>best</span>` +
    `</div>` +
    `<div class="cand-metrics">` +
    `${fmt(Math.round(cand.hpwl_mm))} mm ${delta} · ${fmt(cand.crossings)} crossings</div>`;
```

to:

```javascript
  const spread = cand.sheet_spread_score === undefined ? "—" : cand.sheet_spread_score.toFixed(2);
  const pinch = cand.pinch_fraction === undefined ? "—" : `${Math.round(cand.pinch_fraction * 100)}%`;
  const ws = cand.whitespace_connectivity === undefined ? "—" : `${Math.round(cand.whitespace_connectivity * 100)}%`;
  card.innerHTML =
    `<div class="cand-thumb"><svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">${inner}</svg></div>` +
    `<div class="cand-meta">` +
    `<span class="cand-seed">seed ${cand.seed}</span>` +
    `<span class="badge badge-success cand-badge" hidden>best</span>` +
    `</div>` +
    `<div class="cand-metrics">` +
    `<div class="cand-metrics-row">${fmt(Math.round(cand.hpwl_mm))} mm ${delta} · ${fmt(cand.crossings)} crossings</div>` +
    `<div class="cand-metrics-row cand-metrics-proxy">spread ${spread} · pinch ${pinch} · ws ${ws} · overlaps ${fmt(cand.overlaps)}</div>` +
    `</div>`;
```

- [ ] **Step 2: Add the CSS for the two-row metrics and the routed chip**

In `app/renderer/styles.css`, replace the `.cand-metrics` rule (lines 709-712) from:

```css
.cand-metrics {
  font-size: 12px;
  color: var(--text-muted);
}
```

with:

```css
.cand-metrics {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cand-metrics-proxy { opacity: 0.85; }
.cand-routed {
  font-size: 11px;
  font-weight: 600;
  color: var(--success);
}
.cand-recommended { outline: 2px solid var(--success); }
```

- [ ] **Step 3: Syntax-check the changed JS**

Run: `node --check app/renderer/renderer.js`
Expected: no output (exit 0).

- [ ] **Step 4: Manual visual check**

Run: `cd app && npm start` (or the user's normal launch). Load a board, click Run, and confirm each candidate card shows a second metrics line `spread … · pinch …% · ws …% · overlaps …`. Close the app.

- [ ] **Step 5: Commit**

```bash
git add app/renderer/renderer.js app/renderer/styles.css
git commit -m "Show spread/pinch/whitespace proxies on candidate cards"
```

---

### Task 5: Pure ranking module (D1 core)

**Files:**
- Create: `plugin/plugins/autoplace/ranking.py`
- Test: `tests/test_candidate_ranking.py`

**Interfaces:**
- Produces: `ranking.candidate_key(cand: dict) -> tuple`; `ranking.pre_rank(candidates: list[dict]) -> list[dict]` (best first); `ranking.final_order(candidates: list[dict], routed: dict[int, float]) -> list[dict]` (routed finalists first by `-routed_pct` then pre-rank key, rest by pre-rank key).
- Consumes: candidate projection dicts with keys `overlaps`, `sheet_spread_score`, `pinch_fraction`, `hpwl_mm`, `seed` (from Task 3). `whitespace_connectivity` is displayed but NOT in the key (spec §6 D1).

- [ ] **Step 1: Write the failing test**

Create `tests/test_candidate_ranking.py`:

```python
"""Headless tests for the pure candidate-ranking policy. No pcbnew.

  python -m pytest tests/test_candidate_ranking.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import ranking                          # noqa: E402


def _c(seed, overlaps=0, spread=0.0, pinch=0.0, hpwl=100.0):
    return {"seed": seed, "overlaps": overlaps, "sheet_spread_score": spread,
            "pinch_fraction": pinch, "hpwl_mm": hpwl,
            "whitespace_connectivity": 0.5}


def test_legal_beats_illegal():
    legal = _c(1, overlaps=0, hpwl=999.0)
    illegal = _c(2, overlaps=3, hpwl=1.0)              # tiny HPWL but has overlaps
    assert ranking.pre_rank([illegal, legal])[0]["seed"] == 1


def test_spread_then_pinch_then_hpwl():
    a = _c(1, spread=0.2, pinch=0.5, hpwl=100.0)
    b = _c(2, spread=0.1, pinch=0.9, hpwl=100.0)       # better spread wins first
    c = _c(3, spread=0.1, pinch=0.1, hpwl=500.0)       # ties a-on-spread? no: 0.1<0.2
    order = [x["seed"] for x in ranking.pre_rank([a, b, c])]
    assert order[0] in (2, 3)                          # both spread 0.1 beat a's 0.2
    # between b and c (spread tie 0.1): lower pinch wins -> c before b
    assert order.index(3) < order.index(2)


def test_hpwl_then_seed_tiebreak():
    a = _c(5, hpwl=100.0)
    b = _c(2, hpwl=100.0)                              # identical except seed
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 5]


def test_final_order_routed_finalists_float_to_top():
    a = _c(1, spread=0.0, hpwl=100.0)                  # pre-rank #1
    b = _c(2, spread=0.0, hpwl=200.0)                  # pre-rank #2
    c = _c(3, spread=0.0, hpwl=300.0)                  # pre-rank #3 (not routed)
    routed = {1: 80.0, 2: 95.0}                        # finalist 2 routes better
    order = [x["seed"] for x in ranking.final_order([a, b, c], routed)]
    assert order == [2, 1, 3]                          # routed best, routed, then rest


def test_final_order_no_routes_is_pre_rank():
    a = _c(1, hpwl=100.0)
    b = _c(2, hpwl=50.0)
    assert [x["seed"] for x in ranking.final_order([a, b], {})] == [2, 1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_candidate_ranking.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoplace.ranking'`.

- [ ] **Step 3: Create the ranking module**

Create `plugin/plugins/autoplace/ranking.py`:

```python
"""Deterministic candidate ranking for the multi-seed gallery (pure-Python).

Ranking lives here, NOT in ``anneal._quality`` (that split is load-bearing --
see BUILD_SPEC.md:368-379). Two levels:

1. ``pre_rank``  -- order ALL candidates by cheap proxies (no routing).
2. ``final_order`` -- once the top finalists are routed, float them to the top by
   measured routed-%, leaving the rest in pre-rank order.

Every key element is rounded so cross-machine float noise cannot flip the order,
and ``seed`` is the final element so the order is total (no nondeterministic
ties).
"""
from __future__ import annotations


def candidate_key(cand: dict) -> tuple:
    """Lexicographic pre-rank key; lower is better on every component."""
    return (
        cand["overlaps"],                          # legal layouts win outright
        round(cand["sheet_spread_score"], 3),      # clean per-sheet spread
        round(cand["pinch_fraction"], 3),          # fewer routing pinch points
        round(cand["hpwl_mm"], 2),                 # wirelength is the final metric
        cand["seed"],                              # total order
    )


def pre_rank(candidates: list[dict]) -> list[dict]:
    """All candidates, best first, by ``candidate_key``."""
    return sorted(candidates, key=candidate_key)


def final_order(candidates: list[dict], routed: dict) -> list[dict]:
    """Routed finalists first (by -routed_pct, then pre-rank key); the rest keep
    pre-rank order below them. ``routed`` maps seed -> routed_pct."""
    pre = pre_rank(candidates)
    finalists = [c for c in pre if c["seed"] in routed]
    rest = [c for c in pre if c["seed"] not in routed]
    finalists.sort(key=lambda c: (-routed[c["seed"]], candidate_key(c)))
    return finalists + rest
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_candidate_ranking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/ranking.py tests/test_candidate_ranking.py
git commit -m "Add deterministic two-level candidate ranking module"
```

---

### Task 6: Emit the proxy ranking event from the CLI (D1 wiring)

**Files:**
- Modify: `cli.py:102,115-124` (buffer candidates, emit `ranking` event)

**Interfaces:**
- Consumes: `ranking.pre_rank` (Task 5), candidate dicts (Task 3).
- Produces: a `{"type":"ranking","order":[seed,...],"best_seed":seed}` NDJSON event emitted after the candidate stream and before `done`.

> `cmd_place_multi` needs `pcbnew` (it loads the board), so it is verified by a manual/headless run, mirroring the other CLI commands. The ranking logic itself is unit-tested in Task 5.

- [ ] **Step 1: Import `ranking` in the command**

In `cli.py`, change the import line inside `cmd_place_multi` (line 102) from:

```python
    from autoplace import fabrication, multiseed
```

to:

```python
    from autoplace import fabrication, multiseed, ranking
```

- [ ] **Step 2: Buffer candidates and emit the ranking event**

In `cli.py`, replace the streaming loop + `done` in `cmd_place_multi` (lines 115-124) from:

```python
    for i, cand in enumerate(multiseed.run_candidates(
            model, count, strategy=strategy, connectors=connectors,
            margin=fabrication.margin_for(fab), track=fabrication.track_for(fab))):
        cand["index"] = i
        cand["count"] = count
        emit({"type": "progress", "stage": "place",
              "percent": round(100.0 * (i + 1) / count, 1)})
        emit(cand)
    emit({"type": "done", "count": count})
    return 0
```

with:

```python
    buf = []
    keys = ("seed", "overlaps", "sheet_spread_score", "pinch_fraction",
            "whitespace_connectivity", "hpwl_mm")
    for i, cand in enumerate(multiseed.run_candidates(
            model, count, strategy=strategy, connectors=connectors,
            margin=fabrication.margin_for(fab), track=fabrication.track_for(fab))):
        cand["index"] = i
        cand["count"] = count
        emit({"type": "progress", "stage": "place",
              "percent": round(100.0 * (i + 1) / count, 1)})
        emit(cand)
        if cand.get("type") == "candidate":
            buf.append({k: cand[k] for k in keys})     # lightweight: no board geometry

    ranked = ranking.pre_rank(buf)
    if ranked:
        emit({"type": "ranking", "order": [c["seed"] for c in ranked],
              "best_seed": ranked[0]["seed"]})
    emit({"type": "done", "count": count})
    return 0
```

- [ ] **Step 3: Syntax-check and run the engine suite**

Run: `python -c "import ast; ast.parse(open('cli.py').read())"`
Expected: no output (valid syntax).

Run: `python -m pytest tests/ -q`
Expected: all tests pass (no engine behavior changed).

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "Emit proxy ranking event from place-multi"
```

---

### Task 7: Reorder + badge the gallery on ranking events (D1 app)

**Files:**
- Modify: `app/main.js:273-283` (forward `ranking`/`route-result`/`route-skipped`)
- Modify: `app/renderer/renderer.js:719-732` (event cases), add `applyRanking`/`applyRouteResult`

**Interfaces:**
- Consumes: `ranking`/`route-result`/`route-skipped` events (Tasks 6 and 8).
- Produces: nothing for later tasks (UI behavior).

> No JS unit tests; verified by `node --check` plus a manual launch.

- [ ] **Step 1: Forward the new events in `main.js`**

In `app/main.js`, in `runPlaceMulti`'s `handleLine` (after the `done` line, line 279), change:

```javascript
          if (obj.type === "done") return send(obj);
```

to:

```javascript
          if (obj.type === "done") return send(obj);
          if (obj.type === "ranking" || obj.type === "route-result" ||
              obj.type === "route-skipped") return send(obj);
```

- [ ] **Step 2: Add the renderer handlers**

In `app/renderer/renderer.js`, immediately after `markBestCandidate` (i.e. after line 461) add:

```javascript
function applyRanking(order, bestSeed) {
  const grid = $("galleryGrid");
  order.forEach((seed) => {
    const card = grid.querySelector(`.cand[data-seed="${seed}"]`);
    if (card) grid.appendChild(card);                 // reorder: re-append in rank order
  });
  grid.querySelectorAll(".cand-badge").forEach((b) => (b.hidden = true));
  grid.querySelectorAll(".cand").forEach((c) => c.classList.remove("cand-recommended"));
  const best = grid.querySelector(`.cand[data-seed="${bestSeed}"]`);
  if (best) {
    const badge = best.querySelector(".cand-badge");
    if (badge) { badge.hidden = false; badge.textContent = "recommended"; }
    best.classList.add("cand-recommended");           // highlight (does NOT auto-commit)
  }
}

function applyRouteResult(seed, pct) {
  const card = $("galleryGrid").querySelector(`.cand[data-seed="${seed}"]`);
  if (!card) return;
  let chip = card.querySelector(".cand-routed");
  if (!chip) {
    chip = document.createElement("span");
    chip.className = "cand-routed";
    card.querySelector(".cand-meta").appendChild(chip);
  }
  chip.textContent = `routed ${pct}%`;
}
```

- [ ] **Step 3: Add the event cases**

In `app/renderer/renderer.js`, in the `window.api.onPlaceEvent` handler (lines 719-732), change:

```javascript
  else if (evt.type === "candidate-error") addCandidateError(evt);
```

to:

```javascript
  else if (evt.type === "candidate-error") addCandidateError(evt);
  else if (evt.type === "ranking") applyRanking(evt.order, evt.best_seed);
  else if (evt.type === "route-result") applyRouteResult(evt.seed, evt.routed_pct);
  else if (evt.type === "route-skipped") { /* keep proxy ranking; no chip */ }
```

- [ ] **Step 4: Syntax-check both files**

Run: `node --check app/main.js && node --check app/renderer/renderer.js`
Expected: no output (exit 0).

- [ ] **Step 5: Commit**

```bash
git add app/main.js app/renderer/renderer.js
git commit -m "Reorder and badge the gallery on ranking/route events"
```

---

### Task 8: Auto-route the top-2 finalists with graceful fallback (D1 route step)

**Files:**
- Modify: `cli.py:18` (already has `DEFAULT_JAR`), `cli.py:95-124` (add `_route_candidate`, route loop, final ranking event)

**Interfaces:**
- Consumes: `ranking.final_order` (Task 5), `routing.route_once` (`routing.py:48`), `kicad_io.apply_to_board`, `kicad_io.copy_project`, `_apply_fab` (`cli.py:26`).
- Produces: `{"type":"route-result","seed":int,"routed_pct":float}` per finalist (or `{"type":"route-skipped","seed":int,"reason":str}`), then a second `{"type":"ranking",...}` reflecting `final_order`.

> Routing needs `pcbnew` + Java + FreeRouting; it is verified by a manual run on a real board. The pure re-ranking it drives (`final_order`) is unit-tested in Task 5. The whole step is wrapped so a missing/failed router degrades to proxy ranking.

- [ ] **Step 1: Add the per-candidate route helper**

In `cli.py`, add this module-level function just before `cmd_place_multi` (before line 95):

```python
def _route_candidate(model, pcb, in_path, fab, seed, jar, passes, sides,
                     strategy, connectors):
    """Re-place ``seed`` (deterministic) and route it; return routed %.

    Writes scratch ``_placemulti_cand<seed>.*`` in the CWD (the app sets CWD to a
    writable userData dir). Net-class widths come from the copied .kicad_pro."""
    import copy

    import pcbnew

    from autoplace import engine, fabrication, kicad_io, routing
    cand = copy.deepcopy(model)
    engine.place(cand, seed=seed, strategy=strategy, connectors=connectors,
                 margin=fabrication.margin_for(fab),
                 track=fabrication.track_for(fab))
    work = os.path.join(os.getcwd(), f"_placemulti_cand{seed}.kicad_pcb")
    kicad_io.apply_to_board(cand, pcb)
    pcbnew.SaveBoard(work, pcb)
    kicad_io.copy_project(in_path, work)
    _apply_fab(work, fab)
    r = routing.route_once(work, jar, passes, sides=sides)
    return r["pct"]
```

- [ ] **Step 2: Load the `pcb` object and run the route loop**

In `cli.py`'s `cmd_place_multi`, change the board load (line 113) from:

```python
    model, _ = kicad_io.load_board(in_path)
```

to:

```python
    model, pcb = kicad_io.load_board(in_path)
```

Then, in the block added in Task 6, change:

```python
    ranked = ranking.pre_rank(buf)
    if ranked:
        emit({"type": "ranking", "order": [c["seed"] for c in ranked],
              "best_seed": ranked[0]["seed"]})
    emit({"type": "done", "count": count})
    return 0
```

to:

```python
    ranked = ranking.pre_rank(buf)
    if ranked:
        emit({"type": "ranking", "order": [c["seed"] for c in ranked],
              "best_seed": ranked[0]["seed"]})

    # Auto-route the top finalists (gold-label routability). Slow + needs Java;
    # any failure degrades to the proxy ranking already emitted above.
    route_topk = int(os.environ.get("ROUTE_TOPK", "2"))
    routed = {}
    if ranked and route_topk > 0:
        jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
        passes = int(os.environ.get("ROUTE_PASSES", "10"))
        sides = int(os.environ.get("SIDES", "2"))
        for c in ranked[:route_topk]:
            seed = c["seed"]
            emit({"type": "progress", "stage": "route", "percent": 0.0})
            try:
                pct = _route_candidate(model, pcb, in_path, fab, seed, jar,
                                       passes, sides, strategy, connectors)
                routed[seed] = pct
                emit({"type": "route-result", "seed": seed,
                      "routed_pct": round(pct, 1)})
            except Exception as exc:
                emit({"type": "route-skipped", "seed": seed, "reason": str(exc)})
        if routed:
            final = ranking.final_order(buf, routed)
            emit({"type": "ranking", "order": [c["seed"] for c in final],
                  "best_seed": final[0]["seed"]})

    emit({"type": "done", "count": count})
    return 0
```

- [ ] **Step 3: Syntax-check and run the engine suite**

Run: `python -c "import ast; ast.parse(open('cli.py').read())"`
Expected: no output.

Run: `python -m pytest tests/ -q`
Expected: all tests pass (pure-Python suite unaffected).

- [ ] **Step 4: Manual end-to-end check (needs KiCad python + Java + FreeRouting)**

Run the app on a real board (e.g. the reflow board), click Run, and confirm: cards stream, then the top card is badged "recommended", and the top-2 cards gain a `routed N%` chip after a delay. Temporarily set `ROUTE_TOPK=0` in the environment and confirm the gallery still ranks (proxy-only) with no routing. Confirm a machine without Java shows `route-skipped` behavior (gallery still usable).

- [ ] **Step 5: Commit**

```bash
git add cli.py
git commit -m "Auto-route top-2 finalists and re-rank by routed percentage"
```

---

### Task 9: Fab-derived channel + cross-block gutter (D4)

**Files:**
- Modify: `plugin/plugins/autoplace/anneal.py:27,40-41,44-74,85-103,281-287` (channel from fab, gutter, store `channel_scale`, `track` param)
- Modify: `plugin/plugins/autoplace/engine.py:19-22,81-86` (add `track`, forward to anneal)
- Modify: `plugin/plugins/autoplace/multiseed.py:32-33` (forward `track` to `engine.place`)
- Modify: `cli.py:74-76` (pass `track` in `cmd_place`)
- Test: `tests/test_engine.py` (add two cross-block tests)

**Interfaces:**
- Consumes: `metrics.channel_width` (Task 3), `Component.block` (`model.py:37`).
- Produces: `engine.place(..., track: float = 1.0)`, `anneal.anneal(..., track: float = 1.0)`, `Annealer(..., track: float = 1.0)`; `Annealer.channel_mm`, `Annealer.gutter`, `Annealer.channel_scale`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_cross_block_gutter_widens_channel():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    a = Component("A", 4, 4, x=20, y=20, block="X")
    bb = Component("B", 4, 4, x=27.5, y=20, block="Y")   # gx = 7.5 - 4 = 3.5
    b.components = {"A": a, "B": bb}
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0)
    # gap 3.5 is beyond the single-track channel (2.6) but inside the cross-block
    # target (2.6 + gutter 1.8 = 4.4) -> cross-block pairs are penalised.
    cross = ann._pair_penalty(a, bb, 0.8)
    a.block = bb.block = "X"                              # same block now
    same = ann._pair_penalty(a, bb, 0.8)
    assert cross > 0
    assert same == 0
    assert cross > same


def test_dense_board_zeroes_the_gutter():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    a = Component("A", 4, 4, x=20, y=20, block="X")
    bb = Component("B", 4, 4, x=27.5, y=20, block="Y")
    b.components = {"A": a, "B": bb}
    # channel_scale 0 (dense board): the channel term is off entirely -> no gutter
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=0.0)
    assert ann._pair_penalty(a, bb, 0.8) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_engine.py::test_cross_block_gutter_widens_channel -q`
Expected: FAIL — `cross` is `0` (no cross-block branch exists yet, gap 3.5 ≥ 2.6).

- [ ] **Step 3: Make the annealer fab-derived + gutter-aware**

In `plugin/plugins/autoplace/anneal.py`, change the import (line 27) from:

```python
from .metrics import _is_power
```

to:

```python
from .metrics import _is_power, channel_width
```

Replace the channel constant block (lines 40-41):

```python
# Desired clear gap between courtyards so the router has a channel (mm).
CHANNEL_MM = 2.6          # 1.0 mm track + 2 x 0.8 mm clearance (DTU fiber-laser DR)
```

with:

```python
# Extra cross-block gutter beyond the single-track channel = one more routing
# track (track + clearance), scaled by channel_scale so dense boards relax it.
# The base channel and the gutter are both derived per-board from the fab
# profile in Annealer.__init__ (channel = track + 2*clearance).
```

Change the `Annealer.__init__` signature (lines 45-47) from:

```python
    def __init__(self, board: Board, *, margin: float = 0.8, seed: int = 0,
                 channel_scale: float = 1.0, cohesion_scale: float = 1.0,
                 congestion=None):
```

to:

```python
    def __init__(self, board: Board, *, margin: float = 0.8, seed: int = 0,
                 channel_scale: float = 1.0, cohesion_scale: float = 1.0,
                 track: float = 1.0, congestion=None):
```

and immediately after `self.channel = _Weights.CHANNEL * channel_scale` (line 51) add:

```python
        self.channel_scale = channel_scale
        self.channel_mm = channel_width(self.margin, track)   # base 1-track channel
        self.gutter = track + self.margin                     # one extra cross-block track
```

Replace `_pair_penalty` (lines 85-103) with:

```python
    def _pair_penalty(self, a, b, margin) -> float:
        """Hard overlap area (barrier) + soft channel penalty for tight gaps."""
        # gap between courtyards along each axis (negative => overlapping)
        gx = abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2
        gy = abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2
        ox = margin - gx
        oy = margin - gy
        cost = 0.0
        if ox > 0 and oy > 0:                          # boxes overlap
            cost += _Weights.OVERLAP * ox * oy
        # channel: penalise when the nearer-axis gap is below the channel target
        # and the boxes shadow each other on the other axis (a real routing pinch).
        # Parts in different blocks target a wider gutter (scaled by channel_scale,
        # so a dense board where channel_scale -> 0 keeps the plain channel).
        gap = max(gx, gy)
        shadow = min(gx, gy) < margin
        target = self.channel_mm
        if a.block and b.block and a.block != b.block:
            target += self.gutter * self.channel_scale
        if self.channel and shadow and 0 <= gap < target:
            press = self.cpress.get(a.ref, 0.0) + self.cpress.get(b.ref, 0.0)
            local = self.channel * (1.0 + _Weights.CONG_K * press / 2.0)
            cost += local * (target - gap)
        return cost
```

Change the module-level `anneal()` wrapper (lines 281-287) from:

```python
def anneal(board: Board, *, seed: int = 0, steps: int = 6000, margin: float = 0.8,
           channel_scale: float = 1.0, cohesion_scale: float = 1.0,
           congestion=None, progress=None):
    Annealer(board, margin=margin, seed=seed, channel_scale=channel_scale,
             cohesion_scale=cohesion_scale, congestion=congestion).run(
                 steps=steps, progress=progress)
    return board
```

to:

```python
def anneal(board: Board, *, seed: int = 0, steps: int = 6000, margin: float = 0.8,
           channel_scale: float = 1.0, cohesion_scale: float = 1.0,
           track: float = 1.0, congestion=None, progress=None):
    Annealer(board, margin=margin, seed=seed, channel_scale=channel_scale,
             cohesion_scale=cohesion_scale, track=track, congestion=congestion).run(
                 steps=steps, progress=progress)
    return board
```

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/test_engine.py::test_cross_block_gutter_widens_channel tests/test_engine.py::test_dense_board_zeroes_the_gutter -q`
Expected: PASS.

- [ ] **Step 5: Thread `track` through `engine.place`**

In `plugin/plugins/autoplace/engine.py`, change the `place` signature (lines 19-22) from:

```python
def place(board: Board, *, seed: int = 0, grid: float = 0.5, margin: float = 0.8,
          iters: int = 400, sa_steps: int | None = None,
          strategy: str = "auto", progress=None,
          connectors: list[str] | None = None) -> dict:
```

to:

```python
def place(board: Board, *, seed: int = 0, grid: float = 0.5, margin: float = 0.8,
          track: float = 1.0, iters: int = 400, sa_steps: int | None = None,
          strategy: str = "auto", progress=None,
          connectors: list[str] | None = None) -> dict:
```

and change the `anneal.anneal(...)` call (lines 83-86) from:

```python
        anneal.anneal(board, seed=seed, steps=sa_steps, margin=margin,
                      channel_scale=channel_scale,
                      cohesion_scale=2.5 if use_floorplan else 1.0,
                      progress=lambda f: _report("anneal", 0.15 + 0.77 * f))
```

to:

```python
        anneal.anneal(board, seed=seed, steps=sa_steps, margin=margin,
                      channel_scale=channel_scale,
                      cohesion_scale=2.5 if use_floorplan else 1.0,
                      track=track,
                      progress=lambda f: _report("anneal", 0.15 + 0.77 * f))
```

- [ ] **Step 6: Forward `track` from `run_candidates` to `engine.place`**

In `plugin/plugins/autoplace/multiseed.py`, change the `engine.place(...)` call (lines 32-33) from:

```python
            report = engine.place(board, seed=seed, strategy=strategy,
                                  connectors=connectors, margin=margin)
```

to:

```python
            report = engine.place(board, seed=seed, strategy=strategy,
                                  connectors=connectors, margin=margin, track=track)
```

- [ ] **Step 7: Pass `track` from the single-seed CLI commit path**

In `cli.py`, change the `engine.place(...)` call in `cmd_place` (lines 74-76) from:

```python
    report = engine.place(model, seed=seed, strategy=strategy,
                          connectors=connectors, margin=fabrication.margin_for(fab),
                          progress=progress)
```

to:

```python
    report = engine.place(model, seed=seed, strategy=strategy,
                          connectors=connectors, margin=fabrication.margin_for(fab),
                          track=fabrication.track_for(fab), progress=progress)
```

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all tests pass. (Determinism holds; the channel-amplify test still passes because its parts have empty blocks, so the gutter branch is inert.)

- [ ] **Step 9: Behavioral non-regression gate (manual, needs FreeRouting)**

Route the `system` board (roomy) and the `motor_power` board (dense) before and after this task. Require: `motor_power` shows no regression in `overlaps_remaining` and no HPWL blow-up (its `channel_scale=0` zeroes the gutter); `system` routed-% holds or rises. Record both numbers in the commit body.

- [ ] **Step 10: Commit**

```bash
git add plugin/plugins/autoplace/anneal.py plugin/plugins/autoplace/engine.py plugin/plugins/autoplace/multiseed.py cli.py tests/test_engine.py
git commit -m "Derive routing channel from fab profile and add cross-block gutters"
```

---

## Notes for the implementer

- **`refine.py` is intentionally not threaded with `track`.** Its re-anneal uses `track=1.0` (both fab profiles use track 1.0), and its `place_margin` already carries the fab clearance, so `channel_mm` adapts correctly there too. Leave `refine.py` unchanged.
- **`whitespace_connectivity` is shown but not ranked-on** in v1 (spec §6 D1). Do not add it to `ranking.candidate_key`.
- **FreeRouting has no random-seed flag** in the `route_once` invocation (`java -jar … -de dsn -do ses -mp passes`); determinism of the routed-% is bounded by the fixed pass count, not a seed. The *ranking* is proxy-deterministic regardless. Do not invent a `-de seed` flag.
- **Scratch route files** (`_placemulti_cand<seed>.*`) land in the process CWD, which the app sets to a writable `userData` dir. They are disposable; no cleanup task is required for Phase 0.
