# Liya + `appointment-call-confirm` — Write-up

**Hackathon: CALL-E: Your Code Is Calling.** The actual judged
submission is [`hackathon-submission/skills/appointment-call-confirm/`](hackathon-submission/skills/appointment-call-confirm/)
— a standalone Agent Skill, PR-ready for
[`CALLE-AI/awesome-phone-call-agents`](https://github.com/CALLE-AI/awesome-phone-call-agents),
that solves one specific real-world phone-work problem: a service
business's next-day appointment list going unconfirmed because the
manual "call down the list" chore gets skipped on a busy day, and the
no-shows that follow. This document also covers Liya, the broader
agent this skill was extracted from, since Liya's `call_e` action
(`actions/call_e.py`) is what proves the underlying CALL-E integration
is real, tested code and not a one-off script written for a demo.

## Real-world impact (the thing this hackathon actually judges)

- **The problem is specific, not generic.** Clinics, salons, repair
  shops, tutoring services, and similar small businesses run on booked
  time slots. The standard fix for no-shows is a staff member manually
  phoning every name on tomorrow's schedule — repetitive, easy to
  deprioritize under any real workload, and the first thing that gets
  skipped when the day gets busy. `appointment-call-confirm` automates
  exactly that one chore: call each recipient, ask whether they're
  still coming, capture confirm/reschedule/decline/no-answer as a
  structured result, and write it back to wherever the business
  already tracks appointments.
- **It's worth building further after the hackathon.** The skill's
  output contract (`references/result-schema.md`) is deliberately a
  generic `status` + `requested_new_time` shape, so wiring it to a
  specific calendar/CRM/booking system is a follow-up integration, not
  a rewrite. The batch-CSV runner is the reference implementation; a
  Google Sheets or booking-platform adapter is the natural next step
  (see "What's next" below).
- **It's not "a generic AI that makes phone calls."** The skill
  explicitly refuses to be used for cold outreach, lead generation, or
  recurring/scheduled calling (that's a different, already-maintained
  skill in the target repo — `call-reminder`) — see `SKILL.md`'s "When
  Not To Use" section. Scope discipline is itself part of the
  contribution: a skill that tries to do everything is not reusable by
  anyone with a narrower need.

## Built during the Submission Period

The underlying CALL-E integration (`actions/call_e.py`,
`demo/demo_call_e.py`) and the rest of Liya were originally built for
a different hackathon's Submission Period; the code, governance model,
and CALL-E API usage are unchanged and still real, tested, working
code. What's new for **CALL-E: Your Code Is Calling** is
`hackathon-submission/skills/appointment-call-confirm/` — a fresh,
standalone skill package (SKILL.md, references, a dependency-free
runner script, and a sample dataset) extracted and rebuilt from that
same underlying CALL-E API usage, scoped and documented specifically
to this hackathon's submission format (a PR into CALL-E's public
`awesome-phone-call-agents` repository, under the "Agent Skills"
contribution area). Standard tools, frameworks, and AI coding
assistance were used throughout; no code claimed as new was actually
carried over unchanged from unrelated prior work.

## Problem statement

Most "AI assistant" demos are a chat window bolted onto a single LLM
call: the model answers, but a person still has to open the file, click
the button, run the search — or make the call — themselves. Liya is
built the other way around — the model plans, and the agent actually
carries out the plan against real tools (files, apps, browser,
reminders, web search, messaging, and now real outbound phone calls
via CALL-E) with failure recovery and governance built in, not bolted
on. `appointment-call-confirm` takes that same principle and narrows it
to one concrete business chore end to end: a list of appointments goes
in, a set of real phone calls and structured outcomes come out, with no
person dialing a single number by hand.

## Why this architecture

**Plan ? execute ? recover, as separate concerns.** `agent/planner.py`
only turns a goal into a step list; `agent/executor.py` only runs steps;
`agent/error_handler.py` only decides what to do when a step fails
(retry / skip / replan / abort). Keeping these separate means a failure
in one step doesn't require re-deriving the whole plan, and the
replanning logic can see exactly what succeeded before it before
deciding what to do next.

**Governance is a policy table, not scattered `if` statements.**
`agent/governance.py` maps every tool to `allow` / `confirm` / `deny`
once, in one place, overridable per-deployment via
`config/api_keys.json`. Risky tools (arbitrary computer control, code
execution) default to `confirm`; read-only or low-risk tools (web
search, weather) default to `allow`. This is what makes it reasonable to
let the planner call these tools autonomously at all — the danger isn't
gated per-call by prompt engineering, it's gated structurally.

**Two execution engines, on purpose.** Liya ships both:

1. The planner/executor path — a direct Gemini call producing a JSON
   step plan against a hand-maintained tool schema (`agent/planner.py`'s
   `PLANNER_PROMPT`). Fast and cheap (`gemini-3.5-flash-lite` for
   planning). This was the first piece built for this submission,
   before the ADK integration below was layered on top of it.
2. A Google ADK agent (`agent/adk_agent.py`) wrapping the *same* action
   modules as ADK `FunctionTool`s (`agent/adk_tools.py`), run through
   ADK's own agent loop and session management
   (`agent/adk_runner.py`), sharing the same underlying Gemini client
   (`agent/adk_model.py` ? `config/ai_client.py`).

   The ADK path exists because ADK gives session/state management,
   structured tool-calling, and an agent runtime maintained by Google
   instead of hand-rolled JSON-plan parsing — but rewriting the entire
   executor around ADK in one pass, across ~16 action modules that
   already work in production, was a bigger risk than the benefit
   justified this cycle. Running both side by side (`POST /task` vs
   `POST /task/adk`) lets the ADK path be exercised, tested, and trusted
   incrementally rather than as a single risky cutover.

   **Scope of the ADK path today:** 10 of the repo's 16 actions are
   wrapped as `FunctionTool`s in `agent/adk_tools.py` — `web_search`,
   `file_controller`, `open_app`, `reminder`, `weather_report`,
   `flight_finder`, `file_processor`, `send_message`, `code_helper`, and
   `dev_agent`. The remaining 5 (`browser_control`, `computer_settings`,
   `computer_control`, `desktop_control`, `screen_processor`) pull in
   Playwright/pyautogui/screen-camera capture, which don't belong in a
   headless Cloud Run container the way the 10 above do, so they stay
   legacy-path-only. `youtube_video` is also excluded for now — its
   "play" sub-action opens a URL in a local browser, which is meaningless
   on a server; it would need to be split into a headless-safe subset
   (summarize/get_info/trending) before it could be wrapped, and that
   split hasn't been done yet. `send_message` is included specifically to
   keep a `confirm`-tier tool on the ADK path — see below. `code_helper`
   and `dev_agent` are also `confirm`-tier (they run generated code via
   subprocess), with one exception carved out: `code_helper`'s
   `screen_debug` sub-action reads the live screen and is blocked at the
   ADK wrapper level regardless of governance, since there's no screen to
   read on a server.

   **Governance parity.** The legacy executor enforces
   `agent/governance.py`'s allow/confirm/deny policy before every step
   (`agent/executor.py`'s governance check). The first version of the
   ADK integration didn't — `agent/adk_tools.py` called straight into
   the action functions with no gate, so a `confirm`-tier tool could run
   through the ADK agent with nothing stopping it. `_governed()` in
   `agent/adk_tools.py` now wraps every ADK tool call through the same
   `check_tool_permission()` the legacy path uses, and `POST /task/adk`
   takes the same `auto_approve` field `POST /task` does
   (`backend/server.py`). `demo/demo_governance.py` drives this live: it
   runs the same goal twice through the real ADK agent — once with no
   consent (governance blocks `send_message`), once with
   `auto_approve=True` (it runs) — so the policy is something a judge
   can watch happen, not just read in this file.

**One model client, everywhere.** `config/ai_client.py` is the only
place that constructs a `google.genai.Client` or names a model string.
Every action module, the legacy planner, and the ADK model subclass
(`LiyaGemini`) go through it. A model version bump is a one-line change,
not a repo-wide find-and-replace — which matters more as the ADK and
legacy paths both need to stay in sync on which model they call.

**Firestore is additive, not required.** `memory/memory_manager.py` and
`agent/task_queue.py` both check whether Firestore is configured and
fall back to local-file storage / in-memory state if not. This keeps
local desktop use (no GCP project needed) and Cloud Run deployment (full
persistence) on the same codebase without a feature flag maze.

## Recent additions: Gemma, step-level checkpoint/resume, memory_tool

Three things were added after the initial submission pass, each closing
a gap named in "what's deliberately not in this codebase" below (that
section is left as-is, with strikethroughs, so the before/after is
honest rather than rewritten history):

- **Gemma integration** (`config/ai_client.py`, `agent/error_handler.py`)
  — `error_handler.py`'s retry/skip/replan/abort classification call
  (high-frequency, low-stakes) now runs on `MODEL_GEMMA`
  (`gemma-3-27b-it`) via a new `generate_with_fallback()` helper, which
  transparently drops back to `MODEL_FLASH_LITE` if Gemma isn't reachable
  in a given project/region. Same fallback-chain philosophy as
  `actions/web_search.py`'s Gemini ? DuckDuckGo ? Bing chain: a second
  model is a pure cost/latency win here, never a new failure mode.

- **Step-level checkpoint/resume** (`agent/checkpoint_store.py`, wired
  into `agent/executor.py` and `agent/task_queue.py`) — after every
  successfully completed step, `{plan, step_results, completed_steps,
  replan_attempts}` is persisted (Firestore, or local JSON fallback —
  same pattern as `memory/memory_manager.py`). `AgentExecutor.execute(...,
  resume=True)` reads that checkpoint back and skips any step already in
  `completed_steps` instead of re-running the whole plan, so a step whose
  side effect already happened (a file written, a message sent, a flight
  booked) is never repeated just because the process restarted mid-task.
  The checkpoint is cleared on any terminal outcome (success, security
  abort, explicit ABORT decision, exhausted replans) — it's resume state
  for an in-flight task, not permanent history (that's still
  `task_queue.py`'s Firestore `tasks` collection). Demoed end-to-end,
  against a real cancelled-then-resumed task, in
  `demo/demo_checkpoint_resume.py`.

- **`memory_tool`** (`agent/adk_tools.py`) — the one new ADK tool that
  isn't a re-wrap of an existing `actions/*.py` module. It exposes
  `memory/memory_manager.py`'s `remember()`/`forget()` directly to the
  ADK agent, so a fact learned mid-conversation ("I use VS Code, not
  PyCharm") can be persisted immediately instead of only being available
  to the *next* run's `_load_memory_context()` call. Not governed by
  `agent/governance.py`'s allow/confirm/deny table, since it only ever
  touches the agent's own memory store — there's no external system for
  that policy to gate.

## Fixed just before submission: two ADK tools that couldn't actually succeed on Cloud Run

Two of the "headless-safe" ADK tools were wired up and passed governance
correctly, but couldn't complete their actual job on the deployed
backend — worth naming rather than quietly patching over:

- **`weather_report`** called `webbrowser.open()` on a Google search URL
  and returned a generic "showing weather" message without ever fetching
  data. That's meaningless on a browser-less container. It now calls
  wttr.in's JSON endpoint directly and returns a real text summary —
  same code path on desktop and Cloud Run.
- **`reminder`** relied on an OS-level scheduler (`systemd-run`/`at` on
  Linux) to fire a future notification. A Cloud Run container has
  neither, and with `min-instances=0` there's no guarantee the process
  is even still alive when the reminder is due — so every call failed
  there, including as the third step of the designated demo scenario
  below. It now falls back to persisting the reminder as a durable
  record (Firestore, or local file) when no OS scheduler is available,
  following the same additive-Firestore pattern as `memory_manager.py`
  and `checkpoint_store.py`. Actual delivery (a Cloud Scheduler job or a
  client polling that record) is still open — see "What's next".

## What's deliberately not in this codebase

A few production-agent concerns are real and worth naming even though
most are still out of scope here: a durable/distributed task queue
instead of the in-process one, ~~step-level checkpoint/resume~~ (added
above), idempotency keys on every write action, a prompt-injection
defense pipeline around untrusted tool output, and rate/cost limiting on
model calls. The remaining ones weren't built for this submission - the
in-process queue backed by Firestore status writes, the existing tool
governance table (`agent/governance.py`), and the `MAX_REPLAN_ATTEMPTS`
cap in `agent/executor.py` are the scope-appropriate version of "safe to
run autonomously" for a hackathon build, not the enterprise-hardened one.

## What's next

For `appointment-call-confirm` specifically: a direct adapter so the
appointment batch can be read from (and results written back to) a
Google Sheet or a common booking platform instead of a hand-provided
CSV, and idempotency keys per recipient so a re-run of the same batch
never double-calls someone already confirmed. For Liya more broadly:
idempotency keys on write actions and a distributed task queue are the
next two items on that list, in that order.

## Repo hygiene notes

`requirements.txt` (full desktop superset for `main.py`/`ui.py`) and
`requirements-backend.txt` (the minimal set `Dockerfile` actually
installs — no PyQt6/pyautogui/Playwright) are now separate files;
`requirements-desktop.txt`, a byte-for-byte duplicate of `requirements.txt`, and two ad hoc debug scripts (`patch_audio_mimetype.py`, an already-applied one-off patch; the skill folder's `debug_calle.py` and `check_call_events.py`, exploratory scripts used while gathering `EVIDENCE.md`) were removed as submission cleanup — none were part of the skill's documented file list or referenced by any setup step.
bookmarked. Licensed under MIT (`LICENSE`).

---

## Designated demo scenario

**Goal:** *"Search the web for what judges say separates a winning
hackathon submission from an average one, save the findings as a
submission-readiness checklist to a file called hackathon_checklist.txt
on the Desktop, and set a reminder for tonight at 8:00 PM to do a final
pass against it."*

This is a real chore, not a stock example: it's the actual BYOF (Bring
Your Own Friction) problem this team had while finishing this
submission, run through Liya instead of done by hand. It's the one
scenario used to demo Liya end-to-end, and it's chosen because it
chains three independent tools (`web_search` ? `file_controller` ?
`reminder`) in a single autonomous run, which exercises:

- **Multi-step planning** — the planner has to sequence three
  dependent actions from one sentence, not just route to a single tool.
- **Real execution, not simulation** — a file actually appears on the
  Desktop and a reminder actually gets scheduled; nothing is mocked.
- **Visible autonomy** — every step is logged through
  `observability/logger.py` as it happens, not just summarized at the
  end.

### Running it

```bash
python demo/run_demo.py
```

This submits the goal directly to the real `agent/task_queue.py` (no
server needed) and streams the execution trace live as JSON events are
emitted, formatted as a readable timeline: which step is running, which
tool it called, and its result — as it happens, not after the fact.

Against a running backend instead (local or deployed), including the
Google ADK path:

```bash
# legacy planner/executor path, via POST /task
python demo/run_demo_http.py --url http://localhost:8080 --key dev

# Google ADK agent path, via POST /task/adk
python demo/run_demo_http.py --url http://localhost:8080 --key dev --adk
```

### Where the trace comes from

`observability/logger.py` logs every planning and execution event
(`plan.created`, `step.start`, `step.success`, `step.failure`,
`task.completed`, etc.) to three places at once:

1. stdout, as a JSON line per event (always on)
2. Firestore, under `tasks/{task_id}/trace/` (when configured)
3. An in-memory ring buffer, queryable via `get_trace(task_id)` (always
   on — this is what makes the trace visible on a local run with no
   GCP project at all)

`GET /task/{task_id}/trace` uses Firestore when it's configured and
falls back to the in-memory buffer otherwise, so the same endpoint
works whether or not the deployment has Firestore enabled.
