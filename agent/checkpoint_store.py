"""
agent/checkpoint_store.py — Step-level checkpoint/resume for the executor.

Problem this solves: previously, if a Cloud Run instance restarted (or a
task was cancelled) mid-execution, the entire multi-step plan had to start
over from step 1 on the next run — burning API calls repeating tool calls
that already succeeded, and in the worst case (e.g. send_message,
file writes) re-doing side effects that shouldn't be repeated.

This module persists, after every successfully completed step:
    {
        "goal":            str,
        "plan":            dict,   # the JSON step plan currently being run
        "step_results":    dict,   # {step_num: result_text} for done steps
        "completed_steps": list,   # full step dicts + their results, in order
        "replan_attempts": int,
        "updated_at":      datetime,
    }

keyed by task_id, so agent/executor.py can resume a task from the first
NOT-yet-completed step instead of re-running everything.

Backend selection (same pattern as memory/memory_manager.py and
agent/task_queue.py):
    - Firestore (collection "checkpoints")  — when configured
    - Local JSON file (memory/checkpoints.json) — fallback, never required

Checkpoints are deleted once a task finishes (success, abort, or exhausted
replans) — this is transient resume state, not permanent task history
(that's task_queue.py's Firestore "tasks" collection).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import sys


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE_DIR = _get_base_dir()
_LOCAL_PATH = _BASE_DIR / "memory" / "checkpoints.json"
_lock = Lock()


# ---------------------------------------------------------------------------
# Local-file backend
# ---------------------------------------------------------------------------

def _local_load_all() -> dict:
    if not _LOCAL_PATH.exists():
        return {}
    with _lock:
        try:
            data = json.loads(_LOCAL_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            print(f"[Checkpoint] Local load error: {exc}")
            return {}


def _local_save_all(data: dict) -> None:
    with _lock:
        try:
            _LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LOCAL_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[Checkpoint] Local save error: {exc}")


# ---------------------------------------------------------------------------
# Firestore backend
# ---------------------------------------------------------------------------

def _firestore_save(task_id: str, checkpoint: dict) -> bool:
    try:
        from config.firestore_client import get_db, is_firestore_enabled
        if not is_firestore_enabled():
            return False
        db = get_db()
        doc = dict(checkpoint)
        doc["updated_at"] = datetime.now(timezone.utc)
        db.collection("checkpoints").document(task_id).set(doc)
        return True
    except Exception as exc:
        print(f"[Checkpoint] Firestore save error for [{task_id}]: {exc}")
        return False


def _firestore_load(task_id: str) -> dict | None:
    try:
        from config.firestore_client import get_db, is_firestore_enabled
        if not is_firestore_enabled():
            return None
        db = get_db()
        doc = db.collection("checkpoints").document(task_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as exc:
        print(f"[Checkpoint] Firestore load error for [{task_id}]: {exc}")
        return None


def _firestore_delete(task_id: str) -> bool:
    try:
        from config.firestore_client import get_db, is_firestore_enabled
        if not is_firestore_enabled():
            return False
        db = get_db()
        db.collection("checkpoints").document(task_id).delete()
        return True
    except Exception as exc:
        print(f"[Checkpoint] Firestore delete error for [{task_id}]: {exc}")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_checkpoint(
    task_id: str,
    goal: str,
    plan: dict,
    step_results: dict,
    completed_steps: list,
    replan_attempts: int,
) -> None:
    """Persist progress after a successfully completed step. Best-effort —
    never raises, since losing a checkpoint should degrade to 'start over',
    not crash a task that otherwise succeeded."""
    checkpoint = {
        "goal": goal,
        "plan": plan,
        "step_results": {str(k): v for k, v in step_results.items()},
        "completed_steps": completed_steps,
        "replan_attempts": replan_attempts,
    }
    try:
        if _firestore_save(task_id, checkpoint):
            return
        data = _local_load_all()
        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[task_id] = checkpoint
        _local_save_all(data)
    except Exception as exc:
        print(f"[Checkpoint] save_checkpoint failed for [{task_id}]: {exc}")


def load_checkpoint(task_id: str) -> dict | None:
    """Return the saved checkpoint for task_id, or None if there isn't one."""
    fs = _firestore_load(task_id)
    if fs is not None:
        return fs
    return _local_load_all().get(task_id)


def clear_checkpoint(task_id: str) -> None:
    """Remove a task's checkpoint once it finishes (success/abort/exhausted).
    Best-effort — a leftover checkpoint just means a future resume attempt
    on a reused task_id would be stale, which is harmless since task_ids
    are UUIDs and never reused in practice."""
    deleted_remote = _firestore_delete(task_id)
    if not deleted_remote:
        data = _local_load_all()
        if task_id in data:
            del data[task_id]
            _local_save_all(data)
