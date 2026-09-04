# Devpost submission — CALL-E: Your Code Is Calling

This file is the text for the Devpost submission form. Per that
hackathon's actual requirements, the form itself asks for:

1. **PR URL** to `CALLE-AI/awesome-phone-call-agents` — see
   [`hackathon-submission/README.md`](hackathon-submission/README.md)
   for the exact fork/copy/PR steps.
   **PR URL:** _fill in after opening the PR — do not submit a
   placeholder link._
2. **A ~3 minute demo video**, uploaded to YouTube or Vimeo, public.
   **Video URL:** _fill in once recorded/uploaded._
3. **The email address associated with your CALL-E account.**
   **Email:** _fill in._
4. **(Optional) a URL to a functional demo application.**
   **Demo app URL:** _fill in if applicable, otherwise omit this
   field on the form._
5. **(Optional) the CALL-E Feedback Survey**, to be eligible for a
   Most Valuable Feedback prize — separate from the fields above.

The sections below are the actual "Text description" content for the
submission page.

---

## Inspiration

Every service business that books time slots loses money to no-shows.
The usual fix is a person on staff spending part of every afternoon
manually phoning down tomorrow's appointment list — repetitive,
unglamorous, and the first thing to get skipped when the day gets
busy. We already had a real, working CALL-E integration inside Liya
(an autonomous agent we'd built for a different hackathon) that could
place a goal-driven outbound call and get a structured result back.
This hackathon was the reason to take that one capability, cut away
everything else Liya does, and turn it into something the CALL-E
community could actually install and use on its own.

## What it does

`appointment-call-confirm` takes a batch of upcoming appointments
(name, phone, appointment time, and what the appointment is for) and:

- **Dry-runs the whole batch first** — prints every recipient with a
  masked phone number and requires explicit approval before any call
  goes out. No call is ever placed silently.
- **Places one outbound CALL-E call per recipient, serially** — never
  in parallel, never more than once per recipient per run — asking
  them to confirm, reschedule, or cancel.
- **Gets a structured result back**, not free text: `confirmed` /
  `needs_reschedule` (with the proposed new time) / `declined` /
  `no_answer` / `voicemail` / `unclear`, via CALL-E's `result_schema`
  support.
- **Writes back one row per recipient** to a results CSV (or a
  host-provided webhook), with the CALL-E `call_id` kept for
  auditability, plus a one-line batch summary so a business can act on
  the exceptions first.
- **Never claims a call happened that didn't** — a rejected call, a
  call still in progress at the poll deadline, or a response CALL-E
  couldn't cleanly parse are reported as `failed` / `pending` /
  `unclear` respectively, never guessed into a confirmed outcome.

It's a **skill**, not a new product surface: `SKILL.md` plus a
dependency-light Python runner (`requests` + a CALL-E API key, nothing
else), built to `awesome-phone-call-agents`' own skill folder
template so it's a drop-in PR, not a bolt-on.

## How we built it

The skill wraps CALL-E's Developer API directly:
`POST /v1/calls` to place each call with a `result_schema` describing
the exact JSON shape we want back, then `GET /v1/calls/{call_id}`
polled to a terminal status. Region is inferred from the phone
number's country code against CALL-E's documented region list when
not given explicitly, and the run refuses to guess if it can't. The
task text sent to CALL-E is built from a small template
(`references/result-schema.md`) so every call has a consistent,
polite, on-brand script regardless of which business is using the
skill.

The broader context: Liya (`actions/call_e.py`, `demo/demo_call_e.py`
in this same repo) already called this same Developer API in
production, gated behind an allow/confirm/deny governance table
alongside every other tool with a real-world side effect
(`agent/governance.py`). That existing, tested integration is what
this skill was extracted and rebuilt from — the API calls in the skill
aren't new, untested code written just for a submission.

**Technologies used:** CALL-E Developer API (`POST /v1/calls`,
`GET /v1/calls/{call_id}`), Python, `requests`. (Liya, the broader
context this was extracted from, additionally uses Gemini, Google ADK,
Cloud Run, and Firestore — see `WRITEUP.md` for that architecture.)

## Real-world impact

Judged criteria for this hackathon ask for a specific problem, not a
generic "AI that makes calls" concept — see
[`WRITEUP.md`](WRITEUP.md#real-world-impact-the-thing-this-hackathon-actually-judges)
for the full case: which businesses have this exact problem today,
why the manual version of this workflow fails under normal workload,
and what a real integration (a calendar/CRM/booking-platform adapter)
looks like as the immediate next step past this hackathon.

## Challenges we ran into

- **Scoping down instead of up.** The tempting submission was "look,
  Liya can already call CALL-E" — but a seventeen-tool generalist
  agent with calling as one confirm-gated tool doesn't answer "what
  specific phone-work problem does this solve," which is explicitly
  judged here. Cutting the submission down to one focused, standalone
  skill was more work than shipping the whole repo, but it's the
  actual reusable contribution.
- **Never guessing a result.** It would have been easy to have the
  script infer "probably confirmed" from an ambiguous CALL-E summary.
  Instead, anything that doesn't cleanly match the `result_schema`'s
  enum is reported as `unclear` — a deliberately less impressive-looking
  but honest result.
- **Serial, not parallel, calling.** Batch-calling everyone at once
  would finish faster, but a business suddenly placing ten
  simultaneous outbound calls is a real-world side effect worth being
  conservative about, not just a performance question.

## What we learned

- A hackathon's judging rubric is itself a design constraint: "does
  this solve a real, specific problem" pushed us toward narrowing
  scope, not adding features.
- Structured `result_schema` output turns "the agent talked to someone
  on the phone" into something a business can actually act on
  (confirmed vs. needs-reschedule vs. no-answer), instead of a
  transcript someone still has to read.

## What's next

A direct adapter so appointments can be read from (and results written
back to) a Google Sheet or a common booking platform instead of a
hand-provided CSV, and idempotency keys per recipient so re-running a
batch never double-calls someone already confirmed.
