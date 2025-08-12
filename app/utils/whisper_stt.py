# app/utils/whisper_stt.py
import os
from openai import OpenAI

def transcribe_with_openai(file_path: str):
    """Transcribes an audio file to text using the OpenAI Whisper API."""
    try:
        client = OpenAI()
        
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            return transcript.text
    except Exception as e:
        raise Exception(f"OpenAI transcription error: {e}")
