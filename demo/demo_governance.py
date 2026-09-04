"""
demo/demo_governance.py — Proves agent/governance.py's allow/confirm/deny
policy is actually enforced on the ADK path, not just present in code.

Context: the designated Taskmaster demo (web_search -> file_controller ->
reminder) only exercises `allow`-tier tools, so governance never visibly
does anything on camera even though it's real, tested code. This script
closes that gap the same way demo_failure_recovery.py closes the replan
gap: it drives a real `confirm`-tier tool (send_message) through the real
ADK agent (agent/adk_runner.py -> agent/adk_tools.py -> agent/governance.py)
under two conditions, so the block and the allow are both visible, live,
not asserted in a docstring.

Run 1 — headless, no consent (the Cloud Run default):
    the ADK agent tries to send a message; agent/adk_tools.py._governed()
    calls the same check_tool_permission() the legacy executor uses;
    governance raises SecurityException; the agent gets back a plain
    "[blocked by governance] ..." string as the tool's result and reports
    the block to the user instead of a stack trace.

Run 2 — same goal, same process, with auto_approve=True (what
POST /task/adk's auto_approve field sets before the run):
    the identical tool call is allowed through.

This is the real code path end to end — no mocked governance, no
hand-picked outcome. The only thing scripted is which tool the goal
nudges the model toward; if the model chooses a different `confirm`-tier
tool for messaging, the pattern (block, then allow) still holds.

Run it:
    python demo/demo_governance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.adk_runner import run_goal_sync   # noqa: E402
from agent import adk_tools                  # noqa: E402

DEMO_GOAL = (
    "Send a WhatsApp message to 'Test Contact' that says "
    "'governance demo message' using the send_message tool."
)


def _run(label: str, auto_approve: bool) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    adk_tools.set_auto_approve(auto_approve)
    try:
        result = run_goal_sync(DEMO_GOAL, session_id=f"gov-demo-{auto_approve}")
        print(result)
    finally:
        adk_tools.set_auto_approve(False)  # same reset backend/server.py does per-request


if __name__ == "__main__":
    print(
        "This script drives Liya's real ADK agent against a confirm-tier\n"
        "tool (send_message) twice: once with no consent (expect a\n"
        "governance block), once with auto_approve=True (expect it to run).\n"
        "Both runs go through agent/governance.check_tool_permission() —\n"
        "nothing here is mocked or hard-coded to succeed."
    )
    _run("Run 1: headless, no consent — expect governance to BLOCK", auto_approve=False)
    _run("Run 2: same goal, auto_approve=True — expect governance to ALLOW", auto_approve=True)
