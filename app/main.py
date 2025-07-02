from fastapi import Body
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
import threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.firebase_service import fetch_cycle_data, store_feedback
from app.user_service import create_user, validate_login, get_user_collection
from app.scheduler import start_scheduler
from app.training_utils import train_with_feedback
from contextlib import asynccontextmanager
from pydantic import BaseModel
from zoneinfo import ZoneInfo
import logging
import os
# Ensure .env is loaded for environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logging.warning(
        "python-dotenv not installed; .env file will not be loaded automatically.")
import os
import json

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

# Load feature flag from JSON env variable or fallback to True
scheduler_enabled = True  # Default to True
scheduler_flag_json = os.environ.get("SCHEDULER_FLAGS_JSON")
if scheduler_flag_json:
    try:
        flags = json.loads(scheduler_flag_json)
        scheduler_enabled = flags.get("enable_scheduler", True)
        logging.info(
            f"Scheduler feature flag loaded from env: enable_scheduler={scheduler_enabled}")
    except Exception as e:
        logging.warning(
            f"Failed to parse SCHEDULER_FLAGS_JSON: {e}. Scheduler will run by default.")
else:
    logging.warning(
        "SCHEDULER_FLAGS_JSON not found. Scheduler will run by default.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("FastAPI app startup event triggered.")
    if scheduler_enabled:
        logging.info("Scheduler is enabled. Starting scheduler...")
        start_scheduler()
    else:
        logging.info("Scheduler is disabled by feature flag.")
    yield


# Fix for CORS preflight and error responses

app = FastAPI(lifespan=lifespan)

# Allow CORS for all origins (for local testing)
app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for request validation


# Separate models for login and registration
class LoginCredentials(BaseModel):
    username: str
    password: str


class RegisterCredentials(BaseModel):
    username: str
    password: str
    securityQuestion: str
    securityAnswer: str


class SecurityAnswerRequest(BaseModel):
    username: str
    securityAnswer: str
    newPassword: str

# AddCycleDateRequest model
class AddCycleDateRequest(BaseModel):
    date: str

# Add a new cycle date for a user
@app.post("/add-cycle-date/{username}")
def add_cycle_date(username: str, req: AddCycleDateRequest = Body(...)):
    from firebase_admin import firestore
    db = firestore.client()
    date = req.date
    if not date:
        raise HTTPException(status_code=400, detail="Date is required")
    doc_ref = db.collection("menstrual_data").document(username)
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict() or {}
        numeric_keys = [int(k) for k in data.keys() if k.isdigit()]
        next_key = str(max(numeric_keys) + 1) if numeric_keys else "1"
        doc_ref.update({next_key: date})
    else:
        doc_ref.set({"1": date})
    return {"message": "Cycle date added successfully."}

@app.post("/register")
def register(credentials: RegisterCredentials):
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
        'security_answer': credentials.securityAnswer,
    })
    try:
        result = create_user(
            credentials.username,
            credentials.password,
        )
    finally:
        setattr(main_mod, '_SECURITY_CONTEXT', None)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "User registered successfully"}


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
    from app.user_service import verify_security_answer_and_reset
    result = verify_security_answer_and_reset(
        req.username, req.securityAnswer, req.newPassword)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": result["message"]}


# Utility function to trigger training in the background


def trigger_training_bg(username):
    def train_bg():
        try:
            train_with_feedback(username)
        except Exception as e:
            logging.error(f"Background training failed for {username}: {e}")
    threading.Thread(target=train_bg, daemon=True).start()


@app.post("/login")
def login(credentials: LoginCredentials):
    logging.info(
        f"Received login request for username: {credentials.username}")
    result = validate_login(credentials.username, credentials.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
    # Trigger training in the background after successful login
    trigger_training_bg(credentials.username)
    return {
        "message": "Login successful",
        "username": credentials.username
    }


@app.get("/predict/{username}")
def predict(username: str):
    logging.info(f"Received /predict request for user: {username}")
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    dates = fetch_cycle_data(collection_name)
    from app.model import predict_next_dates
    top_dates = predict_next_dates(dates, top_n=3)
    result = {
        "top_dates": [str(d) for d in top_dates],
        "next_date": str(top_dates[0]) if top_dates else None
    }
    logging.info(f"Prediction result for user {username}: {result}")
    return result


@app.post("/feedback/{username}")
def feedback(username: str, data: dict):
    logging.info(f"Received feedback from user {username}: {data}")
    # Compose a detailed log document name and structure
    from datetime import datetime
    from firebase_admin import firestore
    db = firestore.client()
    now = datetime.now(tz=ZoneInfo('Asia/Kolkata')).strftime('%Y-%b-%d-%H-%M-%S')
    doc_name = f"{username}_{now}"
    log_data = {
        "username": username,
        "timestamp": now,
        "predicted_date": data.get("predicted_date"),
        "actual_date": data.get("actual_date"),
        "comment": data.get("comment", "")
    }
    db.collection("prediction_feedback").document(doc_name).set(log_data)

    # Also add actual_date to menstrual_data collection as next numeric key
    actual_date = data.get("actual_date")
    if actual_date:
        menstrual_doc_ref = db.collection("menstrual_data").document(username)
        menstrual_doc = menstrual_doc_ref.get()
        if menstrual_doc.exists:
            menstrual_data = menstrual_doc.to_dict() or {}
            # Find next numeric key
            numeric_keys = [int(k)
                            for k in menstrual_data.keys() if k.isdigit()]
            next_key = str(max(numeric_keys) + 1) if numeric_keys else "1"
        else:
            next_key = "1"
        menstrual_doc_ref.update({next_key: actual_date}) if menstrual_doc.exists else menstrual_doc_ref.set(
            {next_key: actual_date})

    return {"message": "Feedback received and stored."}


@app.post("/train/{username}")
def train(username: str):
    logging.info(
        f"Received /train request for user: {username}. Training model manually.")
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    success = train_with_feedback(collection_name)
    if success:
        return {"message": "Model trained successfully with feedback."}
    else:
        return {"message": "Not enough data to train the model."}


@app.get("/cycle_data/{username}")
def cycle_data(username: str):
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    dates = fetch_cycle_data(collection_name)
    return {"dates": dates}
