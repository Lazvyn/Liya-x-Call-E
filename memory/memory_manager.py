"""
memory_manager.py — Long-term memory for Liya.

Public API (unchanged):
    load_memory() dict
    save_memory(memory)
    update_memory(memory_update) dict
    remember(key, value, category) str
    forget(key, category) str
    format_memory_for_prompt(mem) str

Backend selection (automatic):
    • Firestore  — when config/api_keys.json has "firestore_project_id" set
    • Local JSON — fallback when Firestore is not configured or unavailable

Firestore schema:
    users/{user_id}/memory/{category} {key: {value: "...", updated: "YYYY-MM-DD"}, ...}
"""

from __future__ import annotations

import json
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = _get_base_dir()
MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"
_lock       = Lock()

MAX_VALUE_LENGTH = 380   # per-field cap (applies to both backends)

# Local-file cap (removed for Firestore, kept as soft limit for local mode)
MEMORY_MAX_CHARS = 2200

MEMORY_CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_memory() -> dict:
    return {cat: {} for cat in MEMORY_CATEGORIES}


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    """Trim oldest entries until under MEMORY_MAX_CHARS (local mode only)."""
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        print(f"[Memory] Trimmed {cat}/{key}")
    return memory


def _recursive_update(target: dict, updates: dict) -> bool:
    """Apply nested updates; return True if anything changed."""
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


# ===========================================================================
# Firestore backend
# ===========================================================================

def _firestore_load() -> dict | None:
    """Load full memory from Firestore. Returns None on any failure."""
    try:
        from config.firestore_client import get_db, get_user_id, is_firestore_enabled
        if not is_firestore_enabled():
            return None
        db      = get_db()
        user_id = get_user_id()
        memory  = _empty_memory()
        for cat in MEMORY_CATEGORIES:
            doc = db.collection("users").document(user_id).collection("memory").document(cat).get()
            if doc.exists:
                data = doc.to_dict() or {}
                memory[cat] = data
        return memory
    except Exception as exc:
        print(f"[Memory] Firestore load error: {exc}")
        return None


def _firestore_save(memory: dict) -> bool:
    """
    Save full memory to Firestore using a batch write (atomic per-category).
    Returns True on success, False on failure.
    """
    try:
        from config.firestore_client import get_db, get_user_id, is_firestore_enabled
        if not is_firestore_enabled():
            return False
        db      = get_db()
        user_id = get_user_id()
        batch   = db.batch()
        for cat in MEMORY_CATEGORIES:
            ref = db.collection("users").document(user_id).collection("memory").document(cat)
            batch.set(ref, memory.get(cat, {}))
        batch.commit()
        print(f"[Memory] Firestore save OK (user: {user_id})")
        return True
    except Exception as exc:
        print(f"[Memory] Firestore save error: {exc}")
        return False


# ===========================================================================
# Local-file backend (fallback)
# ===========================================================================

def _local_load() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as exc:
            print(f"[Memory] Local load error: {exc}")
            return _empty_memory()


def _local_save(memory: dict) -> None:
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ===========================================================================
# Public API
# ===========================================================================

def load_memory() -> dict:
    """Load memory from Firestore (preferred) or local JSON (fallback)."""
    fs_mem = _firestore_load()
    if fs_mem is not None:
        return fs_mem
    return _local_load()


def save_memory(memory: dict) -> None:
    """Save memory to Firestore (preferred) or local JSON (fallback)."""
    if not isinstance(memory, dict):
        return
    saved_to_cloud = _firestore_save(memory)
    if not saved_to_cloud:
        _local_save(memory)


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] Updated: {list(memory_update.keys())}")
    return memory


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = set(MEMORY_CATEGORIES)
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


# Alias for backward compatibility
forget_memory = forget


# ===========================================================================
# Prompt formatting (unchanged)
# ===========================================================================

def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 4000:          # raised from 2000 — Firestore has no hard cap
        result = result[:3997] + "…"

    return result + "\n"


# ---------------------------------------------------------------------------
# Conversation history — local transcript log for the Activity Log panel.
# Independent of the memory categories above; always local-file, never
# synced to Firestore (it's just UI convenience, not durable "memory").
# ---------------------------------------------------------------------------
HISTORY_PATH        = BASE_DIR / "memory" / "conversation_history.json"
HISTORY_MAX_STORED  = 500   # lines kept on disk
HISTORY_MAX_SHOWN   = 60    # lines re-displayed in the UI on startup
_history_lock       = Lock()


def append_history_entry(line: str) -> None:
    """Append a single transcript line (e.g. 'You: ...' / 'Liya: ...') to the
    local conversation history file, trimming to HISTORY_MAX_STORED lines."""
    if not line or not line.strip():
        return
    with _history_lock:
        try:
            entries = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = []
        except Exception:
            entries = []

        entries.append({
            "text": line,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        if len(entries) > HISTORY_MAX_STORED:
            entries = entries[-HISTORY_MAX_STORED:]

        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_PATH.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Memory] Could not save conversation history: {e}")


def load_history(limit: int = HISTORY_MAX_SHOWN) -> list[dict]:
    """Return the most recent `limit` conversation history entries,
    each as {"text": ..., "ts": ...}. Returns [] if none exist."""
    try:
        entries = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return []
        return entries[-limit:]
    except Exception:
        return []


def load_history_grouped_by_date() -> "dict[str, list[dict]]":
    """Return ALL stored history entries grouped by calendar date
    (YYYY-MM-DD), most recent date first, each date's entries in
    chronological order. Used to power a 'past sessions' list in the UI."""
    try:
        entries = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return {}
    except Exception:
        return {}

    grouped: dict[str, list[dict]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts = e.get("ts", "")
        date = ts.split(" ")[0] if ts else "unknown"
        grouped.setdefault(date, []).append(e)

    return dict(sorted(grouped.items(), reverse=True))