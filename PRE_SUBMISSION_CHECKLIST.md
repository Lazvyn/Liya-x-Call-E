# Pre-submission checklist — CALL-E: Your Code Is Calling

Deadline: **Sep 14, 2026 @ 9:15pm GMT+5:30**.

## Done
- [x] `hackathon-submission/skills/appointment-call-confirm/assets/sample_appointments.csv`
      has three appointments (US/SG/IN); dry-run prints all three with
      correct region inference for each.
- [x] **Forked** `CALLE-AI/awesome-phone-call-agents`, copied the skill
      in, ran `scripts/validate_repository.py` (passed), opened the PR.
- [x] **PR merged**: https://github.com/CALLE-AI/awesome-phone-call-agents/pull/294
- [x] PR URL is in `DEVPOST_SUBMISSION.md` and `hackathon-submission/README.md`.
- [x] CALL-E account email is in `DEVPOST_SUBMISSION.md`.
- [x] *(Optional, second entry)* A separate app-based contribution,
      [`apps/python/liya-appointment-confirm`](https://github.com/CALLE-AI/awesome-phone-call-agents/pull/394),
      is also open — same underlying CALL-E integration, different
      contribution format (standalone CLI app vs. Agent Skill). Rules
      permit multiple submissions as long as each is substantially
      different, which this is (app vs. skill, dry-run/confirm flow
      vs. batch CSV runner).

## Still required — cannot be done without your GitHub/CALL-E account
- [ ] **Record a demo video** (≤3 minutes, public on YouTube or
      Vimeo). Suggested beat sheet, all runnable with the fixed
      3-row sample data:
  1. `SKILL.md` — 10s scroll, land on "When Not To Use."
  2. Dry-run: `python3 scripts/place_confirmation_calls.py --in assets/sample_appointments.csv`
     — show the masked numbers + inferred regions, no call placed.
  3. Real run with `--confirm` against one consented test number —
     show the terminal status polling and the written `results.csv`.
  4. 15–20s on `EVIDENCE.md`: the voice-triggered call transcript and
     the duplicate-call safety fix (this is the strongest
     differentiator — don't cut it).
  - Paste the video URL into `DEVPOST_SUBMISSION.md`.
- [ ] **Submit the Devpost form itself** (`call-e.devpost.com`) —
      writing the PR URL and email into these markdown files doesn't
      submit anything; the actual form still needs to be filled in
      and submitted separately, before the deadline.
- [ ] *(Optional, separate prize track)* Submit the CALL-E Feedback
      Survey during the Feedback Period for Most Valuable Feedback
      eligibility — one per person, doesn't affect the main prizes.

## Worth a final glance before you submit
- `EVIDENCE.md` section 2c notes a call_id wasn't captured — either
  pull it from the CALL-E dashboard and add it, or drop the sentence
  flagging it as missing.
- Double-check the sample CSV's phone numbers are fictitious/test
  numbers only if you swap in a real one for the video — don't call a
  number without that person's consent.

