"""
demo/demo_memory_recall.py - Proves memory changes what Liya plans, not
just that memory is stored somewhere.

Long-term memory (memory/memory_manager.py) has always been readable and
writable, but agent/executor.py's create_plan(goal) call never passed
memory in as context - so a stored preference had no way to influence
planning. This is now fixed (agent/executor.py's _load_memory_context,
agent/adk_agent.py's _memory_instruction_block).

This script demonstrates it end-to-end:
    1. Writes a preference to real long-term memory via memory.remember()
       (Firestore if configured, local memory/long_term.json otherwise -
       same backend production tasks use).
    2. Submits a goal to the real TaskQueue that a preference-aware plan
       would visibly act on differently than a preference-blind one.
    3. Streams the execution trace and prints the "plan.memory_applied"
       event so the memory content that was actually injected into the
       planner's context is visible, not just claimed.

Run it:
    python demo/demo_memory_recall.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_demo import _print_event  # noqa: E402

MEMORY_KEY      = "report_style"
MEMORY_VALUE    = "Prefers short, bullet-point summaries - no long paragraphs."
MEMORY_CATEGORY = "preferences"

DEMO_GOAL = (
    "Search the web for the latest trends in electric vehicles and save "
    "a summary to a file called ev_trends_memory_demo.txt on the Desktop."
)


def run() -> None:
    from memory.memory_manager import remember, load_memory, format_memory_for_prompt
    from agent.task_queue import get_queue
    from observability.logger import get_trace

    print("=" * 70)
    print("LIYA - MEMORY RECALL DEMO")
    print("=" * 70)

    print(f"\n1. Writing a preference to long-term memory:")
    print(f"   {MEMORY_CATEGORY}/{MEMORY_KEY} = \"{MEMORY_VALUE}\"")
    print("   " + remember(MEMORY_KEY, MEMORY_VALUE, MEMORY_CATEGORY))

    context_preview = format_memory_for_prompt(load_memory())
    print(f"\n2. What the planner will actually receive as context:")
    print("   " + context_preview.replace("\n", "\n   "))

    print(f"\n3. Submitting goal:\n   {DEMO_GOAL}\n")
    queue = get_queue()
    task_id = queue.submit(goal=DEMO_GOAL)
    print(f"Task submitted: {task_id}")
    print("Watching execution trace live...\n")

    seen = 0
    terminal_statuses = {"completed", "failed", "cancelled"}
    status = "pending"
    memory_event_seen = False

    while status not in terminal_statuses:
        time.sleep(0.5)
        info = queue.get_status(task_id)
        status = info.get("status", "pending") if info else "pending"

        trace = get_trace(task_id)
        for ev in trace[seen:]:
            if ev.get("event_type") == "plan.memory_applied":
                memory_event_seen = True
                print(f"[{ev.get('timestamp', '')[11:19]}] plan.memory_applied - "
                      f"planner received: {ev.get('memory_context_preview')}")
            else:
                _print_event(ev)
        seen = len(trace)

    print("\n" + "=" * 70)
    print(f"FINAL STATUS: {status.upper()}")
    print(f"MEMORY WAS APPLIED TO PLANNING: {'YES' if memory_event_seen else 'NO - check executor.py wiring'}")
    print("=" * 70)


if __name__ == "__main__":
    run()
