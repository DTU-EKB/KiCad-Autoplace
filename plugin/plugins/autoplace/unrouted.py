"""Identify which connections a route failed to make -- not just how many.

The engine could previously only count failures (``kicad_io.unrouted_count`` ->
an integer). You cannot escalate on an integer: growing the board, re-placing a
neighbourhood, or dropping in a wire bridge all need to know *which* pads on
*which* net were left apart, and where they are.

KiCad 10 gives no usable python route to that -- ``GetRatsnestForNet`` returns an
opaque ``SwigPyObject`` with no iterable edges. The supported route is the DRC
report: ``kicad-cli pcb drc --format json`` lists every missing connection with
both pad descriptions and their board coordinates.

Two things that bite, both learned the hard way and both handled here:

* **Always pass ``--refill-zones``.** A copper pour whose fill was invalidated
  (moving a zone between layers does that) connects nothing, so every pad on the
  plane's net is reported unconnected. On a 38-part board that inflated 9 real
  failures to 20.
* **``kicad-cli`` ignores the project's ``.kicad_dru``.** Custom rules are simply
  not loaded -- verified by feeding it a blanket 0.1 mm clearance rule and seeing
  the violation count not budge. So CLI clearance violations include ones the
  GUI would suppress by design rule; ``clearance_violations`` reports them, but
  the caller must not treat them as authoritative. Missing *connections* are
  unaffected -- those are geometry, not rules.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

_ITEM_RE = re.compile(r"^(?:PTH|SMD|NPTH)?\s*pad\s+(\S+)\s+\[(.*?)\]\s+of\s+(\S+)")


@dataclass(frozen=True)
class MissingLink:
    """One connection the router did not make."""
    net: str
    ref_a: str
    pad_a: str
    x_a: float
    y_a: float
    ref_b: str
    pad_b: str
    x_b: float
    y_b: float

    @property
    def length_mm(self) -> float:
        return ((self.x_a - self.x_b) ** 2 + (self.y_a - self.y_b) ** 2) ** 0.5

    @property
    def endpoints(self) -> tuple[str, str]:
        return (f"{self.ref_a}.{self.pad_a}", f"{self.ref_b}.{self.pad_b}")


def parse_drc_report(data: dict) -> list[MissingLink]:
    """Turn a kicad-cli DRC json document into MissingLinks (pure, testable)."""
    out: list[MissingLink] = []
    for entry in data.get("unconnected_items", []):
        items = entry.get("items", [])
        if len(items) < 2:
            continue
        parsed = []
        for it in items[:2]:
            m = _ITEM_RE.match((it.get("description") or "").strip())
            pos = it.get("pos") or {}
            if not m:
                parsed = []
                break
            parsed.append((m.group(3), m.group(1), m.group(2),
                           float(pos.get("x", 0.0)), float(pos.get("y", 0.0))))
        if len(parsed) != 2:
            continue
        (ref_a, pad_a, net, xa, ya), (ref_b, pad_b, _n2, xb, yb) = parsed
        out.append(MissingLink(net, ref_a, pad_a, xa, ya, ref_b, pad_b, xb, yb))
    return out


def clearance_violations(data: dict) -> list[dict]:
    """Clearance entries from the same report. See the module note: kicad-cli
    does not load custom .kicad_dru rules, so intentional design-rule exceptions
    show up here as violations."""
    return [v for v in data.get("violations", []) if v.get("type") == "clearance"]


def find_kicad_cli() -> str | None:
    """Locate kicad-cli next to the pcbnew we are running under, else on PATH."""
    import shutil
    import sys
    exe = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
    guess = os.path.join(os.path.dirname(sys.executable), exe)
    if os.path.exists(guess):
        return guess
    return shutil.which(exe)


def analyse(pcb_path: str, cli: str | None = None, timeout: int = 300) -> dict:
    """Run DRC on a board and report what is missing.

    Returns {"missing": [MissingLink], "clearance": [...], "by_net": {net: n}}.
    Raises RuntimeError if kicad-cli is unavailable or the report is unreadable,
    so a caller can fall back to the plain unrouted count.
    """
    cli = cli or find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found; cannot analyse unrouted connections")
    fd, report = tempfile.mkstemp(suffix=".drc.json")
    os.close(fd)
    try:
        subprocess.run(
            [cli, "pcb", "drc", "--format", "json", "--refill-zones",
             "--severity-error", "-o", report, pcb_path],
            capture_output=True, text=True, timeout=timeout, check=False)
        with open(report, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"DRC analysis failed for {pcb_path}: {exc}") from exc
    finally:
        if os.path.exists(report):
            os.remove(report)

    missing = parse_drc_report(data)
    by_net: dict[str, int] = {}
    for link in missing:
        by_net[link.net] = by_net.get(link.net, 0) + 1
    return {"missing": missing, "clearance": clearance_violations(data),
            "by_net": by_net}
