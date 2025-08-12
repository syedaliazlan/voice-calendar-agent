# Voice Calendar Agent (FastAPI + OpenAI + Google Calendar)

A fully automated, voice-driven appointment booking agent. It listens to the user, extracts details (name, email, date, time, reason), confirms them, and books a Google Calendar event—sending an invite to the guest.

## ✨ Features
- **Hands-free call flow**: auto-listen → process → speak → listen again.
- **STT**: OpenAI Whisper.
- **NLP**: fast rule-first extraction (email/date/time/reason) + LLM fallback for gaps.
- **Email UX**: speaks local-part letter-by-letter but pronounces common domains (e.g., “gmail”).
- **Date/Time**: robust parsing for “this/next Monday 4pm”, “15 Sep 14:30”, etc., with a spoken confirmation.
- **TTS**: OpenAI TTS (returns MP3).
- **Calendar**: creates event with Meet link and **sends invites** (`sendUpdates="all"`).
- **Frontend**: simple web UI with transcript log; shows detailed Calendar errors.

## 🧱 Architecture
```
Frontend (index.html)  ─┐
  - Mic capture (MediaRecorder + VAD)       │
  - Transcript log & status                  │   POST /audio/process (FormData: audio, session_id, init)
  - Plays TTS mp3                            ├── FastAPI backend (app/)
                                             │     - Whisper STT
                                             │     - NLP (rules + LLM fallback)
                                             │     - State machine w/ confirmations
                                             │     - OpenAI TTS (speak_text)
                                             │     - Google Calendar insert (sendUpdates="all")
                                             └── Returns: MP3 + headers (X-User-Transcript, X-Bot-Text, X-Calendar-Error, X-Session-Ended)
```

## 📂 Project Structure
```
app/
  main.py
  routers/
    audio.py
  utils/
    nlp.py
    whisper_stt.py
    tts.py
    calendar.py
index.html
requirements.txt
```

## ✅ Prerequisites
- Python **3.11+**
- An OpenAI API key
- A Google account with Calendar API enabled

## ⚙️ Setup
1) **Clone & venv**
```bash
git clone <your-repo-url>
cd <your-repo>
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

2) **Install deps**
```bash
pip install -r requirements.txt
```

3) **Environment**
Create a `.env` file in the project root:
```
OPENAI_API_KEY=sk-****************
# Optional:
# OPENAI_ORG=org_...
# OPENAI_PROJECT=proj_...
```

4) **Google Calendar API**
- In Google Cloud Console: **APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app**.
- Download the **credentials.json** and place it in the project root (same folder as this README).
- First run will open a browser to authorize; a **token.json** will be created automatically.
- We use scope: `https://www.googleapis.com/auth/calendar.events`.

> **Note:** Don’t commit `credentials.json` or `token.json` (they’re in `.gitignore`).

## ▶️ Run
**Backend**
```bash
uvicorn app.main:app --reload
```

**Frontend**
Serve `index.html` over localhost (so the mic works). For example:
```bash
# Option A (Python):
python -m http.server 5500
# then open http://127.0.0.1:5500/index.html

# Option B: use VS Code “Live Server” extension
```

## 🔌 API (for reference)
`POST /audio/process` (multipart/form-data)
- `audio`: recorded audio blob (`audio/webm`)
- `session_id`: stable UUID per browser
- `init`: `1` to trigger the greeting (tiny-ping)

**Response:** `audio/mpeg` (TTS)  
**Exposed headers:**
- `X-User-Transcript`: last user transcript (url-encoded)
- `X-Bot-Text`: agent’s spoken text (url-encoded)
- `X-Agent-State`: server state (e.g., `ask_email`, `confirm_datetime`, `confirm`)
- `X-Calendar-Error`: detailed Calendar error if booking failed
- `X-Session-Ended`: `1` after a successful booking (frontend stops listening)

## ⚙️ Configuration knobs
- **Voice & pace**: `app/utils/tts.py` (choose model/voice, tweak speed if needed).
- **VAD (turn-taking)**: in `index.html` script:
  ```js
  const SILENCE_MS = 1600;
  const MIN_SPEECH_MS = 800;
  const NO_SPEECH_FAILSAFE_MS = 6000;
  const HARD_STOP_MS = 15000;
  const AMP_SPEECH_THRESHOLD = 0.010;
  ```
- **Appointment duration**: in `app/routers/audio.py` (default 30 min).
- **Timezone**: `Europe/London` (change in `calendar.py` if needed).
- **Calendar invites**: enabled via `sendUpdates="all"` in `calendar.py`.

## ✉️ Guest invites
- Invites are sent to the `patient_email` attendee.
- If the attendee email equals the organizer (your account), Google usually won’t email you (since you created it).
- To copy a clinic mailbox automatically, add it in `app/utils/calendar.py::_safe_attendees`.

## 🧪 Test phrases
- Name: “**My name is Ali Azlan**”
- Email: “**It is ali at gmail dot com**” / “**ali@outlook.com**”
- Date+Time: “**next Monday 2 pm**” / “**15 September at 14:30**”
- Reason: “**general checkup**”, “**about back pain**”

## 🛠️ Troubleshooting
- **`{"detail":"Not Found"}`**: wrong path; ensure `app.include_router(audio_router)` and `@router.post("/process")`.
- **`{"detail":[{"type":"missing","loc":["body","audio"]...}]}`**: frontend must send `audio` form field; verify `formData.append('audio', ...)`.
- **No greeting / mic blocked**: serve `index.html` via `http://localhost` (not `file://`) and allow microphone permissions.
- **`ModuleNotFoundError: dateparser`**: `pip install dateparser`.
- **OpenAI 403 / model not found**: verify `OPENAI_API_KEY` and model names in `tts.py`.
- **Calendar invite not sent**: ensure you updated `calendar.py` to use `sendUpdates="all"`, and test with a different attendee than the organizer.
- **See exact Calendar error**: check UI — we surface `X-Calendar-Error` in the transcript/status.

## 🧹 Security
- **Never commit** your `.env`, `credentials.json`, or `token.json`.
- For production, add auth, rate limiting, persistent storage for sessions, and structured logging.

## 🗺️ Roadmap ideas
- Ambient noise auto-calibration for VAD
- Additional voices + prosody tuning
- Multi-slot scheduling (find next available)
- Exportable transcript logs

## 📄 License
MIT (or your choice)