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


def cmd_place(args):
    in_path = args[0]
    out_path = args[1] if len(args) > 1 else _default_out(in_path)
    seed = int(args[2]) if len(args) > 2 else 0

    strategy = os.environ.get("STRATEGY", "auto")
    model, pcb = kicad_io.load_board(in_path)
    report = engine.place(model, seed=seed, strategy=strategy)
    kicad_io.apply_placement(model, pcb, out_path)

    report["input"] = in_path
    report["output"] = out_path
    print(json.dumps(report, indent=2))
    return 0


def cmd_metrics(args):
    """Just print metrics for a board, no placement (baseline measurement)."""
    from autoplace import metrics
    model, _ = kicad_io.load_board(args[0])
    print(json.dumps(metrics.summary(model), indent=2))
    return 0


def _default_out(in_path):
    stem, _ = os.path.splitext(in_path)
    return stem + ".autoplaced.kicad_pcb"


def main(argv):
    if len(argv) < 2 or argv[1] not in ("place", "metrics"):
        print(__doc__)
        return 2
    return {"place": cmd_place, "metrics": cmd_metrics}[argv[1]](argv[2:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
