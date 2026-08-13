"""Does seeding from a planar embedding actually untangle real boards?

Compares, per board, the straight-line crossings of the net trees under:

  * the board's own placement (what the designer or a previous run left),
  * the current engine seed + anneal (``engine.place``) at several seeds,
  * the planar-embedding seed (``topoplace.seed``) alone,
  * the planar-embedding seed followed by the normal anneal.

Crossings are the cheap stand-in here; the routed truth comes later from
``tools/batch_singlesided.py``. What this answers first is narrower and has to
hold before anything else is worth running: does the embedding reach ZERO
crossings on the boards the planarity census proved are planar? If it does not,
the seed is not realising the embedding and no amount of annealing will fix it.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/eval_topoplace.py \
        OUT.json SEEDS BOARD.kicad_pcb [BOARD2 ...]
"""
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import (engine, fabrication, globalroute, kicad_io,  # noqa: E402
                       metrics, planarity, topoplace)


def _cross(board):
    return len(globalroute.conflicts(globalroute.net_segments(board)))


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    out_path, seeds, boards = argv[1], int(argv[2]), argv[3:]
    fab = os.environ.get("FAB", "cnc")
    margin, track = fabrication.margin_for(fab), fabrication.track_for(fab)
    rows = []

    print(f"{'board':<18} {'forced':>6} {'orig':>6} {'engine':>14} "
          f"{'topo_seed':>10} {'topo+anneal':>14} {'topo_ms':>8}")
    for path in boards:
        tag = os.path.basename(path).replace(".kicad_pcb", "")
        model, _ = kicad_io.load_board(path)
        forced = planarity.forced_bridges(model)
        orig = _cross(model)

        eng = []
        for s in range(seeds):
            b = copy.deepcopy(model)
            engine.place(b, seed=s, margin=margin, track=track)
            eng.append(_cross(b))

        t0 = time.perf_counter()
        tb = copy.deepcopy(model)
        topoplace.seed(tb)
        topo_ms = (time.perf_counter() - t0) * 1000.0
        topo_only = _cross(tb)

        ta = []
        for s in range(seeds):
            b = copy.deepcopy(model)
            topoplace.seed(b)
            engine.place(b, seed=s, margin=margin, track=track, strategy="keep")
            ta.append(_cross(b))

        row = {"tag": tag, "forced_bridges": forced["bridges"],
               "planar": forced["planar"], "orig_crossings": orig,
               "engine_crossings": eng, "topo_seed_crossings": topo_only,
               "topo_anneal_crossings": ta, "topo_ms": round(topo_ms, 1),
               "parts": forced["components"]}
        rows.append(row)
        print(f"{tag:<18} {forced['bridges']:>6} {orig:>6} "
              f"{f'{min(eng)}..{max(eng)}':>14} {topo_only:>10} "
              f"{f'{min(ta)}..{max(ta)}':>14} {topo_ms:>8.1f}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)

    clean = [r for r in rows if r["planar"] and r["topo_seed_crossings"] == 0]
    print(f"\nplanar boards drawn with ZERO crossings by the embedding seed: "
          f"{len(clean)}/{sum(1 for r in rows if r['planar'])}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
