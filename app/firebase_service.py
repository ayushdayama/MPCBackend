import os
import firebase_admin
from firebase_admin import credentials, firestore
import logging
from datetime import datetime

# Load Firestore credentials from env or fallback to file
firebase_key_json = os.environ.get("FIREBASE_KEY_JSON")
if firebase_key_json and os.path.isfile(firebase_key_json):
    cred = credentials.Certificate(firebase_key_json)
else:
    # Try to load from env as JSON string (for Railway)
    import json
    try:
        firebase_key_json_str = os.environ.get("FIREBASE_KEY_JSON")
        if firebase_key_json_str:
            cred = credentials.Certificate(json.loads(firebase_key_json_str))
        else:
            cred = credentials.Certificate("app/firebase_key.json")
    except Exception:
        cred = credentials.Certificate("app/firebase_key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()


def fetch_cycle_data(collection_name: str = "menstrual_data"):
    logging.info(f"Fetching cycle data for user: {collection_name} (document ID) in 'menstrual_data' collection...")
    doc = db.collection("menstrual_data").document(collection_name).get()
    if not doc.exists:
        logging.info(f"No cycle data found for user {collection_name}.")
        return []
    data = doc.to_dict() or {}
    # Only keep fields that are serial numbers (1, 2, 3, ...)
    serial_dates = [(int(k), v) for k, v in data.items() if k.isdigit()]
    serial_dates.sort()
    dates = [v for _, v in serial_dates]
    logging.info(f"Fetched {len(dates)} cycle dates for user {collection_name}.")
    return dates


def fetch_feedback(collection_name: str = "prediction_feedback"):
    logging.info(
        f"Fetching prediction feedback from Firestore collection: {collection_name}...")
    docs = db.collection(collection_name).stream()
    feedback = [doc.to_dict() for doc in docs]
    logging.info(f"Fetched {len(feedback)} feedback entries.")
    return feedback


def store_feedback(feedback_data, collection_name: str = "prediction_feedback", data_collection: str = "menstrual_data"):
    logging.info(f"Storing feedback in collection {collection_name}: {feedback_data}")
    db.collection(collection_name).add(feedback_data)
    logging.info("Feedback stored in Firestore.")
    # --- Correction logic ---
    actual_date = feedback_data.get("actual_date")
    username = feedback_data.get("username")
    if not actual_date or not username:
        return
    # Fetch user's menstrual_data document
    user_doc_ref = db.collection("menstrual_data").document(username)
    user_doc = user_doc_ref.get()
    if user_doc.exists:
        data = user_doc.to_dict() or {}
        # Check if date already exists (within 7 days)
        found = False
        for k, v in data.items():
            try:
                existing_dt = datetime.strptime(v, "%Y-%m-%d")
                actual_dt = datetime.strptime(actual_date, "%Y-%m-%d")
                if abs((existing_dt - actual_dt).days) <= 7:
                    user_doc_ref.update({k: actual_date})
                    logging.info(f"Updated {username}'s cycle date at key {k} to {actual_date}")
                    found = True
                    break
            except Exception:
                continue
        if not found:
            # Add as new serial number
            next_key = str(len([k for k in data.keys() if k.isdigit()]) + 1)
            user_doc_ref.update({next_key: actual_date})
            logging.info(f"Added new cycle date for {username} at key {next_key}: {actual_date}")
    else:
        # Create new document for user
        user_doc_ref.set({"1": actual_date})
        logging.info(f"Created new menstrual_data document for {username} with date {actual_date}")
