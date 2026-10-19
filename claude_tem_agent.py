#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude TEM Agent module.
Runs the Claude-driven focus optimization loop, communicates with the Anthropic client,
and executes safe allowed tool calls via the AutoScriptConnector.
"""

import os
import sys
import math
import numpy as np

# Ensure the local path is in the system path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.autoscript_connector import AutoScriptConnector
from ai_policy import TEMAgentPolicy
from tool_registry import ToolRegistry
from instrument_registry import InstrumentRegistry
from dotenv import load_dotenv

# Load local environment variables
load_dotenv(override=True)

# Load allowed tools from ToolRegistry (config/tools.yaml)
_tool_registry = ToolRegistry()
ALLOWED_TOOLS = _tool_registry.get_tool_names()

# Load safety bounds from InstrumentRegistry (config/instrument_autoscript.yaml)
_instrument = InstrumentRegistry()
MAX_DEFOCUS_CHANGE_PER_CALL = _instrument.get_limit("defocus_max_change_m")

class ClaudeTEMAgent:
    def __init__(self):
        self.policy = TEMAgentPolicy()
        self.connector = AutoScriptConnector()
        self.best_variance = -1.0
        self.best_defocus = None

    def execute_tool(self, name: str, params: dict) -> tuple:
        """
        Executes an allowed tool call and validates inputs.
        Returns: (result_dict, is_error)
        """
        if name not in ALLOWED_TOOLS:
            return {"error": f"Tool '{name}' is not in the list of allowed tools."}, True

        if name == "get_microscope_state":
            try:
                state = self.connector.get_microscope_state()
                # Return only focus-relevant JSON-serializable fields
                return {
                    "current_defocus_m": float(state["defocus_m"]),
                    "magnification": int(state["magnification"]),
                    "voltage_kv": float(state["voltage_kv"]),
                    "beam_blanked": bool(state["beam_blanked"]),
                    "column_valve_open": bool(state["column_valve_open"]),
                    "optical_mode": str(state["detector"]["type"])
                }, False
            except Exception as e:
                return {"error": f"Failed to get microscope state: {e}"}, True

        elif name == "set_defocus":
            target_df = params.get("target_df")
            if target_df is None:
                return {"error": "Missing parameter 'target_df'."}, True

            try:
                target_df = float(target_df)
            except (ValueError, TypeError):
                return {"error": f"Parameter 'target_df' must be a numeric value, got '{target_df}'."}, True

            if not math.isfinite(target_df):
                return {"error": "Parameter 'target_df' must be a finite number (not NaN or Inf)."}, True

            # Query current state to check change magnitude
            try:
                state = self.connector.get_microscope_state()
                current_df = state["defocus_m"]
            except Exception as e:
                return {"error": f"Failed to read current defocus: {e}"}, True

            change = abs(target_df - current_df)
            if change > MAX_DEFOCUS_CHANGE_PER_CALL:
                return {
                    "error": (
                        f"Requested defocus change ({change*1e9:.1f} nm) exceeds the maximum allowed "
                        f"safety limit ({MAX_DEFOCUS_CHANGE_PER_CALL*1e9:.1f} nm) per call. "
                        f"Please make smaller adjustments."
                    )
                }, True

            # Perform defocus write
            try:
                self.connector.set_defocus(target_df)
                # Verify read-back
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

        elif name == "acquire_image":
            try:
                image, pixel_size = self.connector.acquire_image()
                pixel_data = image.pixel_data

                # Calculate real stats
                arr = np.array(pixel_data, dtype=float)
                mean = np.mean(arr)
                std = np.std(arr)
                minimum = float(np.min(arr))
                maximum = float(np.max(arr))

                if abs(mean) < 1e-9:
                    norm_var = 0.0
                else:
                    norm_var = float(np.var(arr) / (mean ** 2))

                state = self.connector.get_microscope_state()
                current_df = state["defocus_m"]

                # Print stats
                print(f"  [Image Stats] Defocus: {current_df*1e9:.1f}nm, Mean: {mean:.2f}, Std: {std:.2f}, NormVar: {norm_var:.6f}, Min: {minimum}, Max: {maximum}")

                # Track best defocus/variance independently in python state
                if norm_var > self.best_variance:
                    self.best_variance = norm_var
                    self.best_defocus = current_df
                    print(f"  *** NEW BEST OBSERVED FOCUS state recorded: {self.best_defocus*1e9:.1f}nm with Normalized Variance {self.best_variance:.6f} ***")

                return {
                    "mean_intensity": float(mean),
                    "standard_deviation": float(std),
                    "normalized_variance": float(norm_var),
                    "minimum": minimum,
                    "maximum": maximum,
                    "current_defocus_m": current_df
                }, False
            except Exception as e:
                return {"error": f"Image acquisition failed: {e}"}, True

        elif name == "finish":
            summary = params.get("summary", "")
            try:
                state = self.connector.get_microscope_state()
                current_df = state["defocus_m"]
            except Exception as e:
                return {"error": f"Failed to read defocus before finish validation: {e}"}, True

            if self.best_defocus is None:
                return {"error": "Cannot finish. No image has been acquired yet to establish focus benchmarks."}, True

            # Tolerance check: 10 nm
            tolerance = 10e-9
            diff = abs(current_df - self.best_defocus)
            if diff > tolerance:
                return {
                    "error": (
                        f"Cannot finish. Current defocus ({current_df*1e9:.1f} nm) does not match the best observed "
                        f"defocus ({self.best_defocus*1e9:.1f} nm) within a 10 nm tolerance. "
                        f"Please restore the best observed defocus state first."
                    )
                }, True

            return {
                "success": True,
                "summary": summary,
                "best_observed_defocus_m": self.best_defocus,
                "best_observed_variance": self.best_variance,
                "final_defocus_m": current_df
            }, False

        elif name == "set_magnification":
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

        elif name == "move_stage":
            dX = params.get("dX", 0.0)
            dY = params.get("dY", 0.0)
            dZ = params.get("dZ", 0.0)
            dA = params.get("dA", 0.0)
            dB = params.get("dB", 0.0)

            # Validate numeric inputs
            for param_name, val in [("dX", dX), ("dY", dY), ("dZ", dZ), ("dA", dA), ("dB", dB)]:
                try:
                    float_val = float(val)
                    if not math.isfinite(float_val):
                        return {"error": f"Parameter '{param_name}' must be a finite number."}, True
                except (ValueError, TypeError):
                    return {"error": f"Parameter '{param_name}' must be a numeric value, got '{val}'."}, True

            dX, dY, dZ, dA, dB = float(dX), float(dY), float(dZ), float(dA), float(dB)

            # Safety bounds loaded from InstrumentRegistry
            MAX_TILT_ADJUST = _instrument.get_limit("stage_tilt_max_deg")
            MAX_TRANSLATE_ADJUST = _instrument.get_limit("stage_translate_max_m")

            if abs(dX) > MAX_TRANSLATE_ADJUST or abs(dY) > MAX_TRANSLATE_ADJUST or abs(dZ) > MAX_TRANSLATE_ADJUST:
                return {
                    "error": f"Relative translation coordinate exceeds the safety limit of {MAX_TRANSLATE_ADJUST*1e6:.1f} um."
                }, True

            if abs(dA) > MAX_TILT_ADJUST or abs(dB) > MAX_TILT_ADJUST:
                return {
                    "error": f"Relative tilt adjustment exceeds the safety limit of {MAX_TILT_ADJUST:.1f} degrees."
                }, True

            try:
                self.connector.move_stage(dX=dX, dY=dY, dZ=dZ, dA=dA, dB=dB)
                state = self.connector.get_microscope_state()
                return {
                    "moved_by": {"dX": dX, "dY": dY, "dZ": dZ, "dA": dA, "dB": dB},
                    "current_stage_position": state["stage_position"],
                    "success": True
                }, False
            except Exception as e:
                return {"error": f"Failed to move stage: {e}"}, True

        elif name == "configure_detector":
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
                return {"error": "Parameter 'image_shape' must be a list of two integers [width, height]."}, True

            try:
                w, h = int(image_shape[0]), int(image_shape[1])
                if w <= 0 or h <= 0:
                    return {"error": "Image resolution dimensions must be positive integers."}, True
            except (ValueError, TypeError):
                return {"error": "Image resolution dimensions must be integers."}, True

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

        elif name == "blank_beam":
            try:
                self.connector.blank_beam()
                state = self.connector.get_microscope_state()
                return {
                    "beam_blanked": bool(state["beam_blanked"]),
                    "success": True
                }, False
            except Exception as e:
                return {"error": f"Failed to blank beam: {e}"}, True

        elif name == "unblank_beam":
            try:
                self.connector.unblank_beam()
                state = self.connector.get_microscope_state()
                return {
                    "beam_blanked": bool(state["beam_blanked"]),
                    "success": True
                }, False
            except Exception as e:
                return {"error": f"Failed to unblank beam: {e}"}, True

        elif name == "open_column_valve":
            try:
                self.connector.open_column_valve()
                state = self.connector.get_microscope_state()
                return {
                    "column_valve_open": bool(state["column_valve_open"]),
                    "success": True
                }, False
            except Exception as e:
                return {"error": f"Failed to open column valve: {e}"}, True

        elif name == "close_column_valve":
            try:
                self.connector.close_column_valve()
                state = self.connector.get_microscope_state()
                return {
                    "column_valve_open": bool(state["column_valve_open"]),
                    "success": True
                }, False
            except Exception as e:
                return {"error": f"Failed to close column valve: {e}"}, True

        elif name == "set_acceleration_voltage":
            voltage_kv = params.get("voltage_kv")
            if voltage_kv is None:
                return {"error": "Missing parameter 'voltage_kv'."}, True
            try:
                voltage_kv = float(voltage_kv)
            except (ValueError, TypeError):
                return {"error": f"Parameter 'voltage_kv' must be a number, got '{voltage_kv}'."}, True
            if not math.isfinite(voltage_kv):
                return {"error": "Parameter 'voltage_kv' must be a finite number."}, True
            
            # Enforce safety limits from InstrumentRegistry
            v_min = _instrument.get_limit("voltage_min_kv")
            v_max = _instrument.get_limit("voltage_max_kv")
            if voltage_kv < v_min or voltage_kv > v_max:
                return {"error": f"Requested acceleration voltage must be between {v_min:.0f} kV and {v_max:.0f} kV for hardware safety."}, True

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

        return {"error": f"Unknown tool execution path: '{name}'"}, True

    def run_goal(self, goal_text: str, max_iterations: int = 40):
        print("\n" + "="*80)
        print(f"USER GOAL: {goal_text}")
        print("="*80)

        self.best_variance = -1.0
        self.best_defocus = None
        self.policy.reset(goal_text)

        # Connect via the swappable AutoScriptConnector
        self.connector.connect()

        try:
            iteration = 0
            while iteration < max_iterations:
                iteration += 1
                print(f"\n[Iteration {iteration}]")

                # Get next actions from Claude
                tool_calls, assistant_text = self.policy.get_next_action()

                if assistant_text:
                    print(f"Claude thought:\n{assistant_text.strip()}\n")

                if not tool_calls:
                    print("\n[Agent Stopped] The agent cannot perform this task with the available microscope tools.")
                    if assistant_text:
                        print(f"Explanation:\n{assistant_text.strip()}\n")
                    break

                results_to_post = []
                finish_called = False
                finish_result = None
                finish_is_error = False

                for call in tool_calls:
                    name = call["name"]
                    params = call["input"]
                    tool_use_id = call["id"]

                    print(f"Claude selected:\n{name}")
                    print(f"Parameters:\n{params}")

                    # Execute matching safe tool
                    result, is_error = self.execute_tool(name, params)
                    print(f"Tool result:\n{result}")

                    results_to_post.append({
                        "tool_use_id": tool_use_id,
                        "content_dict": result,
                        "is_error": is_error
                    })

                    if name == "finish":
                        finish_called = True
                        finish_result = result
                        finish_is_error = is_error

                # Send all results back in a single message
                self.policy.post_tool_results(results_to_post)

                # Check for successful completion
                if finish_called and not finish_is_error:
                    print("\nCLAUDE TEM FOCUS OPTIMIZATION COMPLETE")
                    print(f"Best observed defocus: {self.best_defocus*1e9:.1f} nm")
                    print(f"Best observed normalized variance: {self.best_variance:.6f}")
                    print(f"Final simulator defocus: {finish_result['final_defocus_m']*1e9:.1f} nm")
                    print(f"Claude finish reason: {finish_result['summary']}")
                    print(f"Total model iterations: {iteration}")
                    break
            else:
                print(f"\nWARNING: Safety iteration limit ({max_iterations}) reached! Goal was not achieved.")

            # Specimen protection protocol
            self.connector.blank_beam()
            self.connector.close_column_valve()

        finally:
            # Always cleanly disconnect from the server
            self.connector.disconnect()

if __name__ == '__main__':
    print("================================================================================")
    print("Welcome to the Claude-driven TEM Agent!")
    print("================================================================================")
    default_goal = "Optimize focus using image-quality feedback and leave the microscope at the best observed defocus."
    print(f"Default goal: '{default_goal}'")
    user_goal = input("Enter the goal/task for the microscope (press Enter to use default): ").strip()
    if not user_goal:
        user_goal = default_goal

    agent = ClaudeTEMAgent()
    agent.run_goal(user_goal)
