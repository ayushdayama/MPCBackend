import logging
from app.firebase_service import fetch_cycle_data, fetch_feedback
from app.model import train_model


def train_with_feedback(collection_name: str = "menstrual_data"):
    logging.info(
        f"Training model with feedback-aware logic for collection: {collection_name}")
    dates = fetch_cycle_data(collection_name)
    feedback_collection = collection_name.replace(
        "menstrual_data", "prediction_feedback")
    feedback = fetch_feedback(feedback_collection)
    all_dates = dates[:]
    for fb in feedback:
        if "actual_date" in fb and fb["actual_date"] not in all_dates:
            all_dates.append(fb["actual_date"])
    all_dates = sorted(all_dates)
    model = train_model(all_dates)
    if model is not None:
        logging.info(
            f"Model trained successfully with feedback for {collection_name}")
        return True
    else:
        logging.warning(
            f"Not enough data to train the model for {collection_name}")
        return False
