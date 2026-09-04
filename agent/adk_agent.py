"""
agent/adk_agent.py — Liya's Google ADK agent definition.

This is a real, runnable google.adk.agents.Agent — not a wrapper that only
imports the package. It shares Liya's action tools (agent/adk_tools.py)
and Gemini client (agent/adk_model.py, config/ai_client.py) with the rest
of the codebase.
"""
from google.adk.agents import Agent

from agent.adk_model import LiyaGemini
from agent.adk_tools import get_liya_adk_tools
from config.ai_client import MODEL_FLASH

LIYA_ADK_INSTRUCTION = """You are Liya, an autonomous AI agent.
Break the user's goal into tool calls and execute them directly.
Use web_search_tool for any information lookup or research.
Use file_controller_tool to read, write, or manage files.
Use open_app_tool to launch desktop applications.
Use reminder_tool to schedule reminders.
Use weather_report_tool for weather questions.
Use code_helper_tool to write, edit, explain, run, or optimize code.
Use dev_agent_tool to build a small multi-file coding project end-to-end.
Call tools directly rather than describing what you would do.
Be concise in your final response to the user.
"""


def _memory_instruction_block() -> str:
    """
    Same long-term memory the legacy planner path now reads
    (agent/executor.py's _load_memory_context) - kept in sync so the ADK
    path isn't a second-class citizen that forgets what the user already
    told Liya. Failure to read memory never blocks agent construction.
    """
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt
        context = format_memory_for_prompt(load_memory())
        return f"\n{context}" if context else ""
    except Exception:
        return ""


def build_liya_agent(name: str = "liya_agent") -> Agent:
    """Constructs the ADK Agent Liya uses for tool-calling execution."""
    return Agent(
        name=name,
        model=LiyaGemini(model=MODEL_FLASH),
        instruction=LIYA_ADK_INSTRUCTION + _memory_instruction_block(),
        tools=get_liya_adk_tools(),
    )
