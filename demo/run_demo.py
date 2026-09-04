"""
demo/run_demo.py — Liya's designated end-to-end demo scenario.

Goal:
    "Search the web for what judges say separates a winning hackathon
    submission from an average one, save the findings as a
    submission-readiness checklist to a file called
    hackathon_checklist.txt on the Desktop, and set a reminder for
    tonight at 8:00 PM to do a final pass against it."

Why this goal: it's a real, personal chore (the BYOF — Bring Your Own
Friction — problem this project's own team had while finishing this
submission), not a stock example topic, and it still chains three
different tools (web_search → file_controller → reminder) in one
autonomous run, forcing the planner to sequence dependent steps and the
executor to carry them out end-to-end without further input — the core
claim of the whole project.

What this script does, concretely:
    1. Submits the goal to the real TaskQueue (agent/task_queue.py) —
       the same queue backend/server.py's POST /task uses.
    2. Polls task status until it finishes.
    3. Reads back the full execution trace (observability/logger.py) and
       prints it as a readable, step-by-step timeline, so autonomy is
       visible in the terminal rather than only inferred from a final
       answer.

Run it directly (no server needed):
    python demo/run_demo.py

Or against a running local/deployed backend instead, see
demo/run_demo_http.py for the HTTP-only version.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEMO_GOAL = (
    "Search the web for what judges say separates a winning hackathon "
    "submission from an average one, save the findings as a "
    "submission-readiness checklist to a file called "
    "hackathon_checklist.txt on the Desktop, and set a reminder for "
    "tonight at 8:00 PM to do a final pass against it."
)

_EVENT_ICONS = {
    "task.queued":    "📥",
    "task.started":   "▶️ ",
    "plan.created":   "🧭",
    "plan.replan":    "🔁",
    "step.start":     "→ ",
    "step.success":   "✅",
    "step.failure":   "❌",
    "step.retrying":  "🔄",
    "step.skipped":   "⏭️ ",
    "task.completed": "🏁",
    "task.failed":    "💥",
    "task.cancelled": "🚫",
}


def _print_event(ev: dict) -> None:
    icon = _EVENT_ICONS.get(ev.get("event_type", ""), "•")
    etype = ev.get("event_type", "?")
    ts = ev.get("timestamp", "")[11:19]  # HH:MM:SS
    parts = [f"[{ts}] {icon} {etype}"]

    if etype == "plan.created":
        parts.append(f"({ev.get('step_count')} steps)")
        for s in ev.get("steps", []):
            parts.append(f"\n         step {s.get('step')}: [{s.get('tool')}] {s.get('desc')}")
    elif etype in ("step.start", "step.success", "step.failure", "step.retrying", "step.skipped"):
        parts.append(f"step {ev.get('step')} [{ev.get('tool')}]")
        if ev.get("description"):
            parts.append(f"— {ev['description']}")
        if ev.get("result_preview"):
            parts.append(f"→ {ev['result_preview']}")
        if ev.get("error"):
            parts.append(f"— error: {ev['error']}")
    elif etype in ("task.completed", "task.failed"):
        if ev.get("duration_secs") is not None:
            parts.append(f"({ev['duration_secs']}s)")
        if ev.get("error"):
            parts.append(f"— {ev['error']}")

    print(" ".join(parts))


def run() -> None:
    from agent.task_queue import get_queue
    from observability.logger import get_trace

    print("=" * 70)
    print("LIYA — DESIGNATED DEMO SCENARIO")
    print("=" * 70)
    print(f"Goal: {DEMO_GOAL}\n")

    queue = get_queue()
    task_id = queue.submit(goal=DEMO_GOAL)

    print(f"Task submitted: {task_id}")
    print("Watching execution trace live (autonomy in progress)...\n")

    seen = 0
    terminal_statuses = {"completed", "failed", "cancelled"}
    status = "pending"

    while status not in terminal_statuses:
        time.sleep(0.5)
        info = queue.get_status(task_id)
        status = info.get("status", "pending") if info else "pending"

        trace = get_trace(task_id)
        for ev in trace[seen:]:
            _print_event(ev)
        seen = len(trace)

    print("\n" + "=" * 70)
    print(f"FINAL STATUS: {status.upper()}")
    info = queue.get_status(task_id)
    if info and info.get("result"):
        print(f"RESULT: {info['result']}")
    if info and info.get("error"):
        print(f"ERROR: {info['error']}")
    print("=" * 70)


if __name__ == "__main__":
    run()
