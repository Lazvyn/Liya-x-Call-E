"""
demo/demo_call_e.py — Proves the new call_e tool (actions/call_e.py) is
wired through the exact same governance gate every other confirm-tier tool
uses, on the real ADK path (agent/adk_runner.py -> agent/adk_tools.py ->
agent/governance.py), the same pattern demo_governance.py already
establishes for send_message.

Run 1 — headless, no consent (the Cloud Run default):
    the agent tries to place a call via call_e_tool; agent/adk_tools.py's
    _governed() calls the real check_tool_permission(); governance raises
    SecurityException *before* any HTTP request reaches CALL-E; the agent
    gets back a plain "[blocked by governance] ..." string, not a stack
    trace and not a real phone call.

Run 2 — same goal, auto_approve=True:
    governance lets the call through, so call_e.call_e() actually runs and
    calls CALL-E's Developer API. This will place a REAL outbound call if
    CALLE_API_KEY / CALLE_BASE_URL are configured with a live phone number
    — by design this run is skipped unless --live is passed, specifically
    so this script is safe to run in CI or on a judge's machine without
    accidentally ringing a real phone.

Run it:
    python demo/demo_call_e.py            # governance-block run only
    python demo/demo_call_e.py --live     # also places a real call
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.adk_runner import run_goal_sync   # noqa: E402
from agent import adk_tools                  # noqa: E402

DEMO_GOAL = (
    "Use the call_e tool to call +15550001234 and ask whether they can "
    "attend the 3pm meeting tomorrow."
)


def _run(label: str, auto_approve: bool) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    adk_tools.set_auto_approve(auto_approve)
    try:
        result = run_goal_sync(DEMO_GOAL, session_id=f"calle-demo-{auto_approve}")
        print(result)
    finally:
        adk_tools.set_auto_approve(False)  # same reset backend/server.py does per-request


if __name__ == "__main__":
    live = "--live" in sys.argv

    print(
        "This script drives Liya's real ADK agent against the new call_e\n"
        "(confirm-tier) tool. Run 1 proves governance blocks it with no\n"
        "consent, exactly like send_message. Run 2 (only with --live) proves\n"
        "auto_approve=True lets the real CALL-E API call through — nothing\n"
        "here is mocked."
    )
    _run("Run 1: headless, no consent — expect governance to BLOCK", auto_approve=False)

    if live:
        _run(
            "Run 2: same goal, auto_approve=True — expect a REAL CALL-E call",
            auto_approve=True,
        )
    else:
        print(
            "\nSkipping Run 2 (would place a real phone call). "
            "Re-run with --live and CALLE_API_KEY/CALLE_BASE_URL set to see it."
        )
