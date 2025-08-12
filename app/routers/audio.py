# app/routers/audio.py
import os
import shutil
import re
import datetime
from urllib.parse import quote

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse

from app.utils.calendar import create_google_calendar_event, get_google_calendar_service
from app.utils.tts import speak_text
from app.utils.whisper_stt import transcribe_with_openai
from app.utils.nlp import (
    extract_fields_with_llm,
    is_affirmative,
    is_negative,
    is_filler,
)

router = APIRouter(prefix="/audio", tags=["audio"])

conversation_states: dict[str, dict] = {}

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMP_AUDIO_DIR = os.path.join(BASE_DIR, "routers", "temp_audio")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)


def _normalise_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _get_session_state(session_id: str) -> dict:
    state = conversation_states.get(session_id)
    if not state:
        state = {
            "step": "greeting",
            "captured": {
                "patient_name": None,
                "patient_email": None,
                "appointment_date": None,
                "appointment_time": None,
                "reason": None,
            },
            "email_confirmed": False,
            "datetime_confirmed": False,
        }
        conversation_states[session_id] = state
    return state

# Email playback helper (provider-aware)
_COMMON_PROVIDERS = {
    "gmail", "outlook", "hotmail", "protonmail", "icloud", "yahoo",
    "aol", "zoho", "yandex", "gmx", "hey", "live", "msn", "me"
}
def _speakable_email(email: str) -> str:
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        return email
    local_tokens = []
    for ch in local:
        if ch == ".": local_tokens.append("dot")
        elif ch == "-": local_tokens.append("dash")
        elif ch == "_": local_tokens.append("underscore")
        else: local_tokens.append(ch)
    labels = domain.split(".")
    provider = labels[0] if labels else domain
    rest = labels[1:] if len(labels) > 1 else []
    domain_tokens = []
    if provider.lower() in _COMMON_PROVIDERS:
        domain_tokens.append(provider.lower())
    else:
        for ch in provider:
            domain_tokens.append("dash" if ch == "-" else ch)
    for tld in rest:
        domain_tokens.append("dot"); domain_tokens.append(tld.lower())
    return ",  ".join(local_tokens + ["at"] + domain_tokens)

def _friendly_datetime(date_iso: str, time_24: str | None) -> str:
    y, m, d = [int(x) for x in date_iso.split("-")]
    hour = minute = 0
    if time_24:
        hour, minute = [int(x) for x in time_24.split(":")]
    dt = datetime.datetime(y, m, d, hour, minute)
    weekday = dt.strftime("%A"); month = dt.strftime("%B"); day = dt.day
    if time_24:
        ampm = "am" if hour < 12 else "pm"
        h12 = hour % 12 or 12
        time_part = f"{h12}{'' if minute==0 else ':'+str(minute).zfill(2)} {ampm}"
        return f"{weekday} {day} {month} at {time_part}"
    return f"{weekday} {day} {month}"

def _date_examples() -> str:
    """
    Generate dynamic examples like:
    'this Friday', 'next Monday', '15 September'
    """
    now = datetime.datetime.now()
    today = now.date()
    # this Friday
    target_fri = (4 - today.weekday()) % 7
    this_friday = today + datetime.timedelta(days=target_fri if target_fri != 0 else 0)
    # next Monday
    target_mon = (0 - today.weekday()) % 7 or 7
    next_monday = today + datetime.timedelta(days=target_mon)
    # absolute date ~2 weeks ahead
    abs_date = today + datetime.timedelta(days=14)
    return f"'{this_friday.strftime('%A') if this_friday!=today else 'today'} {this_friday.day} {this_friday.strftime('%B')}', 'next Monday', '{abs_date.day} {abs_date.strftime('%B')}'"

def _next_prompt(state: dict) -> str:
    c = state["captured"]

    if state["step"] == "greeting":
        state["step"] = "ask_name"
        return "Hello! I can help you book an appointment. Could I take your full name, please?"

    if state["step"] == "ask_name":
        if not c["patient_name"]:
            return "Could I take your full name, please?"
        state["step"] = "ask_email"

    if state["step"] in ("ask_email", "confirm_email"):
        if not c["patient_email"]:
            state["step"] = "ask_email"
            return "Thanks. What is your email address?"
        if not state.get("email_confirmed"):
            state["step"] = "confirm_email"
            spelled = _speakable_email(c["patient_email"])
            return f"I heard {spelled}. Is that correct?"
        state["step"] = "ask_date"

    if state["step"] in ("ask_date", "ask_time", "confirm_datetime"):
        if not c["appointment_date"] and not c["appointment_time"]:
            state["step"] = "ask_date"
            examples = _date_examples()
            return f"Great. What date would you like? You can say {examples}."
        if c["appointment_date"] and not c["appointment_time"]:
            state["step"] = "ask_time"
            return "What time would you prefer? You can say '2 pm' or '14:30'."
        if c["appointment_date"] and c["appointment_time"] and not state.get("datetime_confirmed"):
            state["step"] = "confirm_datetime"
            spoken = _friendly_datetime(c["appointment_date"], c["appointment_time"])
            return f"I heard {spoken}. Is that correct?"
        if c["appointment_date"] and c["appointment_time"] and state.get("datetime_confirmed"):
            state["step"] = "ask_reason"

    if state["step"] == "ask_reason":
        if not c["reason"]:
            return "Finally, what is the reason for your visit?"
        state["step"] = "confirm"

    if state["step"] == "confirm":
        return (
            f"Perfect. I’ve got {c['patient_name']} with email {c['patient_email']}, "
            f"on {c['appointment_date']} at {c['appointment_time']} for {c['reason']}. "
            "Shall I book this now?"
        )

    state["step"] = "ask_name"
    return "Could I take your full name, please?"

@router.post("/process")
async def process_audio(
    session_id: str = Form(...),
    audio: UploadFile = File(...),
    init: int = Form(0),
):
    """
    STT -> rule-first NLP (+ LLM fallback only to fill gaps) -> confirmations -> TTS (MP3).
    """
    filename = f"{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}_{audio.filename or 'user.webm'}"
    file_path = os.path.join(TEMP_AUDIO_DIR, filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    user_text = ""
    response_text = ""
    calendar_error = ""
    session_ended = "0"

    try:
        state = _get_session_state(session_id)
        c = state["captured"]

        file_is_tiny = os.path.getsize(file_path) < 600
        is_init_turn = bool(init) or file_is_tiny or state["step"] == "greeting"

        if is_init_turn:
            response_text = _next_prompt(state)
        else:
            user_text = _normalise_text(transcribe_with_openai(file_path))

            expecting_value = state["step"] in {"ask_email", "confirm_email", "ask_date", "ask_time", "confirm_datetime", "ask_reason"}
            if expecting_value and is_filler(user_text):
                response_text = _next_prompt(state)
            else:
                if state["step"] == "confirm_email":
                    if is_affirmative(user_text):
                        state["email_confirmed"] = True
                    elif is_negative(user_text):
                        state["email_confirmed"] = False
                        c["patient_email"] = None
                        state["step"] = "ask_email"

                elif state["step"] == "confirm_datetime":
                    if is_affirmative(user_text):
                        state["datetime_confirmed"] = True
                    elif is_negative(user_text):
                        state["datetime_confirmed"] = False
                        c["appointment_date"] = None
                        c["appointment_time"] = None
                        state["step"] = "ask_date"

                else:
                    before_date = c.get("appointment_date")
                    before_time = c.get("appointment_time")

                    try:
                        fields = extract_fields_with_llm(user_text, c)
                        for k, v in (fields or {}).items():
                            if v:
                                c[k] = v

                        if c.get("patient_email") and state["step"] == "ask_email":
                            state["email_confirmed"] = False
                            state["step"] = "confirm_email"

                        after_date = c.get("appointment_date")
                        after_time = c.get("appointment_time")
                        if (after_date and after_time) and (before_date != after_date or before_time != after_time):
                            state["datetime_confirmed"] = False
                            state["step"] = "confirm_datetime"
                    except Exception:
                        pass

                response_text = _next_prompt(state)

                # Booking on affirmative at confirm step
                booking_complete = False
                if state["step"] == "confirm" and is_affirmative(user_text):
                    try:
                        date_str = c.get("appointment_date")
                        time_str = c.get("appointment_time") or "09:00"
                        if not date_str:
                            raise ValueError("Missing appointment date.")
                        dt = datetime.datetime.fromisoformat(f"{date_str}T{time_str}:00")
                        end = dt + datetime.timedelta(minutes=30)

                        payload = {
                            "patient_name": c.get("patient_name"),
                            "patient_email": c.get("patient_email"),
                            "reason": c.get("reason"),
                            "start": dt,
                            "end": end,
                        }

                        service = get_google_calendar_service()
                        result = create_google_calendar_event(service, payload)
                        if result.get("status") == "success":
                            response_text = "Your appointment is booked. You’ll receive a confirmation by email shortly. Anything else I can help with?"
                            session_ended = "1"
                        else:
                            calendar_error = result.get("message", "Unknown calendar error")
                            response_text = "I couldn't complete the booking just now. Would you like me to try again?"
                        booking_complete = True
                    except Exception as e:
                        calendar_error = str(e)
                        response_text = "I couldn't complete the booking just now. Would you like me to try again?"
                        booking_complete = True

                if booking_complete:
                    conversation_states.pop(session_id, None)

        audio_file_path = await speak_text(response_text)

        headers = {
            "X-User-Transcript": quote(user_text)[:4000],
            "X-Bot-Text": quote(response_text)[:4000],
            "X-Agent-State": quote(state["step"]),
            "X-Calendar-Error": quote(calendar_error)[:4000] if calendar_error else "",
            "X-Session-Ended": session_ended,
            "Access-Control-Expose-Headers": "X-User-Transcript, X-Bot-Text, X-Agent-State, X-Calendar-Error, X-Session-Ended",
        }
        return FileResponse(path=audio_file_path, media_type="audio/mpeg", headers=headers)

    except Exception as e:
        print(f"process_audio error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
