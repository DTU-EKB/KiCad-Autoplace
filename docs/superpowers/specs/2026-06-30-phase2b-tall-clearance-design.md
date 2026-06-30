# Phase 2B — Tall-part clearance halo (THT DFM)

**Date:** 2026-06-30
**Status:** Design approved (data-grounded reprioritization); ready for implementation plan.
**Roadmap context:** Second Phase 2 increment. Crystal hug was **dropped** (zero crystals in
the corpus). Per the data, the highest-value remaining term for these THT power boards is
keeping small parts clear of tall parts (TO-220s, electrolytics, toroids, terminal blocks,
vertical headers, trimpots) so they can be hand-soldered, inspected, and reworked. Shipped as
one term, FreeRouting-gated, like the decap term.

---

## 1. Goal

Give tall through-hole parts extra clearance to their neighbors so a soldering iron / rework
tool fits and small parts aren't buried under a tall part's overhang — a real manufacturability
win on this THT corpus (TO-220 ×13, CP_Radial electrolytics ×29, toroids ×5, terminal blocks
×41, vertical pin headers ×35, trimpots ×4 across the 12 boards).

## 2. Design summary (mirrors the D4 gutter, height-driven instead of block-driven)

A tall part widens the **existing** routing-channel target in `anneal._pair_penalty` by a halo,
scaled by the existing `channel_scale` (so dense boards — where `channel_scale → 0` — are
automatically protected from over-spreading). No new cost-term structure, no `_quality` change,
no hard legalize constraint. Just: tall part involved ⇒ neighbors target a wider gap.

## 3. Scope — three pieces

| # | Piece |
|---|---|
| **B1** | `footprints.height_mm(fpid) -> float` (pure table) + `Component.height` field, populated in `kicad_io.build_model`. |
| **B2** | Height-driven halo in `anneal._pair_penalty`: when either part is tall, add `TALL_HALO_MM * channel_scale` to the channel target. |
| **B3** | `metrics.tall_clearance(board) -> float` validation metric (mean shortfall of tall-part neighbor gaps below the halo; lower better). |

### Out of scope (deferred / dropped)
- **Variable hard per-pair clearance in `legalize.push_apart`** (`max(a.clearance,b.clearance)`):
  riskier (courtyards already encode size; could over-space or make `motor_power`
  un-legalizable). Deferred — the soft channel halo gets most of the benefit at lower risk.
- **Crystal hug** — dropped (no crystals in corpus).
- Gallery `candidate_key` is NOT extended (it already has overlaps/spread/pinch/decap/hpwl;
  adding more dilutes auditability — the audit's warning). `tall_clearance` is a gate metric only.

---

## 4. Guiding invariants

1. **`anneal._quality` is never modified.** The halo is part of `_pair_penalty` (search-bias
   `local_cost`), exactly like the channel/gutter terms.
2. **Dense boards protected:** the halo is `× channel_scale`, so on `motor_power`
   (`util ≥ 0.55 ⇒ channel_scale = 0`) it vanishes — no thrash. Same mechanism as D4.
3. **No regression on boards without tall parts:** if no pair has a tall part, the halo never
   fires → placement unchanged.
4. **Determinism:** `height` is data; the halo is a deterministic function of geometry + height.
5. **`height` is additive data** defaulting to a low value; absent/unknown fpids fall back to a
   conservative low height (no spurious halo).
6. **FreeRouting-gated:** merge only if `system` + `motor_power` routed-% does not regress vs the
   recorded baseline, and `tall_clearance` improves on boards with tall parts.

---

## 5. Current state (verified against `main`)

- `anneal._pair_penalty` (`anneal.py:91-114`): computes axis gaps `gx,gy`, the overlap barrier,
  and a channel term that fires when `shadow and 0 <= gap < target`, where
  `target = self.channel_mm` plus `self.gutter * self.channel_scale` for cross-block pairs.
  **B2 adds the height halo to `target` here.**
- `model.Component` (`model.py`) has `value`, `fpid` (Phase 1) and `eff_w/eff_h`; **no `height`.**
- `kicad_io.build_model` sets `value`/`fpid` via `_safe` (Phase 1); **B1 adds `height`.**
- `metrics.py` has the pure proxy pattern (`sheet_spread_score` etc.); **B3 adds `tall_clearance`.**
- Probed fpids (corpus): tall — `TO-220-*`, `CP_Radial_D{8,18}`, `L_Toroid_*`, `Potentiometer_*`,
  `TerminalBlock_*`/`bornier`, `PinHeader_*_Vertical`; short — `R_Axial_*_Horizontal`,
  `D_DO-*_Horizontal`, `C_Disc_*`, `DIP-*`.

---

## 6. Design

### B1 — `footprints.height_mm` + `Component.height`

New pure module `plugin/plugins/autoplace/footprints.py`:

```python
def height_mm(fpid: str) -> float:
    """Nominal THT body height above the board (mm), keyed on footprint class.
    Coarse by design -- only the tall/short distinction matters for DFM spacing.
    Unknown footprints fall back to a low height (no spurious halo)."""
```

Class → height (mm), matched case-insensitively on `fpid` substrings (order matters; first hit):
`TO-220/TO-247/TO-126` → 18; `CP_Radial` → `D<dia>` + 4 (electrolytic height ≈ diameter), else 12;
`Toroid`/`Inductor` → 22; `Potentiometer`/`3296W` → 10; `TerminalBlock`/`bornier` → 11;
`PinHeader`+`Vertical` → 9; `SW_DIP`/`Switch` → 6; `C_Disc` → 6; `DIP-` → 5;
`Horizontal` (axial lying flat) → 3; default → 4.

`Component.height: float = 4.0` (low default). `kicad_io.build_model` sets
`height=footprints.height_mm(fpid)` (pure call; no pcbnew dependency beyond the fpid it already
reads). `footprints` imports nothing from the engine (pure), so no cycle.

### B2 — Height halo in `_pair_penalty`

Add module constants near `CHANNEL`: `TALL_MM = 8.0` (parts ≥ this cast a shadow), `TALL_HALO_MM
= 2.0` (extra clearance a tall part wants from neighbors). In `_pair_penalty`, after the
cross-block gutter adjustment to `target`:

```python
        if max(a.height, b.height) >= TALL_MM:
            target += TALL_HALO_MM * self.channel_scale
```

So a tall part targets a wider channel to every neighbor; the existing
`if self.channel and shadow and 0 <= gap < target: cost += local * (target - gap)` does the rest.
`channel_scale` scaling makes it inert on dense boards (invariant #2) and zero on boards without
tall parts (invariant #3). `TALL_HALO_MM = 0` would reproduce today exactly — but the deliverable
ships it at 2.0, validated by the gate.

### B3 — `metrics.tall_clearance(board) -> float`

Over neighbor pairs where exactly one part is tall (`height ≥ TALL_MM`) and they shadow on one
axis, the mean shortfall `max(0, halo_target − gap)` (how far the short part intrudes into the
tall part's halo). Lower is better; `0.0` when no tall/short shadowing pairs. Pure; uses the same
`TALL_MM`/`TALL_HALO_MM`/`channel_width` constants so metric and term stay in lockstep. For the
gate + reporting (NOT folded into `candidate_key`).

---

## 7. Validation

**Pure unit tests (plain `pytest`):**
- `tests/test_footprints.py`: `height_mm` returns tall for `TO-220-3_Vertical`,
  `CP_Radial_D18.0mm` (≈22), `L_Toroid_*`, `TerminalBlock_bornier-2`, `PinHeader_1x02_Vertical`;
  short for `R_Axial_*_Horizontal` (3), `DIP-8` (5), `C_Disc` (6); default for an unknown fpid.
- `tests/test_engine.py`: a tall part + a short neighbor at a gap between `channel_mm` and
  `channel_mm + halo` gets a `_pair_penalty > 0` at `channel_scale=1`, and `0` at
  `channel_scale=0` (dense protection); two short parts at the same gap get the plain channel
  target (no halo). A board with no tall parts is byte-identical to `TALL_HALO_MM = 0`.
- `tests/test_metrics_proxies.py`: `tall_clearance` returns the mean shortfall; `0.0` with no
  tall parts.

**FreeRouting gate (KiCad python):**
- Baseline = current `main` (no-connector): `system` 95.0% / `motor_power` 66.1%.
- After B2: re-place + route the same two boards; **require routed-% ≥ baseline − ~2%** (no
  regression), and **`tall_clearance` strictly lower** on `system` (which has TO-220s,
  electrolytics, headers — tall parts). `motor_power` (dense, `channel_scale≈0`) is expected
  ~unchanged, which is the point (halo inert when there's no room). Record both numbers; if
  routing regresses, lower `TALL_HALO_MM` and re-gate.

## 8. Risks

- **R1 — over-spreading roomy boards** hurts routing. Mitigated by `× channel_scale` and the gate;
  `TALL_HALO_MM` tunable down.
- **R2 — height table inaccuracy.** Coarse heuristic; only tall/short matters, and the default is
  low (conservative — a mis-classified part just doesn't get a halo). No hard constraint depends on
  exact height.
- **R3 — interaction with the cross-block gutter** (both widen `target`): they add, which is
  intended (a tall part in another block gets both). `channel_scale` bounds the total.

## 9. Build order

B1 (`footprints.height_mm` + `Component.height` + kicad_io + tests) → B3 (`tall_clearance` metric +
tests) → B2 (`_pair_penalty` halo + tests) → FreeRouting gate. B2 last so the gate measures the
real placement change.
