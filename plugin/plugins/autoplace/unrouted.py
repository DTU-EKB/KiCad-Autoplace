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
* **An endpoint is not always a pad.** KiCad reports the gap between the two
  items nearest it, and on a routed board those are routinely a ``Track`` or the
  ground ``Zone`` rather than a pad -- a half-routed net reads "Track [/VIN] on
  B.Cu" on one side and "PTH pad 1 [/VIN] of J4" on the other. Matching pads
  only dropped every such entry, so a board KiCad reported five missing
  connections on came back as **zero missing**, i.e. fully routed. ``completer``
  trusts this count over the ratsnest, so it would have called an unfinished
  board complete -- the one thing it is written not to do. Every reported gap is
  now counted; pad detail is still extracted where it exists, because that is
  what placing a wire bridge needs (``is_pad_pair``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

# "PTH pad 1 [/VIN] of J4", "SMD pad A2 [/CLK] of U7", "pad 2 [/GND] of R9"
_PAD_RE = re.compile(
    r"^(?:PTH|SMD|NPTH|Aperture)?\s*pad\s+(\S+)\s+\[(.*?)\]\s+of\s+(\S+)", re.I)
# Anything else KiCad names with a net: "Track [/VGND] on B.Cu, length 1.8 mm",
# "Zone [/GND] on B.Cu, priority 0", "Via [/V12] on B.Cu".
_KIND_RE = re.compile(r"^\s*([A-Za-z]+)\b[^\[]*\[(.*?)\]")


@dataclass(frozen=True)
class _Item:
    kind: str          # "pad" | "track" | "zone" | "via" | ... | "unknown"
    ref: str           # component ref for a pad; "" otherwise
    pad: str           # pad number for a pad; "" otherwise
    net: str           # "" when the description names no net
    x: float
    y: float


def _parse_item(it: dict) -> _Item:
    desc = (it.get("description") or "").strip()
    pos = it.get("pos") or {}
    x, y = float(pos.get("x", 0.0)), float(pos.get("y", 0.0))
    m = _PAD_RE.match(desc)
    if m:
        return _Item("pad", m.group(3), m.group(1), m.group(2), x, y)
    m = _KIND_RE.match(desc)
    if m:
        return _Item(m.group(1).lower(), "", "", m.group(2), x, y)
    return _Item("unknown", "", "", "", x, y)


@dataclass(frozen=True)
class MissingLink:
    """One connection the router did not make.

    ``ref``/``pad`` are filled only for endpoints that are pads; a track or zone
    endpoint leaves them empty but still carries its position and net.
    """
    net: str
    ref_a: str
    pad_a: str
    x_a: float
    y_a: float
    ref_b: str
    pad_b: str
    x_b: float
    y_b: float
    kind_a: str = "pad"
    kind_b: str = "pad"

    @property
    def length_mm(self) -> float:
        return ((self.x_a - self.x_b) ** 2 + (self.y_a - self.y_b) ** 2) ** 0.5

    @property
    def is_pad_pair(self) -> bool:
        """True when both ends are pads, i.e. a wire bridge can be placed on it."""
        return self.kind_a == "pad" and self.kind_b == "pad"

    @property
    def endpoints(self) -> tuple[str, str]:
        def name(kind, ref, pad, x, y):
            return f"{ref}.{pad}" if kind == "pad" else f"{kind}@({x:.2f},{y:.2f})"
        return (name(self.kind_a, self.ref_a, self.pad_a, self.x_a, self.y_a),
                name(self.kind_b, self.ref_b, self.pad_b, self.x_b, self.y_b))


def parse_drc_report(data: dict) -> list[MissingLink]:
    """Turn a kicad-cli DRC json document into MissingLinks (pure, testable).

    One link per reported gap. An entry is dropped only when it names fewer than
    two items -- never because an endpoint is a track, a zone, or a description
    shape we do not recognise. Under-counting here reads as "board finished".
    """
    out: list[MissingLink] = []
    for entry in data.get("unconnected_items", []):
        items = entry.get("items", [])
        if len(items) < 2:
            continue
        a, b = _parse_item(items[0]), _parse_item(items[1])
        out.append(MissingLink(a.net or b.net,
                               a.ref, a.pad, a.x, a.y,
                               b.ref, b.pad, b.x, b.y,
                               kind_a=a.kind, kind_b=b.kind))
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
