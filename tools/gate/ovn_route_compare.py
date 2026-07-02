"""Head-to-head routability on a real board: HUMAN placement vs OUR auto-placement.
Both re-routed by FreeRouting from a clean slate (tracks stripped), same netclass.
KiCad python.  python ovn_route_compare.py <scratch_dir> <board.kicad_pcb>
Source board is never modified (copied into scratch first).
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
os.makedirs(scratch, exist_ok=True)
name = os.path.splitext(os.path.basename(src))[0]
src_pro = os.path.splitext(src)[0] + ".kicad_pro"

def route_copy(tag, place_fn):
    wd = os.path.join(scratch, tag)
    os.makedirs(wd, exist_ok=True)
    work = os.path.join(wd, name + ".kicad_pcb")
    shutil.copyfile(src, work)
    if os.path.exists(src_pro):
        shutil.copyfile(src_pro, os.path.splitext(work)[0] + ".kicad_pro")
    model, pcb = kicad_io.load_board(work)
    place_fn(model)                       # mutate placement (or not, for human)
    kicad_io.apply_to_board(model, pcb)
    pcbnew.SaveBoard(work, pcb)
    with open(work, encoding="utf-8") as fh:
        stripped, _ = strip_mod.strip_tracks(fh.read())
    with open(work, "w", encoding="utf-8") as fh:
        fh.write(stripped)
    r = routing.route_once(work, JAR, PASSES, sides=SIDES)
    return {"routed_pct": round(r["pct"], 1), "total": r["total"],
            "routed": r["routed"], "seconds": r["seconds"]}

M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")
out = {}
print("== routing HUMAN placement (original positions, tracks stripped) ...", flush=True)
out["human"] = route_copy("human", lambda m: None)
print("  human ->", out["human"], flush=True)
print("== routing OUR placement (engine.place seed=0, aesthetic ON) ...", flush=True)
out["ours"] = route_copy("ours", lambda m: engine.place(m, seed=0, margin=M, track=T))
print("  ours  ->", out["ours"], flush=True)
with open(os.path.join(scratch, "compare.json"), "w") as f:
    json.dump(out, f, indent=2)
print("COMPARE DONE:", json.dumps(out), flush=True)
