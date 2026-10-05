#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
invoke_tem_ai_model.py: Unified entry point for the TEM Agent.

Supports multiple AI backends via the --model flag:
    python invoke_tem_ai_model.py --model claude
    python invoke_tem_ai_model.py --model gemini

Optional flags:
    --goal "Your custom goal text"
    --max-iterations 40
    --dry-run          Run a policy-only test without connecting to the microscope.
    --instrument-config path/to/custom_instrument.yaml
    --tools-config     path/to/custom_tools.yaml

Environment variables required:
    ANTHROPIC_API_KEY    (for Claude)
    GEMINI_API_KEY       (for Gemini)
    AUTOSCRIPT_HOST      Microscope server IP
    AUTOSCRIPT_PORT      Microscope server port (default: 7521)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from tool_registry import ToolRegistry
from instrument_registry import InstrumentRegistry
from tem_capabilities import TEMCapabilities
from tem_agent import TEMAgent

load_dotenv()

DEFAULT_GOAL = (
    "Optimize focus using image-quality feedback and leave the "
    "microscope at the best observed defocus."
)


def build_policy(model_name: str):
    """Instantiate the correct policy for the chosen model."""
    if model_name == "claude":
        from policies.claude_policy import ClaudeTEMPolicy
        return ClaudeTEMPolicy()
    elif model_name == "gemini":
        from policies.gemini_policy import GeminiTEMPolicy
        return GeminiTEMPolicy()
    elif model_name == "rule":
        from policies.rule_policy import RuleTEMPolicy
        return RuleTEMPolicy()
    else:
        raise ValueError(
            f"Unknown model '{model_name}'. Supported values: claude, gemini, rule."
        )


def invoke_tem_ai_model(
    model_name: str,
    goal_text: str,
    max_iterations: int = 40,
    dry_run: bool = False,
    tools_config: str = None,
    instrument_config: str = None
):
    """
    Main entry point for running the TEM AI Agent.

    Args:
        model_name:        AI backend to use ("claude" or "gemini").
        goal_text:         The natural language task/goal for the agent.
        max_iterations:    Maximum agentic loop iterations before safety cutoff.
        dry_run:           If True, run policy-only without connecting to hardware.
        tools_config:      Optional path to a custom tools.yaml.
        instrument_config: Optional path to a custom instrument yaml.
    """
    print("=" * 80)
    print(f"TEM AI Agent  |  Model: {model_name.upper()}  |  Dry-run: {dry_run}")
    print("=" * 80)

    # Load registries
    registry = ToolRegistry(tools_config)
    instrument = InstrumentRegistry(instrument_config)
    print(f"[Registry] Loaded {len(registry.get_tool_names())} tools from ToolRegistry.")
    print(f"[Registry] Instrument: {instrument.instrument_name}")

    # Build policy
    policy = build_policy(model_name)

    # Build connector
    if dry_run:
        from tests.mock_connector import MockTEMConnector
        connector = MockTEMConnector()
        print("[Connector] Using MockTEMConnector (dry-run mode, no hardware).")
    else:
        from connectors.autoscript_connector import AutoScriptConnector
        connector = AutoScriptConnector()

    # Build capabilities and agent
    capabilities = TEMCapabilities(connector, instrument)
    agent = TEMAgent(policy, capabilities, registry)

    # Connect to hardware
    if not dry_run:
        connector.connect()

    try:
        agent.run_goal(goal_text, max_iterations=max_iterations)
    finally:
        if not dry_run:
            # Specimen protection: always blank and close on exit
            try:
                connector.blank_beam()
                connector.close_column_valve()
            except Exception:
                pass
            connector.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="Invoke the TEM AI Agent with a chosen model backend."
    )
    parser.add_argument(
        "--model", choices=["claude", "gemini", "rule"], default="claude",
        help="AI model backend to use (default: claude). 'rule' is the deterministic, "
             "model-free policy: no API key, no network call."
    )
    parser.add_argument(
        "--goal", type=str, default=None,
        help="Natural language goal/task for the microscope agent."
    )
    parser.add_argument(
        "--max-iterations", type=int, default=40,
        help="Maximum safety iteration limit (default: 40)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run policy-only test without connecting to the microscope hardware."
    )
    parser.add_argument(
        "--tools-config", type=str, default=None,
        help="Optional path to a custom tools.yaml config file."
    )
    parser.add_argument(
        "--instrument-config", type=str, default=None,
        help="Optional path to a custom instrument YAML config file."
    )

    args = parser.parse_args()

    goal = args.goal
    if not goal:
        if sys.stdin.isatty():
            print(f"\nDefault goal: '{DEFAULT_GOAL}'")
            goal = input("Enter microscope goal (press Enter for default): ").strip()
        if not goal:
            goal = DEFAULT_GOAL

    invoke_tem_ai_model(
        model_name=args.model,
        goal_text=goal,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        tools_config=args.tools_config,
        instrument_config=args.instrument_config
    )


if __name__ == "__main__":
    main()
