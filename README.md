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
```

Then just run the desktop app:

```bash
python main.py
```

On first launch, with no `config/api_keys.json` present yet, a setup
popup appears asking for your Gemini API key and OS — enter it once,
submit, and Liya writes `config/api_keys.json` itself and goes live.
No manual config file editing needed.

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

**Requirements:** Python 3.12+, a Gemini API key.

```bash
git clone https://github.com/Lazvyn/Liya-x-Call-E.git
cd Liya-x-Call-E

pip install -r requirements.txt
```

Then create `config/api_keys.json`:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "os_system": "windows",
  "calle_api_key": "YOUR_CALLE_API_KEY",
  "calle_base_url": "https://api.heycall-e.com"
}
```

`calle_api_key` / `calle_base_url` are needed to actually place calls
via `actions/call_e.py`. Get a key by following
[CALL-E's install guide](https://github.com/CALLE-AI/call-e-integrations);
new accounts include 20 free calls. `CALLE_API_KEY` / `CALLE_BASE_URL`
environment variables work as a fallback if no local config file is
present.

Run it:

```bash
python main.py
```

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