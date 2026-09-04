"""
Centralized Firestore client — single source of truth for Cloud Firestore access.

Usage:
    from config.firestore_client import get_db, is_firestore_enabled

    if is_firestore_enabled():
        db = get_db()
        db.collection("tasks").document("abc").set({"status": "running"})

Auth priority:
    1. GOOGLE_APPLICATION_CREDENTIALS env-var (path to service account JSON)
    2. Application Default Credentials (gcloud auth application-default login)
    3. If project ID is missing or Firestore init fails returns None (graceful fallback)

Project ID is read from config/api_keys.json["firestore_project_id"].
If absent, Firestore is disabled and Liya falls back to local-file storage.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE_DIR = _get_base_dir()
_API_CONFIG_PATH = _BASE_DIR / "config" / "api_keys.json"


def _load_firestore_config() -> tuple[str | None, str]:
    """
    Returns (project_id, user_id). project_id is None when not configured.

    Local/desktop: reads config/api_keys.json (firestore_project_id).
    Cloud Run: that file doesn't exist in the container, so fall back to
    the GOOGLE_CLOUD_PROJECT env var, which cloudbuild.yaml already sets
    (--set-env-vars=GOOGLE_CLOUD_PROJECT=$PROJECT_ID,...).
    """
    try:
        data = json.loads(_API_CONFIG_PATH.read_text(encoding="utf-8"))
        project_id = data.get("firestore_project_id") or None
        user_id = data.get("firestore_user_id", "default") or "default"
        if project_id:
            return project_id, user_id
    except Exception:
        pass

    env_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if env_project:
        return env_project, "default"
    return None, "default"


@lru_cache(maxsize=1)
def _init_client():
    """
    Lazily initialise the Firestore client exactly once.
    Returns (client, project_id, user_id) or (None, None, 'default') on failure.
    """
    project_id, user_id = _load_firestore_config()
    if not project_id:
        print("[Firestore] No project ID configured - running in local-file mode.")
        return None, None, "default"

    try:
        from google.cloud import firestore  # type: ignore
        client = firestore.Client(project=project_id)
        print(f"[Firestore] Connected to project '{project_id}' (user: '{user_id}')")
        return client, project_id, user_id
    except Exception as exc:
        print(f"[Firestore] Init failed - falling back to local files. Reason: {exc}")
        return None, None, "default"


def get_db():
    """Return the Firestore client, or None if not available."""
    client, _, _ = _init_client()
    return client


def get_user_id() -> str:
    """Return the configured Firestore user ID (default: 'default')."""
    _, _, user_id = _init_client()
    return user_id


def is_firestore_enabled() -> bool:
    """True when Firestore is configured and the client initialised successfully."""
    client, _, _ = _init_client()
    return client is not None