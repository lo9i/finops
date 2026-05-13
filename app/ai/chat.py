"""Chat orchestration. Each request runs Claude through the read-only tools."""
from __future__ import annotations

import os
from typing import Any

import anthropic

from .tools import ALL_TOOLS, SYSTEM_PROMPT


def is_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def run_chat(message: str, history: list[dict[str, str]]) -> dict[str, Any]:
    """Run one chat turn through the Anthropic tool runner.

    Returns a dict with:
      - response: final assistant text
      - tool_calls: list of {tool, input} for transparency
    Raises RuntimeError if ANTHROPIC_API_KEY is not configured.
    """
    if not is_enabled():
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic()
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})

    # Cache the system prompt so it isn't re-tokenized on every chat turn.
    runner = client.beta.messages.tool_runner(
        model="claude-opus-4-7",
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=ALL_TOOLS,
        messages=messages,
    )

    final_text = ""
    tool_calls: list[dict[str, Any]] = []
    for msg in runner:
        for block in msg.content:
            if block.type == "text":
                final_text = block.text
            elif block.type == "tool_use":
                tool_calls.append({"tool": block.name, "input": block.input})

    return {"response": final_text or "(no response)", "tool_calls": tool_calls}
