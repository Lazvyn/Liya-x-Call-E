"""
demo/demo_checkpoint_resume.py - Proves step-level checkpoint/resume with a
real interrupted task (not a simulated/mocked one).

agent/checkpoint_store.py persists {plan, step_results, completed_steps}
to Firestore (or local JSON fallback) after every successfully completed
step. agent/executor.py's execute(resume=True) reads that checkpoint back
and skips straight past any step whose step number is already in
completed_steps, instead of re-running the whole plan from step 1.

This script proves the loop end-to-end against real execution:
    1. Submits a real multi-step goal.
    2. Cancels it partway through — a genuine interruption, not a fake one
       (mirrors what happens if a Cloud Run instance restarts mid-task).
    3. Re-submits the SAME task_id with resume=True.
    4. Confirms via the real execution trace that the steps already done
       before cancellation are NOT re-run the second time.

Caveat, stated plainly: exactly how many steps complete before the
cancellation lands is timing-dependent, so the number of steps skipped
on resume can vary run to run. The goal deliberately includes one
network-bound step (web_search) between two fast file writes — file
I/O alone completes in tens of milliseconds, too fast for any external
poll loop to reliably land inside, so without a slower step in the
middle the whole task can finish before the first poll tick even fires.
What's guaranteed regardless of timing: whichever steps DID complete
before cancellation are skipped on resume — that's the real
checkpoint/resume code path firing, not a scripted answer.

Run it:
    python demo/demo_checkpoint_resume.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_demo import _print_event  # noqa: E402

DEMO_GOAL = (
    "Create a file called checkpoint_demo_step1.txt on the Desktop with the "
    "text 'step one'. Then search the web for 'current AI agent news'. "
    "Then create a second file called checkpoint_demo_step2.txt on the "
    "Desktop with the text 'step two', then create a third file called "
    "checkpoint_demo_step3.txt on the Desktop with the text 'step three'."
)


def run() -> None:
    from agent.task_queue import get_queue
    from agent.checkpoint_store import load_checkpoint
    from observability.logger import get_trace

    print("=" * 70)
    print("LIYA - CHECKPOINT / RESUME DEMO")
    print("=" * 70)
    print(f"Goal: {DEMO_GOAL}\n")

    queue = get_queue()
    task_id = queue.submit(goal=DEMO_GOAL)
    print(f"Task submitted: {task_id}")
    print("Waiting for the first real step to complete, then cancelling...\n")

    # Don't guess a fixed sleep duration — poll the real trace instead and
    # cancel the instant we see the first genuine step.success. The plan's
    # middle step (web_search) is a real network call, so there's a
    # multi-second gap between step 1 finishing and the whole task
    # finishing — comfortably wider than this poll interval — for the
    # cancel to land inside.
    seen_before_cancel = 0
    cancel_timeout_s   = 30
    waited             = 0.0
    poll_interval      = 0.1
    while waited < cancel_timeout_s:
        time.sleep(poll_interval)
        waited += poll_interval
        trace = get_trace(task_id)
        for ev in trace[seen_before_cancel:]:
            _print_event(ev)
        seen_before_cancel = len(trace)
        if any(ev.get("event_type") == "step.success" for ev in trace):
            break
        info = queue.get_status(task_id)
        if info and info.get("status") in ("completed", "failed", "cancelled"):
            break  # finished before we ever got to cancel — nothing to interrupt

    queue.cancel(task_id)
    print(f"\nCancelled [{task_id}] mid-run.\n")

    # Let the cancellation land.
    time.sleep(1)

    checkpoint = load_checkpoint(task_id)
    done_before = len(checkpoint.get("completed_steps", [])) if checkpoint else 0
    print(f"Checkpoint after interruption: {done_before} step(s) saved as done.\n")

    print("-" * 70)
    print(f"Re-submitting [{task_id}] with resume=True ...")
    print("-" * 70 + "\n")

    queue.submit(
        goal=DEMO_GOAL,
        resume=True,
        resume_task_id=task_id,
    )

    seen = seen_before_cancel  # don't re-print events from before cancellation
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
    print(f"STEPS ALREADY DONE BEFORE INTERRUPTION: {done_before}")
    if done_before == 0:
        print("\nNo step completed before the cancel landed this run, so there")
        print("was nothing to resume from — the second run replanned from")
        print("scratch, which is correct behavior, just not a resume proof.")
        print("Re-run the script — the middle web_search step should normally")
        print("leave a multi-second window for the poll loop to catch.")
    else:
        print("Check the console output above for lines like:")
        print('  "[Executor] Step N: already done (resumed) — skipping"')
        print("— that's the checkpoint actually being read back and honored,")
        print("not the plan being re-run from scratch.")
    print("=" * 70)


if __name__ == "__main__":
    run()
