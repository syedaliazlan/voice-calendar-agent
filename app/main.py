# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import openai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set the OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("Warning: OPENAI_API_KEY environment variable is not set. API calls will fail.")

# Create the FastAPI app instance
app = FastAPI(
    title="Automated Appointment Agent",
    description="An automated patient appointment booking system using OpenAI's chained architecture.",
    version="1.0.0"
)

# Set up CORS middleware
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the audio router (we'll define this below)
from app.routers.audio import router as audio_router
app.include_router(audio_router)

@app.get("/")
async def root():
    return {"message": "Hello, world! FastAPI server is running."}
