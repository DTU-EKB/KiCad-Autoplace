#!/usr/bin/env python3
"""Route a placed board with FreeRouting and report completion -- the ground-truth
routability test for a placement. Run with KiCad's Python.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/route_check.py \
        placed.kicad_pcb [jar] [passes]

Exports Specctra DSN, runs FreeRouting head-less, imports the SES back, and counts
the remaining unrouted connections via pcbnew connectivity. Writes
``<stem>.routed.kicad_pcb`` next to the input.
"""
import os
import subprocess
import sys
import time

import pcbnew

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))
from autoplace.kicad_io import unrouted_count as _unrouted  # noqa: E402

DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")


def route_check(in_pcb, jar=DEFAULT_JAR, passes=10):
    board = pcbnew.LoadBoard(in_pcb)
    if board is None:
        raise SystemExit(f"could not load {in_pcb}")
    total = _unrouted(board)                      # ratsnest = connections to make
    stem = os.path.splitext(in_pcb)[0]
    dsn, ses = stem + ".dsn", stem + ".ses"

    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise SystemExit("DSN export failed")
    if os.path.exists(ses):
        os.remove(ses)

    t0 = time.time()
    proc = subprocess.run(
        ["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes)],
        capture_output=True, text=True, timeout=1800)
    dt = time.time() - t0

    # A 0-byte SES counts as failure too: at high pass counts FreeRouting can
    # bail after auto-routing and write an empty file. Importing it would
    # silently report 0 routed -- so treat empty-or-missing as an error and
    # surface FreeRouting's own output (which the harness otherwise swallows).
    if not os.path.exists(ses) or os.path.getsize(ses) == 0:
        print("FreeRouting produced no usable SES "
              f"(exit {proc.returncode}). Tail of output:")
        print((proc.stdout or "")[-1200:])
        print((proc.stderr or "")[-400:])
        raise SystemExit(1)

    pcbnew.ImportSpecctraSES(board, ses)
    left = _unrouted(board)
    routed = total - left
    out = stem + ".routed.kicad_pcb"
    pcbnew.SaveBoard(out, board)

    pct = 100.0 * routed / total if total else 100.0
    print(f"{os.path.basename(in_pcb)}")
    print(f"  connections : {total}")
    print(f"  routed      : {routed}  ({pct:.1f}%)")
    print(f"  unrouted    : {left}")
    print(f"  freerouting : {dt:.0f}s, {passes} passes")
    print(f"  -> {out}")
    return {"total": total, "routed": routed, "unrouted": left, "pct": pct}


if __name__ == "__main__":
    args = sys.argv[1:]
    pcb = args[0]
    jar = args[1] if len(args) > 1 else DEFAULT_JAR
    passes = int(args[2]) if len(args) > 2 else 10
    route_check(pcb, jar, passes)
