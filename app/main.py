from fastapi import Body
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.firebase_service import fetch_cycle_data
from app.user_service import create_user, validate_login, get_user_collection
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
    result = {
        "top_dates": [str(d) for d in top_dates],
        "next_date": str(top_dates[0]) if top_dates else None
    }
    logging.info(f"Prediction result for user {username}: {result}")
    return result


@app.get("/cycle_data/{username}")
def cycle_data(username: str):
    collection_name = get_user_collection(username)
    if not collection_name:
        raise HTTPException(status_code=404, detail="User not found")

    dates = fetch_cycle_data(collection_name)
    return {"dates": dates}
