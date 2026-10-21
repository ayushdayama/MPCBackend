import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from cryptography.fernet import Fernet
from firebase_admin import firestore
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.firebase_service import fetch_cycle_data
from app.security import APP_SECRET_KEY

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
]
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DEFAULT_CLIENT_FILE = Path(__file__).with_name("MPCBackend_Calendar_Credentials.json")
DEFAULT_REDIRECT_URI = "http://localhost:8000/auth/google/callback"


class GoogleCalendarPermissionDenied(Exception):
    pass


def _client_config() -> dict:
    raw_config = os.getenv("GOOGLE_OAUTH_CLIENT_JSON")
    if raw_config:
        config = json.loads(raw_config)
    else:
        path = Path(os.getenv("GOOGLE_OAUTH_CLIENT_FILE", str(DEFAULT_CLIENT_FILE)))
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        with path.open(encoding="utf-8") as credentials_file:
            config = json.load(credentials_file)
    if "web" not in config:
        raise ValueError("Google OAuth credentials must be a Web application client")
    return config


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI)


def _frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173/MPCFrontend/").rstrip("/")


def _flow(state: str | None = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = _redirect_uri()
    return flow


def create_authorization_url(state: str) -> str:
    flow = _flow(state)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )
    return authorization_url


def _token_cipher() -> Fernet:
    key = hashlib.sha256(
        ("cycle-calendar-refresh-token:" + APP_SECRET_KEY).encode("utf-8")
    ).digest()
    import base64

    return Fernet(base64.urlsafe_b64encode(key))


def _user_ref(username: str):
    return firestore.client().collection("cycle-sense-users").document(username)


def _calendar_data(username: str) -> dict:
    snapshot = _user_ref(username).get()
    data = snapshot.to_dict() or {} if snapshot.exists else {}
    return data.get("googleCalendar") or {}


def get_calendar_status(username: str) -> dict:
    data = _calendar_data(username)
    return {
        "connected": bool(data.get("encryptedRefreshToken")),
        "email": data.get("email"),
        "sync_status": data.get("syncStatus", "idle"),
        "last_sync": data.get("lastSyncAt"),
        "sync_error": data.get("syncError"),
    }


def _fetch_authorization_token(flow: Flow, code: str) -> None:
    try:
        flow.fetch_token(code=code)
    except Warning as exc:
        token = getattr(exc, "token", None)
        granted_scopes = set(token.get("scope", "").split()) if token else set()
        if CALENDAR_EVENTS_SCOPE not in granted_scopes:
            raise GoogleCalendarPermissionDenied from exc
        flow.oauth2session.token = token


def complete_authorization(username: str, code: str, state: str) -> None:
    flow = _flow(state)
    _fetch_authorization_token(flow, code)
    credentials = flow.credentials
    calendar_data = _calendar_data(username)
    refresh_token = credentials.refresh_token
    if not refresh_token and calendar_data.get("encryptedRefreshToken"):
        refresh_token = _token_cipher().decrypt(
            calendar_data["encryptedRefreshToken"].encode("ascii")
        ).decode("utf-8")
    if not refresh_token:
        raise RuntimeError("Google did not provide a refresh token. Reconnect and approve access.")

    email = calendar_data.get("email")
    try:
        response = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=10,
        )
        response.raise_for_status()
        email = response.json().get("email") or email
    except requests.RequestException:
        logging.warning("Could not retrieve the authorized Google account email")

    _user_ref(username).set(
        {
            "googleCalendar": {
                "encryptedRefreshToken": _token_cipher().encrypt(
                    refresh_token.encode("utf-8")
                ).decode("ascii"),
                "email": email,
                "syncStatus": "idle",
                "syncError": None,
            }
        },
        merge=True,
    )


def _calendar_service(username: str):
    calendar_data = _calendar_data(username)
    encrypted_token = calendar_data.get("encryptedRefreshToken")
    if not encrypted_token:
        return None

    refresh_token = _token_cipher().decrypt(encrypted_token.encode("ascii")).decode("utf-8")
    client = _client_config()["web"]
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=client["token_uri"],
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )
    credentials.refresh(Request())
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _date_string(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    value = str(value)
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return datetime.strptime(value, "%d-%b-%Y").date().isoformat()


def _event_id(username: str, date_value: str) -> str:
    digest = hashlib.sha256(f"{username}:{date_value}".encode("utf-8")).hexdigest()
    return f"cs{digest}"


def _prediction_context(recorded_dates: list[str]) -> tuple[str | None, int | None]:
    dates = sorted({_date_string(value) for value in recorded_dates})
    if len(dates) < 2:
        return None, None

    from app.model import predict_next_dates

    predictions = predict_next_dates(dates, top_n=1)
    if not predictions:
        return None, None

    predicted_date = _date_string(predictions[0])
    days_since_last = (
        date.fromisoformat(predicted_date) - date.fromisoformat(dates[-1])
    ).days
    return predicted_date, days_since_last


def _upsert_date_event(
    service,
    username: str,
    date_value: str,
    next_predicted_date: str | None = None,
    days_since_last_recorded: int | None = None,
) -> None:
    start = date.fromisoformat(date_value)
    title_date = f"{start.strftime('%B')} {start.day}"
    prediction_text = (
        datetime.strptime(next_predicted_date, "%Y-%m-%d").strftime("%d-%b-%Y")
        if next_predicted_date
        else "Not available yet"
    )
    days_text = (
        str(days_since_last_recorded)
        if days_since_last_recorded is not None
        else "Not available"
    )
    event = {
        "id": _event_id(username, date_value),
        "summary": f"Menstrual Cycle - {title_date}",
        "description": (
            f"Next Predicted Date: {prediction_text}\n"
            f"Days Since Last Recorded Date: {days_text}"
        ),
        "start": {"date": date_value},
        "end": {"date": (start + timedelta(days=1)).isoformat()},
        "extendedProperties": {
            "private": {
                "cycleSenseApp": "cycle-sense",
                "cycleSenseUser": username,
            }
        },
    }
    try:
        service.events().insert(calendarId="primary", body=event).execute()
    except HttpError as exc:
        if exc.resp.status != 409:
            raise
        existing = service.events().get(
            calendarId="primary", eventId=event["id"]
        ).execute()
        if any(
            existing.get(field) != event[field]
            for field in ("summary", "description", "start", "end", "extendedProperties")
        ):
            service.events().update(
                calendarId="primary", eventId=event["id"], body=event
            ).execute()


def set_sync_error(username: str) -> None:
    _user_ref(username).set(
        {
            "googleCalendar": {
                "syncStatus": "error",
                "syncError": "Calendar sync failed. Try syncing again.",
            }
        },
        merge=True,
    )


def mark_sync_queued(username: str) -> None:
    _user_ref(username).set(
        {"googleCalendar": {"syncStatus": "queued", "syncError": None}},
        merge=True,
    )


def mark_sync_needed(username: str) -> bool:
    if not _calendar_data(username).get("encryptedRefreshToken"):
        return False
    _user_ref(username).set(
        {"googleCalendar": {"syncStatus": "manual_required", "syncError": None}},
        merge=True,
    )
    return True


def sync_all_recorded_dates(username: str) -> None:
    _user_ref(username).set(
        {"googleCalendar": {"syncStatus": "syncing", "syncError": None}},
        merge=True,
    )
    try:
        service = _calendar_service(username)
        if service is None:
            raise RuntimeError("Calendar is not connected")
        dates = sorted({_date_string(value) for value in fetch_cycle_data(username)})
        next_predicted_date, days_since_last = _prediction_context(dates)
        for date_value in dates:
            _upsert_date_event(
                service,
                username,
                date_value,
                next_predicted_date,
                days_since_last,
            )
        _user_ref(username).set(
            {
                "googleCalendar": {
                    "syncStatus": "ready",
                    "lastSyncAt": datetime.utcnow().isoformat() + "Z",
                    "syncError": None,
                }
            },
            merge=True,
        )
    except Exception:
        logging.exception("Calendar history sync failed for app user %s", username)
        set_sync_error(username)


def desync_calendar_entries(username: str) -> None:
    _user_ref(username).set(
        {"googleCalendar": {"syncStatus": "syncing", "syncError": None}},
        merge=True,
    )
    try:
        service = _calendar_service(username)
        if service is None:
            raise RuntimeError("Calendar is not connected")

        event_ids = {
            _event_id(username, _date_string(value))
            for value in fetch_cycle_data(username)
        }
        page_token = None
        while True:
            params = {
                "calendarId": "primary",
                "privateExtendedProperty": [
                    "cycleSenseApp=cycle-sense",
                    f"cycleSenseUser={username}",
                ],
                "maxResults": 2500,
            }
            if page_token:
                params["pageToken"] = page_token
            response = service.events().list(**params).execute()
            event_ids.update(
                event["id"] for event in response.get("items", []) if event.get("id")
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        deleted = 0
        for event_id in event_ids:
            try:
                service.events().delete(
                    calendarId="primary", eventId=event_id
                ).execute()
                deleted += 1
            except HttpError as exc:
                if exc.resp.status not in (404, 410):
                    raise

        _user_ref(username).set(
            {
                "googleCalendar": {
                    "syncStatus": "ready",
                    "lastSyncAt": datetime.utcnow().isoformat() + "Z",
                    "syncError": None,
                }
            },
            merge=True,
        )
        logging.info(
            "Removed %s Cycle Sense calendar events for app user %s",
            deleted,
            username,
        )
    except Exception:
        logging.exception("Calendar de-sync failed for app user %s", username)
        set_sync_error(username)


def disconnect_calendar(username: str) -> None:
    calendar_data = _calendar_data(username)
    encrypted_token = calendar_data.get("encryptedRefreshToken")
    if encrypted_token:
        refresh_token = _token_cipher().decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        try:
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": refresh_token},
                timeout=10,
            )
        except requests.RequestException:
            logging.warning("Could not revoke the Google Calendar grant")
    _user_ref(username).update({"googleCalendar": firestore.DELETE_FIELD})
