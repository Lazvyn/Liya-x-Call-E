import warnings
warnings.filterwarnings("ignore", message=".*non-data parts.*")
import os
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

import asyncio
import re
import threading
import time
import json
import sys
import traceback
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from config.ai_client import get_api_key, MODEL_LIVE
from ui import LiyaUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.call_e            import call_e
from agent.tool_result         import is_tool_result

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = MODEL_LIVE
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Liya, an autonomous AI agent. "
            "Be helpful, concise, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _tool_text(r) -> str:
    """Unwrap a tool's return value (structured ToolResult dict or legacy
    plain string) into display text for the realtime function response."""
    if is_tool_result(r):
        return r["message"]
    return r


_TASK_LABEL_KEYS = ("goal", "message", "query", "app_name", "url", "text", "task", "prompt")

def _task_goal_label(name: str, args: dict) -> str:
    """Build a short human-readable label for the Task Queue panel."""
    for k in _TASK_LABEL_KEYS:
        v = args.get(k)
        if v:
            return f"{name}: {str(v)[:50]}"
    return name


def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [

    {
        "name": "shutdown_liya",
        "description": (
            "Shuts down Liya completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close Liya, say goodbye, or stop LIYA. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "call_e",
        "description": (
            "Places a REAL outbound phone call to a real phone number to accomplish a goal "
            "(e.g. confirm an appointment, ask a question, relay a message). This has a real "
            "irreversible consequence in the world — a phone actually rings — so you must NEVER "
            "call this with confirmed=true on the first attempt. "
            "Instead: first speak back a short summary of who will be called, at what number, "
            "and what the call will say, then explicitly ask the user to confirm out loud "
            "(e.g. 'Should I go ahead and call them?'). Only call this tool with confirmed=true "
            "after the user has clearly said yes in this same conversation. "
            "If confirmed is missing or false, do not call this tool at all — just ask for "
            "confirmation via speech first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task":      {"type": "STRING",  "description": "Plain-language description of what the call should accomplish, e.g. 'Confirm whether Asha Rao can attend her 3pm appointment Saturday, or ask to reschedule.'"},
                "phone":     {"type": "STRING",  "description": "Recipient phone number in full E.164 form, e.g. +12025550147 or +919876543210. If the user gives you a bare number with no leading '+' and no country mentioned, ASK which country it belongs to before calling this tool — do not guess by just adding a '+' in front, since that gets misread as a different country's code and the call will be rejected."},
                "region":    {"type": "STRING",  "description": "Recipient region code CALL-E supports (US, IN, SG, MY, AE, AU, CA, GB, VN, DE, JP, FR, MX, BR, ID, PH, KE). MUST match the country of the phone number above, not default to US when the number is clearly from elsewhere (e.g. use IN for a Indian number)."},
                "locale":    {"type": "STRING",  "description": "Spoken language/locale for the call, e.g. en-US, en-IN."},
                "confirmed": {"type": "BOOLEAN", "description": "Set true ONLY after the user has explicitly confirmed out loud that this specific call should be placed."},
            },
            "required": ["task", "phone", "confirmed"]
        }
    },
    {
        "name": "call_status",
        "description": (
            "Checks the current status/result of a phone call that was previously placed with "
            "call_e (e.g. if the user asks 'did that call go through', 'what happened with that "
            "call', or 'check on that call'). call_e's live mode fires the call and returns "
            "immediately without waiting, so this is the only way to learn what actually "
            "happened. If the user doesn't give a call id, use the id from the most recent "
            "call_e call in this conversation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "call_id": {"type": "STRING", "description": "The call id returned when the call was placed. If omitted, the most recently placed call id in this session is used."},
            },
            "required": []
        }
    },
]


class LiyaLive:

    def __init__(self, ui: LiyaUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None
        self._greeted        = False
        self._last_call_e    = (None, 0.0)  # last called phone (digits only), timestamp
        self._last_call_id   = None         # call_id of the most recently placed call

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_realtime_input(text=text),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        # NOTE: send_realtime_input(text=...) is the same channel real user
        # text/audio comes in on (see _on_text_command below) — the model
        # treats it as something the USER said, not as Liya's own speech.
        # Sending a bare status string like "Placing the call now, sir."
        # through here gets read by the model as a confusing, out-of-place
        # user turn instead of being spoken aloud, and can collide with a
        # tool call that's still awaiting its send_tool_response. Wrapping
        # it as an explicit [SYSTEM] instruction (the same trick already
        # used for the launch greeting below) makes the model actually
        # say the message instead of reacting to it as user input.
        payload = text if text.lstrip().startswith("[SYSTEM]") else (
            f"[SYSTEM] Say the following to the user now, out loud, in your "
            f"own natural voice (paraphrase slightly if needed, but keep the "
            f"meaning): \"{text}\""
        )
        asyncio.run_coroutine_threadsafe(
            self.session.send_realtime_input(text=payload),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"I encountered an error in {tool_name}. {short}")

    def _time_of_day_greeting_prompt(self) -> str:
        from datetime import datetime
        hour = datetime.now().hour
        if hour < 12:
            period = "morning"
        elif hour < 17:
            period = "afternoon"
        elif hour < 21:
            period = "evening"
        else:
            period = "late night"
        return (
            f"[SYSTEM] The user just launched the app. It is currently {period}. "
            f"Greet them now with a short, warm, natural \"Good {period}\"-style "
            f"greeting appropriate for this time of day, addressing them as "
            f"\"Boss\", then ask how you can help."
        )

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        address_ctx = (
            "[FORM OF ADDRESS]\n"
            "Always address the user as \"Boss\" (e.g. \"Sure, Boss\", "
            "\"On it, Boss\", \"Good morning, Boss\"). Use it naturally, "
            "not in every single sentence, but enough that it's clearly "
            "your habit when speaking to them.\n\n"
        )

        parts = [time_ctx, address_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Zephyr"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[LIYA] {name} {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "call_e":
                if not args.get("confirmed"):
                    result = (
                        "Not placed — this places a real phone call, so it requires the user's "
                        "explicit verbal confirmation first. Read back the task and phone number, "
                        "ask if they want to proceed, and only call call_e again with "
                        "confirmed=true if they clearly say yes."
                    )
                else:
                    # Dedupe by phone number alone, not (task, phone) — a
                    # slightly reworded task ("Ask Tom if..." vs "Confirm
                    # if Tom...") for the SAME recipient moments later is
                    # still almost always an accidental repeat, not a
                    # deliberate second call, and exact-text matching let
                    # that slip through and dial the same person twice.
                    sig      = "".join(c for c in str(args.get("phone", "")) if c.isdigit())
                    now      = time.time()
                    last_sig, last_time = self._last_call_e
                    if sig and sig == last_sig and (now - last_time) < 600:
                        result = (
                            f"Skipped — a call to this same number was already placed "
                            f"{int(now - last_time)}s ago (the task wording changed, but "
                            f"it's the same recipient), so this looks like an accidental "
                            f"repeat rather than a deliberate second call. If the user "
                            f"really wants to call this number again right now, ask them "
                            f"to confirm again explicitly."
                        )
                    else:
                        self._last_call_e = (sig, now)
                        call_args = dict(args)
                        # Fire-and-forget: a live voice turn shouldn't block for up to 3
                        # minutes waiting on CALL-E to poll to completion — that risks the
                        # Live session timing out and replaying this function call, which
                        # would place a second real call. speak() inside call_e() still
                        # announces progress; final status just isn't awaited here.
                        call_args["wait"] = False
                        r = await loop.run_in_executor(
                            None,
                            lambda: call_e(parameters=call_args, response=None, player=self.ui,
                                            session_memory=None, speak=self.speak)
                        )
                        result = _tool_text(r) or f"Call to {args.get('phone')} started."
                        m = re.search(r"call id ([\w\-]+)", result)
                        if m:
                            self._last_call_id = m.group(1)

            elif name == "call_status":
                from actions.call_e import call_status
                call_id = str(args.get("call_id") or "").strip() or self._last_call_id
                if not call_id:
                    result = (
                        "No call id to check yet — no call has been placed in this "
                        "session. Place a call first, or give me the call id directly."
                    )
                else:
                    r = await loop.run_in_executor(
                        None, lambda: call_status(parameters={"call_id": call_id})
                    )
                    result = _tool_text(r) or f"Checked call {call_id}."

            elif name == "shutdown_liya":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye! Take care ♡")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if name != "save_memory":
            try:
                from agent.task_queue import get_queue, TaskStatus
                failed = isinstance(result, str) and result.startswith(f"Tool '{name}' failed:")
                get_queue().record(
                    goal   = _task_goal_label(name, args),
                    status = TaskStatus.FAILED if failed else TaskStatus.COMPLETED,
                    result = None if failed else result,
                    error  = result if failed else "",
                )
            except Exception:
                pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[LIYA] {name} {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(
                audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
            )

    async def _listen_audio(self):
        print("[LIYA] Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                liya_speaking = self._is_speaking
            if not liya_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[LIYA] Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[LIYA] Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[LIYA] Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Liya: {full_out}")
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[LIYA] {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[LIYA] Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[LIYA] Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[LIYA] Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        backoff = 3
        while True:
            connected_at = None
            try:
                print("[LIYA] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()
                    connected_at = time.time()

                    print("[LIYA] Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: LIYA online. ♡ Ready.")

                    if not self._greeted:
                        self._greeted = True
                        self.speak(self._time_of_day_greeting_prompt())

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                print(f"[LIYA] {e}")
                traceback.print_exc()
            self.set_speaking(False)
            self.ui.set_state("THINKING")

            # A session that survived a reasonable while before dying is a
            # different situation than one being bounced immediately on
            # every attempt (e.g. rate limit / quota / policy close) — only
            # reset the backoff once we've proven the connection is actually
            # holding, so a fast-failing loop backs off instead of hammering
            # the API and getting closed again for the same reason.
            if connected_at is not None and (time.time() - connected_at) > 30:
                backoff = 3
            else:
                backoff = min(backoff * 2, 60)

            print(f"[LIYA] Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)


def main():
    ui = LiyaUI("face.png")

    def runner():
        ui.wait_for_api_key()
        liya = LiyaLive(ui)
        try:
            asyncio.run(liya.run())
        except KeyboardInterrupt:
            print("\n LIYA shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()