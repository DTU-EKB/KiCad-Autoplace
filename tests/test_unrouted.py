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


def test_entry_with_too_few_items_is_skipped_not_crashed():
    bad = {"unconnected_items": [
        {"items": [{"description": "something unparseable", "pos": {}}]},      # 1 item
    ]}
    assert parse_drc_report(bad) == []


def test_empty_report_is_empty_not_an_error():
    assert parse_drc_report({}) == []
    assert clearance_violations({}) == []


# --- non-pad endpoints -----------------------------------------------------
# A missing connection is reported between whatever two items KiCad found on
# either side of the gap, and only one of them has to be a pad -- the other is
# routinely a Track or the ground Zone. Matching pads only made the parser drop
# those entries silently and report a board with five missing connections as
# fully routed, which ``completer`` then presented as a finished board.
# Captured verbatim from kicad-cli 10 on a routed single-sided board.

def _mixed_report():
    return {"unconnected_items": [
        {"items": [
            {"description": "Track [/VIN] on B.Cu, length 15.8749 mm",
             "pos": {"x": 120.0, "y": 60.0}},
            {"description": "PTH pad 1 [/VIN] of J4", "pos": {"x": 124.0, "y": 63.0}},
        ]},
        {"items": [
            {"description": "PTH pad 2 [/GND] of R9", "pos": {"x": 102.19, "y": 89.5}},
            {"description": "Zone [/GND] on B.Cu, priority 0",
             "pos": {"x": 100.0, "y": 90.0}},
        ]},
        {"items": [
            {"description": "Track [/VGND] on B.Cu, length 0.0015 mm",
             "pos": {"x": 90.0, "y": 50.0}},
            {"description": "Track [/VGND] on B.Cu, length 1.8663 mm",
             "pos": {"x": 92.0, "y": 50.0}},
        ]},
    ]}


def test_counts_missing_connections_with_track_and_zone_endpoints():
    """The count is what ``completer`` steers on: every reported gap must count,
    whatever kind of item sits on each side of it."""
    assert len(parse_drc_report(_mixed_report())) == 3


def test_track_to_pad_gap_keeps_the_net_and_the_pad_it_can_identify():
    link = parse_drc_report(_mixed_report())[0]
    assert link.net == "/VIN"
    assert (link.kind_a, link.kind_b) == ("track", "pad")
    assert (link.ref_b, link.pad_b) == ("J4", "1")
    assert (link.ref_a, link.pad_a) == ("", "")       # a track has no refdes
    assert (link.x_a, link.y_a) == (120.0, 60.0)


def test_zone_endpoint_is_recognised():
    link = parse_drc_report(_mixed_report())[1]
    assert link.net == "/GND"
    assert (link.kind_a, link.kind_b) == ("pad", "zone")
    assert link.ref_a == "R9"


def test_track_to_track_gap_still_counts():
    link = parse_drc_report(_mixed_report())[2]
    assert link.net == "/VGND"
    assert (link.kind_a, link.kind_b) == ("track", "track")


def test_pad_pairs_are_still_bridgeable():
    """Pad-to-pad gaps keep full detail -- that is what a wire bridge needs."""
    links = parse_drc_report(_mixed_report())
    assert [ln for ln in links if ln.is_pad_pair] == []
    gnd = parse_drc_report(_report())[0]
    assert gnd.is_pad_pair is True
    assert gnd.endpoints == ("C10.2", "R9.2")


def test_unrecognised_description_still_counts_as_a_missing_connection():
    """Better to report a gap we cannot name than to report a complete board."""
    odd = {"unconnected_items": [
        {"items": [{"description": "PTH pad 1 [/N] of A", "pos": {"x": 0, "y": 0}},
                   {"description": "garbage", "pos": {"x": 1, "y": 1}}]},
    ]}
    links = parse_drc_report(odd)
    assert len(links) == 1
    assert links[0].net == "/N"
    assert links[0].kind_b == "unknown"
    assert links[0].is_pad_pair is False
