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
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))
from autoplace import routing  # noqa: E402

DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")


def route_check(in_pcb, jar=DEFAULT_JAR, passes=10):
    try:
        r = routing.route_once(in_pcb, jar, passes)
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)
    print(f"{os.path.basename(in_pcb)}")
    print(f"  connections : {r['total']}")
    print(f"  routed      : {r['routed']}  ({r['pct']:.1f}%)")
    print(f"  unrouted    : {r['unrouted']}")
    print(f"  freerouting : {r['seconds']:.0f}s, {passes} passes")
    print(f"  -> {r['routed_pcb']}")
    return r


if __name__ == "__main__":
    args = sys.argv[1:]
    pcb = args[0]
    jar = args[1] if len(args) > 1 else DEFAULT_JAR
    passes = int(args[2]) if len(args) > 2 else 10
    route_check(pcb, jar, passes)
