#!/usr/bin/env python3
"""Does crossing-minimal re-treeing beat the MST on real placements?

``nettopo`` claims that choosing each net's spanning tree to dodge other nets,
instead of to be short, lowers the crossings and wire bridges a placement is
predicted to need. The repo's rule is that nothing ships unless it beats the
baseline on real data, so this harness measures exactly that claim: place a
board at several seeds and, for each seed, score the SAME placement twice --
once with ``globalroute``'s MST trees (the baseline in use today) and once with
``nettopo``'s trees -- through the same ``conflicts`` and ``min_bridges`` code.

Placing is the slow part (seconds per seed) and pcbnew is the only reason this
needs KiCad's Python, so every placement is cached as JSON on first use:

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/eval_nettopo.py \\
        --cache WORKDIR --seeds 6

and afterwards the same command re-runs offline under any Python, which is what
makes sweeping the exchange-rate constant affordable:

  py -3.13 tools/eval_nettopo.py --cache WORKDIR --cross-mm 5,25,100

Boards default to the DTU set plus the Bose sub board. ``--json OUT`` writes the
per-seed rows.

Predicting FEWER bridges is not the same as predicting them BETTER, and only the
second is worth anything. ``--truth correlation.json`` answers that question
against real FreeRouting runs, by replaying a study from
``tools/correlate_globalroute.py`` through both predictors:

  py -3.13 tools/eval_nettopo.py --truth WORKDIR/correlation.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute, nettopo, serialize  # noqa: E402

DTU = r"C:\Users\Mads2\DTU\4. Semester\Electrical Energy Systems\team\hardware\kicad\boards"
BOSE = (r"C:\Users\Mads2\Documents\Projects\Projects\Bose Sub Integration"
        r"\hardware\kicad\subxo.kicad_pcb")

DEFAULT_BOARDS = [
    os.path.join(DTU, "buck", "buck_v2", "buck_v2.kicad_pcb"),
    os.path.join(DTU, "c2000_feedback", "c2000_feedback.kicad_pcb"),
    os.path.join(DTU, "mppt_buck", "mppt_buck.kicad_pcb"),
    os.path.join(DTU, "current_sense", "current_sense.kicad_pcb"),
    os.path.join(DTU, "motor_power", "motor_power.kicad_pcb"),
    BOSE,
]


def placement(path, seed, cache_dir, fab):
    """The board placed at ``seed``, from cache when possible.

    The cache is what lets the expensive half (pcbnew + engine.place) run once
    under KiCad's Python and the cheap half (the actual comparison) run as often
    as tuning needs, under any Python.
    """
    tag = os.path.basename(path).replace(".kicad_pcb", "")
    blob = os.path.join(cache_dir, f"{tag}.seed{seed}.json") if cache_dir else None
    if blob and os.path.exists(blob):
        with open(blob, encoding="utf-8") as f:
            return serialize.board_from_dict(json.load(f)), 0.0

    from autoplace import engine, fabrication, kicad_io   # needs pcbnew
    side = os.path.splitext(path)[0] + ".autoplace.json"
    connectors = None
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            connectors = json.load(f).get("connectors")
    model, _pcb = kicad_io.load_board(path)
    t0 = time.perf_counter()
    engine.place(model, seed=seed, connectors=connectors,
                 margin=fabrication.margin_for(fab), track=fabrication.track_for(fab))
    secs = time.perf_counter() - t0
    if blob:
        os.makedirs(cache_dir, exist_ok=True)
        with open(blob, "w", encoding="utf-8") as f:
            json.dump(serialize.board_to_dict(model), f)
    return model, secs


def score(board, cross_mm):
    """Baseline vs. re-treed, both through globalroute's own scorers."""
    t0 = time.perf_counter()
    base = globalroute.net_segments(board)
    base_ms = (time.perf_counter() - t0) * 1000.0
    conf = globalroute.conflicts(base)

    # Timed on net_segments, not on retree: retree also scores the baseline and
    # solves two vertex covers, none of which a caller in the placer would pay.
    t0 = time.perf_counter()
    nettopo.net_segments(board, cross_mm=cross_mm)
    topo_ms = (time.perf_counter() - t0) * 1000.0
    r = nettopo.retree(board, cross_mm=cross_mm)
    # The baseline must be the same object the placer scores today; if these ever
    # disagree the comparison is meaningless, so it is asserted rather than hoped.
    assert r.conflicts_mst == len(conf), (r.conflicts_mst, len(conf))
    return {
        "segments": len(base),
        "nets_retreed": len(r.changed),
        "passes": r.passes,
        "conflicts_mst": r.conflicts_mst, "conflicts": r.conflicts,
        "bridges_mst": r.bridges_mst, "bridges": r.bridges,
        "wl_mst": r.wirelength_mst, "wl": r.wirelength,
        "mst_ms": round(base_ms, 2), "nettopo_ms": round(topo_ms, 2),
    }


def _pct(before, after):
    return 0.0 if not before else 100.0 * (before - after) / before


def against_truth(paths, cross_mm):
    """Do the re-treed numbers predict the ROUTER better, or only lower?

    Replays the placements stored by ``correlate_globalroute`` -- geometry and
    all -- through the MST predictor and the re-treed one, and ranks both
    against what FreeRouting actually needed. Rank correlation is the metric
    because the predictor's job is to order candidate placements; the bootstrap
    is there because a handful of routed seeds cannot resolve a 0.1 gap in rho
    and pretending otherwise is how a predictor gets adopted on noise.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import score_correlation as sc

    for path in paths:
        with open(path, encoding="utf-8") as f:
            study = json.load(f)
        rows = [r for r in study["rows"] if r.get("actual_bridges") is not None]
        if len(rows) < 4:
            print(f"{path}: only {len(rows)} routed seeds, skipping")
            continue
        # Same ground truth as score_correlation: a board that closes
        # single-sided costs zero bridges even though an unconditional stage-2
        # run still lands copper on the top layer.
        truth = [0 if r.get("drc_missing") == 0 else r["actual_bridges"]
                 for r in rows]
        pred = {k: [] for k in ("mst conflicts", "mst bridges",
                                "nettopo conflicts", "nettopo bridges")}
        for r in rows:
            board = serialize.board_from_dict(r["board"])
            base = globalroute.net_segments(board)
            conf = globalroute.conflicts(base)
            t = nettopo.retree(board, cross_mm=cross_mm)
            pred["mst conflicts"].append(len(conf))
            pred["mst bridges"].append(globalroute.min_bridges(len(base), conf))
            pred["nettopo conflicts"].append(t.conflicts)
            pred["nettopo bridges"].append(t.bridges)
        # A bridge count is a small integer and re-treeing makes it smaller, so
        # it ties more placements together than the MST version does -- which
        # costs rank correlation even when the number itself is more accurate.
        # These two ask whether a crossing tie-break wins that resolution back.
        for tag in ("mst", "nettopo"):
            pred[f"{tag} bridges + conf/100"] = [
                b + c / 100.0 for b, c in zip(pred[f"{tag} bridges"],
                                              pred[f"{tag} conflicts"])]

        print(f"\n{os.path.basename(os.path.dirname(path)) or path}  "
              f"({len(rows)} routed seeds, actual bridges "
              f"{min(truth)}..{max(truth)})")
        # rho and regret judge the RANKING (which placement to hand the user);
        # bias and MAE judge the NUMBER (how many wires the report promises they
        # will solder). A predictor can improve one and wreck the other, and the
        # two are used for different things, so both are printed.
        print(f"  {'predictor':<28} {'rho':>7} {'regret':>7} {'bias':>7} {'MAE':>6}")
        for name in ("mst conflicts", "nettopo conflicts",
                     "mst bridges", "nettopo bridges",
                     "mst bridges + conf/100", "nettopo bridges + conf/100"):
            p = pred[name]
            bias = sum(a - b for a, b in zip(p, truth)) / len(truth)
            mae = sum(abs(a - b) for a, b in zip(p, truth)) / len(truth)
            print(f"  {name:<28} {sc.spearman(p, truth):>7.3f} "
                  f"{sc.top1_regret(p, truth):>7.1f} {bias:>+7.2f} {mae:>6.2f}")
        for a, b in (("nettopo bridges", "mst bridges"),
                     ("nettopo conflicts", "mst conflicts"),
                     ("nettopo bridges + conf/100", "mst bridges + conf/100")):
            mean, lo, hi, better = sc.bootstrap_delta(pred[a], pred[b], truth)
            print(f"  {a} - {b}: rho delta {mean:+.3f} "
                  f"[{lo:+.3f}, {hi:+.3f}], P(better) = {better:.2f}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("boards", nargs="*", default=DEFAULT_BOARDS)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--cache", default="")
    ap.add_argument("--fab", default="cnc")
    ap.add_argument("--cross-mm", default=str(nettopo.CROSS_MM),
                    help="comma-separated exchange rates to compare")
    ap.add_argument("--truth", default="",
                    help="correlation.json studies to score against (comma list)")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    rates = [float(x) for x in args.cross_mm.split(",")]
    if args.truth:
        return against_truth(args.truth.split(","), rates[0])
    rows = []
    for rate in rates:
        if len(rates) > 1:
            print(f"\n===== cross_mm = {rate:g} =====")
        print(f"{'board':<16} {'parts':>5} {'segs':>5} {'retree':>6} "
              f"{'conflicts':>13} {'bridges':>11} {'wire mm':>15} {'ms':>7}")
        totals = []
        for path in args.boards:
            tag = os.path.basename(path).replace(".kicad_pcb", "")
            if not os.path.exists(path) and not args.cache:
                print(f"{tag:<16} (missing)")
                continue
            per_board = []
            for seed in range(args.seeds):
                try:
                    board, _s = placement(path, seed, args.cache, args.fab)
                except Exception as exc:                     # noqa: BLE001
                    print(f"{tag:<16} seed {seed}: {exc}")
                    break
                r = score(board, rate)
                r.update(board=tag, seed=seed, cross_mm=rate,
                         parts=len(board.components))
                rows.append(r)
                per_board.append(r)
            if not per_board:
                continue
            n = len(per_board)
            mean = {k: sum(x[k] for x in per_board) / n for k in
                    ("conflicts_mst", "conflicts", "bridges_mst", "bridges",
                     "wl_mst", "wl", "segments", "nets_retreed", "nettopo_ms")}
            totals.append((tag, mean, n))
            print(f"{tag:<16} {per_board[0]['parts']:>5} {mean['segments']:>5.0f} "
                  f"{mean['nets_retreed']:>6.1f} "
                  f"{mean['conflicts_mst']:>5.1f}->{mean['conflicts']:<5.1f}"
                  f"{_pct(mean['conflicts_mst'], mean['conflicts']):>+5.0f}% "
                  f"{mean['bridges_mst']:>4.1f}->{mean['bridges']:<4.1f}"
                  f"{_pct(mean['bridges_mst'], mean['bridges']):>+4.0f}% "
                  f"{mean['wl_mst']:>6.0f}->{mean['wl']:<6.0f}"
                  f"{-_pct(mean['wl_mst'], mean['wl']):>+4.0f}% "
                  f"{mean['nettopo_ms']:>7.1f}")

        if totals:
            # Unweighted mean over boards: a big board must not decide the verdict
            # for a corpus this small.
            mc = sum(_pct(m["conflicts_mst"], m["conflicts"]) for _t, m, _n in totals) / len(totals)
            mb = sum(_pct(m["bridges_mst"], m["bridges"]) for _t, m, _n in totals) / len(totals)
            mw = sum(-_pct(m["wl_mst"], m["wl"]) for _t, m, _n in totals) / len(totals)
            worse = sum(1 for r in rows if r["cross_mm"] == rate
                        and r["bridges"] > r["bridges_mst"])
            print(f"{'MEAN':<16} {'':>5} {'':>5} {'':>6} "
                  f"{'':>11}{mc:>+5.1f}% {'':>9}{mb:>+4.1f}% {'':>11}{mw:>+4.1f}%")
            print(f"  seeds where bridges got WORSE: {worse}/"
                  f"{sum(1 for r in rows if r['cross_mm'] == rate)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
