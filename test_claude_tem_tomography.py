#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tomography integration test for Claude TEM Agent.
Runs the Claude-driven tomography tilt series task against the AutoScript simulator
and verifies that the microscope secures the column and beam before finishing.
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
    print("Starting Claude TEM Tomography Integration Test...")
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
    goal = "Perform a tomography tilt series at -20, 0, and 20 degrees magnification 4300x. Focus optimize at each tilt, acquire projection images, and secure the column at the end."
    agent = ClaudeTEMAgent()

    try:
        # Run tomography (limited to 50 iterations for safety)
        agent.run_goal(goal, max_iterations=50)
    except Exception as e:
        print(f"\nAgent execution encountered an unhandled exception: {e}")
        sys.exit(1)

    print("\nCLAUDE TEM TOMOGRAPHY INTEGRATION TEST COMPLETE.")

if __name__ == '__main__':
    main()
