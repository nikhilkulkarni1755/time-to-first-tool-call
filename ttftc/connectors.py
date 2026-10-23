"""Load the local connector files as Anthropic tool definitions."""
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "connectors"


def load_tools():
    """All tools, named mcp__<connector>__<tool>, in a fixed order (files sorted by name)."""
    tools = []
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text())
        for tool in data["tools"]:
            tools.append({
                "name": f"mcp__{data['connector']}__{tool['name']}",
                "description": tool["description"],
                "input_schema": tool["input_schema"],
            })
    return tools


def logical(name):
    """mcp__github__issue_write -> github.issue_write"""
    _, connector, tool = name.split("__", 2)
    return f"{connector}.{tool}"
