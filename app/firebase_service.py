import firebase_admin
from firebase_admin import credentials, firestore
import logging
from datetime import datetime

cred = credentials.Certificate("app/firebase_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def fetch_cycle_data():
    logging.info("Fetching cycle data from Firestore...")
    docs = db.collection("menstrual_data").stream()
    dates = sorted(doc.to_dict()["date"] for doc in docs)
    logging.info(f"Fetched {len(dates)} cycle dates.")
    return dates

def fetch_feedback():
    logging.info("Fetching prediction feedback from Firestore...")
    docs = db.collection("prediction_feedback").stream()
    feedback = [doc.to_dict() for doc in docs]
    logging.info(f"Fetched {len(feedback)} feedback entries.")
    return feedback

def store_feedback(feedback_data):
    logging.info(f"Storing feedback: {feedback_data}")
    db.collection("prediction_feedback").add(feedback_data)
    logging.info("Feedback stored in Firestore.")
    # --- Correction logic ---
    actual_date = feedback_data.get("actual_date")
    if not actual_date:
        return
    # Convert actual_date to datetime
    actual_dt = datetime.strptime(actual_date, "%Y-%m-%d")
    # Search for existing dates within +/- 7 days
    docs = db.collection("menstrual_data").stream()
    found = False
    for doc in docs:
        doc_data = doc.to_dict()
        doc_date_str = doc_data.get("date")
        if not doc_date_str:
            continue
        try:
            doc_dt = datetime.strptime(doc_date_str, "%Y-%m-%d")
        except Exception:
            continue
        if abs((doc_dt - actual_dt).days) <= 7:
            # Update this document with the new date
            db.collection("menstrual_data").document(doc.id).update({"date": actual_date})
            logging.info(f"Updated menstrual_data doc {doc.id} to date {actual_date}")
            found = True
            break
    if not found:
        # Add as new entry
        db.collection("menstrual_data").add({"date": actual_date})
        logging.info(f"Added new menstrual_data entry with date {actual_date}")
