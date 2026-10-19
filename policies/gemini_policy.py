#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GeminiTEMPolicy: Implements BaseTEMPolicy using the Google Gemini API.
Tool schemas are received from ToolRegistry and converted to Gemini
FunctionDeclaration format automatically — none are hardcoded here.

Requires: pip install google-generativeai
Environment: GEMINI_API_KEY must be set.
"""

import os
import json
from policies.base_policy import BaseTEMPolicy


def _convert_tool_to_gemini(tool: dict) -> dict:
    """
    Convert a tool definition dict (Anthropic/OpenAI format) into a
    Gemini FunctionDeclaration-compatible dict.

    The Gemini SDK accepts function declarations as plain dicts with
    the structure: {name, description, parameters: {type, properties, required}}
    """
    schema = tool.get("input_schema", {})
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": {
            "type": schema.get("type", "object"),
            "properties": schema.get("properties", {}),
            "required": schema.get("required", [])
        }
    }


class GeminiTEMPolicy(BaseTEMPolicy):
    """
    Gemini-based AI policy for the TEM Agent.
    Uses the Google Generative AI SDK with function-calling support.
    """

    label = "Gemini"

    def __init__(self, context_path: str = None):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai package is not installed. "
                "Run: .venv/bin/python3 -m pip install google-generativeai"
            )

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please export it before running."
            )

        genai.configure(api_key=api_key)
        self._genai = genai
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        # Load system context
        if context_path is None:
            context_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "tem_context.txt"
            )
        if not os.path.exists(context_path):
            raise FileNotFoundError(f"Context file not found at: {context_path}")
        with open(context_path, "r", encoding="utf-8") as f:
            self.context = f.read()

        self._gemini_tools = []
        self._chat = None

    def reset(self, goal_text: str, tools: list):
        """Reset chat session with goal and tool list from ToolRegistry."""
        # Convert tools to Gemini format
        gemini_declarations = [_convert_tool_to_gemini(t) for t in tools]
        self._gemini_tools = [{"function_declarations": gemini_declarations}]

        # Create a fresh Gemini model + chat session
        model = self._genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.context,
            tools=self._gemini_tools
        )
        self._chat = model.start_chat(history=[])
        self._goal_text = goal_text
        self._first_turn = True

    def get_next_action(self) -> tuple:
        if self._first_turn:
            message = f"User Goal: {self._goal_text}"
            self._first_turn = False
        else:
            # Subsequent turns: send a continuation prompt
            message = "Continue with the next action."

        response = self._chat.send_message(message)

        tool_calls = []
        assistant_text = ""

        for part in response.parts:
            if hasattr(part, "text") and part.text:
                assistant_text += part.text
            elif hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                # Generate a pseudo-id for compatibility with TEMAgent
                tool_id = f"gemini_call_{fc.name}_{len(tool_calls)}"
                # Gemini returns args as a MapComposite — convert to plain dict
                args = dict(fc.args) if fc.args else {}
                tool_calls.append({
                    "id": tool_id,
                    "name": fc.name,
                    "input": args
                })

        return tool_calls, assistant_text

    def post_tool_results(self, results: list):
        """Send function results back to the Gemini chat session."""
        import google.generativeai.types as genai_types

        function_responses = []
        for res in results:
            # Find the original function name from the tool_use_id
            # Format: gemini_call_{name}_{index}
            parts = res["tool_use_id"].split("_")
            # Reconstruct: parts[2] to parts[-2] is the function name
            # (tool_use_id = "gemini_call_set_defocus_0" → name = "set_defocus")
            name = "_".join(parts[2:-1])
            content = res["content_dict"]
            if res["is_error"]:
                content = {"error": content.get("error", "Unknown error")}

            function_responses.append(
                genai_types.Part.from_function_response(
                    name=name,
                    response=content
                )
            )

        if function_responses:
            self._chat.send_message(function_responses)
