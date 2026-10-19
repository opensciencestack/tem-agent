#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration test for Claude TEM Agent.
Runs the Claude-driven focus optimization loop against the AutoScript simulator
and verifies that the microscope is left at the best observed defocus.
"""

import os
import sys
from dotenv import load_dotenv

# Ensure local path is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv(override=True)

from claude_tem_agent import ClaudeTEMAgent
import autoscript_interface as mi

def main():
    print("================================================================================")
    print("Starting Claude TEM Focus Optimization Integration Test...")
    print("================================================================================")

    # 1. Verify environment variables
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable is missing!")
        sys.exit(1)

    if not os.environ.get("AUTOSCRIPT_HOST"):
        print("ERROR: AUTOSCRIPT_HOST environment variable is missing!")
        print("Please export it in your terminal: export AUTOSCRIPT_HOST='<ip>'")
        sys.exit(1)

    # 2. Instantiate and run the agent
    goal = "Optimize focus using image-quality feedback and leave the microscope at the best observed defocus."
    agent = ClaudeTEMAgent()

    try:
        # Run focus optimization (limited to 20 iterations for safety)
        agent.run_goal(goal, max_iterations=20)
    except Exception as e:
        print(f"\nAgent execution encountered an unhandled exception: {e}")
        sys.exit(1)

    # 3. Post-run verification against the simulator
    print("\nStarting post-test verification...")
    try:
        # Connect one more time just to read the final state
        mi.connect()
        state = mi.get_microscope_state()
        final_df = float(state["defocus_m"])
        mi.disconnect()

        print(f"Final microscope defocus read back: {final_df*1e9:.2f} nm")
        if agent.best_defocus is not None:
            print(f"Best observed defocus tracked in Python: {agent.best_defocus*1e9:.2f} nm")
            print(f"Best observed normalized variance: {agent.best_variance:.6f}")
        else:
            print("Best observed defocus tracked in Python: None")

        # Verify best focus is not None
        if agent.best_defocus is None:
            print("VERIFICATION FAILURE: No best observed defocus state was recorded!")
            sys.exit(1)

        # Check tolerance (10 nm)
        tolerance = 10e-9
        diff = abs(final_df - agent.best_defocus)
        if diff <= tolerance:
            print(f"VERIFICATION SUCCESS: Final defocus matches best defocus within {tolerance*1e9:.1f} nm tolerance (diff: {diff*1e9:.2f} nm).")
            print("\nCLAUDE TEM FOCUS OPTIMIZATION TEST PASSED.")
        else:
            print(f"VERIFICATION FAILURE: Final defocus diff ({diff*1e9:.2f} nm) exceeds the {tolerance*1e9:.1f} nm tolerance threshold!")
            sys.exit(1)

    except Exception as e:
        print(f"Verification process failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
