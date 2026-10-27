from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Response
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.firebase_service import fetch_cycle_data
from app.user_service import create_user, validate_login, get_user_collection
from pydantic import BaseModel, Field
import logging
import os
from datetime import datetime
# Ensure .env is loaded for environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logging.warning(
        "python-dotenv not installed; .env file will not be loaded automatically.")

from app.security import APP_SECRET_KEY_CONFIGURED, create_calendar_state, create_session_token, decode_token, get_current_username
from app.password_encryption import (
    consume_password_encryption_challenge,
    decrypt_credential_payload,
    get_password_encryption_public_key,
    issue_password_encryption_challenge,
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


# Fix for CORS preflight and error responses

app = FastAPI()

# Allow CORS for all origins (for local testing)
app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for request validation


class EncryptedCredentialPayload(BaseModel):
    challenge: str = Field(min_length=32, max_length=128)
    ephemeral_public_key: dict[str, object]
    salt: str = Field(min_length=20, max_length=32)
    iv: str = Field(min_length=16, max_length=24)
    ciphertext: str = Field(min_length=22, max_length=24576)


# Separate models for login and registration
class LoginCredentials(BaseModel):
    username: str
    encrypted: EncryptedCredentialPayload


class RegisterCredentials(BaseModel):
    username: str
    securityQuestion: str
    encrypted: EncryptedCredentialPayload


class SecurityAnswerRequest(BaseModel):
    username: str
    encrypted: EncryptedCredentialPayload


def _decrypt_submitted_credentials(encrypted: EncryptedCredentialPayload) -> dict:
    try:
        plaintext = decrypt_credential_payload(encrypted.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid encrypted credentials") from exc

    if not consume_password_encryption_challenge(encrypted.challenge):
        raise HTTPException(status_code=400, detail="Expired or used credential challenge")
    return plaintext

# AddCycleDateRequest model
class AddCycleDateRequest(BaseModel):
    date: str

# Add a new cycle date for a user
def _to_storage_date(date_str: str) -> str:
    if not date_str:
        raise ValueError("Date is required")
    try:
        dt = datetime.fromisoformat(date_str)
    except Exception:
        try:
            dt = datetime.strptime(date_str, "%d-%b-%Y")
        except Exception:
            raise ValueError("Invalid date format. Use YYYY-MM-DD or DD-MMM-YYYY")
    return dt.strftime("%Y-%m-%d")


def _to_display_date(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str)
    except Exception:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%b-%Y")


@app.post("/add-cycle-date/{username}")
def add_cycle_date(
    username: str,
    req: AddCycleDateRequest = Body(...),
    current_username: str = Depends(get_current_username),
):
    if username != current_username:
        raise HTTPException(status_code=403, detail="You can only update your own cycle dates")
    from firebase_admin import firestore
    db = firestore.client()
    try:
        date = _to_storage_date(req.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    doc_ref = db.collection("menstrual_data").document(username)
    doc = doc_ref.get()
    data = doc.to_dict() or {} if doc.exists else {}
    recorded_dates = set()
    for key, value in data.items():
        if key.isdigit():
            try:
                recorded_dates.add(_to_storage_date(str(value)))
            except ValueError:
                continue

    already_recorded = date in recorded_dates
    if doc.exists:
        if not already_recorded:
            numeric_keys = [int(k) for k in data.keys() if k.isdigit()]
            next_key = str(max(numeric_keys) + 1) if numeric_keys else "1"
            doc_ref.update({next_key: date})
    else:
        doc_ref.set({"1": date})

    from app.calendar_service import mark_sync_needed
    calendar_sync = "manual_required" if mark_sync_needed(username) else "not_connected"

    return {
        "message": "Cycle date already recorded." if already_recorded else "Cycle date added successfully.",
        "calendar_sync": calendar_sync,
    }

@app.get("/auth/encryption-challenge")
def password_encryption_challenge(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return issue_password_encryption_challenge()


@app.post("/register")
def register(credentials: RegisterCredentials):
    secret_fields = _decrypt_submitted_credentials(credentials.encrypted)
    password = secret_fields.get("password")
    security_answer = secret_fields.get("securityAnswer")
    if not isinstance(password, str) or not isinstance(security_answer, str):
        raise HTTPException(status_code=400, detail="Invalid encrypted credentials")

    logging.info(
        f"Received registration request for username: {credentials.username}")
    # Pass security question and answer to user_service
    # Pass as extra args using a wrapper function
    # Instead of using frame hacks, pass security question/answer as global variables for this call
    # Use a global dict for passing security question/answer for this call
    import sys
    main_mod = sys.modules[__name__]
    setattr(main_mod, '_SECURITY_CONTEXT', {
        'security_question': credentials.securityQuestion,
        'security_answer': security_answer,
    })
    try:
        result = create_user(
            credentials.username,
            password,
        )
    finally:
        setattr(main_mod, '_SECURITY_CONTEXT', None)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {
        "message": "User registered successfully",
        "username": credentials.username,
        "access_token": create_session_token(credentials.username),
    }


# Password recovery endpoints
@app.get("/security-question/{username}")
def get_security_question_endpoint(username: str):
    from app.user_service import get_security_question
    result = get_security_question(username)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"securityQuestion": result["securityQuestion"]}


@app.post("/reset-password")
def reset_password(req: SecurityAnswerRequest):
    secret_fields = _decrypt_submitted_credentials(req.encrypted)
    security_answer = secret_fields.get("securityAnswer")
    new_password = secret_fields.get("newPassword")
    if not isinstance(security_answer, str) or not isinstance(new_password, str):
        raise HTTPException(status_code=400, detail="Invalid encrypted credentials")

    from app.user_service import verify_security_answer_and_reset
    result = verify_security_answer_and_reset(
        req.username, security_answer, new_password)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": result["message"]}


def _calendar_frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173/MPCFrontend/").rstrip("/")


@app.post("/calendar/connect")
def calendar_connect(current_username: str = Depends(get_current_username)):
    if not APP_SECRET_KEY_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="Set a persistent APP_SECRET_KEY before connecting Google Calendar",
        )
    from app.calendar_service import create_authorization_url

    state = create_calendar_state(current_username)
    try:
        return {"authorization_url": create_authorization_url(state)}
    except Exception as exc:
        logging.exception("Could not start Google Calendar authorization")
        raise HTTPException(
            status_code=503,
            detail="Google Calendar OAuth is not configured on the backend",
        ) from exc


@app.get("/auth/google/callback")
def google_calendar_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    redirect_url = f"{_calendar_frontend_url()}/?calendar_sync="
    if error or not code or not state:
        return RedirectResponse(f"{redirect_url}cancelled")

    from app.calendar_service import GoogleCalendarPermissionDenied, complete_authorization

    try:
        username = decode_token(state, "calendar_oauth")
        complete_authorization(username, code, state)
    except GoogleCalendarPermissionDenied:
        logging.warning("Google account did not grant the Calendar Events permission")
        return RedirectResponse(f"{redirect_url}permission")
    except Exception:
        logging.exception("Google Calendar authorization failed")
        return RedirectResponse(f"{redirect_url}error")
    return RedirectResponse(f"{redirect_url}connected")


def _sync_calendar_history(username: str) -> None:
    from app.calendar_service import sync_all_recorded_dates

    sync_all_recorded_dates(username)


def _desync_calendar_entries(username: str) -> None:
    from app.calendar_service import desync_calendar_entries

    desync_calendar_entries(username)


@app.get("/calendar/status")
def calendar_status(current_username: str = Depends(get_current_username)):
    from app.calendar_service import get_calendar_status

    return get_calendar_status(current_username)


@app.post("/calendar/sync")
def calendar_sync(
    background_tasks: BackgroundTasks,
    current_username: str = Depends(get_current_username),
):
    from app.calendar_service import get_calendar_status, mark_sync_queued

    if not get_calendar_status(current_username)["connected"]:
        raise HTTPException(status_code=409, detail="Connect a Google Calendar first")
    mark_sync_queued(current_username)
    background_tasks.add_task(_sync_calendar_history, current_username)
    return {"status": "queued"}


@app.post("/calendar/desync")
def calendar_desync(
    background_tasks: BackgroundTasks,
    current_username: str = Depends(get_current_username),
):
    from app.calendar_service import get_calendar_status, mark_sync_queued

    if not get_calendar_status(current_username)["connected"]:
        raise HTTPException(status_code=409, detail="Connect a Google Calendar first")
    mark_sync_queued(current_username)
    background_tasks.add_task(_desync_calendar_entries, current_username)
    return {"status": "queued"}


@app.post("/calendar/disconnect")
def calendar_disconnect(current_username: str = Depends(get_current_username)):
    from app.calendar_service import disconnect_calendar

    disconnect_calendar(current_username)
    return {"message": "Google Calendar disconnected"}


# Utility function to trigger training in the background


def _warm_prediction_model():
    from app.model import load_model
    load_model()


@app.post("/login")
def login(credentials: LoginCredentials, background_tasks: BackgroundTasks):
    secret_fields = _decrypt_submitted_credentials(credentials.encrypted)
    password = secret_fields.get("password")
    if not isinstance(password, str):
        raise HTTPException(status_code=400, detail="Invalid encrypted credentials")

    logging.info(
        f"Received login request for username: {credentials.username}")
    result = validate_login(credentials.username, password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
    username = result.get("username", credentials.username)
    background_tasks.add_task(_warm_prediction_model)
    return {
        "message": "Login successful",
        "username": username,
        "access_token": create_session_token(username),
    }


@app.get("/auth/me")
def get_authenticated_user(current_username: str = Depends(get_current_username)):
    return {"username": current_username}


@app.get("/predict/{username}")
def predict(username: str, current_username: str = Depends(get_current_username)):
    if username != current_username:
        raise HTTPException(status_code=403, detail="You can only access your own cycle dates")
    logging.info(f"Received /predict request for user: {username}")
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    dates = fetch_cycle_data(collection_name)
    from app.model import predict_next_dates
    top_dates = predict_next_dates(dates, top_n=3)

    formatted_dates = []
    for d in top_dates:
        if isinstance(d, datetime):
            formatted_dates.append(d.strftime("%d-%b-%Y"))
            continue

        # support strings in several formats
        if isinstance(d, str):
            try:
                dt = datetime.fromisoformat(d)
            except Exception:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                except Exception:
                    dt = datetime.strptime(d, "%d-%b-%Y")
            formatted_dates.append(dt.strftime("%d-%b-%Y"))
            continue

        formatted_dates.append(str(d))

    # Format last date
    last_date = None
    if dates:
        try:
            last_d = dates[-1]
            if isinstance(last_d, datetime):
                last_date = last_d.strftime("%d-%b-%Y")
            elif isinstance(last_d, str):
                dt = datetime.fromisoformat(last_d) if "T" in last_d else datetime.strptime(last_d, "%Y-%m-%d")
                last_date = dt.strftime("%d-%b-%Y")
        except Exception:
            last_date = dates[-1]

    result = {
        "top_dates": formatted_dates,
        "next_date": formatted_dates[0] if formatted_dates else None,
        "last_date": last_date
    }
    logging.info(f"Prediction result for user {username}: {result}")
    return result


TRAIN_STATUS = {
    "state": "idle",
    "last_run": None,
    "last_mae": None,
    "last_error": None,
}


def _train_model_task():
    from app.firebase_service import fetch_all_menstrual_data
    from app.model import train_model

    TRAIN_STATUS.update({"state": "running", "last_error": None})
    try:
        data = fetch_all_menstrual_data()
        if not data:
            TRAIN_STATUS.update({"state": "failed", "last_error": "No menstrual data available for training"})
            return
        _, mae = train_model(data)
        TRAIN_STATUS.update({"state": "ready", "last_run": datetime.utcnow().isoformat(), "last_mae": float(mae), "last_error": None})
    except Exception as e:
        logging.error(f"Training failed: {e}")
        TRAIN_STATUS.update({"state": "failed", "last_error": str(e)})


@app.post("/train-model")
def train_model_endpoint(
    background_tasks: BackgroundTasks,
    current_username: str = Depends(get_current_username),
):
    if TRAIN_STATUS["state"] == "running":
        return {"message": "Training already in progress"}
    background_tasks.add_task(_train_model_task)
    TRAIN_STATUS["state"] = "queued"
    return {"message": "Training started in background", "status_endpoint": "/train-status"}


@app.get("/train-status")
def train_status(current_username: str = Depends(get_current_username)):
    return TRAIN_STATUS


@app.get("/cycle_data/{username}")
def cycle_data(username: str, current_username: str = Depends(get_current_username)):
    if username != current_username:
        raise HTTPException(status_code=403, detail="You can only access your own cycle dates")
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    dates = fetch_cycle_data(collection_name)
    formatted = []
    for d in dates:
        try:
            formatted.append(_to_display_date(d))
        except Exception:
            formatted.append(d)
    return {"dates": formatted}
