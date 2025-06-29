import pandas as pd
from datetime import timedelta
import numpy as np

def train_model(dates):
    # No longer needed for rolling average, but kept for compatibility
    return True

def predict_next_dates(dates, top_n=3):
    dates = pd.to_datetime(dates)
    if len(dates) < 2:
        return []
    intervals = (dates[1:] - dates[:-1]).days
    intervals = np.array(intervals)
    # Use last 3 intervals for rolling average, but also consider min/max for alternatives
    if len(intervals) >= 3:
        last_intervals = intervals[-3:]
    else:
        last_intervals = intervals
    avg_interval = int(round(last_intervals.mean()))
    min_interval = int(np.floor(last_intervals.min()))
    max_interval = int(np.ceil(last_intervals.max()))
    # Most confident: average, then min, then max
    next_dates = [dates[-1] + timedelta(days=avg_interval)]
    if min_interval != avg_interval:
        next_dates.append(dates[-1] + timedelta(days=min_interval))
    if max_interval != avg_interval and max_interval != min_interval:
        next_dates.append(dates[-1] + timedelta(days=max_interval))
    # Return up to top_n unique dates
    unique_dates = []
    for d in next_dates:
        if d.date() not in unique_dates:
            unique_dates.append(d.date())
        if len(unique_dates) == top_n:
            break
    return unique_dates

def predict_next_date(dates):
    # For compatibility, return the most confident date
    top_dates = predict_next_dates(dates, top_n=1)
    return top_dates[0] if top_dates else None
