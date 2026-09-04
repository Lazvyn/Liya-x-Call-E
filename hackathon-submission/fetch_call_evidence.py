import os, json
from pathlib import Path
import requests

api_key = os.environ["CALLE_API_KEY"]
call_id = "call_laEJsdEVAIyKsiLDjhyedQ"

state = requests.get(
    f"https://api.heycall-e.com/v1/calls/{call_id}",
    headers={"Authorization": f"Bearer {api_key}"},
).json()

events = requests.get(
    f"https://api.heycall-e.com/v1/calls/{call_id}/events",
    headers={"Authorization": f"Bearer {api_key}"},
).json()

evidence_dir = Path("evidence")
evidence_dir.mkdir(exist_ok=True)

out_path = evidence_dir / f"call_evidence_{call_id}.json"
out_path.write_text(
    json.dumps({"call_state": state, "call_events": events}, indent=2),
    encoding="utf-8",
)

# Human-readable transcript pulled out separately, for quick viewing/screenshotting
transcript_lines = []
for r in state.get("recipients", []):
    for att in r.get("attempts", []):
        for turn in att.get("transcript_turns", []):
            transcript_lines.append(f"[{turn.get('offset_seconds', '?')}s] {turn.get('speaker')}: {turn.get('text')}")

txt_path = evidence_dir / f"call_transcript_{call_id}.txt"
txt_path.write_text("\n".join(transcript_lines) or "(no transcript captured)", encoding="utf-8")

print(f"Status: {state.get('status')}")
print(f"Structured result: {json.dumps(state.get('structured_result'), indent=2)}")
print(f"Saved:\n  {out_path}\n  {txt_path}")
