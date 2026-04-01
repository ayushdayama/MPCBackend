import os
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import joblib

MODEL_PATH = os.getenv('CYCLE_MODEL_PATH', os.path.join(os.path.dirname(__file__), 'cycle_interval_model.joblib'))
# On platforms like Render, prefer /tmp or a mounted persistent volume:
# export CYCLE_MODEL_PATH=/tmp/cycle_interval_model.joblib
MIN_CYCLES_TO_TRAIN = 5
MAX_LAGS = 6


def _parse_dates(dates):
    if not dates:
        return []
    parsed_dates = []
    for d in dates:
        if isinstance(d, datetime):
            parsed_dates.append(d)
            continue
        try:
            parsed_dates.append(datetime.fromisoformat(d))
        except Exception:
            try:
                parsed_dates.append(datetime.strptime(d, "%d-%b-%Y"))
            except Exception:
                raise ValueError(f"Invalid date format: {d}. Expected YYYY-MM-DD or DD-MMM-YYYY")
    return sorted(parsed_dates)


def _intervals(date_list):
    return np.array([(date_list[i] - date_list[i - 1]).days for i in range(1, len(date_list))])


def _build_features_from_intervals(intervals: np.ndarray, max_lags: int = MAX_LAGS) -> tuple[pd.DataFrame, np.ndarray]:
    rows: list[dict[str, float]] = []
    targets: list[int] = []
    for i in range(max_lags, len(intervals)):
        lags = intervals[i-max_lags:i][::-1]
        row: dict[str, float] = {f'lag_{j+1}': float(int(lags[j])) for j in range(max_lags)}
        row['avg_3'] = float(np.mean(lags[:3]))
        row['avg_6'] = float(np.mean(lags))
        row['std_3'] = float(np.std(lags[:3], ddof=0))
        row['std_6'] = float(np.std(lags, ddof=0))
        row['delta_1'] = float(lags[0] - lags[1])
        row['min_lag'] = float(np.min(lags))
        row['max_lag'] = float(np.max(lags))
        rows.append(row)
        targets.append(int(intervals[i]))
    return pd.DataFrame(rows), np.array(targets)


def train_model(user_cycle_data: dict[str, list[str]], model_path: str = MODEL_PATH) -> tuple[HistGradientBoostingRegressor, float]:
    X_all: list[pd.DataFrame] = []
    y_all: list[np.ndarray] = []
    for username, dates in user_cycle_data.items():
        parsed = _parse_dates(dates)
        if len(parsed) <= MIN_CYCLES_TO_TRAIN:
            continue
        intervals = _intervals(parsed)
        X, y = _build_features_from_intervals(intervals)
        if len(X) == 0:
            continue
        X_all.append(X)
        y_all.append(y)

    if len(X_all) == 0:
        raise ValueError('Not enough data to train model')

    X_all_df = pd.concat(X_all, ignore_index=True)
    y_all_arr = np.concatenate(y_all, axis=0)

    X_train, X_val, y_train, y_val = train_test_split(X_all_df, y_all_arr, test_size=0.2, random_state=42)

    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_depth=10, random_state=42)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_val)
    mae = mean_absolute_error(y_val, y_pred)
    logging.info(f'Trained cycle interval model; val MAE={mae:.2f} days')

    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(model, model_path)
    logging.info(f'Saved model at {model_path}')
    return model, mae


def load_model(model_path=MODEL_PATH):
    if os.path.exists(model_path):
        return joblib.load(model_path)
    logging.warning('Model file not found, predictions will use heuristic fallback')
    return None


def _get_feature_vector_from_dates(dates, max_lags=MAX_LAGS):
    parsed = _parse_dates(dates)
    if len(parsed) < max_lags + 1:
        return None
    intervals = _intervals(parsed)
    lags = intervals[-max_lags:][::-1]
    base: dict[str, float] = {f'lag_{j+1}': float(int(lags[j])) for j in range(max_lags)}
    return pd.DataFrame([{
        **base,
        'avg_3': float(np.mean(lags[:3])), 'avg_6': float(np.mean(lags)),
        'std_3': float(np.std(lags[:3], ddof=0)), 'std_6': float(np.std(lags, ddof=0)),
        'delta_1': float(lags[0] - lags[1]), 'min_lag': int(np.min(lags)), 'max_lag': int(np.max(lags)),
    }])


def predict_interval(dates, model=None, max_lags=MAX_LAGS):
    parsed = _parse_dates(dates)
    if len(parsed) < 2:
        return None

    intervals = _intervals(parsed)
    if len(parsed) < max_lags + 1 or model is None:
        fallback = int(round(np.mean(intervals[-3:] if len(intervals) >= 3 else intervals)))
        return max(21, min(45, fallback))

    features = _get_feature_vector_from_dates(dates, max_lags=max_lags)
    if features is None:
        fallback = int(round(np.mean(intervals[-3:] if len(intervals) >= 3 else intervals)))
        return max(21, min(45, fallback))

    pred = model.predict(features)[0]
    pred = float(pred)
    pred = max(21.0, min(45.0, pred))
    return int(round(pred))


def predict_next_dates(dates, top_n=3):
    parsed = _parse_dates(dates)
    if len(parsed) < 2:
        return []

    model = load_model()
    interval = predict_interval(dates, model=model)
    if interval is None:
        return []

    last_date = parsed[-1]
    primary = last_date + timedelta(days=interval)
    candidates = [primary]
    for diff in [-2, 2, -1, 1]:
        if len(candidates) >= top_n:
            break
        guess = last_date + timedelta(days=max(21, min(45, interval + diff)))
        if guess not in candidates:
            candidates.append(guess)

    return sorted(candidates)[:top_n]


def predict_next_date(dates):
    single = predict_next_dates(dates, top_n=1)
    return single[0] if single else None
