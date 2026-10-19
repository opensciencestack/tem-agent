#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Policy Module.
Interfaces with the Anthropic Messages API, defines the TEM tool schemas,
and manages conversation history with Claude.
"""

import os
import json
from anthropic import Anthropic
from tool_registry import ToolRegistry

class TEMAgentPolicy:
    def __init__(self):
        # 1. Load API key from environment variable
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is not set! "
                "Please make sure it is exported in your environment."
            )
            
        self.client = Anthropic(api_key=api_key)
        self.model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
        
        # 2. Load system context
        context_path = os.path.join(os.path.dirname(__file__), "tem_context.txt")
        if not os.path.exists(context_path):
            raise FileNotFoundError(f"Context file not found at: {context_path}")
            
        with open(context_path, "r", encoding="utf-8") as f:
            self.context = f.read()
            
        # 3. Initialize conversation history
        self.messages = []
        
        # 4. Load tool schemas from ToolRegistry (config/tools.yaml)
        self.tools = ToolRegistry().get_all_tools()

    def reset(self, goal_text: str):
        """
        Resets the conversation history and sets the initial user goal.
        """
        self.messages = [
            {
                "role": "user",
                "content": f"User Goal: {goal_text}"
            }
        ]

    def get_next_action(self) -> tuple:
        """
        Sends the current history to Claude and returns the next actions.
        Returns:
            (tool_calls, assistant_text)
            where tool_calls is a list of dicts: [{"id": ..., "name": ..., "input": ...}]
        """
        # Call the Messages API
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self.context,
            messages=self.messages,
            tools=self.tools
        )
        
        assistant_content = []
        tool_calls = []
        assistant_text = ""
        
        # Parse response blocks
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text
                assistant_content.append({
                    "type": "text",
                    "text": block.text
                })
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
                
        # Append assistant response to dialogue history
        self.messages.append({
            "role": "assistant",
            "content": assistant_content
        })
        
        return tool_calls, assistant_text

    def post_tool_results(self, results: list):
        """
        Appends the execution results of the tools to the conversation history.
        results: list of dicts: [{"tool_use_id": ..., "content_dict": ..., "is_error": ...}]
        """
        content_blocks = []
        for res in results:
            content_blocks.append({
                "type": "tool_result",
                "tool_use_id": res["tool_use_id"],
                "content": json.dumps(res["content_dict"]),
                "is_error": res["is_error"]
            })
            
        self.messages.append({
            "role": "user",
            "content": content_blocks
        })
