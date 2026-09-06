# Hackathon submission staging: `appointment-call-confirm`

This folder is the actual judged artifact for **CALL-E: Your Code Is
Calling**. Everything else in this repository (Liya, the generalist
agent) is context for *why* this skill exists and proof that its
underlying CALL-E integration is real — the skill itself is what gets
submitted.

## What this is

`skills/appointment-call-confirm/` in this folder is a complete,
standalone [Agent Skill](https://github.com/CALLE-AI/awesome-phone-call-agents)
package, built to that repository's exact skill folder template
(`SKILL.md`, `references/`, `scripts/`, `assets/`). It solves one
specific, real phone-work problem — service businesses losing revenue
to no-shows because confirming tomorrow's appointments by phone is a
manual, easy-to-skip chore — using CALL-E's Developer API directly.

It does **not** depend on Liya, on Google ADK, or on any of the
agent-framework machinery in the rest of this repo. `scripts/
place_confirmation_calls.py` only needs `requests` and a CALL-E API
key, so any host that adopts this skill can run it standalone.

## How to submit / review it

1. Fork [`CALLE-AI/awesome-phone-call-agents`](https://github.com/CALLE-AI/awesome-phone-call-agents).
2. Copy `hackathon-submission/skills/appointment-call-confirm/` into
   that fork's `skills/appointment-call-confirm/` (matching the
   "Agent Skills" contribution area in that repo's README).
3. Run that repo's validation script before opening the PR:
   ```bash
   python3 scripts/validate_repository.py
   ```
4. Follow that repo's branch-naming convention
   (`docs/git-naming-conventions.md`) and open the PR against `main`.
5. Paste the PR URL into the Devpost submission form for this
   hackathon (see `../DEVPOST_SUBMISSION.md` in this repo for the rest
   of the submission text).

**PR URL:** https://github.com/CALLE-AI/awesome-phone-call-agents/pull/294
## Why this scope, not the whole Liya agent

Liya's `actions/call_e.py` (in the main repo) already wraps CALL-E's
Developer API and is real, tested, working code — see
`demo/demo_call_e.py`. But judging for this hackathon asks for "a
real, specific phone-work problem," not a generic AI assistant that
happens to have call-making as one of seventeen tools. So rather than
submit the whole generalist agent, this hackathon submission narrows
to exactly one reusable, focused contribution — a batch appointment
confirmation skill — that stands on its own, is easy for a judge (or
any other developer) to read start to finish, and is something the
CALL-E community can actually install and use, per that repository's
own contribution guidance.
