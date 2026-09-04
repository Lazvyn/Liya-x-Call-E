"""
observability/logger.py — Structured execution logging for Liya.

Design:
    • Every event is emitted as a single JSON line to stdout.
      On Cloud Run, stdout is automatically ingested into Google Cloud Logging
      and can be queried with Log Explorer — no extra SDK needed.

    • Every event is also written as a document into Firestore:
          tasks/{task_id}/trace/{auto_id}
      so judges / developers can query the full execution trace of any task
      via the  GET /task/{id}/trace  endpoint.

    • If Firestore is not configured, the same event is kept in an
      in-memory ring buffer (get_trace(task_id)) so the trace is still
      queryable locally — GET /task/{id}/trace falls back to this buffer.

Public API:
    log(event_type, task_id=None, **fields) low-level

    # High-level helpers (used by executor / planner / task_queue):
    log_task_queued(task_id, goal, priority)
    log_task_started(task_id, goal)
    log_task_completed(task_id, goal, duration_secs)
    log_task_failed(task_id, goal, error, replan_attempts)
    log_task_cancelled(task_id, goal)
    log_plan_created(task_id, goal, steps)
    log_replan(task_id, goal, reason, attempt)
    log_step_start(task_id, step_num, tool, description)
    log_step_success(task_id, step_num, tool, result_preview)
    log_step_failure(task_id, step_num, tool, error, attempt)
    log_step_skipped(task_id, step_num, tool)
    log_step_retrying(task_id, step_num, tool, attempt)
    log_http_request(method, path, status_code, duration_ms, task_id=None)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Stdout encoding guard (Windows cp1252 safety)
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_stdout_lock = threading.Lock()

# ---------------------------------------------------------------------------
# In-memory trace buffer (fallback when Firestore isn't configured)
#
# GET /task/{id}/trace previously 400'd whenever Firestore was disabled,
# which meant local/no-GCP runs had no way to inspect a task's execution
# trace even though every event was already being logged to stdout. This
# buffer makes the same trace queryable in-process without Firestore, so
# autonomy is visible locally too, not just when deployed.
# ---------------------------------------------------------------------------
_MAX_TASKS_BUFFERED = 200
_MAX_EVENTS_PER_TASK = 500
_trace_buffer: dict[str, list[dict]] = {}
_trace_buffer_order: list[str] = []
_trace_lock = threading.Lock()


def _buffer_trace(task_id: str | None, record: dict) -> None:
    if not task_id:
        return
    with _trace_lock:
        if task_id not in _trace_buffer:
            _trace_buffer[task_id] = []
            _trace_buffer_order.append(task_id)
            if len(_trace_buffer_order) > _MAX_TASKS_BUFFERED:
                oldest = _trace_buffer_order.pop(0)
                _trace_buffer.pop(oldest, None)
        events = _trace_buffer[task_id]
        events.append(record)
        if len(events) > _MAX_EVENTS_PER_TASK:
            del events[0]


def get_trace(task_id: str) -> list[dict]:
    """Returns the buffered in-memory trace for a task (most recent first
    events last). Empty list if the task hasn't logged anything yet, or
    the buffer has aged it out."""
    with _trace_lock:
        return list(_trace_buffer.get(task_id, []))


# ---------------------------------------------------------------------------
# Core emitter
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_stdout(record: dict) -> None:
    """Write one JSON line to stdout (Cloud Logging picks this up automatically)."""
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _stdout_lock:
            print(line, flush=True)
    except Exception:
        pass  # never crash the agent because of logging


def _emit_firestore(task_id: str | None, record: dict) -> None:
    """
    Append the record as a new document in  tasks/{task_id}/trace/.
    Runs in the calling thread (records are small; write is fast).
    """
    if not task_id:
        return
    try:
        from config.firestore_client import get_db, is_firestore_enabled
        if not is_firestore_enabled():
            return
        db = get_db()
        db.collection("tasks").document(task_id).collection("trace").add(record)
    except Exception:
        pass  # observability must never break the agent


# ---------------------------------------------------------------------------
# Public low-level API
# ---------------------------------------------------------------------------

def log(event_type: str, task_id: str | None = None, **fields: Any) -> None:
    """
    Emit a structured log event.

    Args:
        event_type: e.g. "task.queued", "step.success", "http.request"
        task_id:    optional — links this event to a specific task trace
        **fields:   arbitrary key-value pairs added to the JSON record
    """
    record: dict[str, Any] = {
        "timestamp":  _now_iso(),
        "service":    "liya-agent",
        "event_type": event_type,
    }
    if task_id:
        record["task_id"] = task_id
    record.update(fields)

    _emit_stdout(record)
    _buffer_trace(task_id, record)
    # Fire-and-forget Firestore write
    threading.Thread(
        target=_emit_firestore,
        args=(task_id, record),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Task lifecycle helpers
# ---------------------------------------------------------------------------

def log_task_queued(task_id: str, goal: str, priority: str) -> None:
    log("task.queued", task_id=task_id, goal=goal[:120], priority=priority)


def log_task_started(task_id: str, goal: str) -> None:
    log("task.started", task_id=task_id, goal=goal[:120])


def log_task_completed(task_id: str, goal: str, duration_secs: float) -> None:
    log("task.completed", task_id=task_id, goal=goal[:120],
        duration_secs=round(duration_secs, 2))


def log_task_failed(task_id: str, goal: str, error: str,
                    replan_attempts: int = 0) -> None:
    log("task.failed", task_id=task_id, goal=goal[:120],
        error=error[:300], replan_attempts=replan_attempts)


def log_task_cancelled(task_id: str, goal: str) -> None:
    log("task.cancelled", task_id=task_id, goal=goal[:120])


# ---------------------------------------------------------------------------
# Planning helpers
# ---------------------------------------------------------------------------

def log_plan_created(task_id: str | None, goal: str, steps: list[dict]) -> None:
    step_summary = [
        {"step": s.get("step"), "tool": s.get("tool"), "desc": s.get("description", "")[:60]}
        for s in steps
    ]
    log("plan.created", task_id=task_id, goal=goal[:120],
        step_count=len(steps), steps=step_summary)


def log_replan(task_id: str | None, goal: str, reason: str, attempt: int) -> None:
    log("plan.replan", task_id=task_id, goal=goal[:120],
        reason=reason[:200], replan_attempt=attempt)


# ---------------------------------------------------------------------------
# Step-level helpers
# ---------------------------------------------------------------------------

def log_step_start(task_id: str | None, step_num: Any,
                   tool: str, description: str) -> None:
    log("step.start", task_id=task_id, step=step_num,
        tool=tool, description=description[:120])


def log_step_success(task_id: str | None, step_num: Any,
                     tool: str, result_preview: str) -> None:
    log("step.success", task_id=task_id, step=step_num,
        tool=tool, result_preview=str(result_preview)[:200])


def log_step_failure(task_id: str | None, step_num: Any,
                     tool: str, error: str, attempt: int) -> None:
    log("step.failure", task_id=task_id, step=step_num,
        tool=tool, error=error[:300], attempt=attempt)


def log_step_skipped(task_id: str | None, step_num: Any, tool: str) -> None:
    log("step.skipped", task_id=task_id, step=step_num, tool=tool)


def log_step_retrying(task_id: str | None, step_num: Any,
                      tool: str, attempt: int) -> None:
    log("step.retrying", task_id=task_id, step=step_num,
        tool=tool, attempt=attempt)


# ---------------------------------------------------------------------------
# HTTP request helper (used by FastAPI middleware)
# ---------------------------------------------------------------------------

def log_http_request(method: str, path: str, status_code: int,
                     duration_ms: float, task_id: str | None = None) -> None:
    log("http.request", task_id=task_id,
        method=method, path=path,
        status_code=status_code,
        duration_ms=round(duration_ms, 1))
