#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BaseTEMPolicy: Abstract base class for any AI policy that drives the TEM Agent.
Any new AI backend (Claude, Gemini, GPT-4, etc.) must implement this interface.
"""

from abc import ABC, abstractmethod


class BaseTEMPolicy(ABC):
    """
    Abstract interface for a TEM AI policy.
    Implementations receive the tool list from ToolRegistry,
    so they never need to hardcode tool schemas.
    """

    # What the agent loop calls this policy when it prints a decision. A
    # model-backed policy names its model; the deterministic one says "Rule".
    label = "AI"

    @abstractmethod
    def reset(self, goal_text: str, tools: list):
        """
        Reset the conversation history with a new goal and the current tool list.

        Args:
            goal_text: The user's natural language task/goal.
            tools: List of tool definition dicts from ToolRegistry.get_all_tools().
        """

    @abstractmethod
    def get_next_action(self) -> tuple:
        """
        Ask the AI model for the next action(s) to take.

        Returns:
            (tool_calls, assistant_text) where:
                tool_calls: list of dicts [{id, name, input}, ...]
                assistant_text: str of any reasoning text returned by the model
        """

    @abstractmethod
    def post_tool_results(self, results: list):
        """
        Feed back the results of executed tools into the conversation history.

        Args:
            results: list of dicts [{tool_use_id, content_dict, is_error}, ...]
        """
