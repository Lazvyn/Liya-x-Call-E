"""
config_manager.py — API key + Firestore project config management.

Reads/writes  config/api_keys.json  which has the shape:
{
    "gemini_api_key":        "...",
    "os_system":             "windows",
    "firestore_project_id": "my-gcp-project", NEW (optional)
    "firestore_user_id": "default" NEW (optional)
}
"""

import json
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = _get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f" Failed to load api_keys.json: {exc}")
        return {}


def _write_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API — Gemini (unchanged)
# ---------------------------------------------------------------------------

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_api_keys(gemini_api_key: str) -> None:
    data = _read_config()
    data["gemini_api_key"] = gemini_api_key.strip()
    _write_config(data)


def load_api_keys() -> dict:
    return _read_config()


def get_gemini_key() -> str | None:
    return _read_config().get("gemini_api_key")


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


# ---------------------------------------------------------------------------
# Public API — Firestore (new)
# ---------------------------------------------------------------------------

def save_firestore_config(project_id: str, user_id: str = "default") -> None:
    """
    Persist the Firestore project ID and user ID into api_keys.json.
    Call this from the UI setup screen once the user supplies the GCP project.
    After saving, calling config.firestore_client.get_db() (which uses lru_cache)
    won't pick up the change until the process restarts — that's fine for the
    setup-then-restart flow.
    """
    data = _read_config()
    data["firestore_project_id"] = project_id.strip()
    data["firestore_user_id"]    = user_id.strip() or "default"
    _write_config(data)
    print(f"[Config] Firestore project saved: '{project_id}' (user: '{user_id}')")


def get_firestore_config() -> dict:
    """
    Return Firestore config as a dict:
        {"project_id": str | None, "user_id": str}
    """
    data = _read_config()
    return {
        "project_id": data.get("firestore_project_id") or None,
        "user_id":    data.get("firestore_user_id", "default") or "default",
    }


def is_firestore_configured() -> bool:
    """True when a non-empty Firestore project ID is present in config."""
    cfg = get_firestore_config()
    return bool(cfg.get("project_id"))