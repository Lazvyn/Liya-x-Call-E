# actions/call_e.py
"""
CALL-E integration — lets Liya place a real outbound phone call to get a
goal-driven phone task done (e.g. "call the recipient and ask whether they
can come in for the 3pm appointment") and get a structured result back.

This wraps CALL-E's stable Calls API directly over HTTP (same surface as the
`calle-ai` 0.2.x SDK, per https://docs.heycall-e.com):

    POST /v1/calls                 -> create a call, returns {"id": ...}
    GET  /v1/calls/{id}            -> read call status + structured_result
    GET  /v1/calls/{id}/events     -> developer-facing call events (used
                                        for the live trace CALL-E supports)

Following the same shape every other action module in this repo uses:

    call_e(parameters: dict, response=None, player=None, session_memory=None) -> ToolResult

so it plugs into agent/executor.py's legacy planner path exactly like
flight_finder.py or send_message.py, and into agent/adk_tools.py's ADK
wrapper the same way.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

from agent.tool_result import ok, fail


# ---------------------------------------------------------------------------
# Config — mirrors config/ai_client.py's precedence: local api_keys.json
# first (desktop), environment variables as the Cloud Run / headless
# fallback (CALLE_API_KEY / CALLE_BASE_URL, matching CALL-E's own SDK/API
# docs so the same env vars work whether you're using the calle CLI, the
# SDK, or this action).
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_API_CONFIG_PATH = _base_dir() / "config" / "api_keys.json"
_DEFAULT_BASE_URL = "https://api.heycall-e.com"


def _load_local_config() -> dict:
    try:
        return json.loads(_API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_calle_api_key() -> Optional[str]:
    cfg = _load_local_config()
    return cfg.get("calle_api_key") or os.environ.get("CALLE_API_KEY")


def _get_calle_base_url() -> str:
    cfg = _load_local_config()
    return (
        cfg.get("calle_base_url")
        or os.environ.get("CALLE_BASE_URL")
        or _DEFAULT_BASE_URL
    )


# ---------------------------------------------------------------------------
# Terminal states a call run can settle into. CALL-E's exact vocabulary may
# shift while the Phase-1 API is in beta, so this list is deliberately
# generous rather than a strict enum match.
# ---------------------------------------------------------------------------
_TERMINAL_STATUSES = {
    "succeeded", "completed", "failed", "canceled", "cancelled", "error",
}


def _headers(api_key: str, idempotency_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }


def _create_call(
    base_url: str,
    api_key: str,
    idempotency_key: str,
    task: str,
    phone: str,
    region: str,
    locale: Optional[str],
    result_schema: Optional[dict],
    webhook_url: Optional[str],
    metadata: Optional[dict],
) -> dict:
    # Current live CALL-E API (Calls API, SDK 0.2.x): recipients is a list,
    # each entry needs a non-empty "phones" array (not a singular "phone"
    # key — that's the deprecated 0.1.x shape and gets rejected with
    # "Extra inputs are not permitted" on the whole recipient object).
    recipient: dict = {"phones": [phone]}
    if region:
        recipient["region"] = region
    if locale:
        recipient["locale"] = locale

    # Single-recipient call: use the task-level result_schema only (skip
    # recipient_result_schema, which is for aggregating structured results
    # across a multi-recipient batch call).
    payload: dict = {"task": task, "recipients": [recipient]}
    if result_schema:
        payload["result_schema"] = result_schema
    if webhook_url:
        payload["webhook_url"] = webhook_url
    if metadata:
        payload["metadata"] = metadata

    resp = requests.post(
        f"{base_url}/v1/calls",
        headers=_headers(api_key, idempotency_key),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_call(base_url: str, api_key: str, call_id: str) -> dict:
    resp = requests.get(
        f"{base_url}/v1/calls/{call_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _poll_until_done(
    base_url: str,
    api_key: str,
    call_id: str,
    timeout_seconds: int,
    speak=None,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_status = None
    poll_interval = 5

    while time.time() < deadline:
        call = _get_call(base_url, api_key, call_id)
        status = str(call.get("status", "")).lower()

        if status != last_status:
            print(f"[CallE] call {call_id} -> {status}")
            last_status = status

        if status in _TERMINAL_STATUSES:
            return call

        time.sleep(poll_interval)

    # Timed out waiting — return what we last saw, marked as not-yet-done
    # rather than raising, so the caller can still report the call_id and
    # let the person check back via GET /v1/calls/{call_id} themselves.
    call = _get_call(base_url, api_key, call_id)
    call.setdefault("status", "pending")
    call["_timed_out"] = True
    return call


def _summarize(call: dict, task: str, phone: str) -> str:
    status = str(call.get("status", "unknown"))
    call_id = call.get("id", "")
    structured = call.get("structured_result") or call.get("structuredResult")
    summary = call.get("summary") or call.get("result_summary")

    lines = [f"CALL-E call {call_id} to {phone}: {status}."]

    if call.get("_timed_out"):
        lines.append(
            "Still running past the wait window — check back with the "
            f"call id ({call_id}) for the final result."
        )
        return " ".join(lines)

    if summary:
        lines.append(str(summary))
    if structured:
        lines.append(f"Structured result: {json.dumps(structured, ensure_ascii=False)}")
    if not summary and not structured:
        lines.append(f"Task was: \"{task}\".")

    return " ".join(lines)


def call_e(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
):
    """Place a real outbound phone call through CALL-E to accomplish a
    goal-driven phone task, and return the (optionally structured) result.

    parameters:
        task           str, required  — what the call should accomplish,
                                         in plain language, e.g. "Ask the
                                         recipient if they can attend the
                                         3pm appointment tomorrow."
        phone           str, required  — recipient phone number, E.164
                                          preferred (e.g. "+14155551234").
        region          str, optional  — recipient region code CALL-E
                                          supports (US, SG, MY, IN, AE, AU,
                                          CA, GB, VN, DE, JP, FR, MX, BR,
                                          ID, PH, KE). Defaults to "US".
        locale          str, optional  — spoken language/locale, e.g. "en-US".
        result_schema   dict, optional — JSON schema CALL-E should fill in
                                          from the call outcome.
        webhook_url     str, optional  — where CALL-E should POST the
                                          terminal result.
        metadata        dict, optional — free-form metadata echoed back.
        wait            bool, optional — poll for completion before
                                          returning (default True).
        timeout_seconds int, optional  — max seconds to poll (default 180).
    """
    params = parameters or {}

    task  = str(params.get("task", "")).strip()
    phone = str(params.get("phone", "")).strip()

    if not task:
        return fail("Please specify what the call should accomplish (the task).")
    if not phone:
        return fail("Please specify the recipient's phone number.")

    api_key = _get_calle_api_key()
    if not api_key:
        return fail(
            "CALL-E is not configured: set calle_api_key in "
            "config/api_keys.json, or the CALLE_API_KEY environment "
            "variable."
        )

    base_url = _get_calle_base_url().rstrip("/")

    region       = str(params.get("region", "US")).strip() or "US"
    locale       = params.get("locale")
    result_schema = params.get("result_schema")
    webhook_url  = params.get("webhook_url")
    metadata     = params.get("metadata")
    wait         = bool(params.get("wait", True))
    timeout_seconds = int(params.get("timeout_seconds", 180))

    idempotency_key = str(params.get("idempotency_key") or uuid.uuid4())

    print(f"[CallE] placing call -> {phone} ({region}): {task[:80]}")
    if player:
        player.write_log(f"[CallE] {phone}: {task[:60]}")
    if speak:
        speak(f"Placing the call now, sir. Task: {task}")

    try:
        created = _create_call(
            base_url, api_key, idempotency_key,
            task, phone, region, locale, result_schema, webhook_url, metadata,
        )
    except requests.exceptions.HTTPError as e:
        detail = ""
        try:
            body = e.response.json()
            # CALL-E's error envelope is {"error": {"code", "message", "details"}}
            err = body.get("error", body)
            detail = err.get("message", "")
            errs = (err.get("details") or {}).get("validation_errors")
            if errs:
                detail += " (" + "; ".join(
                    f"{'.'.join(str(p) for p in ve.get('loc', []))}: {ve.get('msg')}"
                    for ve in errs
                ) + ")"
        except Exception:
            detail = e.response.text[:200] if e.response is not None else str(e)
        return fail(f"CALL-E rejected the call request: {detail or e}")
    except requests.exceptions.RequestException as e:
        return fail(f"Could not reach CALL-E: {e}")

    call_id = created.get("id")
    if not call_id:
        return fail(f"CALL-E did not return a call id: {created}")

    if not wait:
        return ok(
            f"Call to {phone} started (call id {call_id}). "
            f"Task: \"{task}\". Poll GET /v1/calls/{call_id} for the result."
        )

    if speak:
        speak("The call is in progress, sir. I'll let you know once it's done.")

    try:
        final_call = _poll_until_done(base_url, api_key, call_id, timeout_seconds, speak=speak)
    except requests.exceptions.RequestException as e:
        return fail(
            f"Call {call_id} was placed but checking its status failed: {e}"
        )

    summary = _summarize(final_call, task, phone)
    status = str(final_call.get("status", "")).lower()

    if final_call.get("_timed_out"):
        return ok(summary)  # not a failure — the call is still running
    if status in {"failed", "canceled", "cancelled", "error"}:
        return fail(summary)
    return ok(summary)