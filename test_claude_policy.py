#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit test for Claude Agent Policy.
Verifies that Claude responds with a valid allowlisted tool call.
This test does not connect to the AutoScript microscope server.
"""

import os
import sys
from dotenv import load_dotenv

# Ensure local path is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv(override=True)

from ai_policy import TEMAgentPolicy

def main():
    print("================================================================================")
    print("Running Claude Policy Tool-Selection Test (No Microscope Connection)...")
    print("================================================================================")

    # 1. Verify Anthropic API Key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is missing!")
        print("Please set it in your environment: export ANTHROPIC_API_KEY='your-key'")
        sys.exit(1)

    # 2. Instantiate Policy
    try:
        policy = TEMAgentPolicy()
    except Exception as e:
        print(f"Failed to initialize TEMAgentPolicy: {e}")
        sys.exit(1)

    # 3. Reset policy with focus optimization goal
    goal = "Optimize focus using image-quality feedback and leave the microscope at the best observed defocus."
    policy.reset(goal)

    print("Sending optimization goal and available tools to Claude...")
    
    # 4. Request next actions from Claude
    try:
        tool_calls, assistant_text = policy.get_next_action()
    except Exception as e:
        print(f"Failed to query Messages API: {e}")
        sys.exit(1)

    # 5. Print results
    print("\n[Claude Response]")
    if assistant_text:
        print(f"Assistant thoughts:\n{assistant_text.strip()}\n")
    print(f"Number of tool calls returned: {len(tool_calls)}")

    # 6. Verify and mock execute all tool calls
    allowed_tools = {
        "get_microscope_state", "set_defocus", "acquire_image", "finish",
        "set_magnification", "move_stage", "configure_detector",
        "blank_beam", "unblank_beam", "open_column_valve", "close_column_valve",
        "set_acceleration_voltage"
    }
    results_to_post = []

    for call in tool_calls:
        name = call["name"]
        params = call["input"]
        tool_use_id = call["id"]

        print(f"\nProcessing tool call: {name}")
        print(f"Parameters: {params}")
        print(f"Tool Use ID: {tool_use_id}")

        if name not in allowed_tools:
            print(f"FAILURE: Claude selected an invalid tool name: '{name}'!")
            sys.exit(1)

        # Determine mock result
        if name == "get_microscope_state":
            mock_result = {
                "current_defocus_m": -120e-9,
                "magnification": 200000,
                "voltage_kv": 300.0,
                "beam_blanked": False,
                "column_valve_open": True,
                "optical_mode": "Tem"
            }
        elif name == "acquire_image":
            mock_result = {
                "mean_intensity": 128.5,
                "standard_deviation": 32.1,
                "normalized_variance": 0.0624,
                "minimum": 0,
                "maximum": 255,
                "current_defocus_m": -120e-9
            }
        elif name == "move_stage":
            mock_result = {
                "moved_by": {"dX": params.get("dX", 0.0), "dY": params.get("dY", 0.0), "dZ": params.get("dZ", 0.0), "dA": params.get("dA", 0.0), "dB": params.get("dB", 0.0)},
                "current_stage_position": {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": params.get("dA", 0.0), "B": params.get("dB", 0.0)},
                "success": True
            }
        elif name == "set_magnification":
            mock_result = {
                "requested_magnification": params.get("magnification"),
                "actual_magnification": params.get("magnification"),
                "success": True
            }
        elif name == "configure_detector":
            mock_result = {
                "configured_detector": params.get("detector_type"),
                "dwell_time_s": params.get("dwell_time_s", 2e-6),
                "image_shape": params.get("image_shape", [512, 512]),
                "success": True
            }
        elif name == "set_acceleration_voltage":
            mock_result = {
                "requested_voltage_kv": params.get("voltage_kv"),
                "actual_voltage_kv": params.get("voltage_kv"),
                "success": True
            }
        else:
            mock_result = {"success": True}

        results_to_post.append({
            "tool_use_id": tool_use_id,
            "content_dict": mock_result,
            "is_error": False
        })

    print("\nSUCCESS: All Claude tool calls are valid!")

    # Post mock results
    try:
        policy.post_tool_results(results_to_post)
        print("Successfully verified posting tool results back to Claude's history.")

        # Test second turn call
        print("\nTriggering second call to get_next_action()...")
        tool_calls_2, assistant_text_2 = policy.get_next_action()
        print(f"Second Turn - Tool calls count: {len(tool_calls_2)}")
        if tool_calls_2:
            print(f"Second Turn - First tool name: {tool_calls_2[0]['name']}")
    except Exception as e:
        print(f"Failed during multi-turn testing: {e}")
        sys.exit(1)

    print("\nPolicy Tool-Selection Test Passed.")

if __name__ == '__main__':
    main()
