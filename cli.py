#!/usr/bin/env python3
"""Dev CLI -- run the placement engine on a .kicad_pcb and report metrics.

Run with KiCad's Python (it needs pcbnew):
  & "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" cli.py place IN.kicad_pcb [OUT.kicad_pcb]

Never overwrites the input unless an explicit OUT path equal to IN is given;
by default it writes <stem>.autoplaced.kicad_pcb next to the output dir.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "plugin", "plugins"))

from autoplace import engine, kicad_io  # noqa: E402

DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")


def _fab():
    """Fabrication profile name from the FAB env var (default 'cnc')."""
    return os.environ.get("FAB", "cnc")


def _apply_fab(out_path, fab):
    """Write the fabrication's clearance/track into the output .kicad_pro."""
    from autoplace import fabrication
    out_pro = os.path.splitext(out_path)[0] + ".kicad_pro"
    return fabrication.apply_to_project(out_pro, fab)


def _read_connectors(in_path):
    """Read the connector ref list from <stem>.autoplace.json, or None."""
    side = os.path.splitext(in_path)[0] + ".autoplace.json"
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                return json.load(f).get("connectors")
        except Exception:
            return None
    return None


def cmd_place(args):
    in_path = args[0]
    out_path = args[1] if len(args) > 1 else _default_out(in_path)
    seed = int(args[2]) if len(args) > 2 else 0

    # Streaming mode (set by the desktop app): emit newline-delimited JSON --
    # one compact object per line -- so the host can show a live progress bar.
    #   {"type":"progress","stage":"anneal","percent":45}
    #   {"type":"result", ...full report... }
    # Default (human / no env): a single pretty-printed report, as before.
    stream = os.environ.get("AUTOPLACE_STREAM") == "1"

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    progress = None
    if stream:
        def progress(stage, frac):
            emit({"type": "progress", "stage": stage,
                  "percent": round(100.0 * frac, 1)})

    from autoplace import fabrication
    strategy = os.environ.get("STRATEGY", "auto")
    fab = _fab()
    if stream:
        emit({"type": "progress", "stage": "load", "percent": 0.0})
    model, pcb = kicad_io.load_board(in_path)
    connectors = _read_connectors(in_path)
    report = engine.place(model, seed=seed, strategy=strategy,
                          connectors=connectors, margin=fabrication.margin_for(fab),
                          progress=progress)
    kicad_io.apply_placement(model, pcb, out_path)
    # carry the project file so net-class (track/clearance) rules survive: the
    # router reads widths from <stem>.kicad_pro, not the .kicad_pcb.
    report["project_copied"] = kicad_io.copy_project(in_path, out_path)
    # set this fabrication's clearance/track in the copied project (routing + DRC)
    report["fab"] = fab
    report["fab_applied"] = _apply_fab(out_path, fab)

    report["input"] = in_path
    report["output"] = out_path
    if stream:
        report["type"] = "result"
        emit(report)
    else:
        print(json.dumps(report, indent=2))
    return 0


def cmd_place_multi(args):
    """Run N seeds and stream one candidate (geometry + metrics) per seed.

    Preview-only: writes no board/project files. The app renders the candidates
    as a gallery; picking one re-runs ``place`` with that seed (deterministic, so
    the saved board matches the previewed thumbnail).
    """
    from autoplace import fabrication, multiseed
    in_path = args[0]
    count = int(args[1]) if len(args) > 1 else 6

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    strategy = os.environ.get("STRATEGY", "auto")
    fab = _fab()
    emit({"type": "progress", "stage": "load", "percent": 0.0})
    model, _ = kicad_io.load_board(in_path)
    connectors = _read_connectors(in_path)
    for i, cand in enumerate(multiseed.run_candidates(
            model, count, strategy=strategy, connectors=connectors,
            margin=fabrication.margin_for(fab))):
        cand["index"] = i
        cand["count"] = count
        emit({"type": "progress", "stage": "place",
              "percent": round(100.0 * (i + 1) / count, 1)})
        emit(cand)
    emit({"type": "done", "count": count})
    return 0


def cmd_finalize(args):
    """Promote a finished board to the project's main .kicad_pcb and sweep temps.

      cli.py finalize FINISHED.kicad_pcb PROJECT.kicad_pcb [--commit]

    Dry-run by default: prints the plan (promote target, backup path, files that
    would be deleted) and touches nothing. With --commit it performs the promote
    + backup + delete. Pure filesystem work -- no pcbnew needed.
    """
    from autoplace import finalize as fin
    commit = "--commit" in args
    paths = [a for a in args if not a.startswith("--")]
    finished, project = paths[0], paths[1]

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    if not os.path.exists(finished):
        emit({"type": "error", "error": f"finished board not found: {finished}"})
        return 1
    if not os.path.exists(project):
        emit({"type": "error", "error": f"project board not found: {project}"})
        return 1

    if not commit:
        directory = os.path.dirname(os.path.abspath(project))
        base = os.path.basename(project)
        if base.endswith(".kicad_pcb"):
            base = base[: -len(".kicad_pcb")]
        same = os.path.abspath(finished) == os.path.abspath(project)
        emit({
            "type": "plan",
            "promote": None if same else {"from": finished, "to": project},
            "backup": None if same else project + ".bak",
            "delete": sorted(fin.classify_temp_files(os.listdir(directory), base)),
        })
        return 0

    res = fin.finalize_project(finished, project)
    res["type"] = "result"
    emit(res)
    return 0


def cmd_preflight(args):
    """Inspect a board and emit pre-run checklist rows for the desktop app."""
    import pcbnew
    from autoplace import preflight
    in_path = args[0]
    pcb = pcbnew.LoadBoard(in_path)

    fps = list(pcb.GetFootprints())
    locked = sum(1 for f in fps if f.IsLocked())
    has_outline = any(d.GetLayer() == pcbnew.Edge_Cuts for d in pcb.GetDrawings())
    gnd = kicad_io.find_gnd_net(pcb)
    pours = []
    for i in range(pcb.GetAreaCount()):
        z = pcb.GetArea(i)
        if z.IsOnLayer(pcbnew.B_Cu) or z.IsOnLayer(pcbnew.F_Cu):
            pours.append({"layer": pcb.GetLayerName(z.GetLayer()),
                          "net": z.GetNetname()})
    info = {
        "has_outline": has_outline,
        "footprints": len(fps), "movable": len(fps) - locked, "locked": locked,
        "gnd_net": gnd.GetNetname() if gnd is not None else None,
        "pours": pours,
    }
    sys.stdout.write(json.dumps(
        {"type": "preflight", "rows": preflight.evaluate(info), "info": info}) + "\n")
    return 0


def cmd_metrics(args):
    """Just print metrics for a board, no placement (baseline measurement)."""
    from autoplace import metrics
    model, _ = kicad_io.load_board(args[0])
    print(json.dumps(metrics.summary(model), indent=2))
    return 0


def cmd_dump(args):
    """Emit board geometry as one JSON line for the desktop canvas."""
    from autoplace import blocks, serialize
    model, _ = kicad_io.load_board(args[0])
    blocks.detect_blocks(model)
    sys.stdout.write(json.dumps(serialize.board_to_dict(model)) + "\n")
    return 0


def cmd_refine(args):
    """Route-driven refinement: route -> re-anneal congested spots -> repeat (keep best).

    Refines the EXISTING placement in the input board (it does not re-place from
    scratch). Connectors named in the sidecar keep their board edge: their edge
    is inferred from their current position so the re-anneal slides them along it
    rather than pulling them inward. Needs KiCad's python + Java + FreeRouting.
    """
    from autoplace import edge as edge_mod
    from autoplace import fabrication
    from autoplace import refine as refine_mod
    in_path = args[0]
    out_path = args[1] if len(args) > 1 else _refine_out(in_path)
    seed = int(args[2]) if len(args) > 2 else 0
    fab = _fab()
    jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
    passes = int(os.environ.get("REFINE_PASSES", "20"))
    budget = int(os.environ.get("REFINE_BUDGET", "8"))
    sides = int(os.environ.get("SIDES", "2"))         # 1 = single-sided (B.Cu)
    stream = os.environ.get("AUTOPLACE_STREAM") == "1"

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    progress = None
    if stream:
        def progress(it, pct, best_pct):
            emit({"type": "iteration", "iter": it, "budget": budget,
                  "routed_pct": round(pct, 1), "best_pct": round(best_pct, 1)})

    model, pcb = kicad_io.load_board(in_path)
    # keep flagged connectors pinned to the edge they already sit on
    for ref in (_read_connectors(in_path) or []):
        c = model.components.get(ref)
        if c is not None and not c.locked:
            c.is_connector = True
            c.edge = edge_mod.nearest_edge(model, c.x, c.y)
    # net-class widths must sit next to the file route_once reloads; then stamp
    # this fabrication's clearance/track so the route uses the right rules
    kicad_io.copy_project(in_path, out_path)
    _apply_fab(out_path, fab)
    r = refine_mod.refine(model, pcb, jar=jar, work_pcb=out_path, passes=passes,
                          seed=seed, budget=budget,
                          place_margin=fabrication.margin_for(fab),
                          sides=sides, progress=progress)
    kicad_io.apply_placement(model, pcb, out_path)        # write the best placement
    stem = os.path.splitext(out_path)[0]
    report = {"input": in_path, "output": out_path,
              "routed_pct": round(r["best_pct"], 1), "iterations": r["iterations"],
              "history": r["history"], "routed_output": stem + ".routed.kicad_pcb"}
    if stream:
        report["type"] = "result"
        emit(report)
    else:
        print(json.dumps(report, indent=2))
    return 0


def _default_out(in_path):
    stem, _ = os.path.splitext(in_path)
    return stem + ".autoplaced.kicad_pcb"


def _refine_out(in_path):
    stem, _ = os.path.splitext(in_path)
    return stem + ".refined.kicad_pcb"


def main(argv):
    cmds = {"place": cmd_place, "place-multi": cmd_place_multi,
            "metrics": cmd_metrics, "dump": cmd_dump, "refine": cmd_refine,
            "finalize": cmd_finalize, "preflight": cmd_preflight}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    return cmds[argv[1]](argv[2:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
