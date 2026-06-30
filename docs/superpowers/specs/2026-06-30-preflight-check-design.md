# Preflight check — design

**Date:** 2026-06-30
**Status:** Approved

## Problem

A board needs a few things in place before auto-placement/routing gives good
results: a board outline, footprints, a ground net, and copper pours (so GND and
power are planed, not routed). When one is missing the user only finds out after
a wasted run (the reflow board routed ground because its `/GND` pour wasn't
recognised). A pre-run checklist surfaces these up front.

## Behaviour

When a board loads, the app shows a small status panel above **Run
AutoPlacement** with one row per prerequisite, each green ✓ or amber ⚠ with a
short detail. **Reminders, not blockers** — Run stays enabled (you may run
without pours intentionally).

## Checks (rows)

1. **Board outline** — Edge.Cuts present (placement boundary). ⚠ if absent.
2. **Footprints** — `N parts (M movable, L locked)`. ⚠ if zero. Lets you confirm
   locked crucial parts registered.
3. **Ground net** — a net whose leaf name is `GND` (`/GND`, `/Power/GND`).
   ⚠ if none.
4. **Copper pours** — count + nets of B.Cu/F.Cu pours (so they plane instead of
   route). ⚠ if none.

## Architecture

### `cli.py preflight <board>`

Loads via pcbnew, gathers raw facts, runs the pure evaluator, emits one JSON
line: `{"type":"preflight","rows":[…],"info":{…}}`.

Raw `info`:
- `has_outline` — any drawing on `Edge.Cuts`.
- `footprints`, `movable`, `locked` — counts (`fp.IsLocked()`).
- `gnd_net` — `kicad_io.find_gnd_net(pcb)` name or null.
- `pours` — `[{"layer","net"}]` for zones on B.Cu/F.Cu.

### New pure module `autoplace/preflight.py` (no pcbnew)

```python
def evaluate(info: dict) -> list[dict]:
    """Raw preflight facts -> checklist rows.

    Each row: {key, label, status: "ok"|"warn", detail}. Pure -> unit-testable.
    """
```

Rules: outline ok iff `has_outline`; footprints ok iff `footprints > 0`
(detail names movable/locked); ground ok iff `gnd_net`; pours ok iff `pours`
non-empty (detail lists the distinct nets).

### App

- **main.js**: `preflight` IPC → `runCliJson(python, ["preflight", board])`,
  return the parsed `preflight` object (`{ok, rows, info}`).
- **preload.js**: expose `preflight(opts)`.
- **renderer.js**: after `loadBoardView` succeeds, call `api.preflight`; render
  `rows` into a `#preflightRows` list (icon + label + detail per row). Re-run
  after the user toggles connectors? No — preflight reflects the board file, not
  connector marks; render once per board load.
- **index.html**: a `#preflight` panel in the setup card (hidden until a board
  loads).
- **styles.css**: `.pf-row` with ok/warn colour (reuse `--good` / `--warn`).

## Files

- Create: `plugin/plugins/autoplace/preflight.py`, `tests/test_preflight.py`
- Modify: `cli.py` (subcommand + dispatch), `app/main.js` (IPC),
  `app/preload.js`, `app/renderer/renderer.js`, `app/renderer/index.html`,
  `app/renderer/styles.css`.

## Testing

Pure-Python `tests/test_preflight.py`:
1. All-good info → every row `ok`; footprints detail names movable/locked counts.
2. Missing outline / zero footprints / no `gnd_net` / empty `pours` each →
   that row `warn`, others unaffected.
3. Pours detail lists distinct nets (deduped), e.g. `/GND, /+24V`.

End-to-end (KiCad python, manual): `cli.py preflight reflow.kicad_pcb` reports the
3 pours (`/GND`, `/+24V`, `/HEATER_RET`), the `/GND` ground net, footprint counts,
and outline present.

## Out of scope (YAGNI)

- Blocking Run on warnings.
- Auto-fixing (drawing outlines / pours).
- Per-net DRC or clearance checks (separate concern).
