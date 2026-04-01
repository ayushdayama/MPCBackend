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
