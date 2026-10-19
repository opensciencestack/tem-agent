#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClaudeTEMPolicy: Implements BaseTEMPolicy using the Anthropic Messages API.
Tool schemas are received from ToolRegistry — none are hardcoded here.
"""

import os
import json
from anthropic import Anthropic
from policies.base_policy import BaseTEMPolicy


class ClaudeTEMPolicy(BaseTEMPolicy):
    """
    Claude-based AI policy for the TEM Agent.
    Uses the Anthropic Messages API with tool_use support.
    """

    label = "Claude"

    def __init__(self, context_path: str = None):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Please export it before running."
            )

        self.client = Anthropic(api_key=api_key)
        self.model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

        # Load system context
        if context_path is None:
            context_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "tem_context.txt"
            )
        if not os.path.exists(context_path):
            raise FileNotFoundError(f"Context file not found at: {context_path}")
        with open(context_path, "r", encoding="utf-8") as f:
            self.context = f.read()

        self._tools = []
        self.messages = []

    def reset(self, goal_text: str, tools: list):
        """Reset conversation history with goal and tool list from ToolRegistry."""
        self._tools = tools
        self.messages = [{"role": "user", "content": f"User Goal: {goal_text}"}]

    def get_next_action(self) -> tuple:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self.context,
            messages=self.messages,
            tools=self._tools
        )

        assistant_content = []
        tool_calls = []
        assistant_text = ""

        for block in response.content:
            if block.type == "text":
                assistant_text += block.text
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        self.messages.append({"role": "assistant", "content": assistant_content})
        return tool_calls, assistant_text

    def post_tool_results(self, results: list):
        content_blocks = []
        for res in results:
            content_blocks.append({
                "type": "tool_result",
                "tool_use_id": res["tool_use_id"],
                "content": json.dumps(res["content_dict"]),
                "is_error": res["is_error"]
            })
        self.messages.append({"role": "user", "content": content_blocks})
