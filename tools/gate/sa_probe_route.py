"""Does more SA effort improve routability? Place a board at several sa_steps
multipliers, route each, report routed-%. KiCad python. Source never modified.
  python sa_probe_route.py <scratch_dir> <board.kicad_pcb> [mult1 mult2 ...]
Default multipliers: 0.5 1 2. Looks for a TREND (survives ±3-net noise better than one delta).
"""
import json
import os
import shutil
import sys
import time
sys.path.insert(0, "plugin/plugins")
import pcbnew  # noqa: E402
from autoplace import engine, fabrication, kicad_io, routing  # noqa: E402
from autoplace import strip as strip_mod  # noqa: E402

JAR = os.path.expandvars(
    os.environ.get("FREEROUTING_JAR", r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar"))
PASSES = int(os.environ.get("GATE_PASSES", "20"))
SIDES = int(os.environ.get("GATE_SIDES", "2"))
scratch, src = sys.argv[1], sys.argv[2]
mults = [float(x) for x in sys.argv[3:]] or [0.5, 1.0, 2.0]
os.makedirs(scratch, exist_ok=True)
name = os.path.splitext(os.path.basename(src))[0]
src_pro = os.path.splitext(src)[0] + ".kicad_pro"
M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")

base, _ = kicad_io.load_board(src)
n_free = len(base.free())
default = max(3500, min(45000, n_free * 700))
print(f"board={name} free={n_free} default_sa_steps={default}", flush=True)

out = {}
for mult in mults:
    steps = round(default * mult)
    wd = os.path.join(scratch, f"sa{mult}")
    os.makedirs(wd, exist_ok=True)
    work = os.path.join(wd, name + ".kicad_pcb")
    shutil.copyfile(src, work)
    if os.path.exists(src_pro):
        shutil.copyfile(src_pro, os.path.splitext(work)[0] + ".kicad_pro")
    model, pcb = kicad_io.load_board(work)
    t_place = time.time()
    engine.place(model, seed=0, margin=M, track=T, sa_steps=steps)
    place_s = round(time.time() - t_place, 1)
    kicad_io.apply_to_board(model, pcb)
    pcbnew.SaveBoard(work, pcb)
    with open(work, encoding="utf-8") as fh:
        stripped, _ = strip_mod.strip_tracks(fh.read())
    with open(work, "w", encoding="utf-8") as fh:
        fh.write(stripped)
    r = routing.route_once(work, JAR, PASSES, sides=SIDES)
    out[mult] = {"steps": steps, "routed_pct": round(r["pct"], 1),
                 "routed": r["routed"], "total": r["total"],
                 "route_s": r["seconds"], "place_s": place_s}
    print(f"  mult={mult} steps={steps} -> {out[mult]['routed_pct']}% "
          f"({out[mult]['routed']}/{out[mult]['total']}) place={place_s}s route={r['seconds']}s",
          flush=True)

with open(os.path.join(scratch, "sa_probe.json"), "w") as f:
    json.dump(out, f, indent=2)
print("SA PROBE DONE:", json.dumps(out), flush=True)
