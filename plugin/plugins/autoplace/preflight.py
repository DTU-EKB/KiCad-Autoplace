"""Turn raw board facts into pre-run checklist rows (pure-Python, no pcbnew).

``cli.py preflight`` gathers the raw facts with pcbnew and calls ``evaluate``;
the desktop app renders the returned rows as green/amber reminders before a run.
"""
from __future__ import annotations


def evaluate(info: dict) -> list[dict]:
    """Raw preflight facts -> checklist rows.

    Each row: ``{key, label, status: "ok"|"warn", detail}``.
    """
    fp = info.get("footprints", 0)
    movable = info.get("movable", 0)
    locked = info.get("locked", 0)
    gnd = info.get("gnd_net")
    pours = info.get("pours", []) or []
    nets = list(dict.fromkeys(p.get("net", "") for p in pours))  # distinct, ordered

    return [
        {
            "key": "outline",
            "label": "Board outline",
            "status": "ok" if info.get("has_outline") else "warn",
            "detail": "Edge.Cuts present" if info.get("has_outline")
            else "no Edge.Cuts outline — placement needs a board boundary",
        },
        {
            "key": "footprints",
            "label": "Footprints",
            "status": "ok" if fp > 0 else "warn",
            "detail": (f"{fp} parts ({movable} movable, {locked} locked)"
                       if fp > 0 else "no footprints found"),
        },
        {
            "key": "ground",
            "label": "Ground net",
            "status": "ok" if gnd else "warn",
            "detail": f"{gnd}" if gnd else "no GND net found",
        },
        {
            "key": "pours",
            "label": "Copper pours",
            "status": "ok" if pours else "warn",
            "detail": (f"{len(pours)} pour(s): {', '.join(nets)}" if pours
                       else "no copper pours — GND/power will be routed as traces"),
        },
    ]
