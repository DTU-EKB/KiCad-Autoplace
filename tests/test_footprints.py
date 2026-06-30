"""Headless tests for the footprint-class height table. No pcbnew.

  python -m pytest tests/test_footprints.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import footprints                       # noqa: E402


def test_tall_parts():
    assert footprints.height_mm("energy_system:TO-220-3_Vertical_LaserPads") >= 8.0
    assert footprints.height_mm("Capacitor_THT:CP_Radial_D18.0mm_P7.50mm") >= 18.0
    assert footprints.height_mm("Capacitor_THT:CP_Radial_D8.0mm_P3.50mm") >= 8.0
    assert footprints.height_mm("energy_system:L_Toroid_Vertical_L34.5mm_W15.0mm") >= 8.0
    assert footprints.height_mm("TerminalBlock:TerminalBlock_bornier-2_P5.08mm") >= 8.0
    assert footprints.height_mm("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical") >= 8.0
    assert footprints.height_mm("Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical") >= 8.0


def test_short_parts():
    assert footprints.height_mm("Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal") < 8.0
    assert footprints.height_mm("Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal") < 8.0
    assert footprints.height_mm("Package_DIP:DIP-8_W7.62mm_LongPads") < 8.0
    assert footprints.height_mm("Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm") < 8.0


def test_cp_radial_scales_with_diameter():
    big = footprints.height_mm("Capacitor_THT:CP_Radial_D18.0mm_P7.50mm")
    small = footprints.height_mm("Capacitor_THT:CP_Radial_D8.0mm_P3.50mm")
    assert big > small


def test_unknown_is_low_default():
    assert footprints.height_mm("Some:Unknown_Footprint_XYZ") == 4.0
    assert footprints.height_mm("") == 4.0
