# Liya — a calling-only voice agent, trimmed for CALL-E

**Built for CALL-E: Your Code Is Calling.** Liya started as a broader
autonomous agent with ~16 tools (files, browser, apps, search,
messaging, and more); for this hackathon's scope, this repo has been
trimmed down to exactly one capability — placing and checking real
outbound phone calls through [CALL-E](https://github.com/CALLE-AI/call-e-integrations)
(`actions/call_e.py`) — so the code here matches the "well-scoped,
reusable" contribution the hackathon actually judges, rather than a
generalist agent with calling as one feature among many.

Talk to Liya, ask it to call someone about something, confirm out
loud when it reads back the number and task, and it places a real
CALL-E call and reports back what happened — including a sharper
diagnosis than CALL-E's own generic message when a call fails
instantly (a carrier-level code in under two seconds means the number
itself was unreachable, not that the recipient was busy).

**The actual hackathon submission is a standalone, PR-ready Agent
Skill**, not this whole repo — see
[`hackathon-submission/`](hackathon-submission/) for
`appointment-call-confirm`, a focused CALL-E skill that automates a
real business chore (calling to confirm tomorrow's appointments so
no-shows don't eat the day) and stands on its own without any of
Liya's remaining agent-framework machinery. **That PR is merged:**
https://github.com/CALLE-AI/awesome-phone-call-agents/pull/294. A
second, separately-scoped contribution — a standalone CLI app rather
than a skill — is also open:
https://github.com/CALLE-AI/awesome-phone-call-agents/pull/394
(`apps/python/liya-appointment-confirm`). This repo (Liya) is the
context for why both exist and proof the underlying CALL-E
integration is real, tested code, not a one-off script written for
either submission.

---

## Quick start

```bash
git clone https://github.com/Lazvyn/Liya-x-Call-E.git
cd Liya-x-Call-E
pip install -r requirements.txt
python main.py
```

On first launch, with no `config/api_keys.json` present yet, a setup
popup appears asking for your **Gemini API key** and **CALL-E API
key**, plus your OS — fill both in once, submit, and Liya writes
`config/api_keys.json` itself and goes live. No manual config file
editing needed. (If you don't have a CALL-E key yet, you can leave
that field blank and add it later — see [Local setup](#local-setup)
below for the full walkthrough, including what to do if the popup
doesn't show both fields, and common first-run problems on Windows.)

**Judges/reviewers:** the fastest way to see the actual submission is
[`hackathon-submission/skills/appointment-call-confirm/SKILL.md`](hackathon-submission/skills/appointment-call-confirm/SKILL.md)
— a self-contained skill you can read start to finish in a few
minutes. See [`JUDGE_TESTING.md`](JUDGE_TESTING.md) for the full
verification path, including seeing CALL-E's Developer API called
live from this repo.

Full setup detail and tool scope are documented below.

---

## How it's put together

```
   Voice ──► main.py / ui.py (Gemini Live voice loop, PyQt HUD)
                    │
                    ▼
            actions/call_e.py ──► CALL-E Developer API
              (POST /v1/calls, GET /v1/calls/{id})
                    │
                    ▼
      memory/memory_manager.py (contact/appointment memory,
                Firestore or local-file fallback)
```

`main.py` exposes exactly four tools to the voice model:
`call_e` (place a call — requires explicit verbal confirmation before
any real call fires), `call_status` (check a previously-placed call's
real outcome), `save_memory` (remember a contact/appointment fact),
and `shutdown_liya`. `agent/adk_tools.py` mirrors the same two
side-effecting tools (`call_e_tool`, `memory_tool`) for the separate
Google ADK agent path, gated through the same `agent/governance.py`
allow/confirm/deny table `send_message` used to go through.

**What's still in this repo but out of scope for this submission:**
`agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`,
`backend/server.py` (Cloud Run FastAPI backend), and most of
`demo/*.py` describe/exercise the broader multi-tool agent Liya used
to be — they're retained for history and because other, unrelated
parts of this codebase still reference them, but they're not part of
what this hackathon submission demonstrates, and several of the demo
scripts (`run_demo.py`, `demo_memory_recall.py`,
`demo_checkpoint_resume.py`, `demo_failure_recovery.py`) no longer run
end-to-end since the tools they exercised (`web_search`,
`file_controller`, `send_message`, etc.) were removed along with
everything except `call_e`. `demo/demo_call_e.py` still works — it
only touches `call_e_tool`.

---

## Project layout

| Path | What it is |
|---|---|
| `main.py` | Desktop entry point — voice loop (Gemini Live), tool dispatch (`call_e`, `call_status`, `save_memory`, `shutdown_liya`), UI wiring |
| `ui.py` | PyQt desktop UI (`LiyaUI`) |
| `actions/call_e.py` | Places/checks CALL-E phone calls: phone validation, call creation, polling, and failure diagnosis |
| `agent/adk_tools.py` | ADK `FunctionTool` wrappers for the Google ADK agent path: `call_e_tool` and `memory_tool`, both gated through `agent/governance.py` |
| `agent/governance.py` | Per-tool policy: `allow` / `confirm` / `deny` |
| `memory/memory_manager.py` | Long-term memory read/write (Firestore-backed, local-file fallback) — used to remember contacts/appointments |
| `memory/config_manager.py` | Reads/writes `config/api_keys.json` |
| `config/ai_client.py` | Single source of truth for the Gemini model + client |
| `hackathon-submission/` | The actual judged submission: standalone `appointment-call-confirm` Agent Skill (merged PR #294) |

*(`agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`,
`backend/server.py`, and most of `demo/*.py` are also present but out
of scope for this submission — see "How it's put together" above.)*

---

## Local setup

**Requirements:** Python 3.12 or newer, a Gemini API key, and (to
place real calls) a [CALL-E](https://github.com/CALLE-AI/call-e-integrations)
API key — new accounts include 20 free calls.

### 1. Clone and install dependencies

```bash
git clone https://github.com/Lazvyn/Liya-x-Call-E.git
cd Liya-x-Call-E
pip install -r requirements.txt
```

**If you have more than one Python version installed (common on
Windows):** `pip install` and `python main.py` must use the *same*
interpreter, or packages installed by one won't be visible to the
other — this is the single most common reason a fresh clone fails
with `Import "X" could not be resolved` or `ModuleNotFoundError`
despite `pip install` appearing to succeed. Check which interpreter
you're about to use before installing:

```bash
python -c "import sys; print(sys.executable)"
```

If you're using VS Code, also check the interpreter shown in the
bottom-right status bar (or `Ctrl+Shift+P` → "Python: Select
Interpreter") and make sure it points at the *same* `python.exe` your
terminal is using. If they don't match, either switch VS Code's
interpreter to the one you installed into, or reinstall targeting the
one VS Code is using:

```bash
"C:\path\to\that\python.exe" -m pip install -r requirements.txt
```

Then close and reopen your terminal (or VS Code's integrated
terminal) so it picks up the change.

### 2. Configure your API keys

Run the app:

```bash
python main.py
```

With no `config/api_keys.json` present yet, a setup popup appears
with two fields — **Gemini API key** and **CALL-E API key** — plus
your OS (auto-detected). Fill in what you have and submit; Liya
writes `config/api_keys.json` itself. CALL-E's key is optional at
this point (voice + text chat works without it), but no call will
actually place without one.

**If the popup only shows one field (Gemini), your local `ui.py` is
out of date** — pull the latest `main` branch, which includes the
CALL-E key field in the setup wizard. **If the popup doesn't appear
at all** even though you expect first-run setup, it's because
`config/api_keys.json` already exists from a previous run (the popup
only shows when required keys are missing) — see the next paragraph
if you need to add a key to an already-configured install.

**Adding a CALL-E key later, to an already-configured install:** as
long as your `ui.py` is up to date, just delete `config/api_keys.json`
(or open it and add `"calle_api_key": "YOUR_KEY"` yourself) and rerun
`python main.py` — the popup reappears, pre-filled with your existing
Gemini key, so you only need to add the missing field. Either way,
the file ends up looking like:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "os_system": "windows",
  "calle_api_key": "YOUR_CALLE_API_KEY"
}
```

An optional `calle_base_url` key can also be set by hand if you're
pointed at a non-default CALL-E deployment; it defaults to
`https://api.heycall-e.com`. `CALLE_API_KEY` / `CALLE_BASE_URL`
environment variables work as a fallback if no local config file is
present at all (e.g. a headless/cloud deployment — see
`backend/README_DEPLOY.md`).

### 3. Run it

```bash
python main.py
```

Talk to Liya, ask her to call someone about something specific. She
reads back the number and task and asks you to confirm out loud
before anything real happens — say yes, and she places the call
through CALL-E and reports back the result.

### Troubleshooting checklist

If a call command "does nothing" or fails silently, work through
these in order:

1. **Is `calle_api_key` actually set?** Open `config/api_keys.json`
   and check. If it's missing, `call_e` fails immediately with
   `CALL-E is not configured...` — Liya should say this out loud, but
   check the terminal log either way.
2. **Did you actually say yes to the confirmation?** `call_e` refuses
   to run without explicit verbal confirmation in the same
   conversation — see [Tool governance](#tool-governance) below.
3. **Same recipient called twice within 10 minutes?** That's
   intentionally skipped as a likely accidental repeat — see the
   dedupe note in `main.py`'s `_execute_tool`. Confirm again
   explicitly if it's deliberate.
4. **Is `phonenumbers` installed in the interpreter you're actually
   running?** Missing it doesn't crash the call, but it does disable
   proper number validation — recheck step 1's interpreter-matching
   advice if `pip show phonenumbers` succeeds but VS Code/`python
   main.py` still can't find it.
5. **Check the terminal output.** `main.py` prints
   `[CallE] placing call -> ...` and `[CallE] call <id> -> <status>`
   lines as it goes — these show whether the request ever reached
   CALL-E at all, versus failing before it got that far.

---

## Tool governance

Every tool has a policy in `agent/governance.py` — `allow` (runs
immediately) or `confirm` (needs the user's explicit spoken
confirmation before it fires; for `call_e` this is enforced in
`main.py` directly, not just as a policy label, since it has a
real-world side effect — a phone actually rings):

| Tool | Policy |
|---|---|
| `save_memory`, `call_status` | allow |
| `call_e` | confirm |

Overridable per-deployment via `tool_governance` in
`config/api_keys.json`.

---

## Actions catalog

| Tool | Purpose |
|---|---|
| `call_e` | Place a real outbound phone call via [CALL-E](https://github.com/CALLE-AI/call-e-integrations) to accomplish a goal-driven phone task and return a (optionally structured) result. Requires explicit verbal confirmation before firing; deduped by recipient phone number for 10 minutes to prevent accidental double-calls. See [`hackathon-submission/`](hackathon-submission/) for the standalone `appointment-call-confirm` skill built on this same API. |
| `call_status` | Check the real outcome of a previously-placed call by its `call_id` — needed because `call_e` fires and returns immediately rather than blocking the voice turn for up to 3 minutes. Surfaces a sharper diagnosis than CALL-E's generic message when a call fails instantly (carrier-level code + near-zero duration = unreachable number, not "recipient busy"). |
| `save_memory` | Remember a fact for later — used here mainly to remember a contact's number so you don't have to repeat it every call. |

---

See [`WRITEUP.md`](WRITEUP.md) for the problem statement and
architecture rationale.

---

## Try it: place and check a real call

```bash
python demo/demo_call_e.py                # blocked without consent, then allowed with auto_approve=True
python demo/demo_call_e.py --live          # add CALLE_API_KEY to actually place a call through the ADK path
```

Or run the desktop app (`python main.py`) and just talk to it — see
`hackathon-submission/skills/appointment-call-confirm/` for the
batch-CSV version of the same underlying call.

---

## Evidence - proving each claim, not just stating it

| Claim | Proof script | What it actually shows |
|---|---|---|
| CALL-E phone calls gated by governance, same as any confirm-tier tool | `python demo/demo_call_e.py` (add `--live` + `CALLE_API_KEY` to actually place a call) | Runs the real ADK agent against `call_e_tool` — blocked with no consent, allowed with `auto_approve=True`, via the live `check_tool_permission()` call |
| Batch CALL-E confirmation calls, standalone (the merged hackathon submission) | `python hackathon-submission/skills/appointment-call-confirm/scripts/place_confirmation_calls.py --in hackathon-submission/skills/appointment-call-confirm/assets/sample_appointments.csv` (dry run; add `--confirm` + `CALLE_API_KEY` to actually call) | Calls CALL-E's Developer API directly, with no Liya/ADK dependency, against a batch of appointments — dry-run list, then serial calls, structured per-recipient results |
| Phone-number validation catches misread country codes before they reach CALL-E | Try `python main.py`, ask it to call a bare 10-digit number with no country code | It asks which country the number belongs to rather than guessing — see `actions/call_e.py`'s `_normalize_phone` |
| Failure diagnosis distinguishes an unreachable number from a busy line | Place a call to an invalid number, then ask "did that call go through?" | `call_status` surfaces the carrier failure code + near-instant duration pattern instead of repeating CALL-E's generic "may be busy" summary |

---

## Known limitation

CALL-E's own generic failure summary (e.g. "recipient may be busy,
try again in 45 minutes") doesn't always reflect what actually
happened — an instantly-failing call with a carrier-level error code
is a different failure mode (unreachable number) than a genuinely
busy line, and CALL-E's summary text doesn't distinguish the two.
`call_status`'s diagnosis logic catches this pattern specifically, but
any failure mode CALL-E doesn't expose a carrier code for still falls
back to CALL-E's own (sometimes generic) summary.