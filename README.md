# Cycle Predictor: Menstrual Cycle Prediction App

A modern, feedback-aware menstrual cycle prediction app built with FastAPI, Firestore, and a rolling-average ML model. Features a robust backend, a user-friendly UI, and best practices for Python project structure and version control.

---

## Features
- **Accurate Predictions:** Predicts next N cycles and for a selected month using a rolling average of recent intervals.
- **Feedback Integration:** Users can submit feedback on predictions, which is used to correct and retrain the model.
- **Manual & Scheduled Training:** Retrain the model on demand or via a scheduler (feature-flag controlled).
- **Modern UI:** Three views—Prediction, Feedback, and Train Model—built for clarity and ease of use.
- **Firestore Integration:** All data and feedback are securely stored in Firestore.
- **Comprehensive Logging:** All backend modules log key actions for traceability.
- **Best Practices:** Clean Python project structure, .gitignore for secrets and artifacts, and clear separation of concerns.

---

## Project Structure
```
cycle-predictor/
├── app/
│   ├── main.py                # FastAPI app, endpoints, CORS, feature flag
│   ├── model.py               # Rolling average prediction logic
│   ├── firebase_service.py    # Firestore integration, feedback, correction
│   ├── scheduler.py           # Scheduled retraining logic
│   ├── training_utils.py      # Shared feedback-aware training logic
│   ├── cycle_predictor_ui.html# Modern UI (Prediction, Feedback, Train)
├── data/                      # Data and models (ignored)
├── requirements.txt           # Python dependencies
├── .gitignore                 # Ignore rules for secrets, data, venv, etc.
└── README.md                  # Project documentation
```

---

## Getting Started

### 1. Clone the Repository
```sh
git clone <repo-url>
cd cycle-predictor
```

### 2. Set Up Python Environment
```sh
python -m venv venv
.\venv\Scripts\activate  # On Windows
pip install -r requirements.txt
```

### 3. Configure Firestore
- Place your `firebase_key.json` in `app/` (never commit this file).
- Set up your Firestore project as described in Google Cloud docs.

### 4. Run the App
```sh
cd app
uvicorn main:app --reload
```
- Access the UI at: [http://localhost:8000/cycle_predictor_ui.html](http://localhost:8000/cycle_predictor_ui.html)

### 5. (Optional) Reset Menstrual Data
- Use `app/reset_menstrual_data.py` to initialize or reset your data (one-time script).

---

## Usage
- **Prediction:** Enter your last period date and predict upcoming cycles or for a specific month.
- **Feedback:** Submit actual period dates and comments to improve predictions.
- **Train Model:** Manually retrain the model with all available data and feedback.

---

## Development & Best Practices
- All secrets, data, and environment files are ignored via `.gitignore`.
- Logging is enabled throughout the backend for debugging and traceability.
- Feature flags (e.g., scheduler) are managed via `config.properties`.
- UI and backend are decoupled for easy updates and maintenance.

---

## License
MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgments
- Built with FastAPI, Firestore, and modern Python best practices.
- UI inspired by modern web app design principles.
