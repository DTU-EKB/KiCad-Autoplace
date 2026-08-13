"""Score predictors against the routed ground truth from correlate_globalroute.

Pure Python (no pcbnew, no numpy): it replays the placements saved in
``correlation.json`` through any predictor variant and reports how well each one
ranks them against what FreeRouting actually did.

  py -3.13 tools/score_correlation.py WORKDIR/correlation.json

Three numbers per predictor, and the third is the one that matters:

* **Spearman rho** -- rank agreement. Placement selection is a ranking problem;
  absolute calibration is irrelevant as long as the order is right.
* **Pearson r** -- linear agreement, for reference.
* **Top-1 regret** -- pick the placement this predictor likes best, and count how
  many more bridges it costs than the truly best placement in the set. This is
  the decision the predictor is actually used to make, so a predictor with a
  decent rho but a bad regret is still a bad predictor.

A predictor that does not beat the incumbent proxy (``crossings``) has no
business in the cost function.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import globalroute, serialize  # noqa: E402


def _ranks(xs):
    """Fractional ranks (ties share their mean rank)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        mean = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mean
        i = j + 1
    return ranks


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def spearman(xs, ys):
    return pearson(_ranks(xs), _ranks(ys))


def bootstrap_delta(pred_a, pred_b, truth, rounds=4000, seed=20260813):
    """Is predictor A's rank correlation really better than B's, or is it noise?

    Resamples the placements with replacement and recomputes both correlations
    on each resample. Returns (mean delta, 5th pct, 95th pct, P(A better)).
    Comparing two rho values head-to-head on a couple of dozen samples is
    exactly the situation where a 0.1 gap means nothing, so the gap gets a
    confidence interval rather than a ranking.
    """
    import random
    rng = random.Random(seed)
    n = len(truth)
    deltas = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        t = [truth[i] for i in idx]
        if max(t) == min(t):
            continue
        a = spearman([pred_a[i] for i in idx], t)
        b = spearman([pred_b[i] for i in idx], t)
        if a == a and b == b:
            deltas.append(a - b)
    if not deltas:
        return (float("nan"),) * 3 + (float("nan"),)
    deltas.sort()
    mean = sum(deltas) / len(deltas)
    lo = deltas[int(0.05 * len(deltas))]
    hi = deltas[int(0.95 * len(deltas)) - 1]
    better = sum(1 for d in deltas if d > 0) / len(deltas)
    return mean, lo, hi, better


def spearman_ci(pred, truth, rounds=4000, seed=20260813):
    """Bootstrap 90% interval for one predictor's rank correlation."""
    import random
    rng = random.Random(seed)
    n = len(truth)
    vals = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        t = [truth[i] for i in idx]
        if max(t) == min(t):
            continue
        r = spearman([pred[i] for i in idx], t)
        if r == r:
            vals.append(r)
    if not vals:
        return float("nan"), float("nan")
    vals.sort()
    return vals[int(0.05 * len(vals))], vals[int(0.95 * len(vals)) - 1]


def top1_regret(pred, actual):
    """Extra bridges paid by trusting this predictor's favourite placement.

    Ties in the predictor are broken pessimistically (worst actual among the
    tied best), because a predictor that cannot separate two placements gives
    no guarantee about which one you get.
    """
    best_pred = min(pred)
    tied = [actual[i] for i in range(len(pred)) if pred[i] == best_pred]
    return max(tied) - min(actual)


def _endpoint_slack_conflicts(segs, slack_mm):
    """Crossings that a small detour cannot obviously fix.

    The straight-line model is pessimistic: the router bends wires, so two
    segments crossing near one of their endpoints often route around each other
    for a few millimetres of extra copper. Crossings nearer than ``slack_mm`` to
    an endpoint of either segment are therefore discounted.
    """
    out = []
    for i, j in globalroute.conflicts(segs):
        s, t = segs[i], segs[j]
        p = _intersection(s, t)
        if p is None:
            continue
        near = min(
            math.hypot(p[0] - ax, p[1] - ay)
            for ax, ay in ((s.ax, s.ay), (s.bx, s.by), (t.ax, t.ay), (t.bx, t.by))
        )
        if near >= slack_mm:
            out.append((i, j))
    return out


def _intersection(s, t):
    d1x, d1y = s.bx - s.ax, s.by - s.ay
    d2x, d2y = t.bx - t.ax, t.by - t.ay
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return None
    u = ((t.ax - s.ax) * d2y - (t.ay - s.ay) * d2x) / den
    return (s.ax + u * d1x, s.ay + u * d1y)


# Predictor variants. Each takes a rebuilt Board plus the recorded row and
# returns a scalar where LOWER is predicted-better. The incumbent proxy is in
# the list on purpose: a new predictor that cannot beat it is not worth wiring in.
def _variants(board, row, track, clearance):
    est = globalroute.analyse(board, track=track, clearance=clearance)
    segs = globalroute.net_segments(board)
    n = len(segs)
    v = {
        "crossings (incumbent proxy)": row["crossings"],
        "hpwl_mm": row["hpwl_mm"],
        "pinch_fraction": row["pinch_fraction"],
        "whitespace_connectivity": -row["whitespace_connectivity"],  # higher better
        "gr_conflicts": est.conflicts,
        "gr_bridges": est.bridges,
        "gr_overflow": est.overflow,
        "gr_peak": est.peak,
        "gr_wirelength": est.wirelength,
        "gr_bridges + overflow/50": est.bridges + est.overflow / 50.0,
        "gr_bridges + wirelen/500": est.bridges + est.wirelength / 500.0,
    }
    # Detour-tolerant variants: discount crossings close to a segment end.
    for slack in (2.0, 5.0, 8.0):
        c = _endpoint_slack_conflicts(segs, slack)
        v[f"conflicts slack {slack:g}mm"] = len(c)
        v[f"bridges slack {slack:g}mm"] = globalroute.min_bridges(n, c)

    # The incumbent proxy differs from gr_* in TWO ways at once, so isolate them:
    #   (a) metrics.crossings drops every power-ish net by NAME (+12V, VIN, VCC...),
    #       while globalroute drops only the nets a pour actually connects;
    #   (b) gr_bridges is a vertex cover, which collapses many distinguishable
    #       placements onto the same small integer.
    # Without this split a difference in rho cannot be attributed to either.
    # The terms the shipped ranking key is actually built from, so the key can be
    # judged against the alternatives rather than assumed good.
    from autoplace import metrics as _m
    v["sheet_spread_score"] = row["sheet_spread_score"]
    v["decap_proximity"] = _m.decap_proximity(board)
    v["overlaps"] = row["overlaps"]

    poured = globalroute.plane_nets(board)
    nopower = poured | {nt for nt in board.nets() if _m._is_power(nt)}
    psegs = globalroute.net_segments(board, planes=nopower)
    pconf = globalroute.conflicts(psegs)
    v["gr_conflicts (power excluded)"] = len(pconf)
    v["gr_bridges (power excluded)"] = globalroute.min_bridges(len(psegs), pconf)
    return v


INCUMBENT = "crossings (incumbent proxy)"

_SHORT = {"crossings (incumbent proxy)": "cross", "hpwl_mm": "hpwl",
          "gr_bridges (power excluded)": "gr_bridges_pe", "gr_overflow": "overflow"}


def _short(name):
    return _SHORT.get(name, name)

TRUTHS = ("bridges_required", "drc_missing", "raw_s2_bridges")


def _prepare(rows, fab):
    """Predictor columns + truth columns for one board's placements."""
    from autoplace import fabrication
    track, clearance = fabrication.track_for(fab), fabrication.margin_for(fab)
    rows = [r for r in rows if r.get("actual_bridges") is not None]
    # Ground truth is the bridges the REAL pipeline would pay, not the ones an
    # unconditional stage-2 run happens to emit. ``completion.next_action``
    # returns "done" as soon as DRC reports zero missing, so a board that closes
    # single-sided costs zero bridges -- yet running stage 2 on it anyway still
    # lands copper on the top layer, because FreeRouting works from the ratsnest
    # rather than from DRC. Scoring against that would train on router noise.
    for r in rows:
        r["bridges_required"] = 0 if r.get("drc_missing") == 0 else r["actual_bridges"]
    preds = {}
    for r in rows:
        b = serialize.board_from_dict(r["board"])
        for name, val in _variants(b, r, track, clearance).items():
            preds.setdefault(name, []).append(val)

    # Rank-sum combinations. The question the project actually poses is not
    # "does the global router beat the proxies" but "does it add anything they
    # do not already say". Summing RANKS rather than raw values keeps the
    # combination scale-free, so neither term wins by having the bigger numbers.
    def combo(*names):
        rs = [_ranks(preds[n]) for n in names]
        return [sum(col) for col in zip(*rs)]

    for a, b_ in (("crossings (incumbent proxy)", "gr_bridges (power excluded)"),
                  ("crossings (incumbent proxy)", "gr_overflow"),
                  ("crossings (incumbent proxy)", "hpwl_mm"),
                  ("hpwl_mm", "gr_bridges (power excluded)")):
        preds[f"rank[{_short(a)}]+rank[{_short(b_)}]"] = combo(a, b_)
    preds["rank[cross]+rank[hpwl]+rank[gr_bridges_pe]"] = combo(
        "crossings (incumbent proxy)", "hpwl_mm", "gr_bridges (power excluded)")

    # The SHIPPED ranking key, scored as a whole: sort the placements by it and
    # use the resulting position. This is what the gallery actually hands the
    # user, so it is the thing that has to be beaten -- not any single metric.
    from autoplace import ranking as _rank
    cands = [{"overlaps": preds["overlaps"][i],
              "sheet_spread_score": preds["sheet_spread_score"][i],
              "pinch_fraction": preds["pinch_fraction"][i],
              "decap_proximity": preds["decap_proximity"][i],
              "hpwl_mm": preds["hpwl_mm"][i],
              "crossings": preds["crossings (incumbent proxy)"][i],
              "seed": i} for i in range(len(rows))]
    order = {c["seed"]: pos for pos, c in enumerate(_rank.pre_rank(cands))}
    preds["LIVE ranking.pre_rank"] = [order[i] for i in range(len(rows))]
    # The key as it shipped before 2026-08-13, kept as the baseline to beat.
    old = sorted(cands, key=lambda c: (
        c["overlaps"], round(c["sheet_spread_score"], 3),
        round(c["pinch_fraction"], 3), round(c["decap_proximity"] * 2) / 2,
        round(c["hpwl_mm"], 2), c["seed"]))
    order = {c["seed"]: pos for pos, c in enumerate(old)}
    preds["OLD ranking key (pre-fix)"] = [order[i] for i in range(len(rows))]

    # Candidate replacements, scored the same way. The shipped key is
    # lexicographic, so whichever term discriminates FIRST decides the whole
    # order -- and pinch_fraction/decap_proximity are near-continuous, so they
    # almost always discriminate before hpwl is ever reached.
    n = len(rows)
    cross, hp = preds["crossings (incumbent proxy)"], preds["hpwl_mm"]
    ovl = preds["overlaps"]
    lex = sorted(range(n), key=lambda i: (ovl[i], cross[i], round(hp[i], 1), i))
    pos = {s: p for p, s in enumerate(lex)}
    preds["PROPOSED lex (overlaps,cross,hpwl)"] = [pos[i] for i in range(n)]

    rc, rh = _ranks(cross), _ranks(hp)
    ranksum = sorted(range(n), key=lambda i: (ovl[i], rc[i] + rh[i], i))
    pos = {s: p for p, s in enumerate(ranksum)}
    preds["PROPOSED ranksum(cross,hpwl)"] = [pos[i] for i in range(n)]

    rb = _ranks(preds["gr_bridges (power excluded)"])
    rs3 = sorted(range(n), key=lambda i: (ovl[i], rc[i] + rh[i] + rb[i], i))
    pos = {s: p for p, s in enumerate(rs3)}
    preds["PROPOSED ranksum(cross,hpwl,gr_bridges)"] = [pos[i] for i in range(n)]
    truths = {
        "bridges_required": [r["bridges_required"] for r in rows],
        "drc_missing": [r.get("drc_missing") for r in rows],
        "raw_s2_bridges": [r["actual_bridges"] for r in rows],
    }
    if any(v is None for v in truths["drc_missing"]):
        truths["drc_missing"] = None
    return rows, preds, truths


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    # Group by BOARD. Pooling placements from different boards would confound the
    # correlation with board-level differences -- a 58-part board simply needs
    # more bridges than a 31-part one, so a pooled rho would mostly measure
    # "which board is this", not "which placement is better". Correlations are
    # therefore computed per board and aggregated across them.
    boards: dict[str, dict] = {}
    for path in argv[1:]:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        key = os.path.basename(d.get("board", path)).replace(".kicad_pcb", "")
        slot = boards.setdefault(key, {"fab": d.get("fab", "cnc"), "rows": {}})
        for r in d["rows"]:
            slot["rows"][r["seed"]] = r

    per_board = {}
    for name in sorted(boards):
        slot = boards[name]
        rows = [slot["rows"][s] for s in sorted(slot["rows"])]
        rows, preds, truths = _prepare(rows, slot["fab"])
        if len(rows) < 4:
            print(f"{name}: only {len(rows)} usable placements -- skipped")
            continue
        per_board[name] = (rows, preds, truths)

    if not per_board:
        print("no usable boards")
        return 1

    for name, (rows, preds, truths) in per_board.items():
        print(f"################ {name}  ({len(rows)} placements) ################")
        for r in rows:
            print(f"  seed {r['seed']:2d}  gr_bridges={r['gr_bridges']:>3} "
                  f"crossings={r['crossings']:>3} | stage1={r['stage1_pct']:>5.1f}% "
                  f"drc_missing={r.get('drc_missing')!s:>3} "
                  f"REQUIRED_BRIDGES={r['bridges_required']:>3}")
        print()
        for tname in TRUTHS:
            tvals = truths[tname]
            if tvals is None:
                continue
            if max(tvals) == min(tvals):
                print(f"  vs {tname}: constant ({tvals[0]}) -- cannot discriminate\n")
                continue
            print(f"  vs {tname}  (range {min(tvals)}..{max(tvals)})")
            scored = sorted(
                ((spearman(v, tvals), p) for p, v in preds.items()),
                key=lambda t: (-9 if t[0] != t[0] else t[0]), reverse=True)
            for rho, p in scored[:6]:
                lo, hi = spearman_ci(preds[p], tvals)
                mark = "  <-- incumbent" if p == INCUMBENT else ""
                print(f"    {p:<30} rho={rho:>6.3f}  90%CI[{lo:>5.2f},{hi:>5.2f}]"
                      f"  regret={top1_regret(preds[p], tvals)}{mark}")
            if scored[0][1] != INCUMBENT:
                mean, lo, hi, better = bootstrap_delta(
                    preds[scored[0][1]], preds[INCUMBENT], tvals)
                print(f"    head-to-head {scored[0][1]} - incumbent: "
                      f"{mean:+.3f} [{lo:+.3f},{hi:+.3f}] P(better)={better:.0%}")
            print()

    # ---- aggregate across boards -----------------------------------------
    print("=" * 74)
    print("ACROSS BOARDS -- mean per-board Spearman (each board weighted equally)")
    print("=" * 74)
    for tname in TRUTHS:
        usable = {n: (p, t[tname]) for n, (_, p, t) in per_board.items()
                  if t[tname] is not None and max(t[tname]) != min(t[tname])}
        if not usable:
            continue
        print(f"\nvs {tname}   ({len(usable)} boards: {', '.join(sorted(usable))})")
        names = sorted(next(iter(usable.values()))[0])
        table = []
        for p in names:
            rhos = [spearman(pr[p], tv) for pr, tv in usable.values()]
            rhos = [r for r in rhos if r == r]
            if not rhos:
                continue
            table.append((sum(rhos) / len(rhos), p, rhos))
        table.sort(reverse=True)
        print(f"    {'predictor':<30} {'mean rho':>9}  per-board")
        for mean, p, rhos in table:
            mark = "  <-- incumbent" if p == INCUMBENT else ""
            print(f"    {p:<30} {mean:>9.3f}  "
                  f"[{', '.join(f'{r:+.2f}' for r in rhos)}]{mark}")
        # Does the best predictor beat the incumbent on every board?
        best = table[0][1]
        if best != INCUMBENT:
            wins = sum(1 for pr, tv in usable.values()
                       if spearman(pr[best], tv) > spearman(pr[INCUMBENT], tv))
            print(f"    '{best}' beats the incumbent on {wins}/{len(usable)} boards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
