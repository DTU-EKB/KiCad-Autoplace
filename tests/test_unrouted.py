"""Parsing of kicad-cli DRC reports into actionable missing connections.

The fixtures below are the real shape emitted by KiCad 10's
``kicad-cli pcb drc --format json`` (captured from a 38-part single-sided board),
trimmed to the fields the engine reads.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "plugins"))

from autoplace.unrouted import (MissingLink, clearance_violations,  # noqa: E402
                                parse_drc_report)


def _report():
    return {
        "unconnected_items": [
            {"description": "Missing connection between items",
             "items": [
                 {"description": "PTH pad 2 [/GND] of C10", "pos": {"x": 93.0, "y": 77.13}},
                 {"description": "PTH pad 2 [/GND] of R9", "pos": {"x": 102.19, "y": 89.5}},
             ]},
            {"description": "Missing connection between items",
             "items": [
                 {"description": "PTH pad 1 [/OUT1] of R3", "pos": {"x": 10.0, "y": 20.0}},
                 {"description": "PTH pad 2 [/OUT1] of U1", "pos": {"x": 13.0, "y": 24.0}},
             ]},
        ],
        "violations": [
            {"type": "clearance", "description": "Clearance violation ( clearance 0.8500 mm; actual 0.8400 mm)"},
            {"type": "silk_over_copper", "description": "whatever"},
        ],
    }


def test_parses_every_missing_connection():
    links = parse_drc_report(_report())
    assert len(links) == 2


def test_extracts_net_refs_pads_and_positions():
    gnd = parse_drc_report(_report())[0]
    assert gnd == MissingLink("/GND", "C10", "2", 93.0, 77.13, "R9", "2", 102.19, 89.5)
    assert gnd.endpoints == ("C10.2", "R9.2")


def test_length_is_the_span_the_bridge_would_have_to_cover():
    out1 = parse_drc_report(_report())[1]
    assert out1.length_mm == 5.0          # 3-4-5 triangle


def test_counts_per_net_are_derivable():
    by_net = {}
    for link in parse_drc_report(_report()):
        by_net[link.net] = by_net.get(link.net, 0) + 1
    assert by_net == {"/GND": 1, "/OUT1": 1}


def test_clearance_violations_are_separated_from_missing_connections():
    assert len(clearance_violations(_report())) == 1


def test_malformed_entries_are_skipped_not_crashed():
    bad = {"unconnected_items": [
        {"items": [{"description": "something unparseable", "pos": {}}]},      # 1 item
        {"items": [{"description": "PTH pad 1 [/N] of A", "pos": {"x": 0, "y": 0}},
                   {"description": "garbage", "pos": {"x": 1, "y": 1}}]},      # 2nd bad
    ]}
    assert parse_drc_report(bad) == []


def test_empty_report_is_empty_not_an_error():
    assert parse_drc_report({}) == []
    assert clearance_violations({}) == []
