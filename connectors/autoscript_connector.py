#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoScript Connector: Wraps autoscript_interface.py behind a clean class interface.
Swap this connector class to target a real TEM instrument without changing agent code.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import autoscript_interface as _mi


class AutoScriptConnector:
    """
    Connector for the Thermo Scientific AutoScript TEM (simulator or real instrument).
    All agent code talks to this class, never to autoscript_interface directly.
    Swapping to a real instrument requires only replacing this file.
    """

    def connect(self):
        _mi.connect()

    def disconnect(self):
        _mi.disconnect()

    def get_microscope_state(self) -> dict:
        return _mi.get_microscope_state()

    def set_defocus(self, target_df: float):
        _mi.set_defocus(target_df)

    def acquire_image(self):
        """Returns (image, pixel_size) from autoscript_interface."""
        return _mi.acquire_image()

    def set_magnification(self, mag: int):
        _mi.set_magnification(mag)

    def move_stage(self, dX=0.0, dY=0.0, dZ=0.0, dA=0.0, dB=0.0):
        _mi.move_stage(dX=dX, dY=dY, dZ=dZ, dA=dA, dB=dB)

    def configure_detector(self, detector_type: str, settings: dict):
        _mi.configure_detector(detector_type, settings)

    def blank_beam(self):
        _mi.blank_beam()

    def unblank_beam(self):
        _mi.unblank_beam()

    def open_column_valve(self):
        _mi.open_column_valve()

    def close_column_valve(self):
        _mi.close_column_valve()

    def set_acceleration_voltage(self, voltage_kv: float):
        _mi.set_acceleration_voltage(voltage_kv)

    def set_optical_mode(self, mode: str):
        _mi.set_optical_mode(mode)
