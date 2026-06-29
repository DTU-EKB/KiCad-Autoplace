# M7 — Route-driven placement refinement

Status: approved design (2026-06-29). Implementation not started.

## Problem

Placement proxies (HPWL, MST crossings) plateau below full routability. Measured
ground truth on the 131-part **system** board (2-layer / double-sided, the real
target for this board) with the current engine + connectors-on-edge:

| Router effort (FreeRouting) | Routed | Unrouted |
|---|---|---|
| 10 passes | 95.4% (249/261) | 12 |
| 50 passes | 97.3% (254/261) | 7 |
| spec's old baseline | 86.6% | 35 |
| hand placement | 100% | 0 |

More router passes give diminishing returns; the last ~7 connections are
**placement-driven congestion** the router cannot resolve at any effort. Closing
them needs a feedback loop: place → route → locate congestion → nudge → re-route.
This is the spec's documented M7 milestone. The goal is to push automated
routability toward the hand-placed 100% on the acceptance gate that matters
(`route_check.py` / FreeRouting), not the proxies.

## Goals

- An offline refinement loop that measurably raises FreeRouting routed-% by
  iterating placement against real routing results.
- Driven from the Electron app (a "Refine" action), with live per-iteration
  progress (routed-% climbing), reusing the existing NDJSON streaming bridge.
- Deterministic, keep-best behavior: never return a placement that routes worse
  than where it started; report the climb honestly.
- Keep `pcbnew` and the FreeRouting subprocess isolated from the pure engine; the
  congestion analysis is pure-Python and unit-testable.

## Non-goals (this cycle)

- Changing the router or net-class rules.
- Single-sided-specific congestion modelling (the system board is 2-layer;
  via density is treated as congestion pressure, not as a failure).
- In-editor (KiCad plugin) refinement — the loop is offline/app-driven only.
- Guaranteeing 100% — the loop improves routed-% toward the ceiling; it stops
  honestly when it can no longer improve.

## Architecture

Keep the split: `pcbnew` + the FreeRouting subprocess live on the I/O side; the
congestion math and the cost integration are pure-Python.

### New / changed modules

- `autoplace/routing.py` *(new, pcbnew side)* — `route_once(pcb, jar, passes) ->
  {routed, total, pct, ses_path}`. Extracted from today's `tools/route_check.py`
  (export Specctra DSN → run FreeRouting head-less → import SES → count unrouted
  via `kicad_io.unrouted_count`). `tools/route_check.py` becomes a thin CLI over
  `routing.route_once` with no behavior change.
- `autoplace/congestion.py` *(new, **pure-Python, unit-tested**)* — parses the
  `.ses` text into a `CongestionField`: a grid of track density, via locations,
  and per-net detour ratio (routed length ÷ pad-to-pad straight length). No
  `pcbnew`.
- `autoplace/refine.py` *(new)* — the outer keep-best loop. Orchestrates
  `routing.route_once` + `congestion.parse` + re-anneal + keep-best/patience,
  emitting progress.
- `anneal.py` — gains optional `congestion=None`. When given a per-component
  pressure map, it raises the channel/spacing weight locally for pressured
  components (spreads exactly where the router struggled). `None` = today's exact
  behavior (zero regression risk).
- `cli.py refine` *(new subcommand)* — streams the NDJSON protocol the app reads.
- App: a "Refine (route-driven)" action → `run-refine` IPC → spawns `cli.py
  refine` with `AUTOPLACE_STREAM=1`; renderer shows the per-iteration routed-%.

### Why a CongestionField object

A single value type produced by pure code and consumed by the annealer keeps the
SES-parsing (fragile, I/O-shaped) testable in isolation and lets the cost
integration stay a pure function of (board, field).

## Data flow (per iteration)

```
current best placement (Board) + live pcb
  → apply placement, ExportSpecctraDSN, run FreeRouting   (routing.route_once)
  → routed-% (acceptance metric) + .ses path
  → congestion.parse(ses, board) → CongestionField        (pure python)
  → if routed == 100%: stop
  → field → per-component pressure → anneal(board, congestion=field, warm_start)
  → re-route the candidate → routed-%
  → keep-best: adopt candidate only if routed-% beats best by > noise margin
  → patience: stop after N non-improving iters, iteration budget, or 100%
```

The annealer warm-starts from the current best (does not re-seed), so each
iteration is a local refinement, not a fresh placement.

## Congestion model (`congestion.py`)

Parse the `.ses` session file (Specctra text: `wire`/`path` polylines per net per
layer, plus `via` instances). Bin into a grid sized to the board outline
(cell ≈ a few mm). Per cell compute:

- **track density** — total routed wire length whose segments fall in the cell,
  per unit area.
- **via count** — vias located in the cell (layer-change pressure; on a 2-layer
  board, via clusters mark where the router had to jump layers to get through).
- **detour ratio per net** — routed length ÷ straight pad-to-pad length; high
  detour means the net fought its way around an obstacle. Attributed to the cells
  the net passes through.

`CongestionField` exposes `pressure_at(x, y) -> float` combining the three
(normalised), used to sample a per-component pressure: a component sitting in (or
adjacent to) hot cells, or carrying high-detour nets, gets high pressure.

### How the field enters the cost

In `anneal.py`, each component's pressure scales **up the existing channel/
spacing term locally** for that component — so the annealer widens channels
exactly where the router struggled, not everywhere. No new global cost term, just
a per-component multiplier on the channel weight. The field is fixed for one
re-anneal pass (it reflects the previous routing); the loop recomputes it after
re-routing.

## Loop control (keep-best + patience)

FreeRouting is non-deterministic, so routed-% is noisy run-to-run.

- Track `best` placement by routed-%.
- Accept an iteration's candidate only if its routed-% beats `best` by a margin
  (the noise band, e.g. ≥ 1 connection).
- Warm-start the next iteration from `best` (not the rejected candidate).
- Stop on: routed == total (100%), a fixed iteration budget, or `patience`
  consecutive non-improving iterations.
- Fixed RNG seed for the anneal; FreeRouting pass count per iteration is a
  parameter (default tuned for a stable-enough signal within reasonable time —
  e.g. 20 passes; a final confirmation route may use more).
- The returned placement is always `best` — never worse-routing than the input.

## Streaming protocol (NDJSON additions)

Reuses the existing `progress` / `result` / `log` events plus:

- `{"type":"iteration","iter":2,"routed_pct":96.2,"best_pct":96.2,
   "routed":251,"total":261}`
- `progress` reused for sub-steps (`stage:"route 2/8"`, `stage:"anneal"`).
- final `result` extended with `routed_pct`, `iterations`, and `routed_output`
  (path to `<stem>.routed.kicad_pcb`).

## App integration

- `preload.js` / `main.js`: `run-refine(opts)` IPC spawning `cli.py refine IN
  OUT SEED` with `AUTOPLACE_STREAM=1`, forwarding lines as today's `runPlace`
  does, plus the new `iteration` event.
- renderer: a "Refine (route-driven)" button (requires FreeRouting/Java — surface
  a clear error if missing); shows the routed-% per iteration (a small climbing
  list or sparkline) and the final routed-%.

## Error handling

- FreeRouting/Java missing or jar not found → fail the run with a clear message
  (the app surfaces it via the existing log/error path); do not crash silently.
- Empty/0-byte SES (FreeRouting bailed) → that iteration's route is a failure;
  surface FreeRouting's tail output (as `route_check` already does) and skip the
  iteration rather than treating it as 0% routed.
- A `.ses` that parses to no wires → `CongestionField` is empty; the re-anneal
  runs with no extra pressure (degenerates to a plain warm re-anneal).
- No improvement ever → return the input placement unchanged, report honestly.

## Testing

- `congestion.py` (pure-Python): parse a small saved sample `.ses` fixture →
  assert grid density is higher in the known-crowded region; a high-detour net is
  flagged; an empty/short SES yields an empty field without error.
- `anneal.py` congestion integration (pure-Python): with a synthetic field that
  marks one cluster as hot, a warm re-anneal spreads that cluster more than the
  no-field run (cite the spacing increase), while `congestion=None` reproduces
  today's result exactly.
- `refine.py` loop logic (pure-Python) with a **stubbed router** (a fake
  `route_once` returning scripted routed-% sequences): keep-best adopts only on
  improvement beyond the margin; patience stops after N non-improving iters;
  warm-starts from best; returns best on no improvement.
- End-to-end (manual, KiCad python + FreeRouting) on the system board via
  `cli.py refine`: routed-% climbs from ~97% toward 100% and the loop terminates.

## Open follow-ups (not this cycle)

- Single-sided congestion modelling for laser boards (treat vias as failures).
- Replacing the fixed pass count with adaptive routing effort.
