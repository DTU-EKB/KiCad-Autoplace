"""Headless tests for net-name helpers. No pcbnew required."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import nets                              # noqa: E402


def test_plain_and_hierarchical_gnd_match():
    assert nets.is_gnd_name("GND")
    assert nets.is_gnd_name("/GND")
    assert nets.is_gnd_name("/Power/GND")
    assert nets.is_gnd_name("/gnd")                     # case-insensitive


def test_other_grounds_and_power_do_not_match():
    for n in ("AGND", "DGND", "PGND", "GND_MCU", "/+24V", "/HEATER_RET", "VBUS"):
        assert not nets.is_gnd_name(n), n


from autoplace.model import Board, Component, Pad     # noqa: E402


def _board(net_to_pintypes):
    """Build a Board where each net maps to a list of pad pin_type strings
    (one synthetic 1-pad component per pad)."""
    b = Board(0, 0, 10, 10)
    comps = {}
    for i, (net, pts) in enumerate(net_to_pintypes.items()):
        for j, pt in enumerate(pts):
            ref = f"X{i}_{j}"
            comps[ref] = Component(ref, 1, 1, x=0, y=0,
                                   pads=[Pad(str(j), net, 0.0, 0.0, pin_type=pt)])
    b.components = comps
    return b


def test_classify_ground():
    b = _board({"GND": ["passive", "passive"], "/Motor Power/GND": ["passive"],
                "AGND": ["power_in"], "DGND": [""], "PGND": [""]})
    for net in ("GND", "/Motor Power/GND", "AGND", "DGND", "PGND"):
        assert nets.classify_net(b, net) == "GROUND", net


def test_classify_power_by_pintype_even_on_auto_named_net():
    b = _board({"Net-(U1-Pad7)": ["power_in", "passive"]})
    assert nets.classify_net(b, "Net-(U1-Pad7)") == "POWER"


def test_classify_power_by_name():
    b = _board({"+5V_PWR": ["passive"], "+15V2": ["passive"], "-15V": ["passive"],
                "VCC": ["passive"], "VDD": ["passive"]})
    for net in ("+5V_PWR", "+15V2", "-15V", "VCC", "VDD"):
        assert nets.classify_net(b, net) == "POWER", net


def test_classify_sense():
    b = _board({"ADC_V1": ["input"], "FB": ["input"], "ISENSE": ["passive"],
                "/C2000 Feedback/VREF": ["passive"]})
    for net in ("ADC_V1", "FB", "ISENSE", "/C2000 Feedback/VREF"):
        assert nets.classify_net(b, net) == "SENSE", net


def test_classify_signal_default():
    b = _board({"SW": ["output"], "/Motor Power/SW": ["output"],
                "3PH_V": ["passive"], "Net-(R1-Pad2)": ["passive", "passive"]})
    for net in ("SW", "/Motor Power/SW", "3PH_V", "Net-(R1-Pad2)"):
        assert nets.classify_net(b, net) == "SIGNAL", net


def test_classify_nc():
    b = _board({"unconnected-(U302-NC-Pad7)": ["no_connect"],
                "DEAD": ["no_connect", "no_connect"]})
    assert nets.classify_net(b, "unconnected-(U302-NC-Pad7)") == "NC"
    assert nets.classify_net(b, "DEAD") == "NC"


def test_classify_vss_is_power_not_ground():
    # VSS is a rail (documented as POWER), not a ground reference.
    b = _board({"VSS": ["passive", "passive"]})
    assert nets.classify_net(b, "VSS") == "POWER"


def test_classify_empty_pintype_falls_back_to_name():
    # unsynced board: no pin types -> classify by name only
    b = _board({"GND": [""], "+5V": [""], "ADC_X": [""], "SOMESIG": [""]})
    assert nets.classify_net(b, "GND") == "GROUND"
    assert nets.classify_net(b, "+5V") == "POWER"
    assert nets.classify_net(b, "ADC_X") == "SENSE"
    assert nets.classify_net(b, "SOMESIG") == "SIGNAL"
