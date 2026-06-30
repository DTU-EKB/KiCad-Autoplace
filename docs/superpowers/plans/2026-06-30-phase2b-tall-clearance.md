# Phase 2B Tall-Part Clearance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give tall through-hole parts extra neighbor clearance (rework/inspection room) via a height-driven widening of the existing routing-channel target in `anneal._pair_penalty`, validated by a real FreeRouting non-regression.

**Architecture:** A pure `footprints.height_mm(fpid)` table → `Component.height` (set in `kicad_io`); `_pair_penalty` adds `TALL_HALO_MM * channel_scale` to its channel target when a tall part is in the pair (mirrors the D4 cross-block gutter); a `metrics.tall_clearance` validation metric. No `_quality` change, no new cost-term structure.

**Tech Stack:** Python 3 (pure engine + plain `pytest`), KiCad 10 `pcbnew` + Java FreeRouting (gate only).

## Global Constraints

- **`anneal._quality` is NEVER modified.** The halo is part of `_pair_penalty` (search-bias `local_cost`).
- **Dense boards protected:** the halo is `× self.channel_scale` (0 on dense boards), exactly like the D4 gutter.
- **No regression where no tall parts:** if no pair has a tall part, the halo never fires.
- **Determinism:** `height` is data; the halo is deterministic.
- **Constants (in `metrics.py`, imported by `anneal`):** `TALL_MM = 8.0`, `TALL_HALO_MM = 2.0`. `Component.height` default `4.0`.
- **FreeRouting baseline (current `main`, no-connector):** `system` 95.0% (170/179), `motor_power` 66.1% (82/124). After B3: `system` ≥ ~93%, `motor_power` ≥ ~64%; `tall_clearance` strictly lower on `system`.
- **Tests:** plain `python -m pytest tests/` (baseline 89). Real-board check + gate under `"C:\Program Files\KiCad\10.0\bin\python.exe"`.
- **Commits: developer voice, NO AI attribution.**

---

### Task 1: `footprints.height_mm` + `Component.height` (B1)

**Files:**
- Create: `plugin/plugins/autoplace/footprints.py`
- Modify: `plugin/plugins/autoplace/model.py` (add `Component.height`)
- Modify: `plugin/plugins/autoplace/kicad_io.py` (set `height` in `build_model`)
- Test: `tests/test_footprints.py`

**Interfaces:**
- Produces: `footprints.height_mm(fpid: str) -> float`; `Component.height: float = 4.0`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_footprints.py`:

```python
"""Headless tests for the footprint-class height table. No pcbnew.

  python -m pytest tests/test_footprints.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import footprints                       # noqa: E402


def test_tall_parts():
    assert footprints.height_mm("energy_system:TO-220-3_Vertical_LaserPads") >= 8.0
    assert footprints.height_mm("Capacitor_THT:CP_Radial_D18.0mm_P7.50mm") >= 18.0
    assert footprints.height_mm("Capacitor_THT:CP_Radial_D8.0mm_P3.50mm") >= 8.0
    assert footprints.height_mm("energy_system:L_Toroid_Vertical_L34.5mm_W15.0mm") >= 8.0
    assert footprints.height_mm("TerminalBlock:TerminalBlock_bornier-2_P5.08mm") >= 8.0
    assert footprints.height_mm("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical") >= 8.0
    assert footprints.height_mm("Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical") >= 8.0


def test_short_parts():
    assert footprints.height_mm("Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal") < 8.0
    assert footprints.height_mm("Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal") < 8.0
    assert footprints.height_mm("Package_DIP:DIP-8_W7.62mm_LongPads") < 8.0
    assert footprints.height_mm("Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm") < 8.0


def test_cp_radial_scales_with_diameter():
    big = footprints.height_mm("Capacitor_THT:CP_Radial_D18.0mm_P7.50mm")
    small = footprints.height_mm("Capacitor_THT:CP_Radial_D8.0mm_P3.50mm")
    assert big > small


def test_unknown_is_low_default():
    assert footprints.height_mm("Some:Unknown_Footprint_XYZ") == 4.0
    assert footprints.height_mm("") == 4.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_footprints.py -q`
Expected: FAIL — `No module named 'autoplace.footprints'`.

- [ ] **Step 3: Create the table**

Create `plugin/plugins/autoplace/footprints.py`:

```python
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
```

- [ ] **Step 4: Add the `Component.height` field**

In `plugin/plugins/autoplace/model.py`, change the end of the `Component` dataclass (the line `fpid: str = ""`, Phase 1's last field) from:

```python
    fpid: str = ""                 # fp.GetFPIDAsString(): footprint class, e.g. "Capacitor_THT:C_Disc..."
```

to:

```python
    fpid: str = ""                 # fp.GetFPIDAsString(): footprint class, e.g. "Capacitor_THT:C_Disc..."
    height: float = 4.0            # nominal THT body height (mm) from footprints.height_mm(fpid); low default
```

- [ ] **Step 5: Set `height` in `kicad_io.build_model`**

In `plugin/plugins/autoplace/kicad_io.py`, add the import near the top (with the other `from .` imports):

```python
from . import footprints, nets
```

(replacing the existing `from . import nets`).

Then in `build_model`, in the `Component(...)` construction, add `height=footprints.height_mm(fpid)` right after `fpid=fpid`:

```python
            value=_safe(fp.GetValue),
            fpid=fpid,
            height=footprints.height_mm(fpid),
        )
```

- [ ] **Step 6: Run footprint tests + full suite**

Run: `python -m pytest tests/test_footprints.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: 93 passed (89 + 4 new).

- [ ] **Step 7: Real-board height check (KiCad python)**

Write a throwaway script to scratchpad (NOT committed) that loads `system.kicad_pcb` and prints the count of tall parts (height ≥ 8) and a few examples, to confirm `kicad_io` populates `height` on a real board:

```python
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import kicad_io
board, _ = kicad_io.load_board(sys.argv[1])
tall = [(c.ref, round(c.height, 1), c.fpid.split(":")[-1]) for c in board.components.values() if c.height >= 8.0]
print(f"tall parts (>=8mm): {len(tall)} of {len(board.components)}")
for t in sorted(tall)[:12]:
    print("  ", t)
```

Run under KiCad python on `system.kicad_pcb`. Expected: a non-zero count with TO-220s / electrolytics / headers listed. Capture the output in the report.

- [ ] **Step 8: Commit**

```bash
git add plugin/plugins/autoplace/footprints.py plugin/plugins/autoplace/model.py plugin/plugins/autoplace/kicad_io.py tests/test_footprints.py
git commit -m "Add footprints.height_mm + Component.height (THT body height by class)"
```

---

### Task 2: `metrics.tall_clearance` + constants (B3 metric)

**Files:**
- Modify: `plugin/plugins/autoplace/metrics.py` (constants + function)
- Test: `tests/test_metrics_proxies.py`

**Interfaces:**
- Produces: `metrics.TALL_MM = 8.0`, `metrics.TALL_HALO_MM = 2.0`, `metrics.tall_clearance(board, margin=0.8, track=1.0) -> float`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics_proxies.py`:

```python
def test_tall_clearance_penalises_short_near_tall_and_zero_when_none():
    from autoplace.model import Board, Component
    # tall part U1 (height 18) with a short R1 (height 3) inside its halo
    b = Board(0, 0, 80, 80)
    u1 = Component("U1", 4, 4, x=20, y=20, height=18.0)
    r1 = Component("R1", 4, 4, x=27.5, y=20, height=3.0)   # gx = 3.5
    b.components = {"U1": u1, "R1": r1}
    # halo target = channel_width(0.8,1.0)=2.6 + TALL_HALO_MM 2.0 = 4.6; gap 3.5 < 4.6 -> shortfall 1.1
    d = metrics.tall_clearance(b)
    assert 1.0 < d < 1.2

    # no tall parts -> 0.0
    b2 = Board(0, 0, 80, 80)
    b2.components = {"A": Component("A", 4, 4, x=20, y=20, height=3.0),
                     "B": Component("B", 4, 4, x=27.5, y=20, height=3.0)}
    assert metrics.tall_clearance(b2) == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_metrics_proxies.py::test_tall_clearance_penalises_short_near_tall_and_zero_when_none -q`
Expected: FAIL — no `tall_clearance` / `TALL_MM`.

- [ ] **Step 3: Add the constants + function**

Append to `plugin/plugins/autoplace/metrics.py`:

```python
# Tall-part DFM spacing. A part at/above TALL_MM casts a "shadow" -- small parts
# near it can't be hand-soldered / reworked -- so it wants TALL_HALO_MM extra
# neighbor clearance. Shared by anneal._pair_penalty and tall_clearance below.
TALL_MM = 8.0
TALL_HALO_MM = 2.0


def tall_clearance(board: Board, margin: float = 0.8, track: float = 1.0) -> float:
    """Mean shortfall (mm) of tall-part neighbor gaps below the tall halo target.

    Over neighbor pairs that include a tall part (height >= TALL_MM) and shadow on
    one axis (perpendicular gap < margin), the mean of max(0, halo_target - gap),
    where halo_target = channel_width(margin, track) + TALL_HALO_MM. Lower is
    better; 0.0 when no tall part shadows a neighbor. Pure; shares the constants
    with the placement term so metric and term stay in lockstep."""
    target = channel_width(margin, track) + TALL_HALO_MM
    comps = list(board.components.values())
    n = 0
    total = 0.0
    for i in range(len(comps)):
        a = comps[i]
        for j in range(i + 1, len(comps)):
            b = comps[j]
            if max(a.height, b.height) < TALL_MM:
                continue
            gx = abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2
            gy = abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2
            if min(gx, gy) < margin:           # shadow
                n += 1
                total += max(0.0, target - max(gx, gy))
    return round(total / n, 3) if n else 0.0
```

- [ ] **Step 4: Run the proxy tests + full suite**

Run: `python -m pytest tests/test_metrics_proxies.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: 94 passed.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/metrics.py tests/test_metrics_proxies.py
git commit -m "Add TALL_MM/TALL_HALO_MM + metrics.tall_clearance (tall-part halo intrusion)"
```

---

### Task 3: Tall halo in `anneal._pair_penalty` (B2)

**Files:**
- Modify: `plugin/plugins/autoplace/anneal.py` (import constants; widen `target`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Component.height` (Task 1), `metrics.TALL_MM`/`TALL_HALO_MM` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_tall_part_widens_channel_halo():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    tall = Component("U1", 4, 4, x=20, y=20, height=18.0)
    short = Component("R1", 4, 4, x=27.5, y=20, height=3.0)   # gx = 3.5
    b.components = {"U1": tall, "R1": short}
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=1.0)
    with_tall = ann._pair_penalty(tall, short, 0.8)           # 3.5 < channel 2.6 + halo 2.0
    tall.height = 3.0                                          # now both short
    both_short = ann._pair_penalty(tall, short, 0.8)          # 3.5 not < channel 2.6
    assert with_tall > 0
    assert both_short == 0
    assert with_tall > both_short


def test_tall_halo_inert_on_dense_board():
    from autoplace import anneal
    b = Board(0, 0, 80, 80)
    tall = Component("U1", 4, 4, x=20, y=20, height=18.0)
    short = Component("R1", 4, 4, x=27.5, y=20, height=3.0)
    b.components = {"U1": tall, "R1": short}
    ann = anneal.Annealer(b, margin=0.8, seed=0, channel_scale=0.0)   # dense -> channel off
    assert ann._pair_penalty(tall, short, 0.8) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_tall_part_widens_channel_halo -q`
Expected: FAIL — `with_tall == 0` (gap 3.5 ≥ the un-widened channel 2.6).

- [ ] **Step 3: Import the constants**

In `plugin/plugins/autoplace/anneal.py`, change the metrics import (line ~27) from:

```python
from .metrics import _is_power, channel_width
```

to:

```python
from .metrics import _is_power, channel_width, TALL_HALO_MM, TALL_MM
```

- [ ] **Step 4: Widen the channel target for tall parts**

In `plugin/plugins/autoplace/anneal.py` `_pair_penalty`, change the target block from:

```python
        target = self.channel_mm
        if a.block and b.block and a.block != b.block:
            target += self.gutter * self.channel_scale
        if self.channel and shadow and 0 <= gap < target:
```

to:

```python
        target = self.channel_mm
        if a.block and b.block and a.block != b.block:
            target += self.gutter * self.channel_scale
        if max(a.height, b.height) >= TALL_MM:           # tall part needs rework clearance
            target += TALL_HALO_MM * self.channel_scale
        if self.channel and shadow and 0 <= gap < target:
```

- [ ] **Step 5: Run engine tests + full suite**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (including the 2 new).

Run: `python -m pytest tests/ -q`
Expected: 96 passed (94 + 2).

- [ ] **Step 6: Commit**

```bash
git add plugin/plugins/autoplace/anneal.py tests/test_engine.py
git commit -m "Widen the routing-channel halo around tall parts (channel_scale-aware)"
```

---

### Task 4: FreeRouting non-regression gate (validation, no commit)

**Files:** none (runs `scratchpad/route_baseline.py` against the branch + `scratchpad/decap_measure.py`-style tall_clearance check).

- [ ] **Step 1: Route the gate boards with the branch engine**

From the repo root on the `phase2b-tall-clearance` branch (no-connector baseline):

```bash
"/c/Program Files/KiCad/10.0/bin/python.exe" "<scratchpad>/route_baseline.py" "<scratchpad>/after2b" \
  "/c/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb" \
  "/c/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/boards/motor_power/motor_power.kicad_pcb"
```

- [ ] **Step 2: Measure tall_clearance on/off**

Write a throwaway script (scratchpad) that places `system` with the branch engine and prints `metrics.tall_clearance` for `TALL_HALO_MM=2.0` vs `0.0` (monkeypatch `metrics.TALL_HALO_MM` AND re-import path so `_pair_penalty` sees 0 — simplest: set `anneal`-visible constant to 0 by patching `metrics.TALL_HALO_MM` before constructing the Annealer is NOT enough since anneal imported the name; instead place with the default and separately place a copy where each component's `height` is forced to 4.0 to disable the halo). Print both `tall_clearance` values. Record them.

- [ ] **Step 3: Evaluate the gate**

- **Non-regression (hard):** `system` ≥ 93.0 and `motor_power` ≥ 64.0 vs baseline 95.0 / 66.1. If either drops, lower `TALL_HALO_MM` (e.g. 1.0) and re-run; do not merge a regression.
- **Tall-clearance improvement:** on `system`, `tall_clearance` with the halo strictly lower than with heights forced low (term off). Record both numbers.

- [ ] **Step 4: Record the gate result in the ledger.**

---

## Notes for the implementer

- Full-suite counts: T1 → 93, T2 → 94, T3 → 96.
- The halo is intentionally `max(a.height, b.height) >= TALL_MM` (fires if EITHER part is tall — a tall part wants clearance from anything). The `tall_clearance` metric uses the same predicate, so they stay in lockstep.
- `<scratchpad>` = `C:\Users\Mads2\AppData\Local\Temp\claude\C--Users-Mads2-KiCad-Autoplace\212dd608-1b37-447e-bf22-4e15903d8520\scratchpad`.
- Build order: T1 (data) → T2 (metric) → T3 (term) → T4 (gate). T3 last so the gate measures the real placement change.
