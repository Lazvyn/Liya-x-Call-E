# Pre-submission checklist — CALL-E: Your Code Is Calling

Deadline: **Sep 14, 2026 @ 9:15pm GMT+5:30**. Everything below is
outstanding as of this review — the skill, code, and evidence are
done; what's left is submission mechanics.

## Fixed in this pass
- [x] `hackathon-submission/skills/appointment-call-confirm/assets/sample_appointments.csv`
      now has three appointments (US/SG/IN), matching what
      `JUDGE_TESTING.md` step 2 tells a reviewer to expect. Verified
      the dry-run actually prints all three with correct region
      inference for each.

## Still required — cannot be done without your GitHub/CALL-E account
- [ ] **Fork** [`CALLE-AI/awesome-phone-call-agents`](https://github.com/CALLE-AI/awesome-phone-call-agents).
- [ ] **Copy** `hackathon-submission/skills/appointment-call-confirm/`
      into that fork's `skills/appointment-call-confirm/`.
- [ ] **Run that repo's validator** (`python3 scripts/validate_repository.py`)
      before opening the PR.
- [ ] **Open the PR** against `main`, following that repo's branch
      naming convention (`docs/git-naming-conventions.md`).
- [ ] **Paste the PR URL** into:
  - `DEVPOST_SUBMISSION.md` (line ~9)
  - `hackathon-submission/README.md` (line ~40)
  - the Devpost submission form
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
- [ ] **Add your CALL-E account email** to `DEVPOST_SUBMISSION.md`
      and the Devpost form.
- [ ] *(Optional)* Demo app URL, if you stand one up.
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
