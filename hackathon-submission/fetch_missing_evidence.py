import os, json
from pathlib import Path
import requests

api_key = os.environ["CALLE_API_KEY"]
call_ids = ["call_pAI-p3GptfnaaM6h4rSP-w", "call_b5IPMEsxP2Hr9Vp4pYkNGg"]

for call_id in call_ids:
    state = requests.get(f"https://api.heycall-e.com/v1/calls/{call_id}",
                          headers={"Authorization": f"Bearer {api_key}"}).json()
    events = requests.get(f"https://api.heycall-e.com/v1/calls/{call_id}/events",
                           headers={"Authorization": f"Bearer {api_key}"}).json()
    out = Path("evidence") / f"call_evidence_{call_id}.json"
    out.write_text(json.dumps({"call_state": state, "call_events": events}, indent=2), encoding="utf-8")
    print(f"Saved {out}")
