"""
Liya Agent Backend — FastAPI server for Google Cloud Run.

Endpoints:
    GET /health liveness probe (Cloud Run requires this)
    GET /status agent info + Firestore connectivity
    POST /task submit a background task to the agent
    POST /task/adk run a goal synchronously via Liya's Google ADK agent
    GET /task/{task_id} poll a single task's status
    GET /tasks list recent tasks (from Firestore if enabled)
    POST /memory/remember write a memory entry
    GET /memory read current memory

All heavy work (planner + executor) runs in the existing TaskQueue so
the HTTP request returns immediately with the task_id; the client polls
GET /task/{task_id} to track progress.

Auth: requests must include header  X-Liya-Key: <LIYA_API_KEY>
      where LIYA_API_KEY is an env-var set in Cloud Run secrets.
      Set LIYA_API_KEY=dev to skip auth during local development.
"""

from __future__ import annotations

import os
import sys

# Force UTF-8 stdout so emoji don't crash on Windows cp1252 terminals.
# Cloud Run (Linux) is always UTF-8; this guard only matters locally.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import time
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Path setup — backend/ lives inside the project root
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Liya Agent API",
    description="Cloud Run backend for the Liya autonomous AI agent.",
    version="1.0.0",
)

from fastapi import Request

@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    try:
        from observability.logger import log_http_request
        log_http_request(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms
        )
    except Exception:
        pass
    return response

_START_TIME = time.time()
_LIYA_API_KEY = os.environ.get("LIYA_API_KEY", "dev")

# Optional second key, scoped for hackathon judges specifically — set via
# a separate Secret Manager secret (e.g. `liya-judge-key`) and
# `--set-secrets=LIYA_JUDGE_KEY=liya-judge-key:latest` in cloudbuild.yaml.
# Lets a judge test key be handed out and revoked independently of the
# real LIYA_API_KEY, without needing two deployments. Unset by default —
# only the primary key works until this is explicitly configured.
_LIYA_JUDGE_KEY = os.environ.get("LIYA_JUDGE_KEY", "")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def verify_key(x_liya_key: Optional[str] = Header(default=None)) -> None:
    """Require X-Liya-Key header unless key is 'dev' (local mode).

    Accepts either the primary LIYA_API_KEY or, if configured, the
    separate judge-scoped LIYA_JUDGE_KEY — see note above.
    """
    if _LIYA_API_KEY == "dev":
        return  # skip auth in local dev
    if x_liya_key == _LIYA_API_KEY:
        return
    if _LIYA_JUDGE_KEY and x_liya_key == _LIYA_JUDGE_KEY:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing X-Liya-Key header.")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TaskRequest(BaseModel):
    goal: str
    priority: str = "normal"   # "low" | "normal" | "high"
    auto_approve: bool = False


class MemoryRequest(BaseModel):
    key: str
    value: str
    category: str = "notes"    # identity | preferences | projects | relationships | wishes | notes


class AdkTaskRequest(BaseModel):
    goal: str
    session_id: Optional[str] = None
    auto_approve: bool = False   # same meaning as TaskRequest.auto_approve —
                                  # lets `confirm`-tier tools (e.g. send_message)
                                  # run under headless/Cloud Run governance.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _priority_enum(name: str):
    from agent.task_queue import TaskPriority
    return {
        "high":   TaskPriority.HIGH,
        "low":    TaskPriority.LOW,
    }.get(name.lower(), TaskPriority.NORMAL)


def _uptime_str() -> str:
    secs = int(time.time() - _START_TIME)
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["Observability"])
def root():
    """
    Root route — avoids the default FastAPI/Starlette 404 for anyone who
    hits the bare service URL (e.g. a judge poking around in a browser).
    Not used by the demo script itself; /health and /task/adk are.
    """
    return {
        "service": "liya-agent-backend",
        "status":  "running",
        "docs":    "/docs",
        "health":  "/health",
    }


@app.get("/health", tags=["Observability"])
def health():
    """
    Liveness probe required by Cloud Run.
    Returns 200 immediately so Cloud Run knows the container is alive.
    """
    return {
        "status":  "ok",
        "service": "liya-agent-backend",
        "version": "1.0.0",
        "uptime":  _uptime_str(),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/status", tags=["Observability"])
def status():
    """
    Readiness / status probe.  Reports Firestore + task-queue health.
    """
    from config.firestore_client import is_firestore_enabled, get_user_id
    from agent.task_queue import get_queue

    queue = get_queue()
    fs_ok = is_firestore_enabled()

    return {
        "status":    "ready",
        "firestore": "connected" if fs_ok else "local-file-mode",
        "user_id":   get_user_id() if fs_ok else None,
        "queue": {
            "pending": queue.pending_count(),
        },
        "runtime": {
            "python":   platform.python_version(),
            "platform": platform.system(),
            "uptime":   _uptime_str(),
        },
        "gcp": {
            "project": os.environ.get("GOOGLE_CLOUD_PROJECT", "not-set"),
            "region":  os.environ.get("CLOUD_RUN_REGION", "not-set"),
            "service": os.environ.get("K_SERVICE", "not-set"),
            "revision": os.environ.get("K_REVISION", "not-set"),
        },
    }


@app.post("/task", tags=["Agent"], dependencies=[Depends(verify_key)])
def submit_task(req: TaskRequest):
    """
    Submit a goal to the agent's background task queue.
    Returns immediately with a task_id; poll GET /task/{task_id} for progress.
    """
    from agent.task_queue import get_queue

    if not req.goal or not req.goal.strip():
        raise HTTPException(status_code=400, detail="'goal' must not be empty.")

    queue   = get_queue()
    task_id = queue.submit(
        goal         = req.goal.strip(),
        priority     = _priority_enum(req.priority),
        auto_approve = req.auto_approve,
    )

    return {
        "task_id":  task_id,
        "goal":     req.goal.strip(),
        "status":   "pending",
        "poll_url": f"/task/{task_id}",
    }


@app.post("/task/adk", tags=["Agent"], dependencies=[Depends(verify_key)])
def submit_task_adk(req: AdkTaskRequest):
    """
    Run a goal synchronously through Liya's Google ADK agent
    (agent/adk_agent.py + agent/adk_runner.py), using the same action
    tools as the legacy planner/executor but ADK's own agent loop,
    session handling, and tool-calling.

    Unlike POST /task, this blocks until the ADK agent finishes and
    returns its final text response directly — useful for demoing or
    testing the ADK integration path in isolation from the task queue.

    Every tool call the ADK agent makes is still gated by
    agent/governance.py (see agent/adk_tools.py._governed) — this endpoint
    is not a way to bypass the allow/confirm/deny policy the legacy /task
    path enforces, it uses the exact same check.
    """
    from agent.adk_runner import run_goal_sync
    from agent.adk_tools import set_auto_approve

    if not req.goal or not req.goal.strip():
        raise HTTPException(status_code=400, detail="'goal' must not be empty.")

    set_auto_approve(req.auto_approve)
    try:
        result = run_goal_sync(req.goal.strip(), session_id=req.session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ADK agent run failed: {exc}")
    finally:
        set_auto_approve(False)  # never leak consent across requests

    return {
        "goal":   req.goal.strip(),
        "engine": "google-adk",
        "result": result,
    }


@app.get("/task/{task_id}", tags=["Agent"], dependencies=[Depends(verify_key)])
def get_task(task_id: str):
    """Poll the status of a previously submitted task."""
    from agent.task_queue import get_queue

    info = get_queue().get_status(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return info


@app.get("/task/{task_id}/trace", tags=["Agent"], dependencies=[Depends(verify_key)])
def get_task_trace(task_id: str):
    """
    Retrieve execution trace / audit log for a single task.
    Uses Firestore if configured; otherwise falls back to the in-memory
    trace buffer (observability/logger.py) so the trace is still visible
    on local/no-GCP runs.
    """
    from config.firestore_client import get_db, is_firestore_enabled
    from observability.logger import get_trace as get_local_trace

    if not is_firestore_enabled():
        trace = get_local_trace(task_id)
        if not trace:
            from agent.task_queue import get_queue
            if get_queue().get_status(task_id) is None:
                raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
        return {"task_id": task_id, "trace": trace, "source": "in-memory"}

    try:
        db = get_db()
        docs = db.collection("tasks").document(task_id).collection("trace").order_by("timestamp").stream()
        trace = [doc.to_dict() for doc in docs]
        if not trace:
            # Check if task doc exists
            task_doc = db.collection("tasks").document(task_id).get()
            if not task_doc.exists:
                raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
            return {"task_id": task_id, "trace": [], "source": "firestore"}
        return {"task_id": task_id, "trace": trace, "source": "firestore"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/tasks", tags=["Agent"], dependencies=[Depends(verify_key)])
def list_tasks(history: bool = False):
    """
    List tasks.
    ?history=true query Firestore for full run history (up to 50).
    ?history=false current-session tasks only (default).
    """
    from agent.task_queue import get_queue
    return {"tasks": get_queue().get_all_statuses(from_firestore=history)}


@app.post("/memory/remember", tags=["Memory"], dependencies=[Depends(verify_key)])
def remember_endpoint(req: MemoryRequest):
    """Write a key-value pair into Liya's long-term memory (Firestore-backed)."""
    from memory.memory_manager import remember
    result = remember(req.key, req.value, req.category)
    return {"result": result}


@app.get("/memory", tags=["Memory"], dependencies=[Depends(verify_key)])
def read_memory():
    """Read the agent's full long-term memory."""
    from memory.memory_manager import load_memory
    return {"memory": load_memory()}


# ---------------------------------------------------------------------------
# Entry point  (local dev: python backend/server.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[Liya Backend] Starting on port {port} ...")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
