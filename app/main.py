import threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.firebase_service import fetch_cycle_data, store_feedback
from app.user_service import create_user, validate_login, get_user_collection
from app.scheduler import start_scheduler
from app.training_utils import train_with_feedback
from contextlib import asynccontextmanager
from pydantic import BaseModel
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
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware

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


class UserCredentials(BaseModel):
    username: str
    password: str


@app.post("/register")
def register(credentials: UserCredentials):
    logging.info(
        f"Received registration request for username: {credentials.username}")
    result = create_user(credentials.username, credentials.password)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "User registered successfully"}


# Utility function to trigger training in the background


def trigger_training_bg(username):
    def train_bg():
        try:
            train_with_feedback(username)
        except Exception as e:
            logging.error(f"Background training failed for {username}: {e}")
    threading.Thread(target=train_bg, daemon=True).start()


@app.post("/login")
def login(credentials: UserCredentials):
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
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    # Inject username into feedback data for correction logic
    data["username"] = username

    feedback_collection = f"prediction_feedback_{username}"
    store_feedback(data, feedback_collection, collection_name)

    # Trigger training in the background after feedback
    trigger_training_bg(username)

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
