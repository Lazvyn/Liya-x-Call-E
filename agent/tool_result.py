"""
Structured result contract for action/tool functions.

Historically every action function returned a bare string, and the executor
guessed success/failure by checking the string against a growing blacklist
of "sounds like a rejection" phrases (see agent/executor.py _looks_like_failure).
That's inherently fragile — any new phrasing a tool (or an LLM inside a tool)
produces can silently slip past the blacklist and get logged as step.success.

Tools are being migrated one at a time to return `ok(...)` / `fail(...)`
instead of a bare string. The executor understands both: a dict with an
"ok" key is trusted directly; a bare string falls back to the legacy
heuristic for tools not yet migrated.
"""

from typing import TypedDict


class ToolResult(TypedDict):
    ok: bool
    message: str


def ok(message: str) -> ToolResult:
    return {"ok": True, "message": message}


def fail(message: str) -> ToolResult:
    return {"ok": False, "message": message}


def is_tool_result(value) -> bool:
    return isinstance(value, dict) and "ok" in value and "message" in value
