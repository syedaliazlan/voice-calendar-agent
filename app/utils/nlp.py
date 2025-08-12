# app/utils/nlp.py
import re
import json
import datetime
from typing import Optional, Tuple, Dict, Any

from dateparser.search import search_dates
import dateparser
from openai import OpenAI

FILLERS = {
    "ok", "okay", "okay thanks", "thanks", "thank you", "yep", "yeah", "alright",
    "hmm", "mm", "mmm", "please continue", "go on"
}

# ---------- small helpers ----------

def is_affirmative(text: str) -> bool:
    t = (text or "").lower().strip()
    return any(w in t for w in [
        "yes", "yeah", "yep", "correct", "that's right", "confirm",
        "please do", "go ahead", "sure", "yup"
    ])

def is_negative(text: str) -> bool:
    t = (text or "").lower().strip()
    return any(w in t for w in ["no", "nope", "nah", "incorrect", "that's wrong", "don't", "do not"])

def is_filler(text: str) -> bool:
    return (text or "").lower().strip() in FILLERS

def _now():
    return datetime.datetime.now()

def _today_00():
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)

EMAIL_REGEX = re.compile(r"\b([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)\b", re.I)
DOMAIN_REGEX = re.compile(r"([a-z0-9\-]+(?:\.[a-z0-9\-]+)+)", re.I)  # e.g., highwaysindustry.com, example.co.uk

def _normalize_spoken_tokens(text: str) -> str:
    """
    Convert spoken markers to symbols and tidy spacing *around* @ and .
    Avoids gluing stray words into the local-part.
    """
    t = (text or "").lower()

    # Strip lead-ins that often precede the address
    t = re.sub(r"\b(my\s+email\s+is|email\s+is|email\s*address\s*is|the\s+email\s+is|it\s+is|it's)\b[:,\.\s]*", " ", t)

    # Spoken tokens -> symbols
    t = re.sub(r"\bunderscore\b", "_", t)
    t = re.sub(r"\bhyphen\b", "-", t)
    t = re.sub(r"\bdash\b", "-", t)
    t = re.sub(r"\bperiod\b", ".", t)
    t = re.sub(r"\bdot\b", ".", t)
    t = re.sub(r"\bat\b", "@", t)

    # Tighten whitespace around @ and .
    t = re.sub(r"\s*@\s*", "@", t)
    t = re.sub(r"\s*\.\s*", ".", t)

    # Remove spaces occurring inside the domain (after @)
    t = re.sub(r"(?<=@)\s+", "", t)

    # Remove stray spaces adjacent to dots
    t = re.sub(r"\s+(?=\.)", "", t)
    t = re.sub(r"(?<=\.)\s+", "", t)

    return t.strip()

def _extract_email(user_text: str) -> Optional[str]:
    """
    Extracts an email robustly, handling:
      - 'my email is ali at highways industry dot com'
      - 'it is ali@outlook.com'
      - 'aliatdomain.com'  (convert last 'at' before domain to '@')
    Ignores plain websites like 'www.example.com'.
    """
    t = _normalize_spoken_tokens(user_text)

    # 1) Standard email match — choose the last one
    candidates = EMAIL_REGEX.findall(t)
    if candidates:
        return candidates[-1]

    # 2) Recover '...<local>at<domain>' when there's no '@'
    m = DOMAIN_REGEX.search(t)
    if m:
        domain = m.group(1)
        start = m.start(1)
        left = t[:start]
        m2 = re.search(r"([a-z0-9._%+\-]{1,64})\s*at\s*$", left, re.I)
        if m2:
            local = m2.group(1)
            return f"{local}@{domain}"

    return None

# ---------- Date/Time parsing ----------

WEEKDAY_TO_IDX = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
}
WEEKDAYS = set(WEEKDAY_TO_IDX.keys())
MONTHS = {"january","february","march","april","may","june","july","august","september","october","november","december",
          "jan","feb","mar","apr","jun","jul","aug","sep","sept","oct","nov","dec"}

def _mentions_date(text: str) -> bool:
    t = (text or "").lower()
    return (
        any(w in t for w in ["today","tomorrow","this ","next ","coming "]) or
        any(w in t for w in WEEKDAYS) or
        any(w in t for w in MONTHS) or
        bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", t)) or
        bool(re.search(r"\b\d{1,2}[-/]\d{1,2}\b", t))
    )

def _mentions_time(text: str) -> bool:
    t = (text or "").lower()
    return (
        bool(re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", t)) or
        bool(re.search(r"\b\d{1,2}\s*(am|pm)\b", t)) or
        "noon" in t or "midnight" in t
    )

def _next_weekday(base: datetime.date, target_idx: int, include_today: bool=False) -> datetime.date:
    delta = (target_idx - base.weekday()) % 7
    if delta == 0 and not include_today:
        delta = 7
    return base + datetime.timedelta(days=delta)

def _compute_relative_weekday(text: str) -> Optional[datetime.date]:
    """
    Parse 'this/next/coming <weekday>' or plain '<weekday>'.
    """
    t = (text or "").lower()
    base = _today_00().date()

    # explicit 'this/next/coming X'
    m = re.search(r"\b(this|next|coming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        mod, wd = m.group(1), m.group(2)
        idx = WEEKDAY_TO_IDX[wd]
        if mod == "this":
            # 'this' within the current week; if day passed, push next week
            d = _next_weekday(base, idx, include_today=True)
            if d < base:
                d = d + datetime.timedelta(days=7)
            return d
        # next/coming => always >7 days ahead if the day would be today/earlier
        d = _next_weekday(base, idx, include_today=False)
        # ensure it's within next week, not tomorrow if today is earlier weekday
        return d

    # plain weekday
    m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        idx = WEEKDAY_TO_IDX[m.group(1)]
        return _next_weekday(base, idx, include_today=True)

    # keywords today/tomorrow
    if "tomorrow" in t:
        return base + datetime.timedelta(days=1)
    if "today" in t:
        return base

    return None

def _parse_time_component(text: str) -> Optional[str]:
    t = (text or "").lower()

    # noon/midnight
    if "noon" in t:
        return "12:00"
    if "midnight" in t:
        return "00:00"

    # hh:mm am/pm or hh am/pm
    m = re.search(r"\b([0-1]?\d|2[0-3])\s*:\s*([0-5]\d)\s*(am|pm)?\b", t)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2)); ap = m.group(3)
        if ap:
            if hh == 12: hh = 0
            if ap == "pm": hh += 12
        return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b([0-1]?\d)\s*(am|pm)\b", t)
    if m:
        hh = int(m.group(1)); ap = m.group(2)
        if hh == 12: hh = 0
        if ap == "pm": hh += 12
        return f"{hh:02d}:00"

    return None

def _ensure_future(dt: datetime.datetime) -> datetime.datetime:
    now = _now()
    if dt > now:
        return dt
    # If within last week, likely meant next occurrence
    if (now - dt).days < 7:
        return dt + datetime.timedelta(days=7)
    # Else assume next year
    return dt.replace(year=dt.year + 1)

def _parse_date_time(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Rule-first:
      1) Relative weekday (this/next/coming) or plain weekday => date
      2) Time independently
      3) If still missing date, try absolute date via dateparser (15 Sep, 09/15, 2025-09-15)
    """
    date_val: Optional[datetime.date] = _compute_relative_weekday(text)
    time_str: Optional[str] = _parse_time_component(text)

    if not date_val:
        # try absolute dates with dateparser
        settings = {
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": _today_00(),
            "RETURN_AS_TIMEZONE_AWARE": False,
            "TIMEZONE": "Europe/London",
            "PREFER_DAY_OF_MONTH": "current",
        }
        dt = dateparser.parse(text, settings=settings, languages=["en"])
        if not dt:
            found = search_dates(text, settings=settings, languages=["en"])
            if found:
                # choose a sensible candidate in the future if any
                candidates = [d for _, d in found]
                future = [d for d in candidates if d >= _now()]
                dt = future[0] if future else candidates[0]
        if dt:
            dt = _ensure_future(dt)
            date_val = dt.date()
            # If no time yet but dt had a time component (e.g., '2025-09-15 14:30')
            if not time_str and (dt.hour or dt.minute):
                time_str = f"{dt.hour:02d}:{dt.minute:02d}"

    date_str = date_val.isoformat() if date_val else None
    return date_str, time_str

# ---------- Extraction API ----------

def extract_fields_with_rules(user_text: str, captured: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    English-only: robust email + reliable date/relative weekday + time.
    """
    out: Dict[str, Optional[str]] = {
        "patient_name": None,
        "patient_email": None,
        "appointment_date": None,
        "appointment_time": None,
        "reason": None,
    }

    # Email
    email = _extract_email(user_text)
    if email:
        out["patient_email"] = email

    # Date/time
    date_str, time_str = _parse_date_time(user_text)
    if date_str: out["appointment_date"] = date_str
    if time_str: out["appointment_time"] = time_str

    # Name
    if not captured.get("patient_name"):
        m = re.search(r"(?:my name is|i am|it's|it is)\s+([a-z][a-z\s\-'`\.]+)", user_text, re.I)
        if m:
            out["patient_name"] = re.sub(r"\s+", " ", m.group(1)).title()

    # Reason (simple heuristic)
    if not captured.get("reason"):
        m = re.search(r"(?:because|for|regarding|about)\s+([a-z0-9\s\-,'`\.]{3,})", user_text, re.I)
        if m:
            out["reason"] = re.sub(r"\s+", " ", m.group(1)).strip()

    return out

def extract_fields_with_llm(user_text: str, captured: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Rules first; fill only the gaps with LLM (English).
    """
    fields = extract_fields_with_rules(user_text, captured)

    need_llm = any(fields.get(k) is None for k in ("patient_email", "appointment_date", "appointment_time"))
    if not need_llm:
        return fields

    client = OpenAI()
    today = _today_00().date().isoformat()

    system = (
        "Extract fields for a medical appointment from the user's English text. "
        "Return JSON only with keys: patient_name, patient_email, appointment_date, appointment_time, reason. "
        "Rules:\n"
        "- Normalize spoken emails like 'john at gmail dot com' -> 'john@gmail.com'; remove spaces.\n"
        f"- TODAY is {today}. Resolve 'tomorrow', 'this Friday', 'next Monday' as future dates (YYYY-MM-DD).\n"
        "- For times like '2 pm' or '14:30', output 24h 'HH:MM'.\n"
        "- If a field is unknown, set it to null.\n"
        "- Do not infer a date if the user didn't mention any date."
    )

    payload = {"user_text": user_text, "current_captured": captured or {}, "today": today}

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ],
        temperature=0.0,
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)

    merged = {
        "patient_name": fields.get("patient_name") or data.get("patient_name"),
        "patient_email": fields.get("patient_email") or data.get("patient_email"),
        "appointment_date": fields.get("appointment_date") or data.get("appointment_date"),
        "appointment_time": fields.get("appointment_time") or data.get("appointment_time"),
        "reason": fields.get("reason") or data.get("reason"),
    }
    return merged
