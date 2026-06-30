"""Remove routing (tracks/vias/arcs) from a .kicad_pcb textually.

Pure-Python, no pcbnew, no sexpdata. In-process ``pcbnew`` track removal
access-violates on KiCad (the laser pipeline learned this the hard way), so
single-sided routing strips the old routing from the board *file* before loading
it, giving a clean slate to re-route on one layer. Footprints, pads, zones, and
the net table are preserved -- only top-level ``(segment …)`` / ``(via …)`` /
``(arc …)`` blocks are dropped.
"""
from __future__ import annotations


def _end_of_sexp(text: str, start: int) -> int:
    """Index just past the ``)`` matching the ``(`` at ``text[start]``."""
    depth = 0
    in_str = False
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == '"' and text[i - 1] != "\\":
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def strip_tracks(text: str, kinds=("segment", "via", "arc")) -> tuple[str, int]:
    """Return ``(stripped_text, removed_count)`` with routing blocks removed.

    Removes every ``(segment …)`` / ``(via …)`` / ``(arc …)`` s-expression
    (balanced, quote-aware), plus the indentation + newline it leaves behind.
    """
    out = []
    i = 0
    n = len(text)
    removed = 0
    while i < n:
        if text[i] == "(":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            k = j
            while k < n and not text[k].isspace() and text[k] not in "()":
                k += 1
            if text[j:k] in kinds:
                i = _end_of_sexp(text, i)
                removed += 1
                while i < n and text[i] in " \t":
                    i += 1
                if i < n and text[i] == "\n":
                    i += 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out), removed
