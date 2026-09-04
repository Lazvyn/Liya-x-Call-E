"""
agent/adk_runner.py — Executes a goal through Liya's ADK agent.

This is the actual ADK execution path: builds a session via
InMemoryRunner, sends the goal as the user turn, and streams events from
`Runner.run_async` until the agent produces its final response. Tool
calls the model makes along the way run through agent/adk_tools.py,
which in turn calls Liya's real action modules — so this exercises the
whole loop, not a mock.
"""
from __future__ import annotations

import asyncio
import uuid

from google.adk.runners import InMemoryRunner
from google.genai import types

from agent.adk_agent import build_liya_agent

_APP_NAME = "liya"


async def run_goal_async(goal: str, user_id: str = "local-user",
                          session_id: str | None = None) -> str:
    """Runs a single goal through the ADK agent and returns its final reply."""
    runner = InMemoryRunner(agent=build_liya_agent(), app_name=_APP_NAME)
    session_id = session_id or str(uuid.uuid4())

    await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id,
    )

    message = types.Content(role="user", parts=[types.Part(text=goal)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text = part.text

    return final_text or "(Liya's ADK agent produced no text response.)"


def run_goal_sync(goal: str, user_id: str = "local-user",
                   session_id: str | None = None) -> str:
    """Synchronous convenience wrapper for callers that aren't async
    (e.g. FastAPI endpoints defined with `def`, not `async def`)."""
    return asyncio.run(run_goal_async(goal, user_id=user_id, session_id=session_id))
