# tools/gate — FreeRouting placement-quality gate harness

Dev/validation scripts used to gate engine changes on real routability. **Run under KiCad-10 python**
(`C:\Program Files\KiCad\10.0\bin\python.exe`) — they import `pcbnew` and shell out to FreeRouting
(`%USERPROFILE%\.freerouting\freerouting-1.9.0.jar`, Java 21). They copy boards to a scratch dir and
**never modify the source board**. Boards are passed as CLI args (corpus lives outside the repo).

⚠️ **The DTU corpus boards ship FULLY ROUTED.** Every harness that routes a *re-placed* board first
**strips the existing tracks** (textually), else FreeRouting routes around ~800 stale traces and the
routed-% is meaningless. Keep this when writing new gates.

⚠️ **±3-net FreeRouting run-to-run noise** on the `system` board — average over seeds / boards; never
trust sub-3-net deltas. ⚠️ **Don't run heavy placement concurrently with a route** (nondeterminism).

| script | what it does |
|---|---|
| `route_baseline.py <scratch> <board...>` | Core gate: place (engine, seed 0) → strip tracks → route → routed-%. `CONNECTORS=1` pins connectors to edges. |
| `swap_gate.py <scratch> <board> <seed...>` | Multi-seed route (mean over seeds) — beats the ±3-net noise. |
| `sa_probe_route.py <scratch> <board> [mults...]` | Routed-% vs SA-effort multiplier (found the search-limit → 90k cap). Reports place+route seconds. |
| `ovn_route_compare.py <scratch> <board>` | HUMAN (original import positions) vs OURS (engine) routed-%, head-to-head. |
| `determinism_probe.py <board...>` | Place N× in-process, fingerprint geometry — verifies determinism. |
| `align_measure.py` / `spacing_check.py` | Aesthetic metrics (alignment_score / spacing_unevenness) + overlap legality, ON vs OFF. |
| `decap_measure.py` / `decap_sweep.py` | Decap proximity ON/OFF and vs `_Weights.DECAP` weight. |
| `tall_measure.py` | Tall-part clearance halo ON/OFF. |
| `block_scan.py` / `anchor_diag.py` | Block/sheet structure + floorplan order diagnostics (remember to call `blocks.detect_blocks` first!). |

All scripts run from the repo root (they `sys.path.insert(0, "plugin/plugins")`). Paths to the JAR are
hardcoded; parameterize before using on another machine. See `docs/HANDOFF.md` §3–§4 for methodology,
gotchas, and the open questions these gates should help answer.
