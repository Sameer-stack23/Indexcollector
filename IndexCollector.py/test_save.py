"""Manual smoke test for the collector's storage functions."""

from database import init_db, save_candle
from export_csv import NIFTY_ROW_CSV, init_csv, save_to_csv

init_db()
init_csv()

candle = {
    "date": "2026-07-03",
    "time": "10:00",
    "open": 10000.0,
    "high": 10050.0,
    "low": 9950.0,
    "close": 10020.0,
    "volume": 12345,
    "hl_diff": 100.0,
    "co_diff": 20.0,
}

if save_candle(candle):
    save_to_csv(candle)

print(f"Row-wise CSV location: {NIFTY_ROW_CSV}")
