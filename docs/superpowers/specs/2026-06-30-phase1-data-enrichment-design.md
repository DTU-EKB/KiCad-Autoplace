# Phase 1 — Data-layer enrichment (the electrical-intent enabler)

**Date:** 2026-06-30
**Status:** Design approved; ready for implementation plan.
**Roadmap context:** Phase 1 of the placement-quality roadmap (after Phase 0 ranking/proxies,
merged to `main`). This phase adds **electrical intent** to the pure-Python model so later
phases can reason about decoupling, power topology, sense vs. switch nets, etc. It changes
**no placement behavior** — it is purely additive data plus one pure classifier.

---

## 1. Goal

Make the data a senior engineer reasons about *visible to the engine*: component value,
footprint class, pad electrical type, and a net-role classification. Today the model carries
only geometry + nets; the engine literally cannot tell a bypass cap from a bulk electrolytic,
a power pin from a signal pin, or a feedback net from a switch node. Phase 1 closes that gap
as the enabler for Phase 2+ electrical-aware terms.

## 2. Grounding (probed on the real `system` board, KiCad 10)

Verified by running `pcbnew` on
`…/DTU/4. Semester/Electrical Energy Systems/team/hardware/kicad/system/system.kicad_pcb`
(131 footprints, 370 pads):

- **Pin types are populated and reliable** (364/370 non-empty): `passive` 260, `power_in` 28,
  `no_connect` 28, `input` 28, `output` 9, `output+no_connect` 4, `passive+no_connect` 5,
  `power_out` 1, `open_collector` 1, empty 6. `power_in`/`power_out` cleanly mark power pins.
- **`fp.GetValue()` rich**: `100n`, `4700u/50V`, `1N4148`, `IL300`, `20k`, …
- **`fp.GetFPIDAsString()` rich**: `Capacitor_THT:C_Disc…`, `Diode_THT:D_DO-35…`,
  `Package_DIP:DIP-6…`, `Connector_PinHeader…`.
- **Net names**: semantic (`GND`, `+15V2`, `+5V_PWR`, `SW`, `/Motor Power/SW`, `ADC_V1`,
  `3PH_V`) *and* auto-generated (`Net-(R204-Pad2)`, `unconnected-(U302-NC-Pad7)`). Auto-named
  nets have no semantic name, so pin-type is their only role signal.
- **Gotcha:** `net.GetNetClass()` raises `AttributeError` on KiCad 10's `NETINFO_ITEM`. Net-class
  / clearance extraction needs a different API (`pad.GetEffectiveNetClass()`) and is **deferred
  to Phase 3** (it's a creepage signal, not needed here).

**Implication:** pin-type + name are both strong signals; the classifier leans on them. Empty
pin types (unsynced boards) fall back to name regex gracefully.

## 3. Scope — four deliverables

| # | Deliverable |
|---|---|
| **E1** | New pure model fields: `Component.value`, `Component.fpid`, `Pad.pin_type`, `Pad.pin_function` (all default `""`). |
| **E2** | `kicad_io.build_model` populates them from `pcbnew` (defensive). |
| **E3** | `nets.classify_net(board, net) -> {GROUND, POWER, SENSE, SIGNAL, NC}` — pure, deterministic. |
| **E4** | `serialize.board_to_dict` exposes the new fields (additive). |

### Out of scope (later phases)
- Any cost term, detector, or placement change that *consumes* the new data — **Phase 2+**.
- Net-class / clearance / voltage-domain extraction (`GetEffectiveNetClass`) — **Phase 3** (creepage).
- Aggressor/victim sub-classification (SW/GATE vs FB/SENSE as a separable axis) — **Phase 3**.
- Diff-pair / Kelvin / thermal-pad detection — later/deferred.

---

## 4. Guiding invariants

1. **Purely additive — zero placement behavior change.** New fields default to `""`; nothing
   in the engine consumes `classify_net` yet. The existing 70-test suite must pass unchanged,
   and placement output on every board is byte-identical to current `main`.
2. **Engine stays `pcbnew`-free.** `classify_net` is a pure function over the model (`nets.py`).
   Only `kicad_io.py` reads `pcbnew` (E2).
3. **`anneal._quality` is never touched.**
4. **Determinism.** `classify_net` is a pure, deterministic function of model data.
5. **Graceful degradation.** Missing pin types (unsynced board) or `pcbnew` API differences must
   degrade to `""` / name-based classification, never crash `build_model`.

---

## 5. Current state (verified against `main`)

- `model.Pad` (`model.py:17-22`): `name, net, ox, oy`. `model.Component` (`model.py:25-38`):
  `ref, w, h, pads, x, y, rot, locked, is_connector, sheet, block, edge`.
- `kicad_io.build_model` (`kicad_io.py`) builds these; `_is_connector` already calls
  `fp.GetFPIDAsString()` (`kicad_io.py:32`) and discards it.
- `nets.is_gnd_name(name)` (`nets.py`): leaf-segment match for the ground net (`/GND`,
  `/Power/GND`); deliberately does **not** match `AGND`/`DGND`.
- `metrics.POWER_HINTS` (`metrics.py:17-18`): substring hints used by `_is_power`.
- `serialize.board_to_dict` (`serialize.py:11-30`): emits per-footprint `ref/x/y/w/h/rot/block/
  sheet/edge/is_connector_guess/locked` + per-pad `net/ox/oy`.

---

## 6. Design

### E1 — Model fields (`model.py`)

Add to `Pad`:
```python
pin_type: str = ""       # schematic electrical type: power_in/power_out/input/output/passive/no_connect/... ("" = unknown)
pin_function: str = ""    # schematic pin name: VDD, SW, GATE, ... ("" = none)
```
Add to `Component`:
```python
value: str = ""           # fp.GetValue(): "100n", "4700u/50V", "1N4148", ...
fpid: str = ""            # fp.GetFPIDAsString(): "Capacitor_THT:C_Disc...", footprint class
```
All have defaults, so positional construction and existing tests are unaffected. `Pad` gains
two trailing fields (it's a dataclass with `name, net, ox, oy` — append after `oy`).

### E2 — `kicad_io.build_model` reads (`kicad_io.py`)

When building each `Component`, set `value = fp.GetValue()` and `fpid = fp.GetFPIDAsString()`
(reuse the value already fetched for `_is_connector` rather than calling twice). When building
each `Pad`, set `pin_type` and `pin_function` from `pad.GetPinType()` / `pad.GetPinFunction()`.

Defensive: wrap each new read so a missing method or odd object yields `""`:
```python
def _safe(getter, default=""):
    try:
        v = getter()
        return v if v is not None else default
    except Exception:
        return default
```
(or `getattr(pad, "GetPinType", lambda: "")()`). A board that errors on these reads still
produces a valid model with empty fields — invariant #5.

### E3 — `nets.classify_net(board, net) -> str` (`nets.py`)

Returns one of `"GROUND" | "POWER" | "SENSE" | "SIGNAL" | "NC"`. First match wins, in this order:

1. **NC** — net name starts with `unconnected-`, **or** the net has members and *every* member
   pad has `pin_type == "no_connect"`.
2. **GROUND** — `is_gnd_name(net)` **or** the net's leaf segment (last `/`-part, uppercased) is in
   `{GND, AGND, DGND, PGND, GNDA, GNDD, EARTH}`.
3. **POWER** — any member pad has `pin_type in {"power_in", "power_out"}` (substring test, since
   KiCad concatenates like `passive+no_connect`), **or** the leaf name matches the power pattern:
   `^[+-]\d` (e.g. `+15V2`, `+5V_PWR`, `-15V`) **or** leaf in
   `{VCC, VDD, VBAT, VIN, VOUT, VBUS, VMOT, VDDA, VCCA}` **or** contains `VCC`/`VDD`.
4. **SENSE** — leaf matches `(SENSE|ISNS|ISEN|VSEN|FB|FEEDBACK|VREF|ADC)` (whole-word-ish; keep
   conservative to avoid false hits).
5. **SIGNAL** — everything else (auto-named `Net-(...)`, `SW`, `GATE`, `3PH_V`, `PWM_MOTOR`, …).

Signature operates on the enriched model: `members = board.nets().get(net, [])`, look up each
`board.components[ref].pads[idx].pin_type`. Pure, no `pcbnew`, no RNG. The regexes/sets are
documented as **tunable heuristics** — validated by eyeballing `classify_net` output on the real
boards (E-VAL below), not claimed as exhaustive.

Notes on judgment calls (documented in the docstring):
- `VSS` is treated as **POWER** (a rail), not GROUND — only the explicit ground names above are
  GROUND. Tunable.
- `SW`/`GATE` (switch-node/aggressor) classify as **SIGNAL** in this 5-way taxonomy; the
  aggressor-vs-victim axis is a separate Phase 3 concern.

### E4 — `serialize.board_to_dict` (`serialize.py`)

Add `value` and `fpid` to each footprint dict, and `pin_type` / `pin_function` to each pad dict.
Additive keys; existing consumers (the app canvas) ignore unknown keys.

---

## 7. Testing & validation

**Pure unit tests (plain `python -m pytest`):**
- `tests/test_nets.py` (extend): `classify_net` over synthetic `Board`s exercising each branch —
  NC (`unconnected-…` / all-no_connect pads), GROUND (`GND`, `/Motor Power/GND`, `AGND`), POWER by
  pin-type (a `power_in` pad on an auto-named net → POWER), POWER by name (`+5V_PWR`, `+15V2`),
  SENSE (`ADC_V1`, `FB`, `ISENSE`), SIGNAL (`SW`, `Net-(R1-Pad2)`), and the empty-pin-type
  fallback (no pin types → name-only classification).
- `tests/test_serialize.py` (extend): the new keys appear with correct values.
- `tests/test_model` coverage via existing suites: defaults `""` don't perturb anything.

**Real-extraction validation (KiCad 10 python — now runnable):**
- A **throwaway validation script** (in scratchpad, NOT committed — no new CLI subcommand; keep
  Phase 1 to the four deliverables) run under `"C:\Program Files\KiCad\10.0\bin\python.exe"` on
  `system.kicad_pcb` (and 1–2 others) that asserts: `Component.value`/`fpid` populate for ≥90% of
  parts; ≥1 pad has `pin_type` containing `power_in`; and prints the `classify_net` distribution for
  human sanity-check (expect `GND`→GROUND, `+15V2`/`+5V_PWR`→POWER, `ADC_V1`→SENSE, auto-named→
  SIGNAL). This is the manual gate that the `kicad_io` reads work end-to-end (not just unit-mocked).
  It informs heuristic tuning but is not part of the committed test suite.

**Non-regression gate:** the full plain-Python suite stays green and placement output is unchanged
(invariant #1). No FreeRouting run needed for Phase 1 (no behavior change).

---

## 8. Risks

- **R1 — Unsynced boards (empty pin types).** Classifier must fall back to name regex; covered by
  the empty-pin-type test. The probe shows synced boards give reliable pin types.
- **R2 — KiCad version API drift** (`GetPinType`/`GetPinFunction`/`GetValue` signature). Defensive
  `_safe`/`getattr` wrapping; degrade to `""`.
- **R3 — Heuristic misclassification.** `classify_net` is explicitly heuristic; the real-board
  distribution check surfaces gross errors. Since nothing consumes it yet, a wrong tag has zero
  placement effect in Phase 1 — it only matters once Phase 2 detectors read it, at which point the
  rules can be tuned against measured results.
- **R4 — `Pad` field order.** Appending fields to the `Pad` dataclass must keep existing positional
  constructions (`Pad("1", net, ox, oy)`) valid — the two new fields have defaults and come last.

---

## 9. Build order

E1 (model fields) → E3 (`classify_net` + tests, pure) → E4 (serialize + test) → E2 (`kicad_io`
reads, pcbnew) → E-VAL (real-board extraction + distribution check under KiCad python). E2/E-VAL
last because they need the model fields and the classifier in place to validate against.
