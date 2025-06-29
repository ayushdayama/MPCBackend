import logging
from app.firebase_service import fetch_cycle_data, fetch_feedback
from app.model import train_model

def train_with_feedback():
    logging.info("Training model with feedback-aware logic.")
    dates = fetch_cycle_data()
    feedback = fetch_feedback()
    all_dates = dates[:]
    for fb in feedback:
        if "actual_date" in fb and fb["actual_date"] not in all_dates:
            all_dates.append(fb["actual_date"])
    all_dates = sorted(all_dates)
    model = train_model(all_dates)
    if model is not None:
        logging.info("Model trained successfully with feedback.")
        return True
    else:
        logging.warning("Not enough data to train the model.")
        return False
