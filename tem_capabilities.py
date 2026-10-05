#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEMCapabilities: Model-agnostic TEM tool execution layer.

Extracts all execute_tool() logic from the old claude_tem_agent.py into a
standalone class that is shared by any AI policy (Claude, Gemini, etc.).

Usage:
    connector = AutoScriptConnector()
    instrument = InstrumentRegistry()
    caps = TEMCapabilities(connector, instrument)
    result, is_error = caps.execute("set_defocus", {"target_df": -150e-9})
"""

import math
import numpy as np


class TEMCapabilities:
    """
    Shared TEM capability executor.
    Accepts any connector (AutoScriptConnector or a future RealTEMConnector)
    and an InstrumentRegistry for safety limits.
    """

    def __init__(self, connector, instrument_registry):
        """
        Args:
            connector: An object with connect/disconnect and all TEM action methods.
            instrument_registry: An InstrumentRegistry instance providing safety limits.
        """
        self.connector = connector
        self.instrument = instrument_registry

        # Internal focus tracking state — reset per goal
        self.best_variance: float = -1.0
        self.best_defocus: float = None

    def reset(self):
        """Reset per-goal tracking state."""
        self.best_variance = -1.0
        self.best_defocus = None

    def execute(self, name: str, params: dict) -> tuple:
        """
        Execute a named TEM tool call with validated parameters.
        Returns: (result_dict, is_error: bool)
        """
        if name == "get_microscope_state":
            return self._get_microscope_state()

        elif name == "set_defocus":
            return self._set_defocus(params)

        elif name == "acquire_image":
            return self._acquire_image()

        elif name == "finish":
            return self._finish(params)

        elif name == "set_magnification":
            return self._set_magnification(params)

        elif name == "move_stage":
            return self._move_stage(params)

        elif name == "configure_detector":
            return self._configure_detector(params)

        elif name == "blank_beam":
            return self._blank_beam()

        elif name == "unblank_beam":
            return self._unblank_beam()

        elif name == "open_column_valve":
            return self._open_column_valve()

        elif name == "close_column_valve":
            return self._close_column_valve()

        elif name == "set_acceleration_voltage":
            return self._set_acceleration_voltage(params)

        elif name == "set_optical_mode":
            return self._set_optical_mode(params)

        return {"error": f"Unknown TEM capability: '{name}'"}, True

    # -------------------------------------------------------------------------
    # Individual capability implementations
    # -------------------------------------------------------------------------

    def _get_microscope_state(self):
        try:
            state = self.connector.get_microscope_state()
            return {
                "current_defocus_m": float(state["defocus_m"]),
                "magnification": int(state["magnification"]),
                "voltage_kv": float(state["voltage_kv"]),
                "beam_blanked": bool(state["beam_blanked"]),
                "column_valve_open": bool(state["column_valve_open"]),
                "optical_mode": str(state.get("optical_mode", state["detector"].get("type"))),
                "stage_position": state.get("stage_position", {})
            }, False
        except Exception as e:
            return {"error": f"Failed to get microscope state: {e}"}, True

    def _set_defocus(self, params):
        target_df = params.get("target_df")
        if target_df is None:
            return {"error": "Missing parameter 'target_df'."}, True
        try:
            target_df = float(target_df)
        except (ValueError, TypeError):
            return {"error": f"Parameter 'target_df' must be numeric, got '{target_df}'."}, True
        if not math.isfinite(target_df):
            return {"error": "Parameter 'target_df' must be finite (not NaN or Inf)."}, True

        try:
            state = self.connector.get_microscope_state()
            current_df = state["defocus_m"]
        except Exception as e:
            return {"error": f"Failed to read current defocus: {e}"}, True

        max_change = self.instrument.get_limit("defocus_max_change_m")
        change = abs(target_df - current_df)
        if change > max_change:
            return {
                "error": (
                    f"Requested defocus change ({change*1e9:.1f} nm) exceeds the safety limit "
                    f"({max_change*1e9:.1f} nm) per call. Please make smaller adjustments."
                )
            }, True

        try:
            self.connector.set_defocus(target_df)
            state_back = self.connector.get_microscope_state()
            actual_df = state_back["defocus_m"]
            return {
                "requested_defocus_m": target_df,
                "actual_defocus_m": actual_df,
                "change_m": actual_df - current_df,
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to set defocus: {e}"}, True

    def _acquire_image(self):
        try:
            image, pixel_size = self.connector.acquire_image()
            arr = np.array(image.pixel_data, dtype=float)
            mean = float(np.mean(arr))
            std = float(np.std(arr))
            minimum = float(np.min(arr))
            maximum = float(np.max(arr))
            norm_var = float(np.var(arr) / (mean ** 2)) if abs(mean) > 1e-9 else 0.0

            state = self.connector.get_microscope_state()
            current_df = state["defocus_m"]

            print(f"  [Image Stats] Defocus: {current_df*1e9:.1f}nm, Mean: {mean:.2f}, "
                  f"Std: {std:.2f}, NormVar: {norm_var:.6f}, Min: {minimum}, Max: {maximum}")

            if norm_var > self.best_variance:
                self.best_variance = norm_var
                self.best_defocus = current_df
                print(f"  *** NEW BEST OBSERVED FOCUS state recorded: "
                      f"{self.best_defocus*1e9:.1f}nm with Normalized Variance {self.best_variance:.6f} ***")

            return {
                "mean_intensity": mean,
                "standard_deviation": std,
                "normalized_variance": norm_var,
                "minimum": minimum,
                "maximum": maximum,
                "current_defocus_m": current_df
            }, False
        except Exception as e:
            return {"error": f"Image acquisition failed: {e}"}, True

    def _finish(self, params):
        summary = params.get("summary", "")
        try:
            state = self.connector.get_microscope_state()
            current_df = state["defocus_m"]
        except Exception as e:
            return {"error": f"Failed to read defocus before finish validation: {e}"}, True

        if self.best_defocus is None:
            return {"error": "Cannot finish. No image has been acquired yet."}, True

        tolerance = self.instrument.get_limit("finish_defocus_tolerance_m")
        diff = abs(current_df - self.best_defocus)
        if diff > tolerance:
            return {
                "error": (
                    f"Cannot finish. Current defocus ({current_df*1e9:.1f} nm) does not match "
                    f"the best observed defocus ({self.best_defocus*1e9:.1f} nm) within a "
                    f"{tolerance*1e9:.0f} nm tolerance. Please restore the best defocus first."
                )
            }, True

        return {
            "success": True,
            "summary": summary,
            "best_observed_defocus_m": self.best_defocus,
            "best_observed_variance": self.best_variance,
            "final_defocus_m": current_df
        }, False

    def _set_magnification(self, params):
        mag = params.get("magnification")
        if mag is None:
            return {"error": "Missing parameter 'magnification'."}, True
        try:
            mag_val = int(mag)
        except (ValueError, TypeError):
            return {"error": f"Parameter 'magnification' must be an integer, got '{mag}'."}, True
        if mag_val <= 0:
            return {"error": "Parameter 'magnification' must be a positive integer."}, True
        try:
            self.connector.set_magnification(mag_val)
            state = self.connector.get_microscope_state()
            return {
                "requested_magnification": mag_val,
                "actual_magnification": int(state["magnification"]),
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to set magnification: {e}"}, True

    def _move_stage(self, params):
        dX = params.get("dX", 0.0)
        dY = params.get("dY", 0.0)
        dZ = params.get("dZ", 0.0)
        dA = params.get("dA", 0.0)
        dB = params.get("dB", 0.0)

        for param_name, val in [("dX", dX), ("dY", dY), ("dZ", dZ), ("dA", dA), ("dB", dB)]:
            try:
                fv = float(val)
                if not math.isfinite(fv):
                    return {"error": f"Parameter '{param_name}' must be finite."}, True
            except (ValueError, TypeError):
                return {"error": f"Parameter '{param_name}' must be numeric, got '{val}'."}, True

        dX, dY, dZ, dA, dB = float(dX), float(dY), float(dZ), float(dA), float(dB)

        max_t = self.instrument.get_limit("stage_translate_max_m")
        max_deg = self.instrument.get_limit("stage_tilt_max_deg")

        if abs(dX) > max_t or abs(dY) > max_t or abs(dZ) > max_t:
            return {"error": f"Translation exceeds safety limit of {max_t*1e6:.1f} um."}, True
        if abs(dA) > max_deg or abs(dB) > max_deg:
            return {"error": f"Tilt exceeds safety limit of {max_deg:.1f} degrees."}, True

        try:
            self.connector.move_stage(dX=dX, dY=dY, dZ=dZ, dA=dA, dB=dB)
            state = self.connector.get_microscope_state()
            return {
                "moved_by": {"dX": dX, "dY": dY, "dZ": dZ, "dA": dA, "dB": dB},
                "current_stage_position": state.get("stage_position", {}),
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to move stage: {e}"}, True

    def _configure_detector(self, params):
        detector_type = params.get("detector_type")
        if not detector_type:
            return {"error": "Missing parameter 'detector_type'."}, True

        dwell_time_s = params.get("dwell_time_s", 2e-6)
        image_shape = params.get("image_shape", [512, 512])

        try:
            dwell_time_s = float(dwell_time_s)
            if not math.isfinite(dwell_time_s) or dwell_time_s <= 0:
                return {"error": "Parameter 'dwell_time_s' must be a finite positive number."}, True
        except (ValueError, TypeError):
            return {"error": f"Parameter 'dwell_time_s' must be numeric, got '{dwell_time_s}'."}, True

        if not isinstance(image_shape, list) or len(image_shape) != 2:
            return {"error": "Parameter 'image_shape' must be a list of two integers."}, True
        try:
            w, h = int(image_shape[0]), int(image_shape[1])
            if w <= 0 or h <= 0:
                return {"error": "Image dimensions must be positive integers."}, True
        except (ValueError, TypeError):
            return {"error": "Image dimensions must be integers."}, True

        try:
            self.connector.configure_detector(detector_type, {
                "dwell_time": dwell_time_s,
                "image_shape": (w, h)
            })
            return {
                "configured_detector": detector_type,
                "dwell_time_s": dwell_time_s,
                "image_shape": [w, h],
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to configure detector: {e}"}, True

    def _blank_beam(self):
        try:
            self.connector.blank_beam()
            state = self.connector.get_microscope_state()
            return {"beam_blanked": bool(state["beam_blanked"]), "success": True}, False
        except Exception as e:
            return {"error": f"Failed to blank beam: {e}"}, True

    def _unblank_beam(self):
        try:
            self.connector.unblank_beam()
            state = self.connector.get_microscope_state()
            return {"beam_blanked": bool(state["beam_blanked"]), "success": True}, False
        except Exception as e:
            return {"error": f"Failed to unblank beam: {e}"}, True

    def _open_column_valve(self):
        try:
            self.connector.open_column_valve()
            state = self.connector.get_microscope_state()
            return {"column_valve_open": bool(state["column_valve_open"]), "success": True}, False
        except Exception as e:
            return {"error": f"Failed to open column valve: {e}"}, True

    def _close_column_valve(self):
        try:
            self.connector.close_column_valve()
            state = self.connector.get_microscope_state()
            return {"column_valve_open": bool(state["column_valve_open"]), "success": True}, False
        except Exception as e:
            return {"error": f"Failed to close column valve: {e}"}, True

    def _set_acceleration_voltage(self, params):
        voltage_kv = params.get("voltage_kv")
        if voltage_kv is None:
            return {"error": "Missing parameter 'voltage_kv'."}, True
        try:
            voltage_kv = float(voltage_kv)
        except (ValueError, TypeError):
            return {"error": f"Parameter 'voltage_kv' must be numeric, got '{voltage_kv}'."}, True
        if not math.isfinite(voltage_kv):
            return {"error": "Parameter 'voltage_kv' must be finite."}, True

        v_min = self.instrument.get_limit("voltage_min_kv")
        v_max = self.instrument.get_limit("voltage_max_kv")
        if voltage_kv < v_min or voltage_kv > v_max:
            return {
                "error": f"Voltage must be between {v_min:.0f} kV and {v_max:.0f} kV."
            }, True

        try:
            self.connector.set_acceleration_voltage(voltage_kv)
            state = self.connector.get_microscope_state()
            return {
                "requested_voltage_kv": voltage_kv,
                "actual_voltage_kv": float(state["voltage_kv"]),
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to set acceleration voltage: {e}"}, True

    def _set_optical_mode(self, params):
        mode = params.get("mode")
        if mode is None:
            return {"error": "Missing parameter 'mode'."}, True
        if mode not in ("Stem", "Tem"):
            return {"error": f"Parameter 'mode' must be 'Stem' or 'Tem', got '{mode}'."}, True
        try:
            self.connector.set_optical_mode(mode)
            state = self.connector.get_microscope_state()
            return {
                "requested_optical_mode": mode,
                "actual_optical_mode": str(state.get("optical_mode", state["detector"].get("type"))),
                "success": True
            }, False
        except Exception as e:
            return {"error": f"Failed to set optical mode: {e}"}, True
