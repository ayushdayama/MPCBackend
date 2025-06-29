from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.firebase_service import fetch_cycle_data, fetch_feedback, store_feedback
from app.model import predict_next_date
from app.scheduler import start_scheduler
from app.training_utils import train_with_feedback
from contextlib import asynccontextmanager
import logging
import configparser
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Load feature flag from local properties file
config = configparser.ConfigParser()
config_file = os.path.join(os.path.dirname(__file__), "config.properties")
scheduler_enabled = True  # Default to True

if os.path.exists(config_file):
    config.read(config_file)
    scheduler_flag = config.get("features", "enable_scheduler", fallback="Y")
    scheduler_enabled = scheduler_flag.upper() == "Y"
    logging.info(f"Scheduler feature flag loaded: enable_scheduler={scheduler_flag}")
else:
    logging.warning("config.properties not found. Scheduler will run by default.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("FastAPI app startup event triggered.")
    if scheduler_enabled:
        logging.info("Scheduler is enabled. Starting scheduler...")
        start_scheduler()
    else:
        logging.info("Scheduler is disabled by feature flag.")
    yield

app = FastAPI(lifespan=lifespan)

# Allow CORS for all origins (for local testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/predict")
def predict():
    logging.info("Received /predict request.")
    dates = fetch_cycle_data()
    from app.model import predict_next_dates
    top_dates = predict_next_dates(dates, top_n=3)
    result = {
        "top_dates": [str(d) for d in top_dates],
        "next_date": str(top_dates[0]) if top_dates else None
    }
    logging.info(f"Prediction result: {result}")
    return result

@app.post("/feedback")
def feedback(data: dict):
    logging.info(f"Received feedback: {data}")
    # Store structured feedback in Firestore
    store_feedback(data)
    return {"message": "Feedback received and stored."}

@app.post("/train")
def train():
    logging.info("Received /train request. Training model manually.")
    success = train_with_feedback()
    if success:
        return {"message": "Model trained successfully with feedback."}
    else:
        return {"message": "Not enough data to train the model."}

@app.get("/cycle_data")
def cycle_data():
    dates = fetch_cycle_data()
    return {"dates": dates}
