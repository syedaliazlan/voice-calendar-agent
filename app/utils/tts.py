import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from openai import OpenAI

APP_DIR = os.path.dirname(os.path.dirname(__file__))  # .../app
TTS_DIR = os.path.join(APP_DIR, "tts_audio")
os.makedirs(TTS_DIR, exist_ok=True)

# Use a model you have access to — switching to tts-1
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "tts-1")  
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")  # alloy, verse, shimmer

_executor = ThreadPoolExecutor(max_workers=2)

def _generate_tts_audio(text: str) -> str:
    """
    Synchronous TTS using OpenAI Audio->Speech API -> MP3 file path.
    """
    client = OpenAI()

    fname = f"response_{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.mp3"
    out_path = Path(TTS_DIR) / fname

    with client.audio.speech.with_streaming_response.create(
        model=OPENAI_TTS_MODEL,
        voice=OPENAI_TTS_VOICE,
        input=text,
        response_format="mp3",
    ) as resp:
        resp.stream_to_file(out_path)

    return str(out_path)

async def speak_text(text: str) -> str:
    """
    Async wrapper so the router can `await` TTS generation.
    """
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(_executor, _generate_tts_audio, text)
    if not path or not os.path.exists(path):
        raise RuntimeError("Failed to synthesize speech via OpenAI TTS")
    return path
