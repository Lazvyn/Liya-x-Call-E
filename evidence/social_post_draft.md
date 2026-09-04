# Social post draft (X / LinkedIn) — optional

**CALL-E: Your Code Is Calling has no blog/social bonus category.**
Its equivalent bonus prize, *Most Valuable Feedback*, is earned by
submitting the CALL-E Feedback Survey, not by a public post — see the
top-level hackathon rules. This draft is here only in case you want to
share the work publicly anyway; it is **not** part of the graded
submission, and no link from this file should be pasted into the
Devpost form.

## Short version (X)

Built `appointment-call-confirm` for CALL-E's hackathon — a skill that
calls down tomorrow's appointment list so a business doesn't have to.
Confirms, reschedules, or catches a no-show, and hands back a
structured result per call. No agent framework required, just CALL-E's
API + Python.

PR: [PASTE PR URL ONCE OPENED] — Demo: [PASTE DEMO VIDEO LINK]

## Longer version (LinkedIn)

I built a small, focused Agent Skill for CALL-E's "Your Code Is
Calling" hackathon: `appointment-call-confirm`. The problem it solves
is unglamorous but real — service businesses (clinics, salons, repair
shops, tutoring) lose money to no-shows, and the usual fix is a staff
member manually phoning down tomorrow's schedule, which is the first
thing that gets skipped on a busy day.

The skill takes a batch of appointments, dry-runs the full list for
explicit approval, then places one real outbound call per recipient
through CALL-E, asking them to confirm, reschedule, or cancel — and
gets back a structured result (confirmed / needs_reschedule / declined
/ no_answer / voicemail) instead of a transcript someone still has to
read and interpret.

It's a standalone, dependency-light skill — no agent framework
required, just `requests` and a CALL-E API key — built to
`awesome-phone-call-agents`' own skill template so it's a drop-in
contribution, not a bolt-on demo.

PR: [PASTE PR URL ONCE OPENED] | Demo: [PASTE DEMO VIDEO LINK]

#CALLE #AIAgents #PhoneCallAgents
