# Testing this submission — for CALL-E hackathon reviewers

The actual submission is the skill in
[`hackathon-submission/skills/appointment-call-confirm/`](hackathon-submission/skills/appointment-call-confirm/).
Everything below is ordered so you can verify it in under five
minutes, then go as deep as you want into the underlying CALL-E
integration it was built from.

---

## 1. Read the skill (30 seconds)

[`hackathon-submission/skills/appointment-call-confirm/SKILL.md`](hackathon-submission/skills/appointment-call-confirm/SKILL.md)
is self-contained — when to use it, when not to, the exact workflow,
the safety rules, and the output format. No other file is required
reading to understand what it does.

## 2. Dry-run it — no CALL-E key needed, no calls placed

```bash
cd hackathon-submission/skills/appointment-call-confirm
python3 scripts/place_confirmation_calls.py --in assets/sample_appointments.csv
```

This prints the three sample appointments (masked phone numbers,
inferred region, appointment context) and explicitly refuses to call
anyone without `--confirm`. This is the default behavior, not a
special safe mode — see `references/safety.md`.

## 3. Place a real call

```bash
export CALLE_API_KEY=your_calle_key   # from https://github.com/CALLE-AI/call-e-integrations
python3 scripts/place_confirmation_calls.py \
  --in assets/sample_appointments.csv \
  --out results.csv \
  --confirm
```

Replace the sample CSV's phone number with a real, consented one
first. This places one real outbound call per row, serially, through
CALL-E's actual `POST /v1/calls` / `GET /v1/calls/{call_id}` Developer
API — no mocking — and writes a structured result row per recipient to
`results.csv`.

## 4. See the underlying CALL-E integration it was extracted from

The skill's API usage (`result_schema`, region inference, polling to a
terminal status) mirrors `actions/call_e.py` in this same repository,
which is wired into Liya's real ADK agent and gated by governance the
same way `send_message` is:

```bash
python demo/demo_call_e.py                       # blocked without consent, then allowed with auto_approve=True
python demo/demo_call_e.py --live                 # add CALLE_API_KEY to actually place a call through the ADK path
```

That's the full path to evaluate this submission — nothing else is
required.
