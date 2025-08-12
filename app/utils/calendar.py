# app/utils/calendar.py
import datetime
import os
from typing import Dict, Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- Google Calendar API Logic ---
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def get_google_calendar_service():
    """
    Authenticates with the Google Calendar API and returns a service object.
    Expects credentials.json in project root (same folder as token.json).
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def _safe_attendees(patient_email: Optional[str]) -> list[Dict[str, Any]]:
    """
    Build attendees list; skip empty/invalid. You can add your clinic email here too if desired.
    """
    attendees = []
    if patient_email and "@" in patient_email:
        attendees.append({"email": patient_email})
    return attendees

def create_google_calendar_event(service, event_details: Dict[str, Any]):
    """
    Creates an event and sends invites to attendees.
    Required keys in event_details:
      - start: datetime.datetime (timezone-naive OK; we set tz below)
      - end:   datetime.datetime
      - patient_name: str
      - patient_email: str (guest)
      - reason: str
    """
    start_dt = event_details.get("start")
    end_dt = event_details.get("end")

    if not isinstance(start_dt, datetime.datetime) or not isinstance(end_dt, datetime.datetime):
        return {"status": "error", "message": "Could not find a valid start/end datetime for the event."}

    timezone = "Europe/London"

    event = {
        "summary": f"Appointment: {event_details.get('patient_name', 'N/A')} ({event_details.get('reason', 'N/A')})",
        "description": (
            f"Patient: {event_details.get('patient_name', 'N/A')}\n"
            f"Reason: {event_details.get('reason', 'N/A')}\n"
            f"Email: {event_details.get('patient_email', 'N/A')}\n"
            "Booked by Voice Calendar Agent."
        ),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": timezone,
        },
        # Add a Meet link automatically
        "conferenceData": {
            "createRequest": {
                "requestId": os.urandom(16).hex(),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "attendees": _safe_attendees(event_details.get("patient_email")),
        # Optional knobs you may like:
        "guestsCanInviteOthers": False,
        "guestsCanModify": False,
        "guestsCanSeeOtherGuests": False,
        # Use the calendar's default reminders (e.g., 30 min popup)
        "reminders": {"useDefault": True},
    }

    try:
        # sendUpdates='all' ensures invitation emails go out to attendees
        event = (
            service.events()
            .insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",  # <— key line: email guests
            )
            .execute()
        )
        return {"status": "success", "message": f"Event created: {event.get('htmlLink')}"}
    except Exception as e:
        print(f"Error creating calendar event: {e}")
        return {"status": "error", "message": f"Failed to create event: {str(e)}"}
