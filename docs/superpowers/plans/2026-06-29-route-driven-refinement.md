# M7 Route-Driven Refinement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offline loop that re-anneals placement against real FreeRouting results — parsing routed-wire congestion, spreading the congested components, re-routing, keeping the best — to push the system board's routed-% from ~97% toward 100%, driven from the Electron app.

**Architecture:** A pcbnew/FreeRouting I/O layer (`routing.py`, extracted from `route_check.py`) and a pure-Python congestion analyzer (`congestion.py`, parses the `.ses` text into a `CongestionField`) feed a pure keep-best/patience loop (`refine.py`). The annealer gains an optional congestion field that locally widens routing channels where the router struggled. A streaming `cli.py refine` subcommand and an app "Refine" action expose it.

**Tech Stack:** Python 3 (engine, pure stdlib; `pcbnew` only in `routing.py`/`kicad_io.py`), pytest (system python), Java + FreeRouting 1.9.0 (subprocess), Electron/vanilla JS (app).

## Global Constraints

- `pcbnew` and the FreeRouting subprocess live only in `routing.py` (and existing `kicad_io.py`). `congestion.py` and `refine.py`'s loop policy are pure-Python.
- Pure unit tests run under system `python` (3.11/3.13 + pytest): `python -m pytest tests/`. KiCad-python-only smoke steps use `"C:/Program Files/KiCad/10.0/bin/python.exe"`.
- System board for smoke tests: `C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.autoplaced.kicad_pcb`. FreeRouting jar: `%USERPROFILE%\.freerouting\freerouting-1.9.0.jar`.
- SES coordinate scale: `(resolution um 10)` → millimetres = coord / 10000. SES Y is negated vs the model frame: `model_x = ses_x/10000`, `model_y = -ses_y/10000`. The parser must read the resolution value, not hardcode it.
- The board is 2-layer (double-sided); vias are congestion *pressure*, never treated as failures.
- The annealer with `congestion=None` must reproduce today's behavior exactly (zero regression).
- The loop returns the best-routing placement seen; never one that routes worse than the input. Routed-% is the FreeRouting acceptance metric (noisy run-to-run; keep-best uses a margin).
- Streaming protocol: reuse `progress`/`result`/`log`; add `{"type":"iteration","iter":N,"routed_pct":..,"best_pct":..,"routed":..,"total":..}`.

---

### Task 1: `routing.py` — route a board once with FreeRouting

**Files:**
- Create: `plugin/plugins/autoplace/routing.py`
- Modify: `tools/route_check.py`

**Interfaces:**
- Consumes: `kicad_io.unrouted_count` (existing).
- Produces:
  - `routing.clear_tracks(pcb) -> None` — remove all tracks/vias from a live board.
  - `routing.route_once(pcb, jar, passes, stem) -> dict` — clear tracks, export `stem+".dsn"`, run FreeRouting to `stem+".ses"`, import the SES, build connectivity, return `{"total":int,"routed":int,"unrouted":int,"pct":float,"ses_path":str}`. Raises `RuntimeError` on a missing/empty SES (with FreeRouting's tail output).

- [ ] **Step 1: Create `routing.py`**

Create `plugin/plugins/autoplace/routing.py`:

```python
"""FreeRouting bridge: route a placed board once and report completion.

The only engine module besides ``kicad_io`` that imports ``pcbnew``; it also
shells out to FreeRouting. Extracted from ``tools/route_check.py`` so the
refinement loop (``refine.py``) can route a board repeatedly.
"""
from __future__ import annotations

import os
import subprocess
import time

import pcbnew

from .kicad_io import unrouted_count


def clear_tracks(pcb: "pcbnew.BOARD") -> None:
    """Remove every track and via so the next DSN export is unrouted."""
    for t in list(pcb.GetTracks()):
        pcb.Remove(t)
    pcb.BuildConnectivity()


def route_once(pcb: "pcbnew.BOARD", jar: str, passes: int, stem: str) -> dict:
    """Export DSN, run FreeRouting head-less, import the SES, count unrouted.

    Leaves the routed tracks on ``pcb`` (the caller clears them before the next
    export, which ``clear_tracks`` at the top of this function also does). Writes
    ``stem.dsn`` and ``stem.ses``.
    """
    clear_tracks(pcb)
    total = unrouted_count(pcb)                 # ratsnest before routing
    dsn, ses = stem + ".dsn", stem + ".ses"
    if not pcbnew.ExportSpecctraDSN(pcb, dsn):
        raise RuntimeError("DSN export failed")
    if os.path.exists(ses):
        os.remove(ses)

    t0 = time.time()
    proc = subprocess.run(
        ["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes)],
        capture_output=True, text=True, timeout=1800)
    dt = time.time() - t0

    if not os.path.exists(ses) or os.path.getsize(ses) == 0:
        tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-400:]
        raise RuntimeError(
            f"FreeRouting produced no usable SES (exit {proc.returncode}).\n{tail}")

    pcbnew.ImportSpecctraSES(pcb, ses)
    left = unrouted_count(pcb)
    routed = total - left
    return {
        "total": total, "routed": routed, "unrouted": left,
        "pct": (100.0 * routed / total if total else 100.0),
        "ses_path": ses, "seconds": round(dt, 1),
    }
```

- [ ] **Step 2: Refactor `tools/route_check.py` to use `routing.route_once`**

Replace the body of `route_check` in `tools/route_check.py` (keep the module docstring, imports, `DEFAULT_JAR`, and `__main__` block). The current function loads the board, does the export/route/import inline. Replace the function with:

```python
def route_check(in_pcb, jar=DEFAULT_JAR, passes=10):
    board = pcbnew.LoadBoard(in_pcb)
    if board is None:
        raise SystemExit(f"could not load {in_pcb}")
    stem = os.path.splitext(in_pcb)[0]
    try:
        r = routing.route_once(board, jar, passes, stem)
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)
    out = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(out, board)
    print(f"{os.path.basename(in_pcb)}")
    print(f"  connections : {r['total']}")
    print(f"  routed      : {r['routed']}  ({r['pct']:.1f}%)")
    print(f"  unrouted    : {r['unrouted']}")
    print(f"  freerouting : {r['seconds']:.0f}s, {passes} passes")
    print(f"  -> {out}")
    return r
```

Add the import near the top of `tools/route_check.py` (it already does `sys.path.insert(...)` for `autoplace`); after that insert add:

```python
from autoplace import routing  # noqa: E402
```

Remove the now-unused `subprocess`, `time`, and `unrouted_count` import line (`from autoplace.kicad_io import unrouted_count as _unrouted`) from `route_check.py` — `routing` owns them now. Keep `import os`, `import sys`, `import pcbnew`.

- [ ] **Step 3: Smoke test (KiCad python + FreeRouting)**

Run:
```bash
"/c/Program Files/KiCad/10.0/bin/python.exe" tools/route_check.py "C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.autoplaced.kicad_pcb" "$USERPROFILE/.freerouting/freerouting-1.9.0.jar" 10
```
Expected: prints `connections : 261`, `routed : … (≈95%)`, and writes `system.autoplaced.routed.kicad_pcb` — same behavior as before the refactor.

- [ ] **Step 4: Commit**

```bash
git add plugin/plugins/autoplace/routing.py tools/route_check.py
git commit -m "refactor(routing): extract route_once into autoplace.routing"
```

---

### Task 2: `congestion.py` — parse SES into a CongestionField

**Files:**
- Create: `plugin/plugins/autoplace/congestion.py`
- Test: `tests/test_congestion.py`

**Interfaces:**
- Consumes: `Board` from `autoplace.model`; `_is_power` from `autoplace.metrics`.
- Produces:
  - `congestion.parse(ses_path, board, cell_mm=5.0) -> CongestionField`.
  - `CongestionField.pressure_at(x, y) -> float` — normalised 0..~3 congestion at a model-mm point (0.0 for an empty field or out-of-grid).
  - `CongestionField.empty -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_congestion.py`:

```python
"""Pure-Python tests for SES congestion parsing. No pcbnew."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import congestion                            # noqa: E402
from autoplace.model import Board, Component, Pad           # noqa: E402

# A minimal SES in the real KiCad format: resolution um 10 (coord/10000 = mm),
# Y negated. Two dense wires + a via packed in the bottom-left model corner
# (model x~5-15mm, y~5-15mm => ses x 50000-150000, y -50000..-150000), and one
# short wire far away in the top-right.
SAMPLE_SES = """(session test
  (routes
    (resolution um 10)
    (network_out
      (net A
        (wire (path F.Cu 10000 50000 -50000 150000 -50000 150000 -150000))
        (wire (path B.Cu 10000 50000 -150000 150000 -50000))
        (via "Via[0-1]" 100000 -100000)
      )
      (net B
        (wire (path F.Cu 10000 1900000 -1900000 1910000 -1900000))
      )
    )
  )
)
"""


def _board():
    b = Board(0, 0, 200, 200)
    # net A pads near the bottom-left corner; net B pads near top-right
    b.components = {
        "A1": Component("A1", 2, 2, x=5, y=5, pads=[Pad("1", "A", 0, 0)]),
        "A2": Component("A2", 2, 2, x=15, y=15, pads=[Pad("1", "A", 0, 0)]),
        "B1": Component("B1", 2, 2, x=190, y=190, pads=[Pad("1", "B", 0, 0)]),
        "B2": Component("B2", 2, 2, x=191, y=190, pads=[Pad("1", "B", 0, 0)]),
    }
    return b


def _write(tmp_path, text):
    p = os.path.join(tmp_path, "s.ses")
    with open(p, "w") as f:
        f.write(text)
    return p


def test_parse_marks_crowded_corner_hotter(tmp_path):
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert not field.empty
    hot = field.pressure_at(10, 10)       # crowded corner (wires + via)
    cold = field.pressure_at(190, 190)    # single short wire
    assert hot > cold
    assert hot > 0.0


def test_pressure_zero_outside_and_for_empty(tmp_path):
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert field.pressure_at(1e6, 1e6) == 0.0          # far outside grid
    empty = congestion.parse(_write(tmp_path, "(session x (routes))"), _board())
    assert empty.empty
    assert empty.pressure_at(10, 10) == 0.0


def test_high_detour_net_adds_pressure(tmp_path):
    # net A routed length is large vs its straight pad span (5,5)->(15,15);
    # detour pressure should land in the corner cell.
    field = congestion.parse(_write(tmp_path, SAMPLE_SES), _board(), cell_mm=20.0)
    assert field.pressure_at(10, 10) > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_congestion.py -v`
Expected: FAIL — `No module named 'autoplace.congestion'`.

- [ ] **Step 3: Implement `congestion.py`**

Create `plugin/plugins/autoplace/congestion.py`:

```python
"""Parse a FreeRouting .ses session into a placement-congestion field.

Pure-Python (no pcbnew). Reads routed wire polylines and vias, bins them into a
grid over the board outline, and combines track density, via clusters, and
per-net detour into a per-cell pressure. ``anneal.py`` samples this to widen
routing channels exactly where the router struggled.

SES coordinates: ``(resolution um <r>)`` => mm = coord / (r * 1000). KiCad's DSN
negates Y, so model_y = -ses_y_mm. Coordinates therefore map to the model frame
as (x/scale, -y/scale) with scale = r * 1000 (= 10000 for the usual r=10).
"""
from __future__ import annotations

import math
import re

from .metrics import _is_power
from .model import Board

_RES_RE = re.compile(r"\(resolution\s+um\s+(\d+)\)")
_VIA_RE = re.compile(r'\(via\s+"[^"]*"\s+(-?\d+)\s+(-?\d+)')
_PATH_RE = re.compile(r"\(path\s+(\S+)\s+\d+\s+([-\d\s]+?)\)", re.DOTALL)
# a net block: (net NAME ... ) up to the next (net or end of network_out
_NET_RE = re.compile(r"\(net\s+(\"[^\"]+\"|\S+)(.*?)(?=\(net\s|\)\s*\)\s*\)\s*$|\Z)",
                     re.DOTALL)


class CongestionField:
    def __init__(self, x0, y0, cell_mm, nx, ny, pressure):
        self._x0, self._y0, self._cell = x0, y0, cell_mm
        self._nx, self._ny = nx, ny
        self._p = pressure                       # dict (ix, iy) -> float 0..~3
        self.empty = not pressure

    def _cell_of(self, x, y):
        ix = int((x - self._x0) // self._cell)
        iy = int((y - self._y0) // self._cell)
        if 0 <= ix < self._nx and 0 <= iy < self._ny:
            return (ix, iy)
        return None

    def pressure_at(self, x: float, y: float) -> float:
        c = self._cell_of(x, y)
        return self._p.get(c, 0.0) if c is not None else 0.0


def _scale(text: str) -> float:
    m = _RES_RE.search(text)
    return (int(m.group(1)) * 1000.0) if m else 10000.0


def _points(coord_block: str, scale: float):
    nums = [int(t) for t in coord_block.split()]
    return [(nums[i] / scale, -nums[i + 1] / scale)
            for i in range(0, len(nums) - 1, 2)]


def parse(ses_path: str, board: Board, cell_mm: float = 5.0) -> CongestionField:
    with open(ses_path, encoding="utf-8") as f:
        text = f.read()
    scale = _scale(text)

    nx = max(1, int(math.ceil(board.width / cell_mm)))
    ny = max(1, int(math.ceil(board.height / cell_mm)))

    density = {}   # (ix,iy) -> routed mm in cell
    vias = {}      # (ix,iy) -> count
    detour = {}    # (ix,iy) -> summed (ratio-1)

    def cell(x, y):
        ix = int((x - board.x0) // cell_mm)
        iy = int((y - board.y0) // cell_mm)
        if 0 <= ix < nx and 0 <= iy < ny:
            return (ix, iy)
        return None

    # straight pad-span (HPWL) per signal net, for detour ratio
    span = {}
    for net, members in board.nets().items():
        if _is_power(net) or len(members) < 2:
            continue
        pts = [board.components[r].pad_world(board.components[r].pads[pi])
               for r, pi in members]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        span[net] = max(1.0, (max(xs) - min(xs)) + (max(ys) - min(ys)))

    # restrict to the network_out section if present (avoids matching library)
    no = text.split("(network_out", 1)
    body = no[1] if len(no) > 1 else text

    for vx, vy in _VIA_RE.findall(body):
        c = cell(int(vx) / scale, -int(vy) / scale)
        if c:
            vias[c] = vias.get(c, 0) + 1

    for nm in _NET_RE.finditer(body):
        net = nm.group(1).strip('"')
        block = nm.group(2)
        routed = 0.0
        cells_hit = set()
        for _layer, coords in _PATH_RE.findall(block):
            pts = _points(coords, scale)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                seg = math.hypot(bx - ax, by - ay)
                routed += seg
                mc = cell((ax + bx) / 2, (ay + by) / 2)
                if mc:
                    density[mc] = density.get(mc, 0.0) + seg
                    cells_hit.add(mc)
        if net in span and routed > 0:
            ratio = max(0.0, routed / span[net] - 1.0)
            for c in cells_hit:
                detour[c] = detour.get(c, 0.0) + ratio

    if not (density or vias or detour):
        return CongestionField(board.x0, board.y0, cell_mm, nx, ny, {})

    dmax = max(density.values()) if density else 1.0
    vmax = max(vias.values()) if vias else 1.0
    tmax = max(detour.values()) if detour else 1.0
    pressure = {}
    for c in set(density) | set(vias) | set(detour):
        pressure[c] = (density.get(c, 0.0) / dmax
                       + vias.get(c, 0) / vmax
                       + detour.get(c, 0.0) / tmax)
    return CongestionField(board.x0, board.y0, cell_mm, nx, ny, pressure)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_congestion.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/congestion.py tests/test_congestion.py
git commit -m "feat(engine): SES congestion parsing (congestion.py)"
```

---

### Task 3: Annealer accepts a congestion field

**Files:**
- Modify: `plugin/plugins/autoplace/anneal.py`
- Test: `tests/test_engine.py` (add two tests)

**Interfaces:**
- Consumes: a field object with `pressure_at(x, y) -> float` (e.g. `CongestionField`, or any stub).
- Produces: `Annealer(board, *, margin, seed, channel_scale, cohesion_scale, congestion=None)` and `anneal(board, *, ..., congestion=None)`. With a field, each component's channel/spacing weight is scaled up by its sampled pressure; `congestion=None` is unchanged behavior.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def test_congestion_amplifies_channel_penalty():
    from autoplace import anneal
    b = Board(0, 0, 60, 60)
    b.components = {
        "A": Component("A", 4, 4, x=20, y=20),
        "B": Component("B", 4, 4, x=23, y=20),   # close + shadowing -> channel term active
    }

    class HotField:
        empty = False
        def pressure_at(self, x, y):
            return 2.0

    base = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0)
    hot = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0,
                          congestion=HotField())
    a, bb = b.components["A"], b.components["B"]
    assert hot._pair_penalty(a, bb, 0.8) > base._pair_penalty(a, bb, 0.8)


def test_congestion_none_is_unchanged():
    from autoplace import anneal
    b1, b2 = _board(), _board()
    anneal.anneal(b1, seed=7, steps=2000, margin=0.8, channel_scale=0.5)
    anneal.anneal(b2, seed=7, steps=2000, margin=0.8, channel_scale=0.5,
                  congestion=None)
    for ref in b1.components:
        assert b1.components[ref].x == b2.components[ref].x
        assert b1.components[ref].y == b2.components[ref].y
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py::test_congestion_amplifies_channel_penalty tests/test_engine.py::test_congestion_none_is_unchanged -v`
Expected: FAIL — `Annealer.__init__() got an unexpected keyword argument 'congestion'`.

- [ ] **Step 3: Add the congestion field to the annealer**

In `plugin/plugins/autoplace/anneal.py`, add a weight constant inside `_Weights`:

```python
    CHANNEL = 4.0         # soft penalty for gaps narrower than a routing channel
    CONG_K = 3.0          # per-unit-pressure multiplier on the channel term
```

Change `Annealer.__init__` signature and store per-component pressure. Replace the signature line and add the pressure map right after `self.channel = ...`:

```python
    def __init__(self, board: Board, *, margin: float = 0.8, seed: int = 0,
                 channel_scale: float = 1.0, cohesion_scale: float = 1.0,
                 congestion=None):
        import random
        self.board = board
        self.margin = margin
        self.channel = _Weights.CHANNEL * channel_scale
        self.cohesion = _Weights.COHESION * cohesion_scale
        # per-component channel multiplier from the previous routing's congestion
        # (sampled once at the component's start position; fixed for this pass)
        self.cpress = {}
        if congestion is not None and not getattr(congestion, "empty", False):
            self.cpress = {c.ref: congestion.pressure_at(c.x, c.y)
                           for c in board.components.values()}
```

(Keep the rest of `__init__` — `self.rng`, `self.comps`, `self.free`, `self.movable`, the net maps, `self.centroids` — exactly as is, following this insertion.)

In `_pair_penalty`, replace the channel term's use of `self.channel` with a pressure-scaled local weight. Change:

```python
        gap = max(gx, gy)
        shadow = min(gx, gy) < margin
        if self.channel and shadow and 0 <= gap < CHANNEL_MM:
            cost += self.channel * (CHANNEL_MM - gap)
        return cost
```
to:
```python
        gap = max(gx, gy)
        shadow = min(gx, gy) < margin
        if self.channel and shadow and 0 <= gap < CHANNEL_MM:
            press = self.cpress.get(a.ref, 0.0) + self.cpress.get(b.ref, 0.0)
            local = self.channel * (1.0 + _Weights.CONG_K * press / 2.0)
            cost += local * (CHANNEL_MM - gap)
        return cost
```

Update the `anneal` module-level function to pass `congestion` through. Change its signature and the `Annealer(...)` call:

```python
def anneal(board: Board, *, seed: int = 0, steps: int = 6000, margin: float = 0.8,
           channel_scale: float = 1.0, cohesion_scale: float = 1.0,
           congestion=None, progress=None):
    Annealer(board, margin=margin, seed=seed, channel_scale=channel_scale,
             cohesion_scale=cohesion_scale, congestion=congestion).run(
                 steps=steps, progress=progress)
    return board
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: PASS (all, including the two new tests and the existing `test_congestion_none_is_unchanged` proving no regression). `cpress` is empty when `congestion=None`, so `_pair_penalty` adds `CONG_K * 0` — identical arithmetic to before.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/anneal.py tests/test_engine.py
git commit -m "feat(engine): annealer widens channels by per-component congestion"
```

---

### Task 4: `refine.py` — keep-best/patience loop

**Files:**
- Create: `plugin/plugins/autoplace/refine.py`
- Test: `tests/test_refine.py`

**Interfaces:**
- Consumes: nothing engine-specific in the pure loop (callables are injected); the real `refine` wires `routing.route_once`, `congestion.parse`, `anneal.anneal`, `kicad_io.apply_to_board`.
- Produces:
  - `refine.keep_best_loop(initial, route_eval, step, *, budget, patience, margin, progress=None) -> dict` with `{"best":model, "best_pct":float, "iterations":int, "history":list[float]}`. `route_eval(model) -> (pct, field)`; `step(model, field) -> candidate_model`.
  - `refine.refine(board, pcb, *, jar, passes=20, seed=0, budget=8, patience=3, margin_conns=1, cell_mm=5.0, stem, progress=None) -> dict` — the pcbnew-wired loop (returns the same dict; `best` is the refined `board`).

- [ ] **Step 1: Write the failing tests (pure loop, stubbed router)**

Create `tests/test_refine.py`:

```python
"""Pure tests for the keep-best/patience refinement loop. No pcbnew/FreeRouting."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import refine                                # noqa: E402


def _make(pcts):
    """route_eval returning scripted routed-% values in call order."""
    seq = iter(pcts)
    calls = {"step": 0}
    def route_eval(model):
        return next(seq), None                 # (pct, field)
    def step(model, field):
        calls["step"] += 1
        return f"cand{calls['step']}"          # a distinct candidate marker
    return route_eval, step, calls


def test_keeps_best_only_on_improvement_beyond_margin():
    # initial 90; candidates route 90.2 (within margin, reject), 95 (accept)
    route_eval, step, _ = _make([90.0, 90.2, 95.0])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=5, patience=5, margin=1.0)
    assert r["best_pct"] == 95.0
    assert r["best"] == "cand2"


def test_patience_stops_after_non_improving_iters():
    route_eval, step, calls = _make([90.0, 90.1, 90.1, 90.1, 90.1])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=10, patience=2, margin=1.0)
    assert r["best_pct"] == 90.0
    assert r["best"] == "init"                 # never improved
    assert calls["step"] == 2                  # stopped after 2 non-improving


def test_stops_at_100_without_stepping():
    route_eval, step, calls = _make([100.0])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=10, patience=3, margin=1.0)
    assert r["best_pct"] == 100.0
    assert calls["step"] == 0                   # already done, no refinement


def test_warm_starts_from_best_not_last_candidate():
    seen = []
    seq = iter([90.0, 95.0, 91.0, 96.0])
    def route_eval(model):
        return next(seq), None
    def step(model, field):
        seen.append(model)
        return {90.0: "c1", 95.0: "c2", 91.0: "c3"}.get(0, f"c{len(seen)}")
    # simpler: record the model passed to step
    def step2(model, field):
        seen.append(model)
        return f"c{len(seen)}"
    seq2 = iter([90.0, 95.0, 80.0, 99.0])
    def route_eval2(model):
        return next(seq2), None
    r = refine.keep_best_loop("init", route_eval2, step2,
                              budget=3, patience=5, margin=1.0)
    # step1 from "init"(90)->c1 routes 95 accept; step2 from "c1"(95)->c2 routes
    # 80 reject; step3 warm-starts from best "c1" again, not rejected "c2"
    assert seen[0] == "init"
    assert seen[1] == "c1"
    assert seen[2] == "c1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_refine.py -v`
Expected: FAIL — `No module named 'autoplace.refine'`.

- [ ] **Step 3: Implement `refine.py`**

Create `plugin/plugins/autoplace/refine.py`:

```python
"""Route-driven refinement: place -> route -> re-anneal congested spots -> repeat.

``keep_best_loop`` is the pure policy (keep-best + patience), testable with
stubbed callables. ``refine`` wires it to the real router, congestion parser, and
annealer (needs pcbnew + FreeRouting; exercised by cli.py refine).
"""
from __future__ import annotations

import copy


def keep_best_loop(initial, route_eval, step, *, budget, patience, margin,
                   progress=None):
    """Iterate: route the best, refine, re-route, keep only real improvements.

    route_eval(model) -> (pct, field);  step(model, field) -> candidate model.
    Returns {"best", "best_pct", "iterations", "history"}.
    """
    best = initial
    best_pct, field = route_eval(best)
    history = [best_pct]
    if progress is not None:
        progress(0, best_pct, best_pct)
    fails = 0
    it = 0
    while it < budget and best_pct < 100.0 and fails < patience:
        it += 1
        cand = step(best, field)
        pct, cfield = route_eval(cand)
        history.append(pct)
        if pct > best_pct + margin:
            best, best_pct, field, fails = cand, pct, cfield, 0
        else:
            fails += 1
        if progress is not None:
            progress(it, pct, best_pct)
    return {"best": best, "best_pct": best_pct, "iterations": it,
            "history": history}


def refine(board, pcb, *, jar, stem, passes=20, seed=0, budget=8, patience=3,
           margin_conns=1, cell_mm=5.0, progress=None):
    """pcbnew-wired loop. Mutates `board` to the best placement found.

    Inlines the keep-best/patience policy (rather than calling keep_best_loop)
    so the connection-count margin can be derived from the first route's `total`
    and the initial placement is routed only once.
    """
    from . import anneal as anneal_mod
    from . import congestion as cong_mod
    from . import kicad_io
    from . import routing

    state = {"total": 1}

    def route_eval(model):
        kicad_io.apply_to_board(model, pcb)
        r = routing.route_once(pcb, jar, passes, stem)
        state["total"] = r["total"]
        field = cong_mod.parse(r["ses_path"], model, cell_mm=cell_mm)
        return r["pct"], field

    def step(model, field):
        cand = copy.deepcopy(model)
        anneal_mod.anneal(cand, seed=seed, margin=0.8, congestion=field)
        return cand

    best = copy.deepcopy(board)
    best_pct, field = route_eval(best)
    margin_pct = 100.0 * margin_conns / max(1, state["total"])
    history = [best_pct]
    if progress is not None:
        progress(0, best_pct, best_pct)
    fails = 0
    it = 0
    while it < budget and best_pct < 100.0 and fails < patience:
        it += 1
        cand = step(best, field)
        pct, cfield = route_eval(cand)
        history.append(pct)
        if pct > best_pct + margin_pct:
            best, best_pct, field, fails = cand, pct, cfield, 0
        else:
            fails += 1
        if progress is not None:
            progress(it, pct, best_pct)

    board.components = best.components       # write the winner back to the caller
    return {"best": board, "best_pct": best_pct, "iterations": it,
            "history": history, "total": state["total"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_refine.py -v`
Expected: PASS (4 passed). (Only `keep_best_loop` is unit-tested; `refine` is covered by the Task 5 smoke test.)

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/refine.py tests/test_refine.py
git commit -m "feat(engine): keep-best/patience route-driven refinement loop"
```

---

### Task 5: `cli.py refine` — streaming subcommand

**Files:**
- Modify: `cli.py`
- Test: smoke (KiCad python + FreeRouting)

**Interfaces:**
- Consumes: `refine.refine`, `kicad_io.load_board/apply_placement/copy_project`.
- Produces: `cli.py refine IN [OUT] [SEED]` — runs the loop; in stream mode emits `iteration` events and a final `result` with `routed_pct`, `iterations`, `routed_output`.

- [ ] **Step 1: Add `cmd_refine` and route it**

In `cli.py`, add the FreeRouting jar default near the top (after imports):

```python
DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")
```

Add `cmd_refine`:

```python
def cmd_refine(args):
    """Route-driven refinement: place -> route -> re-anneal -> repeat (keep best)."""
    from autoplace import refine as refine_mod
    in_path = args[0]
    out_path = args[1] if len(args) > 1 else _default_out(in_path)
    seed = int(args[2]) if len(args) > 2 else 0
    jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
    passes = int(os.environ.get("REFINE_PASSES", "20"))
    budget = int(os.environ.get("REFINE_BUDGET", "8"))
    stream = os.environ.get("AUTOPLACE_STREAM") == "1"

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

    progress = None
    if stream:
        def progress(it, pct, best_pct):
            emit({"type": "iteration", "iter": it,
                  "routed_pct": round(pct, 1), "best_pct": round(best_pct, 1)})

    model, pcb = kicad_io.load_board(in_path)
    connectors = _read_connectors(in_path)
    if connectors is not None:
        from autoplace import engine
        engine.place(model, seed=seed, connectors=connectors)   # ensure a placement
    stem = os.path.splitext(out_path)[0]
    r = refine_mod.refine(model, pcb, jar=jar, stem=stem, passes=passes,
                          seed=seed, budget=budget, progress=progress)
    kicad_io.apply_placement(model, pcb, out_path)
    report = {"input": in_path, "output": out_path,
              "routed_pct": round(r["best_pct"], 1),
              "iterations": r["iterations"], "history": r["history"],
              "routed_output": stem + ".routed.kicad_pcb",
              "project_copied": kicad_io.copy_project(in_path, out_path)}
    if stream:
        report["type"] = "result"; emit(report)
    else:
        print(json.dumps(report, indent=2))
    return 0
```

Update `main` to accept and route `refine`:

```python
def main(argv):
    if len(argv) < 2 or argv[1] not in ("place", "metrics", "dump", "refine"):
        print(__doc__)
        return 2
    return {"place": cmd_place, "metrics": cmd_metrics, "dump": cmd_dump,
            "refine": cmd_refine}[argv[1]](argv[2:])
```

- [ ] **Step 2: Run the pure suite (no regressions)**

Run: `python -m pytest tests/ -v`
Expected: PASS (all). (cli import path unchanged for pure tests.)

- [ ] **Step 3: Smoke test (KiCad python + FreeRouting), short budget**

Run (budget 2 to keep it quick — a couple of routing iterations):
```bash
REFINE_BUDGET=2 REFINE_PASSES=10 "/c/Program Files/KiCad/10.0/bin/python.exe" cli.py refine "C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.autoplaced.kicad_pcb" "C:/Users/Mads2/AppData/Local/Temp/system.refined.kicad_pcb" 0
```
Expected: a JSON report with `routed_pct` (≈95–100), `iterations` ≤ 2, a `history` list, and `routed_output` written. No exceptions.

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "feat(cli): add streaming refine subcommand"
```

---

### Task 6: App IPC — run-refine

**Files:**
- Modify: `app/main.js`
- Modify: `app/preload.js`

**Interfaces:**
- Consumes: `cli.py refine` streaming protocol (Task 5).
- Produces (on `window.api`): `runRefine(opts) -> {ok, report, output}|{ok:false, error}`, streaming `place-event` messages including the new `iteration` type.

- [ ] **Step 1: Add `runRefine` in `main.js`**

In `app/main.js`, add a `runRefine` function modeled on `runPlace` but spawning the `refine` subcommand and forwarding the `iteration` event. Place it next to `runPlace`:

```javascript
function runRefine(win, { board, python, seed }) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    const stem = board.replace(/\.kicad_pcb$/i, "");
    const out = stem + ".refined.kicad_pcb";
    const send = (evt) => {
      if (!win.isDestroyed()) win.webContents.send("place-event", evt);
    };
    const env = { ...process.env, AUTOPLACE_STREAM: "1" };
    const args = [CLI_PY, "refine", board, out, String(seed ?? 0)];
    send({ type: "log", line: `$ ${python} cli.py refine "${board}" ...` });

    let proc;
    try {
      proc = spawn(python, args, { cwd: REPO_ROOT, env });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    let stdoutBuf = "";
    let result = null;
    const handleLine = (line) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith("{")) {
        try {
          const obj = JSON.parse(t);
          if (obj.type === "iteration") return send({ type: "iteration", ...obj });
          if (obj.type === "progress")
            return send({ type: "progress", stage: obj.stage, percent: obj.percent });
          if (obj.type === "result") {
            result = obj;
            return send({ type: "result", report: obj });
          }
        } catch {
          /* fall through to log */
        }
      }
      send({ type: "log", line });
    };
    proc.stdout.on("data", (chunk) => {
      stdoutBuf += chunk.toString();
      let nl;
      while ((nl = stdoutBuf.indexOf("\n")) >= 0) {
        handleLine(stdoutBuf.slice(0, nl));
        stdoutBuf = stdoutBuf.slice(nl + 1);
      }
    });
    proc.stderr.on("data", (chunk) =>
      chunk.toString().split("\n").forEach((l) => l.trim() && send({ type: "log", line: l }))
    );
    proc.on("error", (e) =>
      resolve({ ok: false, error: `failed to start python: ${e.message}` })
    );
    proc.on("close", (code) => {
      if (stdoutBuf.trim()) handleLine(stdoutBuf);
      if (result) resolve({ ok: true, report: result, output: out });
      else resolve({ ok: false, error: `refine exited ${code} without a result (check the log)` });
    });
  });
}
```

Register the handler in `registerIpc` next to `run-place`:

```javascript
  ipcMain.handle("run-refine", (_e, opts) => runRefine(win, opts));
```

- [ ] **Step 2: Expose `runRefine` in `preload.js`**

In `app/preload.js`, add to the `api` object:

```javascript
  runRefine: (opts) => ipcRenderer.invoke("run-refine", opts),
```

- [ ] **Step 3: Syntax check**

Run: `node --check app/main.js && node --check app/preload.js`
Expected: no output (both parse clean).

- [ ] **Step 4: Commit**

```bash
git add app/main.js app/preload.js
git commit -m "feat(app): run-refine IPC streaming route-driven refinement"
```

---

### Task 7: Renderer — "Refine" action + per-iteration routed-%

**Files:**
- Modify: `app/renderer/index.html`
- Modify: `app/renderer/renderer.js`
- Modify: `app/renderer/styles.css`

**Interfaces:**
- Consumes: `window.api.runRefine` and the `iteration` / `result` events (Tasks 5–6).
- Produces: a "Refine (route-driven)" button that runs the loop and shows routed-% per iteration; on completion re-renders the board canvas from the refined output and shows final routed-%.

- [ ] **Step 1: Add the button and an iteration readout to `index.html`**

In `app/renderer/index.html`, inside the `.controls` div, after the existing run button (`<button id="run" ...>`), add:

```html
          <button id="refine" class="btn btn-ghost" disabled>Refine (route-driven)</button>
```

Inside the `#progressWrap` section, after the `.bar` div, add a routed-% readout:

```html
          <div id="refineReadout" class="refine-readout" hidden>
            routed: <span id="refinePct">–</span>% (best <span id="refineBest">–</span>%)
          </div>
```

- [ ] **Step 2: Add styles to `styles.css`**

Append to `app/renderer/styles.css`:

```css
.refine-readout { margin-top: 6px; font-size: 13px; color: var(--text-secondary, #9aa); }
```

- [ ] **Step 3: Wire the Refine button in `renderer.js`**

In `app/renderer/renderer.js`, add a refine runner modeled on `run()`. Add this function after `run()`:

```javascript
async function runRefine() {
  if (state.running) return;
  state.running = true;
  refreshRunEnabled();
  $("refine").disabled = true;
  $("log").textContent = "";
  setProgress("route", 0);
  $("refineReadout").hidden = false;
  $("refinePct").textContent = "–";
  $("refineBest").textContent = "–";

  const res = await window.api.runRefine({
    board: state.board,
    python: state.python,
    seed: parseInt($("seed").value, 10) || 0,
  });

  state.running = false;
  refreshRunEnabled();
  if (res.ok) {
    setProgress("done", 100);
    showResults(res.report, res.output);
    $("refineBest").textContent = res.report.routed_pct;
    const dump = await window.api.dumpBoard({ python: state.python, board: res.output });
    if (dump.ok) {
      state.geometry = dump.geometry;
      $("boardMode").textContent = "after refinement";
      renderBoard(state.geometry);
    }
  } else {
    setProgress("done", 100);
    $("progressStage").textContent = "Refine failed";
    appendLog("ERROR: " + res.error);
    openLog(true);
  }
}
```

Extend `refreshRunEnabled` to also toggle the refine button. Replace it with:

```javascript
function refreshRunEnabled() {
  const ready = state.python && state.board && !state.running;
  $("run").disabled = !ready;
  const refineBtn = $("refine");
  if (refineBtn) refineBtn.disabled = !ready;
}
```

Handle the `iteration` event in the existing `onPlaceEvent` callback. Find the `window.api.onPlaceEvent((evt) => { ... })` block and add an `iteration` branch:

```javascript
window.api.onPlaceEvent((evt) => {
  if (evt.type === "progress") setProgress(evt.stage, evt.percent);
  else if (evt.type === "iteration") {
    setProgress("route", 100);
    $("progressStage").textContent = `Routing + refining (iter ${evt.iter})`;
    $("refineReadout").hidden = false;
    $("refinePct").textContent = evt.routed_pct;
    $("refineBest").textContent = evt.best_pct;
  } else if (evt.type === "result") showResults(evt.report, evt.report.output);
  else if (evt.type === "log") appendLog(evt.line);
});
```

Add a `route` label to `stageLabel`'s map (so progress shows a sensible label). In the object inside `stageLabel`, add:

```javascript
      route: "Routing with FreeRouting…",
```

Wire the button listener near the other `addEventListener` calls (next to `$("run").addEventListener(...)`):

```javascript
$("refine").addEventListener("click", runRefine);
```

- [ ] **Step 4: Syntax check**

Run: `node --check app/renderer/renderer.js`
Expected: no output (parses clean).

- [ ] **Step 5: Manual verification (full flow)**

From `app/`:
```bash
AUTOPLACE_DEV_BOARD="C:/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.autoplaced.kicad_pcb" npm run dev
```
Verify: the board renders; clicking "Refine (route-driven)" streams `iter 1, 2, …` with routed-% updating in the readout; on completion the canvas switches to "after refinement", the results dashboard shows the final routed-%, and a `system.autoplaced.refined.kicad_pcb` + `.routed.kicad_pcb` are written. (This is the long-running path — each iteration runs FreeRouting.)

- [ ] **Step 6: Commit**

```bash
git add app/renderer/index.html app/renderer/renderer.js app/renderer/styles.css
git commit -m "feat(app): Refine button with per-iteration routed-% readout"
```

---

## Self-Review

**Spec coverage:**
- `routing.route_once` extracted, `route_check` reuses it → Task 1. `congestion.py` SES parse → CongestionField (density + via + detour, pressure_at) → Task 2. Annealer congestion field → per-component channel scaling, `None` unchanged → Task 3. keep-best/patience loop (warm-start from best, margin, patience, budget, stop at 100) → Task 4. `cli.py refine` streaming → Task 5. App `run-refine` IPC + `iteration` event → Task 6. Renderer Refine action + per-iteration routed-% + post-run re-render → Task 7. Error handling: missing/empty SES raises with tail (Task 1), empty field degenerates to plain re-anneal (Tasks 2/3), FreeRouting/Java missing surfaces via the run's error path (Tasks 5/6). Streaming protocol `iteration` event → Tasks 5/6/7. SES scale/Y-negation in the parser → Task 2 (Global Constraints). All spec sections covered.
- Out of scope per spec (single-sided via-as-failure modelling, adaptive pass count) → intentionally absent.

**Placeholder scan:** No TBD/TODO; every code step has complete code; every test step has full assertions. The `refine.refine` function inlines the loop (rather than calling `keep_best_loop`) deliberately, to establish `total` from the first route before converting `margin_conns`→`margin_pct` and to avoid double-routing the initial; `keep_best_loop` remains the unit-tested pure policy.

**Type consistency:** `CongestionField.pressure_at(x,y)->float` (Task 2) consumed by `Annealer` via `congestion.pressure_at` and the `HotField` stub (Task 3). `congestion.parse(ses_path, board, cell_mm)` (Task 2) called in `refine.refine` (Task 4) and indirectly via cli (Task 5). `routing.route_once(pcb, jar, passes, stem)->{...,"pct","ses_path","total"}` (Task 1) consumed in `refine.refine` (Task 4). `anneal.anneal(..., congestion=)` (Task 3) called in `refine.step` (Task 4). `keep_best_loop(initial, route_eval, step, *, budget, patience, margin, progress)` (Task 4) — tests match signature. `refine.refine(board, pcb, *, jar, stem, passes, seed, budget, patience, margin_conns, cell_mm, progress)->{"best","best_pct","iterations","history","total"}` (Task 4) consumed by cli (Task 5). `iteration` event shape `{iter, routed_pct, best_pct}` emitted (Task 5), forwarded (Task 6), rendered (Task 7). `window.api.runRefine` (Task 6) used in renderer (Task 7). Consistent.
