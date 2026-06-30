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
