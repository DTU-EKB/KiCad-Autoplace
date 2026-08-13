"""Does the orientation pass actually remove crossings on real boards?

Nothing ships in this repo unless it beats the baseline on real data, and a
post-placement pass is exactly the kind of change that looks obviously good on
paper and does nothing in practice. So: load the real boards, place each one at
several seeds exactly as ``cli.py`` would, then run ``orient.optimise`` on the
result and record what moved.

Reported per board, averaged over seeds, before -> after:

  * ``metrics.crossings``        the cheap proxy the engine already reports
  * ``globalroute.conflicts``    the crossing graph the pass optimises
  * ``globalroute.min_bridges``  the deliverable: wires the user has to solder
  * ``metrics.hpwl``             the guard -- an untangler that pays for it in
                                 wirelength has not helped anyone
  * overlaps                     the hard constraint; must never grow

Two variants are measured on the *same* placement, so the comparison is exact
rather than seed-noise: ``wl`` breaks crossing ties on tree length,
``xo`` (crossings-only) refuses to move unless crossings themselves improve.

Needs pcbnew only to read the boards, so run it under KiCad's Python:

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/eval_orient.py \
        OUT.json [BOARD.kicad_pcb ...]

With no board arguments it uses the DTU corpus + the Bose sub board. Env:
``SEEDS`` (default 6), ``FAB`` (default cnc), ``STRATEGY`` (default auto),
``VERBOSE=1`` for a line per seed.
"""
import copy
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import (engine, fabrication, globalroute, kicad_io,  # noqa: E402
                       metrics, orient)

_DTU = r"C:\Users\Mads2\DTU\4. Semester\Electrical Energy Systems\team\hardware\kicad\boards"
_BOSE = (r"C:\Users\Mads2\Documents\Projects\Projects\Bose Sub Integration"
         r"\hardware\kicad\subxo.kicad_pcb")

DEFAULT_BOARDS = [
    os.path.join(_DTU, "buck", "buck_v2", "buck_v2.kicad_pcb"),
    os.path.join(_DTU, "c2000_feedback", "c2000_feedback.kicad_pcb"),
    os.path.join(_DTU, "mppt_buck", "mppt_buck.kicad_pcb"),
    os.path.join(_DTU, "current_sense", "current_sense.kicad_pcb"),
    os.path.join(_DTU, "motor_power", "motor_power.kicad_pcb"),
    _BOSE,
]

VARIANTS = (("wl", True), ("xo", False))


def _read_sidecar(in_path):
    side = os.path.splitext(in_path)[0] + ".autoplace.json"
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _snapshot(board, margin, track):
    """Everything worth comparing before and after the pass."""
    segs = globalroute.net_segments(board)
    conf = globalroute.conflicts(segs)
    return {
        "crossings": metrics.crossings(board),
        "conflicts": len(conf),
        "bridges": globalroute.min_bridges(len(segs), conf),
        "hpwl_mm": round(metrics.hpwl(board), 2),
        "tree_mm": round(sum(s.length for s in segs), 2),
        "overlaps": len(metrics.overlaps(board)),
        "pinch_fraction": metrics.pinch_fraction(board, margin, track),
    }


def run_seed(path, seed, margin, track, connectors, strategy):
    """Place once, then run every orientation variant on that same placement."""
    model, _pcb = kicad_io.load_board(path)
    t0 = time.perf_counter()
    engine.place(model, seed=seed, strategy=strategy, connectors=connectors,
                 margin=margin, track=track)
    place_s = time.perf_counter() - t0

    row = {"seed": seed, "place_seconds": round(place_s, 2),
           "free_parts": len(model.free()),
           "before": _snapshot(model, margin, track), "variants": {}}

    for tag, tiebreak in VARIANTS:
        cand = copy.deepcopy(model)
        rep = orient.optimise(cand, margin=margin, wirelength_tiebreak=tiebreak)
        row["variants"][tag] = {
            "after": _snapshot(cand, margin, track),
            "rotated": len(rep["rotated"]),
            "sweeps": rep["sweeps"],
            "evaluated": rep["evaluated"],
            "seconds": rep["seconds"],
            "moved": sum(1 for r in cand.components.values()
                         if (r.x, r.y) != (model.components[r.ref].x,
                                           model.components[r.ref].y)),
        }
    return row


def _mean(vals):
    return statistics.fmean(vals) if vals else 0.0


def summarise(tag, rows, variant):
    b = [r["before"] for r in rows]
    a = [r["variants"][variant]["after"] for r in rows]
    v = [r["variants"][variant] for r in rows]
    hb, ha = _mean([x["hpwl_mm"] for x in b]), _mean([x["hpwl_mm"] for x in a])
    return {
        "board": tag, "variant": variant, "seeds": len(rows),
        "parts": rows[0]["free_parts"] if rows else 0,
        "cross_before": _mean([x["crossings"] for x in b]),
        "cross_after": _mean([x["crossings"] for x in a]),
        "conf_before": _mean([x["conflicts"] for x in b]),
        "conf_after": _mean([x["conflicts"] for x in a]),
        "bridge_before": _mean([x["bridges"] for x in b]),
        "bridge_after": _mean([x["bridges"] for x in a]),
        "hpwl_before": hb, "hpwl_after": ha,
        "hpwl_pct": (ha - hb) / hb * 100.0 if hb else 0.0,
        "tree_before": _mean([x["tree_mm"] for x in b]),
        "tree_after": _mean([x["tree_mm"] for x in a]),
        "ovl_before": max([x["overlaps"] for x in b] or [0]),
        "ovl_after": max([x["overlaps"] for x in a] or [0]),
        "rotated": _mean([x["rotated"] for x in v]),
        "sweeps": max([x["sweeps"] for x in v] or [0]),
        "ms": _mean([x["seconds"] for x in v]) * 1000.0,
        "ms_max": max([x["seconds"] for x in v] or [0.0]) * 1000.0,
        "moved": max([x["moved"] for x in v] or [0]),
        "bridge_wins": sum(1 for x, y in zip(b, a) if y["bridges"] < x["bridges"]),
        "bridge_losses": sum(1 for x, y in zip(b, a) if y["bridges"] > x["bridges"]),
    }


_HDR = (f"{'board':<16} {'var':<3} {'prt':>4} {'crossings':>13} "
        f"{'conflicts':>13} {'bridges':>13} {'HPWL b->a':>17} {'d%':>6} "
        f"{'rot':>5} {'sw':>3} {'ms':>7} {'ovl':>7}")


def _line(s):
    return (f"{s['board']:<16} {s['variant']:<3} {s['parts']:>4} "
            f"{s['cross_before']:>6.1f}->{s['cross_after']:<6.1f} "
            f"{s['conf_before']:>6.1f}->{s['conf_after']:<6.1f} "
            f"{s['bridge_before']:>6.2f}->{s['bridge_after']:<6.2f} "
            f"{s['hpwl_before']:>8.0f}->{s['hpwl_after']:<8.0f} "
            f"{s['hpwl_pct']:>6.2f} {s['rotated']:>5.1f} {s['sweeps']:>3} "
            f"{s['ms']:>7.1f} {s['ovl_before']:>3}->{s['ovl_after']:<3}")


def main(argv):
    out_path = argv[1] if len(argv) > 1 else "orient_eval.json"
    boards = argv[2:] or DEFAULT_BOARDS
    seeds = int(os.environ.get("SEEDS", "6"))
    fab = os.environ.get("FAB", "cnc")
    strategy = os.environ.get("STRATEGY", "auto")
    verbose = os.environ.get("VERBOSE") == "1"
    margin = fabrication.margin_for(fab)
    track = fabrication.track_for(fab)

    # flush everywhere: a full study is minutes of placement per board, and
    # pcbnew writes its own banner straight to fd 1, so a buffered table would
    # appear only at the very end, interleaved with noise.
    print(_HDR, flush=True)
    out = {"fab": fab, "seeds": seeds, "strategy": strategy, "boards": []}
    summaries = []
    for path in boards:
        tag = os.path.basename(path).replace(".kicad_pcb", "")
        if not os.path.exists(path):
            print(f"{tag:<16} (missing: {path})")
            continue
        connectors = _read_sidecar(path).get("connectors")
        rows = []
        for seed in range(seeds):
            try:
                row = run_seed(path, seed, margin, track, connectors, strategy)
            except Exception as exc:                      # keep the study going
                print(f"{tag:<16} seed {seed} ERROR: {exc}")
                continue
            rows.append(row)
            if verbose:
                w = row["variants"]["wl"]
                print(f"  {tag} seed {seed}: "
                      f"conf {row['before']['conflicts']}->{w['after']['conflicts']} "
                      f"bridges {row['before']['bridges']}->{w['after']['bridges']} "
                      f"rot {w['rotated']} in {w['seconds'] * 1000:.0f} ms "
                      f"({w['sweeps']} sweeps, {w['evaluated']} evals)", flush=True)
        if not rows:
            continue
        out["boards"].append({"board": path, "tag": tag, "rows": rows})
        for tag_v, _ in VARIANTS:
            s = summarise(tag, rows, tag_v)
            summaries.append(s)
            print(_line(s), flush=True)
        # Written after every board, not at the end: a full study is a quarter
        # of an hour of placement and must not lose everything to a late crash.
        out["summary"] = summaries
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)

    print()
    for tag_v, _ in VARIANTS:
        sel = [s for s in summaries if s["variant"] == tag_v]
        if not sel:
            continue
        db = sum(s["bridge_before"] - s["bridge_after"] for s in sel)
        dc = sum(s["conf_before"] - s["conf_after"] for s in sel)
        dx = sum(s["cross_before"] - s["cross_after"] for s in sel)
        hb = sum(s["hpwl_before"] for s in sel)
        ha = sum(s["hpwl_after"] for s in sel)
        print(f"[{tag_v}] corpus mean per board: crossings -{dx / len(sel):.2f}, "
              f"conflicts -{dc / len(sel):.2f}, bridges -{db / len(sel):.2f}; "
              f"HPWL {(ha - hb) / hb * 100.0:+.2f}%; "
              f"worst {max(s['ms_max'] for s in sel):.0f} ms; "
              f"bridge wins/losses {sum(s['bridge_wins'] for s in sel)}/"
              f"{sum(s['bridge_losses'] for s in sel)}; "
              f"parts displaced {max(s['moved'] for s in sel)}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
