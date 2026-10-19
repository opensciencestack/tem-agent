#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEMAgent: Generic, model-agnostic TEM agent runner.

Accepts any BaseTEMPolicy implementation (Claude, Gemini, etc.) and a
TEMCapabilities instance, and runs the same agentic loop regardless of
which AI model is being used.

Connection to the hardware is managed externally via the connector
and passed in at construction time.
"""

from tool_registry import ToolRegistry
from tem_capabilities import TEMCapabilities


class TEMAgent:
    """
    Generic TEM agent. Does not know or care which AI model is used.
    All model-specific behaviour is encapsulated in the policy object.
    All hardware interaction is encapsulated in the capabilities object.
    """

    def __init__(self, policy, capabilities: TEMCapabilities,
                 tool_registry: ToolRegistry):
        """
        Args:
            policy:         An instance of any BaseTEMPolicy subclass.
            capabilities:   A TEMCapabilities instance (has the connector injected).
            tool_registry:  A ToolRegistry instance (loads tools.yaml).
        """
        self.policy = policy
        self.capabilities = capabilities
        self.tool_registry = tool_registry
        # The same loop drives a model-backed policy and the deterministic rule
        # one, so each line naming the policy asks it instead of saying "AI".
        self.policy_label = getattr(policy, "label", "AI")

    def run_goal(self, goal_text: str, max_iterations: int = 40):
        print("\n" + "=" * 80)
        print(f"USER GOAL: {goal_text}")
        print("=" * 80)

        # Reset per-goal state
        self.capabilities.reset()
        tools = self.tool_registry.get_all_tools()
        allowed_names = self.tool_registry.get_tool_names()
        self.policy.reset(goal_text, tools)

        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            print(f"\n[Iteration {iteration}]")

            tool_calls, assistant_text = self.policy.get_next_action()

            if assistant_text:
                print(f"{self.policy_label} thought:\n{assistant_text.strip()}\n")

            if not tool_calls:
                print("\n[Agent Stopped] The policy asked for no further tool calls.")
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

                print(f"{self.policy_label} selected:\n{name}")
                print(f"Parameters:\n{params}")

                # Guard: reject any tool not in the registry
                if name not in allowed_names:
                    result = {"error": f"Tool '{name}' is not in the ToolRegistry."}
                    is_error = True
                else:
                    result, is_error = self.capabilities.execute(name, params)

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

            self.policy.post_tool_results(results_to_post)

            if finish_called and not finish_is_error:
                print("\nTEM AGENT TASK COMPLETE")
                best_df = self.capabilities.best_defocus
                best_nv = self.capabilities.best_variance
                if best_df is not None:
                    print(f"Best observed defocus: {best_df*1e9:.1f} nm")
                    print(f"Best observed normalized variance: {best_nv:.6f}")
                print(f"{self.policy_label} finish summary: {finish_result.get('summary', '')}")
                print(f"Total iterations: {iteration}")
                break
        else:
            print(f"\nWARNING: Safety iteration limit ({max_iterations}) reached! "
                  "Goal was not achieved.")
