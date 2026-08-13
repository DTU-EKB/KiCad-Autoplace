"""Validation gate for the global router: predicted vs. what FreeRouting does.

A routability predictor that does not correlate with the router is worse than no
predictor -- it steers placement confidently in the wrong direction. So before
``globalroute`` is allowed anywhere near the annealer's cost function, this
harness routes a spread of real placements and records, per seed:

  * the prediction (``globalroute.analyse`` + the existing cheap proxies), and
  * the ground truth: stage-1 single-sided completion, the DRC missing-connection
    count, and the wire bridges stage-2 actually needs.

Run under KiCad's python (it needs pcbnew, Java and FreeRouting):

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/correlate_globalroute.py \
        BOARD.kicad_pcb WORKDIR [seeds]

Everything -- including each placement's full geometry -- lands in
``WORKDIR/correlation.json``. The geometry is the point: routing one placement
costs 10-60 s, so the set is routed ONCE and replayed offline against as many
predictor variants as tuning needs (``tools/score_correlation.py``).
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


def _read_sidecar(in_path):
    side = os.path.splitext(in_path)[0] + ".autoplace.json"
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _predict(board, margin, track):
    """Everything the pure-Python tiers can say about this placement."""
    est = globalroute.analyse(board, track=track, clearance=margin)
    return {
        "gr_bridges": est.bridges,
        "gr_conflicts": est.conflicts,
        "gr_segments": est.segments,
        "gr_wirelength": est.wirelength,
        "gr_overflow": est.overflow,
        "gr_peak": est.peak,
        "hpwl_mm": metrics.hpwl(board),
        "crossings": metrics.crossings(board),
        "overlaps": len(metrics.overlaps(board)),
        "pinch_fraction": metrics.pinch_fraction(board, margin, track),
        "sheet_spread_score": metrics.sheet_spread_score(board),
        "whitespace_connectivity": metrics.whitespace_connectivity(board),
    }


def run_seed(in_path, workdir, seed, jar, passes, fab, connectors, strategy):
    """Place at ``seed``, predict, then route it for real. Never raises."""
    margin = fabrication.margin_for(fab)
    track = fabrication.track_for(fab)

    model, pcb = kicad_io.load_board(in_path)
    cand = copy.deepcopy(model)
    t0 = time.perf_counter()
    engine.place(cand, seed=seed, strategy=strategy, connectors=connectors,
                 margin=margin, track=track)
    place_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    pred = _predict(cand, margin, track)
    pred["gr_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    row = {"seed": seed, "place_seconds": round(place_s, 2), **pred,
           "board": serialize.board_to_dict(cand)}

    work = os.path.join(workdir, f"corr_seed{seed}.kicad_pcb")
    kicad_io.apply_to_board(cand, pcb)
    pcbnew.SaveBoard(work, pcb)
    kicad_io.copy_project(in_path, work)
    fabrication.apply_to_project(os.path.splitext(work)[0] + ".kicad_pro", fab)

    # --- ground truth stage 1: single-sided route -------------------------
    try:
        r1 = routing.route_once(work, jar, passes, sides=1)
    except Exception as exc:
        row["error"] = f"stage1: {exc}"
        return row
    row.update(stage1_total=r1["total"], stage1_unrouted=r1["unrouted"],
               stage1_pct=round(r1["pct"], 2), stage1_seconds=r1["seconds"])

    # KiCad's DRC (with --refill-zones) is the authoritative missing count; the
    # raw ratsnest is measured on the pour exactly as saved and reads pessimistic.
    try:
        row["drc_missing"] = len(unrouted.analyse(r1["routed_pcb"])["missing"])
    except RuntimeError as exc:
        row["drc_missing"] = None
        row["drc_error"] = str(exc)

    # --- ground truth stage 2: how many wire bridges it really takes ------
    try:
        r2 = routing.route_stage2_bridges(r1["routed_pcb"], jar, passes)
        row.update(actual_bridges=r2["bridges"],
                   bridge_segments=r2["bridge_segments"],
                   after_bridge_unrouted=r2["unrouted"],
                   stage2_seconds=r2["seconds"])
        try:
            row["after_bridge_drc_missing"] = len(
                unrouted.analyse(r2["routed_pcb"])["missing"])
        except RuntimeError:
            row["after_bridge_drc_missing"] = None
    except Exception as exc:
        row["error"] = f"stage2: {exc}"
    return row


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    in_path, workdir = argv[1], argv[2]
    seeds = int(argv[3]) if len(argv) > 3 else 16
    # SEED_START extends an existing study without re-routing what it already
    # has: distinguishing two predictors whose rank correlations differ by ~0.1
    # needs more than 16 samples, and each one costs a 40 s route.
    start = int(os.environ.get("SEED_START", "0"))
    os.makedirs(workdir, exist_ok=True)

    jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
    passes = int(os.environ.get("ROUTE_PASSES", "10"))
    fab = os.environ.get("FAB", "cnc")
    strategy = os.environ.get("STRATEGY", "auto")
    sc = _read_sidecar(in_path)
    connectors = sc.get("connectors")

    out_path = os.path.join(workdir, f"correlation_{start}_{start + seeds}.json"
                            if start else "correlation.json")
    rows = []
    for seed in range(start, start + seeds):
        t0 = time.time()
        row = run_seed(in_path, workdir, seed, jar, passes, fab, connectors,
                       strategy)
        rows.append(row)
        # Write after every seed: a 25-minute run must not lose everything if
        # the last route dies.
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"board": in_path, "fab": fab, "passes": passes,
                       "rows": rows}, f, indent=1)
        print(f"seed {seed:2d}  pred_bridges={row.get('gr_bridges')!s:>3} "
              f"crossings={row.get('crossings')!s:>3} "
              f"| stage1={row.get('stage1_pct')!s:>6}% "
              f"drc_missing={row.get('drc_missing')!s:>3} "
              f"actual_bridges={row.get('actual_bridges')!s:>3} "
              f"| {time.time() - t0:.0f}s {row.get('error', '')}", flush=True)

    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
