"""
agent/governance.py — Security & governance engine for Liya's tools.

Enforces rules regarding which tools the agent is permitted to run,
helping to satisfy the "Security/governance" and "Tool/action governance" gaps.

Policy actions:
    - "allow" run tool immediately
    - "confirm" require confirmation (passes in local UI, fails in headless/cloud unless pre-approved)
    - "deny" block execution and raise a SecurityException

The policy is loaded from config/api_keys.json under "tool_governance".
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

# Custom Security exception
class SecurityException(Exception):
    pass


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE_DIR = _get_base_dir()
_API_CONFIG_PATH = _BASE_DIR / "config" / "api_keys.json"

# Default hardcoded safe defaults
DEFAULT_POLICY = {
    # Safe tools
    "web_search":        "allow",
    "weather_report":    "allow",
    "flight_finder":     "allow",
    "youtube_video":     "allow",
    "open_app":          "allow",

    # Semi-safe (potential side-effects)
    "file_controller":   "allow",
    "send_message":      "confirm",
    "reminder":          "allow",

    # Places a real outbound phone call to a real person — always confirm,
    # same tier as send_message and for the same reason (irreversible
    # real-world side effect), auto-denied in headless/cloud unless the
    # request explicitly set auto_approve=true.
    "call_e":            "confirm",

    # Risky tools (computer settings, control, shell script execution)
    "computer_settings": "confirm",
    "computer_control":  "confirm",
    "desktop_control":   "confirm",
    "code_helper":       "confirm",
    "dev_agent":         "confirm",
    "generated_code":    "confirm",
}


def load_governance_policy() -> dict[str, str]:
    """Load policy dict from config file, falling back to DEFAULT_POLICY."""
    if not _API_CONFIG_PATH.exists():
        return DEFAULT_POLICY.copy()
    try:
        data = json.loads(_API_CONFIG_PATH.read_text(encoding="utf-8"))
        user_policy = data.get("tool_governance")
        if isinstance(user_policy, dict):
            policy = DEFAULT_POLICY.copy()
            policy.update({k: str(v).lower() for k, v in user_policy.items()})
            return policy
        return DEFAULT_POLICY.copy()
    except Exception:
        return DEFAULT_POLICY.copy()


def check_tool_permission(
    tool: str,
    parameters: dict,
    has_ui_consent: bool = False,
    is_headless: bool = False
) -> None:
    """
    Check if a tool is allowed to run with given parameters.
    Raises SecurityException if access is denied or confirmation is missing.
    """
    policy = load_governance_policy()
    action = policy.get(tool, "confirm")  # Default to confirm for unknown tools

    # 1. Deny block
    if action == "deny":
        raise SecurityException(
            f"Execution of tool '{tool}' is explicitly blocked by the security policy."
        )

    # 2. Confirm block
    if action == "confirm":
        # Check if we are running in headless/cloud environment
        if is_headless:
            # Under headless environment (e.g. Cloud Run), confirm-level tools
            # cannot prompt the user unless they were pre-approved or has_ui_consent is True.
            if not has_ui_consent:
                raise SecurityException(
                    f"Execution of risky tool '{tool}' requires user confirmation, "
                    f"but the agent is running in headless cloud mode."
                )
        else:
            # In local GUI/voice mode, if they haven't explicitly consented, raise exception.
            # (The calling executor can intercept this and ask the user for consent).
            if not has_ui_consent:
                raise SecurityException(
                    f"Risky tool '{tool}' requires user consent before execution."
                )

    # 3. Allow block
    # Tool is allowed; return normally.
    return
