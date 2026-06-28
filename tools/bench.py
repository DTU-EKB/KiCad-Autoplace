#!/usr/bin/env python3
"""Benchmark the placer across the DTU boards. Run with KiCad's Python.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/bench.py <kicad_repo_dir>
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))
from autoplace import engine, kicad_io  # noqa: E402

BOARDS = [
    "boards/buck/buck", "boards/boost/boost", "boards/mppt/mppt",
    "boards/c2000_feedback/c2000_feedback", "boards/rectifier/rectifier",
    "boards/current_sense/current_sense", "boards/drive_circuit/drive_circuit",
    "boards/mppt_buck/mppt_buck", "boards/feedback_circuit/feedback_circuit",
    "boards/motor_feedback/motor_feedback", "boards/motor_power/motor_power",
    "system/system",
]


def main(repo):
    out_dir = os.environ.get("BENCH_OUT", ".")
    print(f"{'board':16} {'parts':>5} {'blk':>3} {'HPWL b->a':>16} {'Δ%':>5} "
          f"{'cross b->a':>11} {'ovl':>3} {'s':>5}")
    for rel in BOARDS:
        path = os.path.join(repo, rel + ".kicad_pcb")
        name = os.path.basename(rel)
        if not os.path.exists(path):
            print(f"{name:16} (missing)")
            continue
        try:
            model, pcb = kicad_io.load_board(path)
            t0 = time.time()
            r = engine.place(model)
            dt = time.time() - t0
            kicad_io.apply_placement(model, pcb,
                                     os.path.join(out_dir, name + ".autoplaced.kicad_pcb"))
            b, a = r["before"], r["after"]
            print(f"{name:16} {a['components']:5d} {r['blocks']:3d} "
                  f"{b['hpwl_mm']:7.0f}->{a['hpwl_mm']:<7.0f} {r['hpwl_delta_pct']:5.0f} "
                  f"{b['crossings']:4d}->{a['crossings']:<4d} {r['overlaps_remaining']:3d} "
                  f"{dt:5.1f}")
        except Exception as exc:
            print(f"{name:16} ERROR: {exc}")


if __name__ == "__main__":
    main(sys.argv[1])
