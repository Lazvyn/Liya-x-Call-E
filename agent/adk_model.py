"""
agent/adk_model.py — Wires Google ADK's model layer to Liya's existing
Gemini client instead of duplicating API-key handling.

ADK's `Gemini` model normally builds its own `google.genai.Client` from
GOOGLE_API_KEY / GEMINI_API_KEY env vars. Liya already centralises key
loading + client caching in config/ai_client.py (api_keys.json), so we
subclass `Gemini` and override `api_client` to reuse that single client.
This is the officially documented ADK extension point for custom client
construction (see google.adk.models.google_llm.Gemini docstring).
"""
from functools import cached_property

from google.adk.models import Gemini
from google.genai import Client

from config.ai_client import get_client


class LiyaGemini(Gemini):
    """Gemini model for ADK agents, backed by Liya's shared genai Client."""

    @cached_property
    def api_client(self) -> Client:
        return get_client()
