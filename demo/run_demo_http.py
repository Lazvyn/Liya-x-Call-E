"""
demo/run_demo_http.py — Same demo scenario as run_demo.py, but driven
entirely over HTTP against a running backend/server.py instance (local
or deployed to Cloud Run). Useful for judges who want to see the
backend/server.py endpoints exercised directly, e.g. for the ADK path.

Usage:
    # local:
    LIYA_API_KEY=dev python backend/server.py &
    python demo/run_demo_http.py --url http://localhost:8080 --key dev

    # deployed:
    python demo/run_demo_http.py --url https://YOUR_SERVICE_URL --key YOUR_LIYA_API_KEY

Pass --adk to run the goal through POST /task/adk (the Google ADK agent)
instead of the legacy POST /task + polling loop.
"""
from __future__ import annotations

import argparse
import time

import requests

from run_demo import DEMO_GOAL, _print_event  # noqa: E402


def run_legacy(base_url: str, key: str) -> None:
    headers = {"X-Liya-Key": key, "Content-Type": "application/json"}

    print(f"Goal: {DEMO_GOAL}\n")
    resp = requests.post(f"{base_url}/task", json={"goal": DEMO_GOAL}, headers=headers)
    resp.raise_for_status()
    task_id = resp.json()["task_id"]
    print(f"Task submitted: {task_id}\n")

    seen = 0
    status = "pending"
    while status not in ("completed", "failed", "cancelled"):
        time.sleep(1)
        status_resp = requests.get(f"{base_url}/task/{task_id}", headers=headers)
        status_resp.raise_for_status()
        status = status_resp.json().get("status", "pending")

        trace_resp = requests.get(f"{base_url}/task/{task_id}/trace", headers=headers)
        if trace_resp.ok:
            trace = trace_resp.json().get("trace", [])
            for ev in trace[seen:]:
                _print_event(ev)
            seen = len(trace)

    print(f"\nFINAL STATUS: {status.upper()}")


def run_adk(base_url: str, key: str) -> None:
    headers = {"X-Liya-Key": key, "Content-Type": "application/json"}
    print(f"Goal (via Google ADK agent): {DEMO_GOAL}\n")
    resp = requests.post(f"{base_url}/task/adk", json={"goal": DEMO_GOAL}, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    print(f"Engine: {data.get('engine')}")
    print(f"Result: {data.get('result')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--key", default="dev")
    parser.add_argument("--adk", action="store_true", help="Run via POST /task/adk instead")
    args = parser.parse_args()

    if args.adk:
        run_adk(args.url, args.key)
    else:
        run_legacy(args.url, args.key)
