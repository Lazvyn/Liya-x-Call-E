import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from agent.tool_result   import is_tool_result
from agent.checkpoint_store import save_checkpoint, load_checkpoint, clear_checkpoint
from config.ai_client     import generate, MODEL_FLASH, MODEL_FLASH_LITE


def _load_memory_context() -> str:
    """
    Long-term memory (memory/memory_manager.py) is stored and readable but
    was never actually handed to the planner - create_plan() accepts a
    `context` string, nothing was passing one in. This closes that gap so
    a remembered preference can actually change what the planner decides
    to do, not just sit in Firestore. Never lets a memory read failure
    break planning.
    """
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt
        return format_memory_for_prompt(load_memory())
    except Exception as e:
        print(f"[Executor] Memory context unavailable, planning without it: {e}")
        return ""


def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    system_instruction = (
        "You are an expert Python developer. "
        "Write clean, complete, working Python code. "
        "Use standard library + common packages. "
        "Install missing packages with subprocess + pip if needed. "
        "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
        f"SYSTEM PATHS:\n"
        f"  Desktop   = r'{desktop}'\n"
        f"  Downloads = r'{downloads}'\n"
        f"  Documents = r'{documents}'\n"
        f"  Home      = r'{home}'\n"
    )

    try:
        response = generate(
            MODEL_FLASH,
            f"Write Python code to accomplish this task:\n\n{description}",
            system_instruction=system_instruction,
        )
        code = response.text.strip()
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        print(f"[Executor] Running generated code: {tmp_path}")

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=120, cwd=str(Path.home())
        )

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            return "Task completed successfully."
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        return "Completed."

    except subprocess.TimeoutExpired:
        raise RuntimeError("Generated code timed out after 120 seconds.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")

def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] Injected + translated content")

    return params
def _detect_language(text: str) -> str:
    try:
        response = generate(
            MODEL_FLASH_LITE,
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        )
        return response.text.strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        target_lang = _detect_language(goal)
        print(f"[Executor] Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        response = generate(MODEL_FLASH, prompt)
        translated = response.text.strip()
        print(f"[Executor] Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] Translation failed: {e}")
        return content

_FAILURE_PHRASES = (
    "i couldn't", "i could not", "i can't", "i cannot", "couldn't set",
    "couldn't register", "couldn't parse", "unable to", "wasn't able to",
    "was not able to", "something went wrong", "already passed",
    "in the past", "failed to", "did not work", "didn't work",
    # Non-actionable / needs-more-info responses (e.g. a mis-fired code_helper
    # or dev_agent fallback asking for a parameter it was never given) — these
    # mean nothing happened, not that the step succeeded.
    "please provide", "please specify", "i need more information",
    "missing required", "not enough information", "requires a valid",
    "requires additional", "no file path", "cannot proceed without",
    "no results found", "0 result(s)", "no results were found",
)


class ToolReportedFailure(Exception):
    """Raised when a tool returns a normal string but that string is clearly
    a rejection/failure message, so it isn't silently logged as step.success."""
    pass


def _looks_like_failure(result: str) -> bool:
    if not isinstance(result, str):
        return False
    low = result.lower()
    return any(phrase in low for phrase in _FAILURE_PHRASES)


def _evaluate_result(raw) -> tuple[bool, str]:
    """
    Normalizes a tool's return value into (succeeded, display_text).

    - Migrated tools return a ToolResult dict {"ok": bool, "message": str} —
      trusted directly, no guessing.
    - Legacy tools still return a bare string — fall back to the phrase
      heuristic (best-effort, will miss novel rejection phrasings).
    """
    if is_tool_result(raw):
        return bool(raw["ok"]), str(raw["message"])
    text = str(raw) if raw is not None else ""
    return (not _looks_like_failure(text)), text


def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:

    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=None) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=None) or "Done."
    elif tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=parameters, player=None) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=None) or "Done."

    elif tool == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "dev_agent":
        from actions.dev_agent import dev_agent
        return dev_agent(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        # screen_process now returns a structured ToolResult — previously
        # its return value was discarded here and a fixed success string
        # was always reported, even when capture/session start failed.
        return screen_process(parameters=parameters, player=None)

    elif tool == "send_message":
        from actions.send_message import send_message
        return send_message(parameters=parameters, player=None) or "Done."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."

    elif tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=parameters, player=None) or "Done."

    elif tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=parameters, player=None) or "Done."

    elif tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=parameters, player=None) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    elif tool == "flight_finder":
        from actions.flight_finder import flight_finder
        return flight_finder(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "call_e":
        from actions.call_e import call_e
        return call_e(parameters=parameters, player=None, speak=speak) or "Done."

    else:
        print(f"[Executor] Unknown tool '{tool}' - falling back to generated_code")
        return _run_generated_code(f"Accomplish this task: {parameters}", speak=speak)

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
        task_id:     str | None             = None,
        auto_approve: bool = False, # NEW: for tool governance
        resume: bool = False, # resume from a saved checkpoint
    ) -> str:
        import time as _time
        import os
        from observability.logger import (
            log_plan_created, log_replan,
            log_step_start, log_step_success, log_step_failure,
            log_step_retrying, log_step_skipped,
            log_task_completed, log_task_failed, log_task_cancelled,
        )

        print(f"\n[Executor] Goal: {goal}")
        _start = _time.time()

        replan_attempts = 0
        completed_steps = []
        step_results    = {}
        done_step_nums  = set()

        checkpoint = load_checkpoint(task_id) if (resume and task_id) else None
        if checkpoint:
            # Resuming a previously interrupted run: pick up exactly where
            # it left off instead of re-planning and re-running steps whose
            # side effects (a file write, a sent message, a booked flight)
            # already happened.
            plan             = checkpoint.get("plan") or {"steps": []}
            completed_steps  = list(checkpoint.get("completed_steps") or [])
            step_results     = {int(k): v for k, v in (checkpoint.get("step_results") or {}).items()}
            replan_attempts  = int(checkpoint.get("replan_attempts") or 0)
            done_step_nums   = {s.get("step") for s in completed_steps if s.get("step") is not None}
            print(f"[Executor] ▶ Resuming [{task_id}] from checkpoint — "
                  f"{len(done_step_nums)} step(s) already done")
            if speak:
                speak(f"Picking up where I left off, sir — {len(done_step_nums)} step"
                      f"{'s' if len(done_step_nums) != 1 else ''} already done.")
        else:
            memory_context  = _load_memory_context()
            plan            = create_plan(goal, context=memory_context)

        log_plan_created(task_id, goal, plan.get("steps", []))

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                log_task_failed(task_id or "", goal, "Empty plan", replan_attempts)
                if task_id: clear_checkpoint(task_id)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    log_task_cancelled(task_id or "", goal)
                    return "Task cancelled."
                step_num = step.get("step", "?")

                if step_num in done_step_nums:
                    # Already completed in a prior run (resume) — its side
                    # effect (file write, message sent, booking made, etc.)
                    # already happened, so re-running it would duplicate
                    # that side effect. Skip straight to the next step.
                    print(f"[Executor] Step {step_num}: already done (resumed) — skipping")
                    continue

                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})

                params = _inject_context(params, tool, step_results, goal=goal)

                print(f"\n[Executor] Step {step_num}: [{tool}] {desc}")
                log_step_start(task_id, step_num, tool, desc)

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        # Governance check
                        from agent.governance import check_tool_permission, SecurityException
                        is_headless = os.environ.get("LIYA_HEADLESS", "false").lower() == "true"
                        check_tool_permission(
                            tool=tool,
                            parameters=params,
                            has_ui_consent=auto_approve,
                            is_headless=is_headless
                        )

                        result = _call_tool(tool, params, speak)
                        succeeded, display_text = _evaluate_result(result)

                        if not succeeded:
                            # Tool returned normally but reported failure (either
                            # via structured ok=False, or — for un-migrated tools —
                            # text that matches the legacy rejection heuristic.
                            # Route through normal failure handling instead of
                            # logging step.success.
                            raise ToolReportedFailure(display_text)

                        step_results[step_num] = display_text
                        completed_steps.append({**step, "result": display_text})
                        print(f"[Executor] Step {step_num} done: {display_text[:100]}")
                        log_step_success(task_id, step_num, tool, display_text[:200])
                        step_ok = True
                        if task_id:
                            save_checkpoint(task_id, goal, plan, step_results,
                                             completed_steps, replan_attempts)
                        break

                    except SecurityException as sec_exc:
                        error_msg = str(sec_exc)
                        print(f"[Executor] Security block: {error_msg}")
                        log_step_failure(task_id, step_num, tool, error_msg, attempt)
                        msg = f"Task aborted due to security policy, sir. {error_msg}"
                        if speak: speak(msg)
                        log_task_failed(task_id or "", goal, msg, replan_attempts)
                        if task_id: clear_checkpoint(task_id)
                        return msg

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] Step {step_num} attempt {attempt} failed: {error_msg}")
                        log_step_failure(task_id, step_num, tool, error_msg, attempt)

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            log_step_retrying(task_id, step_num, tool, attempt + 1)
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] Skipping step {step_num}")
                            log_step_skipped(task_id, step_num, tool)
                            completed_steps.append({
                                **step,
                                "result": f"[SKIPPED — not actually completed] {error_msg[:200]}",
                            })
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            log_task_failed(task_id or "", goal, msg, replan_attempts)
                            if task_id: clear_checkpoint(task_id)
                            return msg

                        else:
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak
                                    )
                                    fix_ok, fix_text = _evaluate_result(res)
                                    if not fix_ok:
                                        raise ToolReportedFailure(fix_text)

                                    step_results[step_num] = fix_text
                                    completed_steps.append({**step, "result": fix_text})
                                    log_step_success(task_id, step_num,
                                                     fixed_step["tool"], fix_text[:200])
                                    step_ok = True
                                    if task_id:
                                        save_checkpoint(task_id, goal, plan, step_results,
                                                         completed_steps, replan_attempts)
                                    break
                                except Exception as fix_err:
                                    print(f"[Executor] Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                duration = _time.time() - _start
                log_task_completed(task_id or "", goal, duration)
                if task_id: clear_checkpoint(task_id)
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                log_task_failed(task_id or "", goal,
                                failed_error or "max replans", replan_attempts)
                if task_id: clear_checkpoint(task_id)
                return msg

            if speak: speak("Adjusting my approach, sir.")
            log_replan(task_id, goal, failed_error or "unknown", replan_attempts + 1)

            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        try:
            # Use the ACTUAL result text of each step, not just its planned
            # description — otherwise a rejected/skipped step reads identically
            # to a genuinely successful one and the summary lies about outcome.
            steps_str = "\n".join(
                f"- {s.get('description', '')} -> Result: {s.get('result', '(no result captured)')}"
                for s in completed_steps
            )
            prompt    = (
                f'User goal: "{goal}"\n'
                f"Completed steps (description -> actual result):\n{steps_str}\n\n"
                "Write a single natural sentence summarizing what was accomplished. "
                "If any step's result indicates it was skipped, rejected, or failed, "
                "say so honestly instead of claiming full success. "
                "Address the user as 'sir'. Be direct and accurate."
            )
            response = generate(MODEL_FLASH_LITE, prompt)
            summary  = response.text.strip()
            if speak: speak(summary)
            return summary
        except Exception:
            if speak: speak(fallback)
            return fallback
