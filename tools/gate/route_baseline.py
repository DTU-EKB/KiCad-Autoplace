"""FreeRouting baseline for Phase 2 non-regression gates.

For each board: copy it (+ .kicad_pro) to a scratch workdir (the user's boards
are NEVER modified), place it with the current engine, route once with
FreeRouting, and record routed-%. Run under KiCad 10 python (needs pcbnew + java).

  python route_baseline.py <scratch_dir> <board.kicad_pcb> [<board2.kicad_pcb> ...]
"""
import json
import os
import shutil
import sys

sys.path.insert(0, "plugin/plugins")

import pcbnew  # noqa: E402

from autoplace import engine, fabrication, kicad_io, routing  # noqa: E402
from autoplace import strip as strip_mod  # noqa: E402

JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")
PASSES = 20
FAB = "cnc"
SIDES = 2

scratch = sys.argv[1]
boards = sys.argv[2:]
os.makedirs(scratch, exist_ok=True)

results = {}
for src in boards:
    name = os.path.splitext(os.path.basename(src))[0]
    wd = os.path.join(scratch, name)
    os.makedirs(wd, exist_ok=True)
    work = os.path.join(wd, name + ".kicad_pcb")
    shutil.copyfile(src, work)
    src_pro = os.path.splitext(src)[0] + ".kicad_pro"
    if os.path.exists(src_pro):
        shutil.copyfile(src_pro, os.path.splitext(work)[0] + ".kicad_pro")
    try:
        model, pcb = kicad_io.load_board(work)
        # CONNECTORS=1 -> pin auto-detected connectors to edges (assign_edges),
        # so connector-orientation terms (Phase 2B) are exercised. Default off keeps
        # the decap-era baseline comparable.
        conns = None
        if os.environ.get("CONNECTORS") == "1":
            conns = [r for r, c in model.components.items() if c.is_connector]
        engine.place(model, seed=0,
                     margin=fabrication.margin_for(FAB),
                     track=fabrication.track_for(FAB),
                     connectors=conns)
        kicad_io.apply_to_board(model, pcb)
        pcbnew.SaveBoard(work, pcb)
        # These corpus boards ship FULLY ROUTED. engine.place moved the footprints
        # but the old tracks stay frozen at their original coords; route_once only
        # strips on single-sided. Strip here so FreeRouting routes the NEW placement
        # from a clean slate instead of around 800+ stale traces.
        with open(work, encoding="utf-8") as fh:
            stripped, _ = strip_mod.strip_tracks(fh.read())
        with open(work, "w", encoding="utf-8") as fh:
            fh.write(stripped)
        # Route with the board's OWN netclass (its .kicad_pro was copied above); no
        # fab override -- system is 1.0/0.85, motor_power 1.0/0.8 natively.
        r = routing.route_once(work, JAR, PASSES, sides=SIDES)
        results[name] = {"routed_pct": round(r["pct"], 1), "total": r["total"],
                         "routed": r["routed"], "seconds": r["seconds"]}
        print(name, "->", results[name], flush=True)
    except Exception as e:  # noqa: BLE001
        results[name] = {"error": f"{type(e).__name__}: {e}"}
        print(name, "ERROR", results[name]["error"], flush=True)

with open(os.path.join(scratch, "baseline.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print("BASELINE DONE:", json.dumps(results), flush=True)
