"""
demo/demo_failure_recovery.py - Proves the failure -> analyze -> replan ->
recover loop with a real, induced failure (not a simulated/mocked one).

agent/error_handler.py's retry/skip/replan/abort decision engine and
agent/executor.py's MAX_REPLAN_ATTEMPTS replan loop already exist and are
real code paths used by every task - but nothing in the repo forced one
to actually fire and showed the resulting trace. This script closes that
gap: it hands Liya a goal whose first obvious approach targets a path
that cannot be written to, which raises inside actions/file_controller.py
during the real step execution (no mocking), and streams the trace so
the step.failure -> plan.replan -> step.success sequence is visible as
it happens.

Caveat, stated plainly: the exact plan the planner produces is an LLM
call (agent/planner.py), not hand-scripted, so which step fails and how
many replans occur can vary run to run. What's guaranteed is that IF a
step fails, the real error_handler/replan path - not a demo stand-in -
is what handles it. That's the honest claim this script can make.

Run it:
    python demo/demo_failure_recovery.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_demo import _print_event  # noqa: E402

_UNWRITABLE_PATH_WINDOWS = r"C:\Windows\System32\liya_demo_should_fail.txt"
_UNWRITABLE_PATH_POSIX   = "/root/liya_demo_should_fail.txt"

DEMO_GOAL = (
    f"Write the text 'failure recovery demo' to the file at "
    f"'{_UNWRITABLE_PATH_WINDOWS}' (or '{_UNWRITABLE_PATH_POSIX}' on "
    f"mac/Linux). If that path cannot be written to, instead save the "
    f"same text to a file called failure_recovery_demo.txt on the Desktop."
)


def run() -> None:
    from agent.task_queue import get_queue
    from observability.logger import get_trace

    print("=" * 70)
    print("LIYA - FAILURE -> RECOVERY -> REPLAN DEMO")
    print("=" * 70)
    print(f"Goal: {DEMO_GOAL}\n")
    print("Note: which step fails (if any) depends on the planner's LLM-")
    print("generated plan for this run - that's expected, not a bug. What")
    print("this proves is that IF a step fails, the real error_handler.py")
    print("+ executor.py replan loop handles it, live, on camera.\n")

    queue = get_queue()
    task_id = queue.submit(goal=DEMO_GOAL)
    print(f"Task submitted: {task_id}")
    print("Watching execution trace live...\n")

    seen = 0
    terminal_statuses = {"completed", "failed", "cancelled"}
    status = "pending"
    saw_failure = False
    saw_replan = False

    while status not in terminal_statuses:
        time.sleep(0.5)
        info = queue.get_status(task_id)
        status = info.get("status", "pending") if info else "pending"

        trace = get_trace(task_id)
        for ev in trace[seen:]:
            if ev.get("event_type") == "step.failure":
                saw_failure = True
            if ev.get("event_type") == "plan.replan":
                saw_replan = True
            _print_event(ev)
        seen = len(trace)

    print("\n" + "=" * 70)
    print(f"FINAL STATUS: {status.upper()}")
    print(f"OBSERVED A REAL STEP FAILURE THIS RUN: {'YES' if saw_failure else 'no (planner avoided the bad path outright)'}")
    print(f"OBSERVED A REAL REPLAN THIS RUN:        {'YES' if saw_replan else 'no'}")
    if not saw_failure:
        print("\nTip: if the planner routed around the bad path without ever")
        print("attempting it, re-run - LLM planning is non-deterministic -")
        print("or check the full trace via GET /task/{task_id}/trace.")
    print("=" * 70)


if __name__ == "__main__":
    run()
