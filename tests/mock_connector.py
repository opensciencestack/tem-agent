#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MockTEMConnector: A fully in-memory fake connector for dry-run policy tests.
Mirrors the AutoScriptConnector interface exactly so TEMCapabilities
works without any network connection.

Unlike the real instrument, the mock has a known optimum focus: image contrast
falls off as the defocus moves away from it, so a focus sweep has a real peak to
find and a dry run proves the loop works rather than just running it.
"""


class _MockImage:
    """Minimal image object that mimics the AutoScript image pixel_data attribute."""

    def __init__(self, contrast: float = 11000.0):
        import numpy as np
        rng = np.random.default_rng(42)
        self.pixel_data = rng.normal(loc=22000, scale=contrast, size=(512, 512)).clip(0, 65535)


class MockTEMConnector:
    """
    Fake TEM connector for dry-run / unit tests.
    All state is kept in memory; no network or hardware interaction.
    """

    # The defocus the mock is sharpest at, and how quickly sharpness falls away.
    OPTIMUM_DEFOCUS_M = -60e-9
    FOCUS_WIDTH_M = 120e-9

    def __init__(self):
        self._state = {
            "defocus_m": -150e-9,
            "magnification": 8600,
            "voltage_kv": 300.0,
            "beam_blanked": True,
            "column_valve_open": False,
            "optical_mode": "Tem",
            "detector": {"type": "BM-Ceta"},
            "stage_position": {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": 0.0},
        }
        self._detector_config = {
            "detector_type": "BM-Ceta",
            "dwell_time": 1.0,
            "image_shape": (512, 512)
        }

    def connect(self):
        print("[MockConnector] Connected (mock).")

    def disconnect(self):
        print("[MockConnector] Disconnected (mock).")

    def get_microscope_state(self) -> dict:
        return dict(self._state)

    def set_defocus(self, target_df: float):
        self._state["defocus_m"] = target_df

    def acquire_image(self):
        """Contrast is highest at OPTIMUM_DEFOCUS_M and falls off with defocus."""
        import math
        error = self._state["defocus_m"] - self.OPTIMUM_DEFOCUS_M
        sharpness = math.exp(-(error / self.FOCUS_WIDTH_M) ** 2)
        contrast = 4000.0 + 9000.0 * sharpness
        return _MockImage(contrast), 1e-10  # pixel_size placeholder

    def set_magnification(self, mag: int):
        self._state["magnification"] = mag

    def move_stage(self, dX=0.0, dY=0.0, dZ=0.0, dA=0.0, dB=0.0):
        sp = self._state["stage_position"]
        sp["X"] += dX
        sp["Y"] += dY
        sp["Z"] += dZ
        sp["A"] += dA
        sp["B"] += dB

    def configure_detector(self, detector_type: str, settings: dict):
        self._detector_config = {"detector_type": detector_type, **settings}
        self._state["detector"] = {"type": detector_type}

    def blank_beam(self):
        self._state["beam_blanked"] = True

    def unblank_beam(self):
        self._state["beam_blanked"] = False

    def open_column_valve(self):
        self._state["column_valve_open"] = True

    def close_column_valve(self):
        self._state["column_valve_open"] = False

    def set_acceleration_voltage(self, voltage_kv: float):
        self._state["voltage_kv"] = voltage_kv

    def set_optical_mode(self, mode: str):
        self._state["optical_mode"] = mode
