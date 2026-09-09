# Result schema

`appointment-call-confirm` sends the same `result_schema` on every
`POST /v1/calls` request so CALL-E returns a structured
`structured_result` instead of free text that would need to be
re-parsed per call:

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": ["confirmed", "needs_reschedule", "declined", "no_answer", "voicemail", "unclear"]
    },
    "requested_new_time": {
      "type": "string",
      "description": "ISO 8601 time the recipient proposed instead, if status is needs_reschedule. Omit otherwise."
    },
    "notes": {
      "type": "string",
      "description": "One short sentence of anything relevant the recipient said, e.g. a reason for cancelling."
    }
  },
  "required": ["status"],
  "additionalProperties": false
}
```

## Task template

The `task` field sent to CALL-E is built from the appointment's
`context` and `business_name`:

```
You are calling on behalf of {business_name} to confirm an upcoming
appointment: {context}, scheduled for {appointment_time_human}.
Politely confirm whether {recipient_name} can still make it. If not,
ask whether they'd like to reschedule and to what time, or would
prefer to cancel. Keep the call brief and courteous.
```

## Region inference

If `region` isn't explicitly provided for a recipient, the script
infers it from the E.164 country code using a small static map covering
16 of CALL-E's currently-documented regions (US, SG, MY, IN, AE, AU,
GB, VN, DE, JP, FR, MX, BR, ID, PH, KE) — a subset chosen for common
single-country calling codes, not the full list CALL-E supports. `+1`
is mapped to `US` only; Canadian (`CA`) numbers share that calling
code and are not auto-inferred, so `region` must be supplied
explicitly for them. If the country code doesn't map to one of the
16 above, the run stops for that recipient and asks the host to
supply `region` explicitly rather than guessing — see CALL-E's
[supported regions and languages list](https://github.com/CALLE-AI/call-e-integrations#supported-regions-and-languages)
for the complete, current set and to extend `_COUNTRY_CODE_TO_REGION`
for additional single-country codes.

## Mapping to skill output

| `structured_result.status` | Skill output status |
|---|---|
| `confirmed` | `confirmed` |
| `needs_reschedule` | `needs_reschedule` (+ `requested_new_time` if present) |
| `declined` | `declined` |
| `no_answer` | `no_answer` |
| `voicemail` | `voicemail` |
| `unclear` / missing / doesn't parse | `unclear` — never guessed into one of the above |
| CALL-E call status is `failed`/`canceled`/`error` | `failed` (+ the API's rejection/failure detail) |
