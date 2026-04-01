from fastapi import Body
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.firebase_service import fetch_cycle_data
from app.user_service import create_user, validate_login, get_user_collection
from pydantic import BaseModel
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
def add_cycle_date(username: str, req: AddCycleDateRequest = Body(...)):
    from firebase_admin import firestore
    db = firestore.client()
    try:
        date = _to_storage_date(req.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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


@app.post("/login")
def login(credentials: LoginCredentials):
    logging.info(
        f"Received login request for username: {credentials.username}")
    result = validate_login(credentials.username, credentials.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
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

    result = {
        "top_dates": formatted_dates,
        "next_date": formatted_dates[0] if formatted_dates else None
    }
    logging.info(f"Prediction result for user {username}: {result}")
    return result


from fastapi import BackgroundTasks

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
def train_model_endpoint(background_tasks: BackgroundTasks):
    if TRAIN_STATUS["state"] == "running":
        return {"message": "Training already in progress"}
    background_tasks.add_task(_train_model_task)
    TRAIN_STATUS["state"] = "queued"
    return {"message": "Training started in background", "status_endpoint": "/train-status"}


@app.get("/train-status")
def train_status():
    return TRAIN_STATUS


@app.get("/cycle_data/{username}")
def cycle_data(username: str):
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
