"""
task_queue.py — Background task queue for Liya's agent executor.

Public API (unchanged):
    get_queue() -> TaskQueue
    queue.submit(goal, priority, speak, on_complete) -> task_id
    queue.record(goal, status, result, error) -> task_id   # history-only entry
    queue.cancel(task_id) -> bool
    queue.get_status(task_id) -> dict | None
    queue.get_all_statuses(from_firestore=False) -> list[dict]

Firestore persistence (new):
    Every task document is written/updated in Firestore under tasks/{task_id}
    whenever its status changes.  If Firestore is not configured the queue
    behaves exactly as before (in-memory only).

Firestore schema — collection "tasks":
    {task_id}: {
        goal:        str,
        status:      "pending" | "running" | "completed" | "failed" | "cancelled",
        priority:    int   (1=HIGH, 2=NORMAL, 3=LOW),
        created_at:  datetime,
        started_at:  datetime | None,
        finished_at: datetime | None,
        result:      str   (truncated to 500 chars),
        error:       str,
    }
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Any


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class TaskStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW    = 3
    NORMAL = 2
    HIGH   = 1


@dataclass(order=True)
class Task:
    priority:    int
    created_at:  float = field(compare=False)
    task_id:     str   = field(compare=False)
    goal:        str   = field(compare=False)
    status:      TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result:      Any        = field(compare=False, default=None)
    error:       str        = field(compare=False, default="")
    speak:       Any        = field(compare=False, default=None)
    on_complete: Any        = field(compare=False, default=None)
    cancel_flag: threading.Event = field(compare=False, default_factory=threading.Event)
    # Timestamps set during execution
    started_at:  float | None = field(compare=False, default=None)
    finished_at: float | None = field(compare=False, default=None)
    auto_approve: bool = field(compare=False, default=False)
    resume:      bool = field(compare=False, default=False)


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts(epoch: float | None):
    """Convert an epoch float to a UTC datetime, or None."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _persist_task(task: Task) -> None:
    """
    Write/update the Firestore document for *task*.
    Silently skips if Firestore is not enabled.
    Runs in the calling thread — intentionally lightweight.
    """
    try:
        from config.firestore_client import get_db, is_firestore_enabled
        if not is_firestore_enabled():
            return
        db = get_db()
        doc = {
            "goal":        task.goal,
            "status":      task.status.value,
            "priority":    task.priority,
            "created_at":  _ts(task.created_at),
            "started_at":  _ts(task.started_at),
            "finished_at": _ts(task.finished_at),
            "result":      str(task.result or "")[:500],
            "error":       task.error[:500] if task.error else "",
        }
        db.collection("tasks").document(task.task_id).set(doc)
    except Exception as exc:
        print(f"[TaskQueue] Firestore persist error for [{task.task_id}]: {exc}")


# ---------------------------------------------------------------------------
# TaskQueue
# ---------------------------------------------------------------------------

class TaskQueue:
    def __init__(self, max_concurrent: int = 1):
        self._queue:         list[Task]            = []
        self._lock:          threading.Lock        = threading.Lock()
        self._condition:     threading.Condition   = threading.Condition(self._lock)
        self._tasks:         dict[str, Task]       = {}
        self._running:       bool                  = False
        self._worker_thread: threading.Thread | None = None
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._executor       = None

    # ------------------------------------------------------------------
    # Lazy executor
    # ------------------------------------------------------------------

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AgentTaskQueue",
        )
        self._worker_thread.start()
        print("[TaskQueue] Started")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        print("[TaskQueue] Stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        goal:        str,
        priority:    TaskPriority      = TaskPriority.NORMAL,
        speak:       Callable | None   = None,
        on_complete: Callable | None   = None,
        auto_approve: bool             = False,
        resume:      bool              = False,
        resume_task_id: str | None     = None,
    ) -> str:
        """
        resume=True + resume_task_id=<id of a previously interrupted task>
        picks that task's checkpoint back up (agent/checkpoint_store.py)
        instead of starting a fresh plan from step 1 — see
        AgentExecutor.execute(resume=...). Uses the SAME task_id so the
        checkpoint lookup finds it, rather than a new random one.
        """
        task_id = resume_task_id if (resume and resume_task_id) else str(uuid.uuid4())[:8]
        task    = Task(
            priority     = priority.value,
            created_at   = time.time(),
            task_id      = task_id,
            goal         = goal,
            speak        = speak,
            on_complete  = on_complete,
            auto_approve = auto_approve,
            resume       = resume,
        )

        with self._condition:
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._condition.notify()

        # Persist initial state asynchronously so submit() stays fast
        threading.Thread(target=_persist_task, args=(task,), daemon=True).start()

        try:
            from observability.logger import log_task_queued
            log_task_queued(task_id, goal, priority.name)
        except Exception:
            pass

        print(f"[TaskQueue] Task queued: [{task_id}] {goal[:60]}")
        return task_id

    def record(
        self,
        goal:   str,
        status: TaskStatus,
        result: Any = None,
        error:  str = "",
    ) -> str:
        """
        Record a task that already ran synchronously outside the queue's
        worker loop (e.g. a direct one-shot tool/action call such as a
        reminder, weather lookup, app open, etc.) purely so it shows up in
        the Task Queue panel's history. This does NOT get picked up and
        (re)executed by the worker — it's a display/history record only.
        """
        now     = time.time()
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            priority    = TaskPriority.NORMAL.value,
            created_at  = now,
            task_id     = task_id,
            goal        = goal,
            status      = status,
            result      = result,
            error       = error,
            started_at  = now,
            finished_at = now,
        )

        with self._lock:
            self._tasks[task_id] = task

        threading.Thread(target=_persist_task, args=(task,), daemon=True).start()

        try:
            from observability.logger import log_task_queued
            log_task_queued(task_id, goal, "NORMAL")
        except Exception:
            pass

        print(f"[TaskQueue] Recorded: [{task_id}] {goal[:60]} {status.value}")
        return task_id

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            task.cancel_flag.set()
            task.status      = TaskStatus.CANCELLED
            task.finished_at = time.time()

        threading.Thread(target=_persist_task, args=(task,), daemon=True).start()
        print(f"[TaskQueue] Task cancelled: [{task_id}]")
        return True

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return self._task_to_dict(task)

    def get_all_statuses(self, from_firestore: bool = False) -> list[dict]:
        """
        Return task statuses.
        from_firestore=True query Firestore for full history (all past runs).
        from_firestore=False return in-memory tasks only (current run).
        """
        if from_firestore:
            return self._load_history_from_firestore()

        with self._lock:
            return [self._task_to_dict(t) for t in self._tasks.values()]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    # ------------------------------------------------------------------
    # Firestore history
    # ------------------------------------------------------------------

    def _load_history_from_firestore(self) -> list[dict]:
        try:
            from config.firestore_client import get_db, is_firestore_enabled
            if not is_firestore_enabled():
                return self.get_all_statuses(from_firestore=False)
            db   = get_db()
            docs = db.collection("tasks").order_by(
                "created_at",
                direction="DESCENDING",  # type: ignore[arg-type]
            ).limit(50).stream()
            return [
                {
                    "task_id": doc.id,
                    "goal":    (doc.to_dict() or {}).get("goal", "")[:60],
                    "status":  (doc.to_dict() or {}).get("status", ""),
                    "created_at": str((doc.to_dict() or {}).get("created_at", "")),
                }
                for doc in docs
            ]
        except Exception as exc:
            print(f"[TaskQueue] Firestore history load error: {exc}")
            return self.get_all_statuses(from_firestore=False)

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while self._running:
            task = None
            with self._condition:
                while self._running and not self._next_task():
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass

            if task:
                threading.Thread(
                    target=self._run_task,
                    args=(task,),
                    daemon=True,
                    name=f"AgentTask-{task.task_id}",
                ).start()

    def _next_task(self) -> Task | None:
        if self._active_count >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set():
                return task
        return None

    def _run_task(self, task: Task) -> None:
        print(f"[TaskQueue] ▶ Running: [{task.task_id}] {task.goal[:60]}")
        task.started_at = time.time()
        # Persist RUNNING state
        threading.Thread(target=_persist_task, args=(task,), daemon=True).start()

        try:
            from observability.logger import log_task_started
            log_task_started(task.task_id, task.goal)
        except Exception:
            pass

        try:
            executor = self._get_executor()
            result   = executor.execute(
                goal         = task.goal,
                speak        = task.speak,
                cancel_flag  = task.cancel_flag,
                task_id      = task.task_id,
                auto_approve = task.auto_approve,
                resume       = task.resume,
            )

            with self._lock:
                if task.cancel_flag.is_set():
                    task.status = TaskStatus.CANCELLED
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                task.finished_at   = time.time()
                self._active_count -= 1

            threading.Thread(target=_persist_task, args=(task,), daemon=True).start()

            if task.on_complete and not task.cancel_flag.is_set():
                try:
                    task.on_complete(task.task_id, result)
                except Exception as cb_exc:
                    print(f"[TaskQueue] on_complete callback error: {cb_exc}")

            print(f"[TaskQueue] Completed: [{task.task_id}]")

        except Exception as exc:
            with self._lock:
                task.status      = TaskStatus.FAILED
                task.error       = str(exc)
                task.finished_at = time.time()
                self._active_count -= 1

            threading.Thread(target=_persist_task, args=(task,), daemon=True).start()
            print(f"[TaskQueue] Failed: [{task.task_id}] {exc}")

        with self._condition:
            self._condition.notify()

    # ------------------------------------------------------------------
    # Serialisation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _task_to_dict(task: Task) -> dict:
        return {
            "task_id":     task.task_id,
            "goal":        task.goal[:60],
            "status":      task.status.value,
            "result":      str(task.result or "")[:200],
            "error":       task.error,
            "created_at":  str(_ts(task.created_at)),
            "started_at":  str(_ts(task.started_at)) if task.started_at else None,
            "finished_at": str(_ts(task.finished_at)) if task.finished_at else None,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_queue         = TaskQueue()
_queue_started = False
_queue_lock    = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue_started
    with _queue_lock:
        if not _queue_started:
            _queue.start()
            _queue_started = True
    return _queue