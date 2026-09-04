import subprocess
import sys
import json
import re
import time
from pathlib import Path

from config.ai_client import generate, get_client, MODEL_FLASH
from agent.tool_result import ok, fail


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

DESKTOP            = Path.home() / "Desktop"
MAX_BUILD_ATTEMPTS = 3
GEMINI_MODEL       = MODEL_FLASH


class _GeminiModel:
    """Thin shim so existing `model.generate_content(prompt)` call sites in
    this file don't need to change — backed by config.ai_client's shared
    google-genai client instead of the deprecated google.generativeai SDK."""

    def __init__(self, model: str):
        self._model = model

    def generate_content(self, contents):
        return generate(self._model, contents)


def _get_gemini(model: str = GEMINI_MODEL):
    return _GeminiModel(model)


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c",
        "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
        "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    if output_path:
        p = Path(output_path)
        return p if p.is_absolute() else DESKTOP / p
    ext = ext_map.get((language or "python").lower(), ".py")
    return DESKTOP / f"liya_code{ext}"


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _save_file(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Saved to: {path}"
    except Exception as e:
        return f"Could not save: {e}"


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _has_error(output: str) -> bool:
    error_signals = ["error", "exception", "traceback", "syntaxerror",
                     "nameerror", "typeerror", "stderr", "failed", "crash"]
    return any(s in output.lower() for s in error_signals)


def _take_screenshot() -> Path | None:
    try:
        import pyautogui
        screenshot_path = Path.home() / "Desktop" / f"liya_debug_{int(time.time())}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(str(screenshot_path))
        print(f"[Code] Screenshot: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        print(f"[Code] Screenshot failed: {e}")
        return None


def _image_to_base64(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _detect_intent(description: str, file_path: str, code: str) -> str:
    desc = (description or "").lower()

    screen_kw = ["screen", "ekrandaki", "ekranda", "bu hatay", "why am i getting",
                 "neden hata", "what wrong", "ne yanl", "screenshot"]
    if any(k in desc for k in screen_kw):
        return "screen_debug"

    optimize_kw = ["optimize", "refactor", "clean up", "improve", "temizle",
                   "iyilestir", "daha iyi", "make it better", "hizlandir"]
    if any(k in desc for k in optimize_kw) and (code or file_path):
        return "optimize"

    if file_path:
        p = Path(file_path)
        edit_kw  = ["edit", "update", "modify", "change", "add", "remove",
                    "refactor", "fix", "rename", "replace"]
        run_kw   = ["run", "execute", "launch", "start"]
        build_kw = ["build", "make it work", "try", "attempt"]

        if p.exists() and any(k in desc for k in edit_kw):
            return "edit"
        if p.exists() and any(k in desc for k in run_kw):
            return "run"
        if any(k in desc for k in build_kw):
            return "build"
        if p.exists():
            return "explain"

    explain_kw = ["explain", "what does", "describe", "analyze", "what is"]
    if any(k in desc for k in explain_kw) and (code or file_path):
        return "explain"

    build_kw = ["build", "make it work", "try and", "attempt"]
    if any(k in desc for k in build_kw):
        return "build"

    return "write"


def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Add helpful inline comments.
- Handle errors and edge cases properly.
- Use modern best practices.

Description: {description}

Code:"""

    response = model.generate_content(prompt)
    code     = _clean_code(response.text)
    path     = _resolve_save_path(output_path, lang)
    _save_file(path, code)
    return code, path


def _fix_code(code: str, error_output: str, description: str) -> str:
    model  = _get_gemini()
    prompt = f"""You are an expert debugger.
The code below failed with the following error. Fix it.
Return ONLY the corrected code — no explanation, no markdown, no backticks.

Original goal: {description}

Error:
{error_output[:2000]}

Broken code:
{code}

Fixed code:"""

    response = model.generate_content(prompt)
    return _clean_code(response.text)


def _run_file(path: Path, args: list, timeout: int) -> str:
    interpreters = {
        ".py":  [sys.executable],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }
    interp = interpreters.get(path.suffix.lower())
    if not interp:
        return f"No interpreter for {path.suffix}."

    try:
        result = subprocess.run(
            interp + [str(path)] + (args or []),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent)
        )
        output = result.stdout.strip()
        error  = result.stderr.strip()
        parts  = []
        if output: parts.append(f"Output:\n{output}")
        if error:  parts.append(f"Stderr:\n{error}")
        return "\n\n".join(parts) if parts else "Executed with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interp[0]}."
    except Exception as e:
        return f"Execution error: {e}"


def _build(description, language, output_path, args, timeout, speak=None, player=None) -> str:
    if not description:
        return "Please describe what you want me to build."

    if player:
        player.write_log("[Code] Build started...")

    lang = language or "python"

    try:
        code, path = _write(description, lang, output_path, player)
        print(f"[Code] Written: {path}")
    except Exception as e:
        msg = f"Could not write initial code: {e}"
        if speak: speak(msg)
        return msg

    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"[Code] Attempt {attempt}/{MAX_BUILD_ATTEMPTS}")
        if player:
            player.write_log(f"[Code] Attempt {attempt}...")

        last_output = _run_file(path, args, timeout)

        if not _has_error(last_output):
            msg = (
                f"Build complete! The code is working after {attempt} "
                f"attempt{'s' if attempt > 1 else ''}. Saved to {path}."
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        print(f"[Code] Error on attempt {attempt}, fixing...")
        if player:
            player.write_log(f"[Code] Fixing (attempt {attempt})...")

        try:
            code = _fix_code(code, last_output, description)
            _save_file(path, code)
        except Exception as e:
            msg = f"Could not fix code on attempt {attempt}: {e}"
            if speak: speak(msg)
            return msg

    msg = (
        f"Unable to build a working version after {MAX_BUILD_ATTEMPTS} attempts. "
        f"Last error: {last_output[:200]}"
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast code saved to: {path}"


def _write_action(description, language, output_path, player) -> str:
    if not description:
        return "Please describe what you want me to write."
    if player:
        player.write_log("[Code] Writing code...")
    try:
        code, path = _write(description, language, output_path, player)
        print(f"[Code] Written: {path}")
        return f"Code written and saved to: {path}\n\nPreview:\n{_preview(code)}"
    except Exception as e:
        return f"Could not generate code: {e}"


def _edit_action(file_path, instruction, player) -> str:
    if not file_path:
        return "Please provide a file path to edit."
    if not instruction:
        return "Please describe what change to make."

    content, err = _read_file(file_path)
    if err:
        return err

    if player:
        player.write_log("[Code] Editing file...")

    model  = _get_gemini()
    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no explanation, no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""

    try:
        response = model.generate_content(prompt)
        edited   = _clean_code(response.text)
    except Exception as e:
        return f"Could not edit code: {e}"

    status = _save_file(Path(file_path), edited)
    print(f"[Code] Edited: {file_path}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain."

    if player:
        player.write_log("[Code] Analyzing code...")

    model  = _get_gemini()
    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Could not explain code: {e}"


def _run_action(file_path, args, timeout, player) -> str:
    if not file_path:
        return "Please provide a file path to run."
    p = Path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"
    if player:
        player.write_log(f"[Code] Running {p.name}...")
    return _run_file(p, args, timeout)


def _optimize_action(file_path, code, language, output_path, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to optimize."

    if player:
        player.write_log("[Code] Optimizing code...")

    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for performance, readability, and best practices.
Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""

    try:
        response  = model.generate_content(prompt)
        optimized = _clean_code(response.text)
    except Exception as e:
        return f"Could not optimize code: {e}"

    save_path = Path(file_path) if file_path else _resolve_save_path(output_path, lang)
    status    = _save_file(save_path, optimized)
    print(f"[Code] Optimized: {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )


def _screen_debug_action(description, file_path, player, speak=None) -> str:
    if player:
        player.write_log("[Code] Taking screenshot for analysis...")

    screenshot_path = _take_screenshot()
    if not screenshot_path:
        return "Could not take screenshot. Please make sure PyAutoGUI is installed."

    file_content = ""
    if file_path:
        file_content, err = _read_file(file_path)
        if err:
            print(f"[Code] Could not read file: {err}")

    try:
        from google.genai import types

        client = get_client()

        image_bytes = screenshot_path.read_bytes()

        user_question = description or "What error or problem do you see on the screen? How can it be fixed?"

        context = ""
        if file_content:
            context = f"\n\nRelated file content:\n```\n{file_content[:4000]}\n```"

        analysis_prompt = f"""You are an expert programmer analyzing a screenshot.

User question: {user_question}{context}

Please:
1. Identify any errors or problems visible on screen
2. Explain what is causing the problem
3. Provide a concrete fix or solution
4. If there is code visible, show the corrected version"""

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            analysis_prompt,
        ]

        response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=contents,
        )

        analysis = response.text.strip()

        try:
            screenshot_path.unlink()
        except Exception:
            pass

        if file_path and file_content:
            code_match = re.search(r"```[a-zA-Z]*\n(.*?)```", analysis, re.DOTALL)
            if code_match:
                fixed_code = code_match.group(1).strip()
                _save_file(Path(file_path), fixed_code)
                analysis += f"\n\nFixed code saved to: {file_path}"

        return analysis

    except Exception as e:
        try:
            screenshot_path.unlink()
        except Exception:
            pass
        return f"Screen analysis failed: {e}"


# Every negative-outcome prefix this module's internal action functions can
# return. Exact and authoritative — not a guess — same pattern as
# file_controller.py. Deliberately does NOT use the fuzzy _has_error()
# keyword heuristic above, which exists only to decide whether to loop
# another build/fix attempt, not to judge the final tool-level outcome —
# a successful run's stdout/stderr can legitimately contain words like
# "error" without the step having failed.
_ERROR_PREFIXES = (
    "Please describe what you want me to write.",
    "Please describe what you want me to build.",
    "Please provide a file path to edit.",
    "Please describe what change to make.",
    "Please provide code or a file path to explain.",
    "Please provide a file path to run.",
    "Please provide code or a file path to optimize.",
    "No file path provided.",
    "File not found:",
    "Could not read file:",
    "Could not generate code:",
    "Could not edit code:",
    "Could not explain code:",
    "Could not optimize code:",
    "Could not write initial code:",
    "Could not fix code on attempt",
    "Could not take screenshot.",
    "Screen analysis failed:",
    "No interpreter for",
    "Timed out after",
    "Interpreter not found:",
    "Execution error:",
    "Unable to build a working version",
    "Unknown action:",
)


def _is_error_result(text: str) -> bool:
    return any(text.startswith(p) for p in _ERROR_PREFIXES)


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
):
    result = _code_helper_impl(parameters, response, player, session_memory, speak)
    return fail(result) if _is_error_result(result) else ok(result)


def _code_helper_impl(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    p           = parameters or {}
    action      = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language    = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path   = p.get("file_path", "").strip()
    code        = p.get("code", "").strip()
    args        = p.get("args", [])
    timeout     = int(p.get("timeout", 30))

    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] Auto-detected action: {action}")

    if action == "write":
        return _write_action(description, language, output_path, player)
    elif action == "edit":
        return _edit_action(file_path, description or p.get("instruction", ""), player)
    elif action == "explain":
        return _explain_action(file_path, code, player)
    elif action == "run":
        return _run_action(file_path, args, timeout, player)
    elif action == "build":
        return _build(description, language, output_path, args, timeout, speak, player)
    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)
    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)
    else:
        return f"Unknown action: '{action}'. Use write, edit, explain, run, build, optimize, or screen_debug."