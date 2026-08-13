"""Route a whole board set single-sided and report what each one costs.

The per-board baseline every single-sided experiment has to beat. For each
board it places N seeds, routes each one single-sided for real, bridges the
leftovers, and records the best result -- plus enough per-seed detail to see how
much of the spread is placement luck.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/batch_singlesided.py \
        OUTDIR SEEDS BOARD.kicad_pcb [BOARD2.kicad_pcb ...]

Writes OUTDIR/batch.json (machine-readable, resumable -- a board already present
is skipped) and prints a table. Safe to kill and restart.
"""
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

import pcbnew  # noqa: E402

from autoplace import (engine, fabrication, globalroute, kicad_io,  # noqa: E402
                       metrics, routing, serialize, unrouted)

DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")


def _sidecar(path):
    p = os.path.splitext(path)[0] + ".autoplace.json"
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def route_seed(in_path, workdir, tag, seed, jar, passes, fab, connectors):
    """Place at one seed, route single-sided, bridge the rest. Never raises."""
    margin, track = fabrication.margin_for(fab), fabrication.track_for(fab)
    model, pcb = kicad_io.load_board(in_path)
    cand = copy.deepcopy(model)
    engine.place(cand, seed=seed, connectors=connectors, margin=margin, track=track)
    est = globalroute.analyse(cand, track=track, clearance=margin)
    row = {"seed": seed, "hpwl_mm": round(metrics.hpwl(cand), 2),
           "crossings": metrics.crossings(cand),
           "gr_bridges": est.bridges, "gr_conflicts": est.conflicts,
           "board": serialize.board_to_dict(cand)}

    work = os.path.join(workdir, f"{tag}_s{seed}.kicad_pcb")
    kicad_io.apply_to_board(cand, pcb)
    pcbnew.SaveBoard(work, pcb)
    kicad_io.copy_project(in_path, work)
    fabrication.apply_to_project(os.path.splitext(work)[0] + ".kicad_pro", fab)
    try:
        r1 = routing.route_once(work, jar, passes, sides=1)
        row["stage1_pct"] = round(r1["pct"], 2)
        row["stage1_total"] = r1["total"]
        row["missing_after_stage1"] = len(unrouted.analyse(r1["routed_pcb"])["missing"])
    except Exception as exc:
        row["error"] = f"stage1: {exc}"
        return row
    if row["missing_after_stage1"] == 0:
        row.update(bridges=0, missing_final=0)       # closed single-sided outright
        return row
    try:
        r2 = routing.route_stage2_bridges(r1["routed_pcb"], jar, passes)
        row["bridges"] = r2["bridges"]
        row["missing_final"] = len(unrouted.analyse(r2["routed_pcb"])["missing"])
    except Exception as exc:
        row["error"] = f"stage2: {exc}"
    return row


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    outdir, seeds, boards = argv[1], int(argv[2]), argv[3:]
    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, "batch.json")
    data = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)

    jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
    passes = int(os.environ.get("ROUTE_PASSES", "10"))
    fab = os.environ.get("FAB", "cnc")

    for path in boards:
        tag = os.path.basename(path).replace(".kicad_pcb", "")
        if tag in data and not os.environ.get("FORCE"):
            print(f"{tag}: already done, skipping", flush=True)
            continue
        wd = os.path.join(outdir, tag)
        os.makedirs(wd, exist_ok=True)
        conn = _sidecar(path).get("connectors")
        rows = []
        t0 = time.time()
        for seed in range(seeds):
            rows.append(route_seed(path, wd, tag, seed, jar, passes, fab, conn))
            ok = [r for r in rows if r.get("missing_final") is not None]
            print(f"  {tag} seed {seed}: "
                  f"stage1_missing={rows[-1].get('missing_after_stage1')} "
                  f"bridges={rows[-1].get('bridges')} "
                  f"final_missing={rows[-1].get('missing_final')} "
                  f"{rows[-1].get('error','')}", flush=True)
            data[tag] = {"board": path, "seconds": round(time.time() - t0, 1),
                         "rows": rows}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        print(f"{tag}: {len(rows)} seeds in {time.time() - t0:.0f}s", flush=True)

    print(f"\n{'board':<20} {'seeds':>5} {'best_bridges':>13} {'best_missing':>13} "
          f"{'closed_1sided':>14}")
    for tag in sorted(data):
        rows = [r for r in data[tag]["rows"] if r.get("missing_final") is not None]
        if not rows:
            print(f"{tag:<20} {'-':>5} {'all failed':>13}")
            continue
        done = [r for r in rows if r["missing_final"] == 0]
        best = min(done, key=lambda r: r["bridges"]) if done else None
        closed = sum(1 for r in rows if r.get("bridges") == 0)
        print(f"{tag:<20} {len(rows):>5} "
              f"{(best['bridges'] if best else '-'):>13} "
              f"{min(r['missing_final'] for r in rows):>13} "
              f"{closed:>14}")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
