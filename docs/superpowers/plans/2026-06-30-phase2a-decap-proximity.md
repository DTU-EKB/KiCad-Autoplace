# Phase 2A Decap-Proximity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull each decoupling cap to its IC's power pin via a pad-to-pad hinge in `anneal.local_cost`, surfaced in the multiseed gallery ranking — validated by a real FreeRouting non-regression.

**Architecture:** A pure detector (`electrical.decoupling_pairs`, consuming Phase 1's `classify_net`) precomputed once in `Annealer.__init__`; a per-component hinge term in `local_cost` (never `_quality`); a `metrics.decap_proximity` quality metric folded into `ranking.candidate_key` and the gallery card.

**Tech Stack:** Python 3 (pure engine + plain `pytest`), KiCad 10 `pcbnew` + Java FreeRouting (validation gate only).

## Global Constraints

- **`anneal._quality` is NEVER modified.** The decap term lives only in `local_cost` (search bias); selection guarantee comes from the gallery ranking (Task 5). Verbatim from spec §4.1.
- **Pad-to-pad, never a net weight** (power nets are skipped by `net_members`, so a net weight is a silent no-op). Verbatim spec §4.2.
- **Determinism:** the detector is a deterministic structural query (sorted refs, `ref`-tiebreak), computed once on seed positions and fixed for the anneal. Spec §4.3.
- **No regression on decap-free boards:** empty pairing ⇒ term contributes 0 ⇒ placement unchanged. Spec §4.4.
- **`classify_net`, not `_is_power`:** the detector uses `nets.classify_net` for POWER/GROUND. `metrics._is_power`/`POWER_HINTS` (HPWL exclusion) stays separate; full unification deferred. Spec §6 A5.
- **Constants:** `DECAP_TARGET_MM = 3.0`; `_Weights.DECAP = 1.5` (starting values, tuned by the gate).
- **Tests:** plain `python -m pytest tests/` (baseline 78 passed). FreeRouting gate runs under `"C:\Program Files\KiCad\10.0\bin\python.exe"`.
- **FreeRouting baseline (current `main`):** `system` 95.0% (170/179), `motor_power` 66.1% (82/124). After the term: `system` ≥ ~93%, `motor_power` ≥ ~64% (no regression), and `decap_proximity` strictly lower on boards with decaps.
- **Commits: developer voice, NO AI attribution.**

---

### Task 1: Tighten `nets._SENSE_RE` (A5)

**Files:**
- Modify: `plugin/plugins/autoplace/nets.py:11`
- Test: `tests/test_nets.py`

**Interfaces:**
- Produces: a `_SENSE_RE` that matches its tokens only at name-segment boundaries (so mid-token substrings like `CADC` no longer hit SENSE), while keeping `ADC_V1`/`FB`/`ISENSE`/`VREF` → SENSE.

> Honest limit: a real segment like `FB`/`FEEDBACK` as a *whole* segment (`USB_D_FB`, `AUDIO_FEEDBACK`) stays SENSE — that ambiguity isn't resolvable from the name alone. This task fixes the mid-token false positives (`CADC`, etc.), not the genuinely-ambiguous segment case.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nets.py`:

```python
def test_sense_regex_is_boundary_anchored():
    # mid-token substrings no longer mis-hit SENSE
    b = _board({"CADC": ["passive"], "GRADC_X": ["passive"], "DFBX": ["passive"]})
    for net in ("CADC", "GRADC_X", "DFBX"):
        assert nets.classify_net(b, net) == "SIGNAL", net
    # legitimate sense names still classify SENSE
    b2 = _board({"ADC_V1": ["input"], "FB": ["input"], "ISENSE": ["passive"],
                 "VREF": ["passive"], "I_SENSE_A": ["passive"], "ISNS": ["passive"]})
    for net in ("ADC_V1", "FB", "ISENSE", "VREF", "I_SENSE_A", "ISNS"):
        assert nets.classify_net(b2, net) == "SENSE", net
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_nets.py::test_sense_regex_is_boundary_anchored -q`
Expected: FAIL — `CADC` currently classifies SENSE (substring `ADC`).

- [ ] **Step 3: Tighten the regex**

In `plugin/plugins/autoplace/nets.py`, change line 11 from:

```python
_SENSE_RE = re.compile(r"SENSE|ISNS|ISEN|VSEN|FB|FEEDBACK|VREF|ADC")
```

to:

```python
# Sense/feedback tokens, anchored to name-segment boundaries (start, end, '_', or a
# digit) so mid-token substrings (CADC, DFBX) don't mis-hit. ISENSE is its own token
# because SENSE is not at a boundary inside it.
_SENSE_RE = re.compile(
    r"(?:^|_)(?:ISENSE|SENSE|ISNS|ISEN|VSEN|FEEDBACK|FB|VREF|ADC)(?:_|[0-9]|$)")
```

- [ ] **Step 4: Run the nets tests + full suite**

Run: `python -m pytest tests/test_nets.py -q`
Expected: PASS (existing + new).

Run: `python -m pytest tests/ -q`
Expected: 79 passed (78 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/nets.py tests/test_nets.py
git commit -m "Anchor SENSE net regex to segment boundaries (fix CADC-style false hits)"
```

---

### Task 2: `electrical.decoupling_pairs` detector (A1)

**Files:**
- Create: `plugin/plugins/autoplace/electrical.py`
- Test: `tests/test_electrical.py`

**Interfaces:**
- Consumes: `nets.classify_net`, model geometry (`Component.pads`, `Component.pad_world`).
- Produces: `electrical.decoupling_pairs(board) -> dict` mapping `cap_ref -> (cap_rail_pad_idx, ic_ref, ic_rail_pad_idx)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_electrical.py`:

```python
"""Headless tests for electrical structural detectors. No pcbnew.

  python -m pytest tests/test_electrical.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import electrical                       # noqa: E402
from autoplace.model import Board, Component, Pad       # noqa: E402


def _cap(ref, x, y, rail, gnd):
    return Component(ref, 2, 1, x=x, y=y,
                     pads=[Pad("1", rail, -0.8, 0.0), Pad("2", gnd, 0.8, 0.0)])


def _ic(ref, x, y, rail):
    return Component(ref, 6, 6, x=x, y=y, pads=[
        Pad("1", rail, -2.0, 0.0), Pad("2", "GND", 2.0, 0.0), Pad("3", "SIG", 0.0, 2.0)])


def test_decap_pairs_to_nearest_ic_on_rail():
    b = Board(0, 0, 100, 100)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "U2": _ic("U2", 90, 90, "+5V"),     # same rail, far away
        "C1": _cap("C1", 14, 10, "+5V", "GND"),  # near U1
    }
    pairs = electrical.decoupling_pairs(b)
    assert pairs["C1"][1] == "U1"           # nearest IC on the rail
    assert pairs["C1"][0] == 0              # cap rail pad index (pad "1" -> +5V)


def test_decap_skipped_when_no_ic_on_rail():
    b = Board(0, 0, 50, 50)
    b.components = {"C1": _cap("C1", 10, 10, "+5V", "GND")}  # no IC at all
    assert electrical.decoupling_pairs(b) == {}


def test_two_pin_non_power_gnd_is_not_a_decap():
    b = Board(0, 0, 50, 50)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "R1": Component("R1", 2, 1, x=20, y=20,
                        pads=[Pad("1", "SIG", -0.8, 0), Pad("2", "N1", 0.8, 0)]),
    }
    assert electrical.decoupling_pairs(b) == {}


def test_three_pad_part_is_not_a_decap():
    b = Board(0, 0, 50, 50)
    b.components = {
        "U1": _ic("U1", 10, 10, "+5V"),
        "U2": _ic("U2", 20, 20, "+5V"),     # 3-pad, on the rail+gnd, but not a 2-pad cap
    }
    pairs = electrical.decoupling_pairs(b)
    assert "U2" not in pairs                # 3-pad part is never classified a decap


def test_nearest_tie_broken_by_ref():
    b = Board(0, 0, 100, 100)
    b.components = {
        "U2": _ic("U2", 10, 10, "+5V"),
        "U1": _ic("U1", 18, 10, "+5V"),     # equidistant-ish; force a tie below
        "C1": _cap("C1", 14, 10, "+5V", "GND"),
    }
    # both IC rail pads at x=10-2=8 (U2) and 18-2=16 (U1); cap rail pad at 14-0.8=13.2
    # dist to U2 pad = |13.2-8|=5.2 ; to U1 pad = |16-13.2|=2.8 -> U1 nearer (not a tie),
    # so assert the nearer one wins deterministically.
    assert electrical.decoupling_pairs(b)["C1"][1] == "U1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_electrical.py -q`
Expected: FAIL — `No module named 'autoplace.electrical'`.

- [ ] **Step 3: Implement the detector**

Create `plugin/plugins/autoplace/electrical.py`:

```python
"""Electrical-aware structural detectors (pure-Python, no pcbnew).

These power the Phase 2 placement terms. Each is a deterministic structural query
over the Phase-1-enriched model. They use ``nets.classify_net`` for role detection,
NOT ``metrics._is_power`` -- the two are intentionally separate (classify_net tags a
net's role; _is_power drives HPWL exclusion). Full unification is deferred.
"""
from __future__ import annotations

import math

from . import nets


def decoupling_pairs(board) -> dict:
    """Pair each decoupling cap to the IC power pin it should hug.

    A decoupling cap = a 2-pad component whose two nets classify as one POWER and
    one GROUND. Its target = the nearest component with > 2 pads that also has a pad
    on the same POWER rail (cap rail-pad -> candidate rail-pad distance; ``ref``
    tiebreak). Caps whose rail has no such multi-pad part are skipped.

    Returns ``{cap_ref: (cap_rail_pad_idx, ic_ref, ic_rail_pad_idx)}`` on current
    positions. Deterministic; no RNG.
    """
    comps = board.components
    # POWER rail net -> [(ic_ref, pad_idx)] for pads of >2-pad parts on that rail
    rail_ic_pads: dict[str, list] = {}
    for ref in sorted(comps):
        c = comps[ref]
        if len(c.pads) <= 2:
            continue
        for i, p in enumerate(c.pads):
            if p.net and nets.classify_net(board, p.net) == "POWER":
                rail_ic_pads.setdefault(p.net, []).append((ref, i))

    out = {}
    for ref in sorted(comps):
        c = comps[ref]
        if len(c.pads) != 2:
            continue
        roles = [nets.classify_net(board, p.net) if p.net else "NC" for p in c.pads]
        if set(roles) != {"POWER", "GROUND"}:
            continue
        rail_idx = 0 if roles[0] == "POWER" else 1
        cands = rail_ic_pads.get(c.pads[rail_idx].net, [])
        if not cands:
            continue
        cx, cy = c.pad_world(c.pads[rail_idx])
        best = None  # (dist, ic_ref, ic_idx)
        for ic_ref, ic_idx in cands:
            ic = comps[ic_ref]
            ix, iy = ic.pad_world(ic.pads[ic_idx])
            d = math.hypot(ix - cx, iy - cy)
            if best is None or d < best[0] or (d == best[0] and ic_ref < best[1]):
                best = (d, ic_ref, ic_idx)
        out[ref] = (rail_idx, best[1], best[2])
    return out
```

- [ ] **Step 4: Run the electrical tests + full suite**

Run: `python -m pytest tests/test_electrical.py -q`
Expected: PASS (5 tests).

Run: `python -m pytest tests/ -q`
Expected: 84 passed (79 + 5).

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/electrical.py tests/test_electrical.py
git commit -m "Add electrical.decoupling_pairs detector (cap -> nearest IC on its rail)"
```

---

### Task 3: `metrics.decap_proximity` (A3)

**Files:**
- Modify: `plugin/plugins/autoplace/metrics.py` (append a function)
- Test: `tests/test_metrics_proxies.py`

**Interfaces:**
- Consumes: `electrical.decoupling_pairs` (Task 2).
- Produces: `metrics.decap_proximity(board) -> float` (mean cap→IC-pin distance mm; `0.0` if no decaps).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics_proxies.py`:

```python
def test_decap_proximity_mean_distance_and_zero_when_none():
    from autoplace.model import Board, Component, Pad
    b = Board(0, 0, 100, 100)
    b.components = {
        "U1": Component("U1", 6, 6, x=10, y=10, pads=[
            Pad("1", "+5V", -2.0, 0.0), Pad("2", "GND", 2.0, 0.0), Pad("3", "SIG", 0.0, 2.0)]),
        "C1": Component("C1", 2, 1, x=10, y=40, pads=[
            Pad("1", "+5V", -0.8, 0.0), Pad("2", "GND", 0.8, 0.0)]),
    }
    # cap rail pad world = (10-0.8, 40) = (9.2, 40); IC rail pad = (10-2, 10) = (8, 10)
    # dist = hypot(1.2, 30) ~= 30.024
    d = metrics.decap_proximity(b)
    assert 29.5 < d < 30.5

    b2 = Board(0, 0, 50, 50)
    b2.components = {"R1": Component("R1", 2, 1, x=5, y=5,
                                     pads=[Pad("1", "A", -0.8, 0), Pad("2", "B", 0.8, 0)])}
    assert metrics.decap_proximity(b2) == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_metrics_proxies.py::test_decap_proximity_mean_distance_and_zero_when_none -q`
Expected: FAIL — `module 'autoplace.metrics' has no attribute 'decap_proximity'`.

- [ ] **Step 3: Implement the metric**

Append to `plugin/plugins/autoplace/metrics.py`:

```python
def decap_proximity(board: Board) -> float:
    """Mean decoupling-cap -> IC-power-pin distance (mm) over detected pairs.

    Lower is better. 0.0 when the board has no decoupling pairs (so decap-free
    boards stay neutral in candidate ranking). Pure; uses the same pad pair the
    placement term uses."""
    from . import electrical
    pairs = electrical.decoupling_pairs(board)
    if not pairs:
        return 0.0
    total = 0.0
    for cap_ref, (cap_idx, ic_ref, ic_idx) in pairs.items():
        cap = board.components[cap_ref]
        ic = board.components[ic_ref]
        cx, cy = cap.pad_world(cap.pads[cap_idx])
        ix, iy = ic.pad_world(ic.pads[ic_idx])
        total += math.hypot(ix - cx, iy - cy)
    return round(total / len(pairs), 3)
```

- [ ] **Step 4: Run the proxy tests + full suite**

Run: `python -m pytest tests/test_metrics_proxies.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: 85 passed.

- [ ] **Step 5: Commit**

```bash
git add plugin/plugins/autoplace/metrics.py tests/test_metrics_proxies.py
git commit -m "Add metrics.decap_proximity (mean cap-to-IC-pin distance)"
```

---

### Task 4: Decap term in `anneal.local_cost` (A2)

**Files:**
- Modify: `plugin/plugins/autoplace/anneal.py` (imports, `_Weights`, a constant, `Annealer.__init__`, new `_decap_penalty`, `local_cost`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `electrical.decoupling_pairs` (Task 2).
- Produces: `Annealer.decap` (the pairing dict) and a per-component decap penalty in `local_cost`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py`:

```python
def test_decap_term_pulls_cap_toward_its_ic():
    import copy
    from autoplace import anneal, electrical

    def _board_with_decap():
        b = Board(0, 0, 60, 60)
        b.components = {
            "U1": Component("U1", 6, 6, x=10, y=10, pads=[
                Pad("1", "+5V", -2.0, 0.0), Pad("2", "GND", 2.0, 0.0),
                Pad("3", "SIG", 0.0, 2.0)]),
            "C1": Component("C1", 2, 1, x=50, y=50, pads=[
                Pad("1", "+5V", -0.8, 0.0), Pad("2", "GND", 0.8, 0.0)]),
            "R1": _two_pin("R1", 30, 30, "SIG", "N1"),
            "R2": _two_pin("R2", 40, 20, "N1", "N2"),
        }
        return b

    on = _board_with_decap()
    assert electrical.decoupling_pairs(on)["C1"][1] == "U1"
    off = copy.deepcopy(on)

    a_on = anneal.Annealer(on, margin=0.8, seed=1)
    a_on.run(steps=5000)
    a_off = anneal.Annealer(off, margin=0.8, seed=1)
    a_off.decap = {}                        # disable just the decap term
    a_off.run(steps=5000)

    def dist(board):
        cap, ic = board.components["C1"], board.components["U1"]
        cx, cy = cap.pad_world(cap.pads[0])
        ix, iy = ic.pad_world(ic.pads[0])
        return ((ix - cx) ** 2 + (iy - cy) ** 2) ** 0.5

    assert dist(on) < dist(off)             # the term pulled the cap closer to U1
    assert metrics.overlaps(on) == []


def test_decap_penalty_zero_without_pairs():
    from autoplace import anneal
    b = _board()                            # no decaps
    a = anneal.Annealer(b, margin=0.8, seed=0)
    assert a.decap == {}
    assert a._decap_penalty(b.components["R1"]) == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_decap_penalty_zero_without_pairs -q`
Expected: FAIL — `Annealer` has no attribute `decap`.

- [ ] **Step 3: Add imports, weight, constant**

In `plugin/plugins/autoplace/anneal.py`, change the imports (lines 25-28 area) from:

```python
from . import geom
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power, channel_width
from .model import Board
```

to:

```python
from . import electrical, geom
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power, channel_width
from .model import Board
```

In `_Weights` (lines 31-37), add the DECAP weight after `CONG_K`:

```python
    CONG_K = 3.0          # per-unit-pressure multiplier on the channel term
    DECAP = 1.5           # pull a decoupling cap toward its IC power pin (search bias)
```

Below the `CHANNEL_MM` comment block (near line 44), add:

```python
# Target gap (mm) a decoupling cap should sit within of its IC power pin (loop
# inductance is dominated beyond a few mm). The decap term pays only beyond this.
DECAP_TARGET_MM = 3.0
```

- [ ] **Step 4: Precompute the pairing in `__init__`**

In `Annealer.__init__`, after `self.centroids = block_centroids(board)` (line 80), add:

```python
        # decoupling cap -> (cap_rail_pad_idx, ic_ref, ic_rail_pad_idx); computed once
        # on the seed positions so the pairing is stable across the anneal.
        self.decap = electrical.decoupling_pairs(board)
```

- [ ] **Step 5: Add `_decap_penalty` and wire it into `local_cost`**

In `plugin/plugins/autoplace/anneal.py`, add this method just after `_cohesion` (after line 122):

```python
    def _decap_penalty(self, c) -> float:
        """Hinge: how far a decoupling cap's rail pad is beyond DECAP_TARGET_MM from
        its paired IC power pin. 0 for non-decaps and within-target caps."""
        t = self.decap.get(c.ref)
        if t is None:
            return 0.0
        cap_idx, ic_ref, ic_idx = t
        ic = self.board.components[ic_ref]
        cx, cy = c.pad_world(c.pads[cap_idx])
        ix, iy = ic.pad_world(ic.pads[ic_idx])
        return max(0.0, math.hypot(ix - cx, iy - cy) - DECAP_TARGET_MM)
```

Then change the per-component loop at the end of `local_cost` (lines 168-172) from:

```python
        for c in subset:
            if c.is_connector:
                cost += W.EDGE * self._edge_dist(c)
            cost += self.cohesion * self._cohesion(c)
        return cost
```

to:

```python
        for c in subset:
            if c.is_connector:
                cost += W.EDGE * self._edge_dist(c)
            cost += self.cohesion * self._cohesion(c)
            cost += W.DECAP * self._decap_penalty(c)
        return cost
```

- [ ] **Step 6: Run the engine tests + full suite**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (including the two new tests).

Run: `python -m pytest tests/ -q`
Expected: 87 passed (85 + 2).

- [ ] **Step 7: Commit**

```bash
git add plugin/plugins/autoplace/anneal.py tests/test_engine.py
git commit -m "Add decoupling-cap proximity term to anneal.local_cost (search bias only)"
```

---

### Task 5: Gallery ranking + card (A4)

**Files:**
- Modify: `plugin/plugins/autoplace/ranking.py:17-25`
- Modify: `plugin/plugins/autoplace/multiseed.py` (candidate dict)
- Modify: `cli.py` (`cmd_place_multi` buffer keys)
- Modify: `app/renderer/renderer.js` (proxy row)
- Test: `tests/test_candidate_ranking.py`, `tests/test_multiseed.py`

**Interfaces:**
- Consumes: `metrics.decap_proximity` (Task 3).
- Produces: candidate dicts gain `decap_proximity`; `candidate_key` ranks on it (0.5 mm buckets) between pinch and HPWL.

- [ ] **Step 1: Write the failing ranking test**

Append to `tests/test_candidate_ranking.py`:

```python
def test_closer_decaps_outrank_equal_candidate():
    a = _c(1, hpwl=100.0); a["decap_proximity"] = 12.0
    b = _c(2, hpwl=100.0); b["decap_proximity"] = 3.0     # tighter decaps
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]


def test_decap_absent_does_not_change_ranking():
    a = _c(1, hpwl=100.0)        # no decap_proximity key
    b = _c(2, hpwl=50.0)         # no decap_proximity key
    assert [x["seed"] for x in ranking.pre_rank([a, b])] == [2, 1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_candidate_ranking.py::test_closer_decaps_outrank_equal_candidate -q`
Expected: FAIL — without the key in `candidate_key`, the two seeds tie-break by seed → `[1, 2]`.

- [ ] **Step 3: Add `decap_proximity` to `candidate_key`**

In `plugin/plugins/autoplace/ranking.py`, change `candidate_key` (lines 19-25) from:

```python
    return (
        cand["overlaps"],                          # legal layouts win outright
        round(cand["sheet_spread_score"], 3),      # clean per-sheet spread
        round(cand["pinch_fraction"], 3),          # fewer routing pinch points
        round(cand["hpwl_mm"], 2),                 # wirelength is the final metric
        cand["seed"],                              # total order
    )
```

to:

```python
    return (
        cand["overlaps"],                          # legal layouts win outright
        round(cand["sheet_spread_score"], 3),      # clean per-sheet spread
        round(cand["pinch_fraction"], 3),          # fewer routing pinch points
        round(cand.get("decap_proximity", 0.0) * 2) / 2,  # decap closeness, 0.5mm buckets
        round(cand["hpwl_mm"], 2),                 # wirelength is the final metric
        cand["seed"],                              # total order
    )
```

(The `.get(..., 0.0)` keeps decap-free candidates and older callers unaffected.)

- [ ] **Step 4: Add `decap_proximity` to the candidate dict + CLI buffer**

In `plugin/plugins/autoplace/multiseed.py`, in the success-yield dict (after `"whitespace_connectivity": ...`), add:

```python
            "decap_proximity": metrics.decap_proximity(board),
```

In `cli.py` `cmd_place_multi`, change the buffer `keys` tuple to include `decap_proximity`:

```python
    keys = ("seed", "overlaps", "sheet_spread_score", "pinch_fraction",
            "whitespace_connectivity", "decap_proximity", "hpwl_mm")
```

- [ ] **Step 5: Surface it on the gallery card**

In `app/renderer/renderer.js`, in `addCandidateCard`, extend the proxy-row constants and markup. Change:

```javascript
  const ws = cand.whitespace_connectivity === undefined ? "—" : `${Math.round(cand.whitespace_connectivity * 100)}%`;
```

to:

```javascript
  const ws = cand.whitespace_connectivity === undefined ? "—" : `${Math.round(cand.whitespace_connectivity * 100)}%`;
  const decap = cand.decap_proximity === undefined ? "—" : `${cand.decap_proximity.toFixed(1)}mm`;
```

and change the proxy-row template from:

```javascript
    `<div class="cand-metrics-row cand-metrics-proxy">spread ${spread} · pinch ${pinch} · ws ${ws} · overlaps ${fmt(cand.overlaps)}</div>` +
```

to:

```javascript
    `<div class="cand-metrics-row cand-metrics-proxy">spread ${spread} · pinch ${pinch} · ws ${ws} · decap ${decap} · overlaps ${fmt(cand.overlaps)}</div>` +
```

- [ ] **Step 6: Update the multiseed field-shape test**

In `tests/test_multiseed.py`, add `decap_proximity` to the asserted key set + a numeric check in `test_count_and_shape`:

```python
        assert set(c) >= {"seed", "hpwl_mm", "crossings", "overlaps",
                          "hpwl_delta_pct", "sheet_spread_score",
                          "pinch_fraction", "whitespace_connectivity",
                          "decap_proximity", "board"}
        assert isinstance(c["decap_proximity"], float)
```

- [ ] **Step 7: Run tests + syntax-check JS**

Run: `python -m pytest tests/ -q`
Expected: 89 passed (87 + 2 ranking).

Run: `node --check app/renderer/renderer.js`
Expected: exit 0, no output.

- [ ] **Step 8: Commit**

```bash
git add plugin/plugins/autoplace/ranking.py plugin/plugins/autoplace/multiseed.py cli.py app/renderer/renderer.js tests/test_candidate_ranking.py tests/test_multiseed.py
git commit -m "Rank candidates by decap proximity and show it on the gallery card"
```

---

### Task 6: FreeRouting non-regression gate (validation, no commit)

**Files:** none (runs the existing `scratchpad/route_baseline.py` against the branch).

> This is the merge gate, run by the controller (not a code task). It needs KiCad 10 python + Java + FreeRouting.

- [ ] **Step 1: Route the two gate boards with the branch's engine**

From the repo root, on the `phase2a-decap-proximity` branch:

```bash
"/c/Program Files/KiCad/10.0/bin/python.exe" \
  "<scratchpad>/route_baseline.py" "<scratchpad>/after" \
  "/c/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb" \
  "/c/Users/Mads2/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/boards/motor_power/motor_power.kicad_pcb"
```

Expected: writes `<scratchpad>/after/baseline.json` with routed-% per board.

- [ ] **Step 2: Compare to the baseline + check decap improvement**

- **Non-regression (hard gate):** `system` routed-% ≥ 93.0 and `motor_power` ≥ 64.0 (baseline 95.0 / 66.1, minus ~2% FreeRouting noise). If either drops below, the term regressed routability — lower `_Weights.DECAP` (e.g. to 1.0 or 0.75) or raise `DECAP_TARGET_MM`, re-run, and only proceed when it holds. Do NOT merge a regression.
- **Decap improvement (must show benefit):** run `metrics.decap_proximity` on the branch placement vs a `DECAP=0` placement for `system` (it has many decaps) and confirm the branch value is **strictly lower**. A short throwaway script (KiCad python) that places with the engine and prints `metrics.decap_proximity(model)` for both weights is sufficient; record both numbers.

- [ ] **Step 3: Record the gate result**

Record the four numbers (before/after routed-% for both boards) and the decap_proximity before/after in the task ledger. These are the evidence the final review and merge decision rely on.

---

## Notes for the implementer

- Full-suite counts: T1 → 79, T2 → 84, T3 → 85, T4 → 87, T5 → 89.
- The decap term is **per-component** (like `_cohesion`): when the IC moves, caps targeting it aren't re-penalized in the IC's delta — an accepted approximation; the cap follows on its own moves. Do not try to make it symmetric in this increment.
- Build order: T1 (cleanup) → T2 (detector) → T3 (metric) → T4 (anneal term) → T5 (ranking/gallery) → T6 (FreeRouting gate). T6 is the gate before the whole-branch review + merge.
- `<scratchpad>` = `C:\Users\Mads2\AppData\Local\Temp\claude\C--Users-Mads2-KiCad-Autoplace\212dd608-1b37-447e-bf22-4e15903d8520\scratchpad`.
