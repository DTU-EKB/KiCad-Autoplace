# Phase 1 Data-Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add electrical-intent data (component value, footprint class, pad electrical type) and a pure net-role classifier to the model, as the additive enabler for Phase 2 — with zero placement-behavior change.

**Architecture:** Four additive model fields (`model.py`), populated by `kicad_io.build_model` (the only `pcbnew` module), exposed by `serialize.py`, and consumed by one new pure function `nets.classify_net`. No engine/cost code reads any of it yet.

**Tech Stack:** Python 3 (pure engine + plain `pytest`), KiCad 10 `pcbnew` (extraction only, run via KiCad's bundled Python).

## Global Constraints

- **Purely additive — zero placement-behavior change.** New fields default to `""`; nothing consumes `classify_net` yet. The existing **70-test** suite must pass unchanged and placement output stays byte-identical to current `main`.
- **Engine stays `pcbnew`-free.** `classify_net` is pure (in `nets.py`); only `kicad_io.py` reads `pcbnew`.
- **`anneal._quality` is never touched.**
- **Determinism.** `classify_net` is a pure, deterministic function of model data.
- **Graceful degradation.** Missing pin types or `pcbnew` API differences degrade to `""` / name-based classification — never crash `build_model`.
- **`classify_net` taxonomy:** returns exactly one of `"GROUND" | "POWER" | "SENSE" | "SIGNAL" | "NC"`. Documented judgment calls: `VSS` → POWER (a rail, not ground); `SW`/`GATE` → SIGNAL (aggressor/victim axis is Phase 3).
- **Plain-Python tests:** `python -m pytest tests/`. Real-board extraction runs under `"C:\Program Files\KiCad\10.0\bin\python.exe"`.
- **Commits: developer voice, NO AI attribution.**

---

### Task 1: Model fields + serialize exposure (E1 + E4)

**Files:**
- Modify: `plugin/plugins/autoplace/model.py:17-22` (Pad), `:25-38` (Component)
- Modify: `plugin/plugins/autoplace/serialize.py:11-30`
- Test: `tests/test_serialize.py`

**Interfaces:**
- Produces: `Pad(name, net, ox, oy, pin_type="", pin_function="")`; `Component(... , edge="", value="", fpid="")`. `serialize.board_to_dict` footprint dicts gain `value`/`fpid`; pad dicts gain `pin_type`/`pin_function`.

- [ ] **Step 1: Update the failing test**

In `tests/test_serialize.py`, replace `test_board_to_dict_shape` (lines 10-23) with:

```python
def test_board_to_dict_shape():
    b = Board(0, 0, 50, 40)
    b.components = {
        "J1": Component("J1", 4, 4, x=10, y=20, is_connector=True, block="b0",
                        value="CONN_2x1", fpid="Connector:PinHeader_2x1",
                        pads=[Pad("1", "SIG", 1.0, 0.0,
                                  pin_type="input", pin_function="RX")]),
    }
    d = serialize.board_to_dict(b)
    assert d["outline"] == {"x0": 0, "y0": 0, "x1": 50, "y1": 40}
    assert len(d["footprints"]) == 1
    fp = d["footprints"][0]
    assert fp["ref"] == "J1"
    assert fp["is_connector_guess"] is True
    assert fp["block"] == "b0"
    assert fp["value"] == "CONN_2x1"
    assert fp["fpid"] == "Connector:PinHeader_2x1"
    assert fp["pads"] == [{"net": "SIG", "ox": 1.0, "oy": 0.0,
                           "pin_type": "input", "pin_function": "RX"}]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_serialize.py::test_board_to_dict_shape -q`
Expected: FAIL — `Component.__init__() got an unexpected keyword argument 'value'`.

- [ ] **Step 3: Add the `Pad` fields**

In `plugin/plugins/autoplace/model.py`, change the `Pad` dataclass (lines 17-22) from:

```python
@dataclass
class Pad:
    name: str
    net: str          # "" when unconnected
    ox: float         # offset from component centre (mm), current orientation
    oy: float
```

to:

```python
@dataclass
class Pad:
    name: str
    net: str          # "" when unconnected
    ox: float         # offset from component centre (mm), current orientation
    oy: float
    pin_type: str = ""       # schematic electrical type (power_in/power_out/input/output/passive/no_connect/...); "" = unknown
    pin_function: str = ""    # schematic pin name (VDD, SW, GATE, ...); "" = none
```

- [ ] **Step 4: Add the `Component` fields**

In `plugin/plugins/autoplace/model.py`, change the end of the `Component` dataclass (line 38) from:

```python
    edge: str = ""                 # "" free; "L"/"R"/"T"/"B" pinned to that edge
```

to:

```python
    edge: str = ""                 # "" free; "L"/"R"/"T"/"B" pinned to that edge
    value: str = ""                # fp.GetValue(): "100n", "4700u/50V", "1N4148", ...
    fpid: str = ""                 # fp.GetFPIDAsString(): footprint class, e.g. "Capacitor_THT:C_Disc..."
```

- [ ] **Step 5: Expose the fields in `serialize.py`**

In `plugin/plugins/autoplace/serialize.py`, change `board_to_dict` (lines 15-29) from:

```python
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
```

to:

```python
        "footprints": [
            {
                "ref": c.ref,
                "x": c.x, "y": c.y,
                "w": c.eff_w, "h": c.eff_h,
                "rot": c.rot,
                "block": c.block,
                "sheet": c.sheet,
                "edge": c.edge,
                "value": c.value,
                "fpid": c.fpid,
                "is_connector_guess": c.is_connector,
                "locked": c.locked,
                "pads": [{"net": p.net, "ox": p.ox, "oy": p.oy,
                          "pin_type": p.pin_type, "pin_function": p.pin_function}
                         for p in c.pads],
            }
            for c in board.components.values()
        ],
```

- [ ] **Step 6: Run the serialize tests + full suite**

Run: `python -m pytest tests/test_serialize.py -q`
Expected: PASS (both tests).

Run: `python -m pytest tests/ -q`
Expected: 70 passed (additive defaults perturb nothing).

- [ ] **Step 7: Commit**

```bash
git add plugin/plugins/autoplace/model.py plugin/plugins/autoplace/serialize.py tests/test_serialize.py
git commit -m "Add value/fpid + pad pin_type/pin_function model fields and serialize them"
```

---

### Task 2: `nets.classify_net` (E3)

**Files:**
- Modify: `plugin/plugins/autoplace/nets.py`
- Test: `tests/test_nets.py`

**Interfaces:**
- Consumes: `Pad.pin_type` (Task 1), `Board.nets()`, `nets.is_gnd_name`.
- Produces: `nets.classify_net(board: Board, net: str) -> str` in `{"GROUND","POWER","SENSE","SIGNAL","NC"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nets.py`:

```python
from autoplace.model import Board, Component, Pad     # noqa: E402


def _board(net_to_pintypes):
    """Build a Board where each net maps to a list of pad pin_type strings
    (one synthetic 1-pad component per pad)."""
    b = Board(0, 0, 10, 10)
    comps = {}
    for i, (net, pts) in enumerate(net_to_pintypes.items()):
        for j, pt in enumerate(pts):
            ref = f"X{i}_{j}"
            comps[ref] = Component(ref, 1, 1, x=0, y=0,
                                   pads=[Pad(str(j), net, 0.0, 0.0, pin_type=pt)])
    b.components = comps
    return b


def test_classify_ground():
    b = _board({"GND": ["passive", "passive"], "/Motor Power/GND": ["passive"],
                "AGND": ["power_in"], "DGND": [""], "PGND": [""]})
    for net in ("GND", "/Motor Power/GND", "AGND", "DGND", "PGND"):
        assert nets.classify_net(b, net) == "GROUND", net


def test_classify_power_by_pintype_even_on_auto_named_net():
    b = _board({"Net-(U1-Pad7)": ["power_in", "passive"]})
    assert nets.classify_net(b, "Net-(U1-Pad7)") == "POWER"


def test_classify_power_by_name():
    b = _board({"+5V_PWR": ["passive"], "+15V2": ["passive"], "-15V": ["passive"],
                "VCC": ["passive"], "VDD": ["passive"]})
    for net in ("+5V_PWR", "+15V2", "-15V", "VCC", "VDD"):
        assert nets.classify_net(b, net) == "POWER", net


def test_classify_sense():
    b = _board({"ADC_V1": ["input"], "FB": ["input"], "ISENSE": ["passive"],
                "/C2000 Feedback/VREF": ["passive"]})
    for net in ("ADC_V1", "FB", "ISENSE", "/C2000 Feedback/VREF"):
        assert nets.classify_net(b, net) == "SENSE", net


def test_classify_signal_default():
    b = _board({"SW": ["output"], "/Motor Power/SW": ["output"],
                "3PH_V": ["passive"], "Net-(R1-Pad2)": ["passive", "passive"]})
    for net in ("SW", "/Motor Power/SW", "3PH_V", "Net-(R1-Pad2)"):
        assert nets.classify_net(b, net) == "SIGNAL", net


def test_classify_nc():
    b = _board({"unconnected-(U302-NC-Pad7)": ["no_connect"],
                "DEAD": ["no_connect", "no_connect"]})
    assert nets.classify_net(b, "unconnected-(U302-NC-Pad7)") == "NC"
    assert nets.classify_net(b, "DEAD") == "NC"


def test_classify_empty_pintype_falls_back_to_name():
    # unsynced board: no pin types -> classify by name only
    b = _board({"GND": [""], "+5V": [""], "ADC_X": [""], "SOMESIG": [""]})
    assert nets.classify_net(b, "GND") == "GROUND"
    assert nets.classify_net(b, "+5V") == "POWER"
    assert nets.classify_net(b, "ADC_X") == "SENSE"
    assert nets.classify_net(b, "SOMESIG") == "SIGNAL"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_nets.py -q`
Expected: FAIL — `module 'autoplace.nets' has no attribute 'classify_net'`.

- [ ] **Step 3: Implement `classify_net`**

In `plugin/plugins/autoplace/nets.py`, change the file to:

```python
"""Net-name + net-role helpers (pure-Python, no pcbnew)."""
from __future__ import annotations

import re

# Net leaf names treated as ground references.
_GROUND_LEAVES = {"GND", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EARTH"}
# Explicit power-rail leaf names (beyond the +N / -N numeric pattern).
_POWER_LEAVES = {"VCC", "VDD", "VBAT", "VIN", "VOUT", "VBUS", "VMOT", "VDDA", "VCCA"}
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_nets.py -q`
Expected: PASS (original 2 + 7 new).

Run: `python -m pytest tests/ -q`
Expected: 77 passed.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/nets.py tests/test_nets.py
git commit -m "Add pure nets.classify_net (GROUND/POWER/SENSE/SIGNAL/NC)"
```

---

### Task 3: `kicad_io` extraction + real-board validation (E2 + E-VAL)

**Files:**
- Modify: `plugin/plugins/autoplace/kicad_io.py:28-72`

**Interfaces:**
- Consumes: `Component.value/fpid`, `Pad.pin_type/pin_function` (Task 1).
- Produces: a `build_model` that populates all four from `pcbnew`.

> `kicad_io` imports `pcbnew`, which is unavailable under plain `python`. Static gate: `python -c "import ast; ast.parse(open('plugin/plugins/autoplace/kicad_io.py').read())"` + the full plain-Python suite (which does not import `kicad_io`). Behavioral gate: run the real-board extraction under **KiCad 10's Python** (available — verified `pcbnew` 10.0.4).

- [ ] **Step 1: Add a defensive read helper + thread `fpid` through `_is_connector`**

In `plugin/plugins/autoplace/kicad_io.py`, change `_is_connector` (lines 28-33) from:

```python
def _is_connector(fp) -> bool:
    ref = fp.GetReference()
    if ref and ref[0] == "J":
        return True
    fpid = fp.GetFPIDAsString()
    return any(h in fpid for h in _CONNECTOR_HINTS)
```

to:

```python
def _safe(getter, default=""):
    """Call a pcbnew getter, returning ``default`` on any failure / None."""
    try:
        v = getter()
    except Exception:
        return default
    return v if v is not None else default


def _is_connector(fp, fpid: str) -> bool:
    ref = fp.GetReference()
    if ref and ref[0] == "J":
        return True
    return any(h in fpid for h in _CONNECTOR_HINTS)
```

- [ ] **Step 2: Populate the new fields in `build_model`**

In `plugin/plugins/autoplace/kicad_io.py`, change the footprint loop body in `build_model`
(lines 47-71) from:

```python
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        bb = fp.GetBoundingBox(False)               # geometry, no text
        cx, cy = _mm(bb.GetCenter().x), _mm(bb.GetCenter().y)
        try:
            sheet = fp.GetSheetname() or ""
        except Exception:
            sheet = ""
        comp = Component(
            ref=ref,
            w=_mm(bb.GetWidth()), h=_mm(bb.GetHeight()),
            x=cx, y=cy,
            locked=fp.IsLocked(),
            is_connector=_is_connector(fp),
            sheet=sheet,
        )
        for pad in fp.Pads():
            pp = pad.GetPosition()
            comp.pads.append(Pad(
                name=pad.GetNumber(),
                net=pad.GetNetname() or "",
                ox=_mm(pp.x) - cx,                   # offset from bbox centre
                oy=_mm(pp.y) - cy,
            ))
        board.components[ref] = comp
    return board
```

to:

```python
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        bb = fp.GetBoundingBox(False)               # geometry, no text
        cx, cy = _mm(bb.GetCenter().x), _mm(bb.GetCenter().y)
        fpid = _safe(fp.GetFPIDAsString)
        comp = Component(
            ref=ref,
            w=_mm(bb.GetWidth()), h=_mm(bb.GetHeight()),
            x=cx, y=cy,
            locked=fp.IsLocked(),
            is_connector=_is_connector(fp, fpid),
            sheet=_safe(fp.GetSheetname),
            value=_safe(fp.GetValue),
            fpid=fpid,
        )
        for pad in fp.Pads():
            pp = pad.GetPosition()
            comp.pads.append(Pad(
                name=pad.GetNumber(),
                net=pad.GetNetname() or "",
                ox=_mm(pp.x) - cx,                   # offset from bbox centre
                oy=_mm(pp.y) - cy,
                pin_type=_safe(pad.GetPinType),
                pin_function=_safe(pad.GetPinFunction),
            ))
        board.components[ref] = comp
    return board
```

(Note: the old `try/except` around `GetSheetname` is replaced by `_safe(fp.GetSheetname)`, preserving the same graceful behavior.)

- [ ] **Step 3: Static syntax gate + full plain-Python suite**

Run: `python -c "import ast; ast.parse(open('plugin/plugins/autoplace/kicad_io.py').read())"`
Expected: no output (valid syntax).

Run: `python -m pytest tests/ -q`
Expected: 77 passed (the engine suite does not import `kicad_io`; nothing changed for it).

- [ ] **Step 4: Real-board extraction validation under KiCad Python**

Write this throwaway check to the scratchpad (NOT committed) — `validate_extract.py`:

```python
import collections
import sys

sys.path.insert(0, "plugin/plugins")
from autoplace import kicad_io, nets

board, _ = kicad_io.load_board(sys.argv[1])
n = len(board.components)
with_value = sum(1 for c in board.components.values() if c.value)
with_fpid = sum(1 for c in board.components.values() if c.fpid)
power_pads = sum(1 for c in board.components.values()
                 for p in c.pads if "power_in" in p.pin_type or "power_out" in p.pin_type)
roles = collections.Counter(nets.classify_net(board, net) for net in board.nets())

print(f"components={n} value={with_value} fpid={with_fpid} power_pads={power_pads}")
print("classify_net distribution:", dict(roles))
# sanity assertions for a synced board
assert with_value >= 0.9 * n, f"value populated on only {with_value}/{n}"
assert with_fpid >= 0.9 * n, f"fpid populated on only {with_fpid}/{n}"
assert power_pads >= 1, "no power_in/out pads found"
assert roles.get("GROUND", 0) >= 1 and roles.get("POWER", 0) >= 1
print("OK: extraction + classification look sane")
```

Run it under KiCad 10's Python on the real `system` board:

```bash
"/c/Program Files/KiCad/10.0/bin/python.exe" <scratchpad>/validate_extract.py \
  "/c/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb"
```

Expected: prints `components=131 value=131 fpid=131 power_pads>=28`, a `classify_net distribution` with non-zero GROUND/POWER/SENSE/SIGNAL, and `OK: extraction + classification look sane`. Capture the printed distribution in the task report (it's the behavioral evidence that the pcbnew reads work end-to-end). If an assertion fails, STOP and report — do not weaken it.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/kicad_io.py
git commit -m "Populate value/fpid + pad pin_type/pin_function from pcbnew in build_model"
```

---

## Notes for the implementer

- The full plain-Python suite must read **77 passed** after Tasks 2 and 3 (70 baseline + 1 serialize assertion change carries no count delta + 7 classify_net tests). Task 1 keeps it at 70 (it only edits an existing test).
- Do NOT add a `cli.py` subcommand for classification — the validation is a throwaway scratchpad script (YAGNI; Phase 1 is four deliverables).
- Build order is deliberate: Task 1 (fields + serialize) → Task 2 (classifier, pure) → Task 3 (pcbnew reads, validated on the real board). Task 3 last because its validation exercises both the fields and the classifier.
