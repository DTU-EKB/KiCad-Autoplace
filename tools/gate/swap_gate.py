"""Multi-seed routability gate: place a board at several seeds (default cap) and route each.
KiCad python. Source never modified.
  python swap_gate.py <scratch_dir> <board.kicad_pcb> <seed1> [seed2 ...]
"""
import json
import os
import shutil
import sys
sys.path.insert(0, "plugin/plugins")
import pcbnew  # noqa: E402
from autoplace import engine, fabrication, kicad_io, routing  # noqa: E402
from autoplace import strip as strip_mod  # noqa: E402

JAR = os.path.expandvars(
    os.environ.get("FREEROUTING_JAR", r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar"))
PASSES = int(os.environ.get("GATE_PASSES", "20"))
SIDES = int(os.environ.get("GATE_SIDES", "2"))
scratch, src = sys.argv[1], sys.argv[2]
seeds = [int(s) for s in sys.argv[3:]] or [0]
os.makedirs(scratch, exist_ok=True)
name = os.path.splitext(os.path.basename(src))[0]
src_pro = os.path.splitext(src)[0] + ".kicad_pro"
M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")

out = {}
for seed in seeds:
    wd = os.path.join(scratch, f"seed{seed}")
    os.makedirs(wd, exist_ok=True)
    work = os.path.join(wd, name + ".kicad_pcb")
    shutil.copyfile(src, work)
    if os.path.exists(src_pro):
        shutil.copyfile(src_pro, os.path.splitext(work)[0] + ".kicad_pro")
    model, pcb = kicad_io.load_board(work)
    engine.place(model, seed=seed, margin=M, track=T)
    kicad_io.apply_to_board(model, pcb)
    pcbnew.SaveBoard(work, pcb)
    with open(work, encoding="utf-8") as fh:
        stripped, _ = strip_mod.strip_tracks(fh.read())
    with open(work, "w", encoding="utf-8") as fh:
        fh.write(stripped)
    r = routing.route_once(work, JAR, PASSES, sides=SIDES)
    out[seed] = {"routed_pct": round(r["pct"], 1), "routed": r["routed"], "total": r["total"]}
    print(f"  {name} seed={seed} -> {out[seed]['routed_pct']}% "
          f"({out[seed]['routed']}/{out[seed]['total']})", flush=True)

pcts = [v["routed_pct"] for v in out.values()]
print(f"  {name} MEAN={round(sum(pcts)/len(pcts),1)}% over seeds {seeds}", flush=True)
with open(os.path.join(scratch, "gate.json"), "w") as f:
    json.dump(out, f, indent=2)
