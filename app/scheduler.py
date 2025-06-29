from apscheduler.schedulers.background import BackgroundScheduler
from app.training_utils import train_with_feedback
import logging

def start_scheduler():
    logging.info("Starting background scheduler...")
    scheduler = BackgroundScheduler()

    def retrain_model():
        logging.info("Retraining model job started (with feedback-aware logic).")
        train_with_feedback()
        logging.info("Model retraining completed.")

    scheduler.add_job(retrain_model, 'interval', weeks=2)
    scheduler.start()
    logging.info("Scheduler started and retrain job scheduled every 2 weeks.")
