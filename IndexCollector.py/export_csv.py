"""Store close-minus-open values in one row-wise CSV file."""

import csv
from datetime import datetime, time, timedelta
from pathlib import Path

NIFTY_ROW_CSV = Path("data/NIFTY50_ROW.csv")
DATE_HEADER = "DATE"


def _market_time_headers():
    """Return 5-minute NSE market time slots, including 09:15 and 15:30."""
    current = datetime.combine(datetime.today(), time(9, 15))
    end = datetime.combine(datetime.today(), time(15, 30))
    headers = []
    while current <= end:
        headers.append(current.strftime("%H:%M"))
        current += timedelta(minutes=5)
    return headers


TIME_HEADERS = _market_time_headers()
HEADERS = [DATE_HEADER, *TIME_HEADERS]


def _format_difference(value):
    """Keep CSV values readable despite binary floating-point precision."""
    rounded = round(float(value), 2)
    if rounded == 0:
        rounded = 0
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def init_csv():
    """Create the one row-wise CSV without duplicating OHLC data from SQLite."""
    NIFTY_ROW_CSV.parent.mkdir(parents=True, exist_ok=True)

    if not NIFTY_ROW_CSV.exists():
        with NIFTY_ROW_CSV.open("w", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=HEADERS).writeheader()
        print(f"Row-wise CSV created -> {NIFTY_ROW_CSV}")
    else:
        print(f"Row-wise CSV ready -> {NIFTY_ROW_CSV}")


def _read_rows():
    with NIFTY_ROW_CSV.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != HEADERS:
            raise ValueError(
                f"{NIFTY_ROW_CSV} has unexpected headers. "
                "It must contain DATE followed by the 5-minute market times."
            )
        return list(reader)


def _write_rows(rows):
    with NIFTY_ROW_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def save_to_csv(candle):
    """Store a candle's signed value at the exact date/time CSV cell.

    The CSV has one row per trading date and one column per 5-minute time
    slot. Full OHLC data is intentionally stored only in SQLite.
    """
    candle_time = candle["time"]
    if candle_time not in TIME_HEADERS:
        print(
            f"Skipping row-wise CSV export: {candle['date']} {candle_time} "
            "is not a 5-minute market time slot"
        )
        return

    rows = _read_rows()
    row = next((item for item in rows if item[DATE_HEADER] == candle["date"]), None)
    if row is None:
        row = {header: "" for header in HEADERS}
        row[DATE_HEADER] = candle["date"]
        rows.append(row)

    # ``co_diff`` is close - open: up candles are positive and down candles
    # are negative, as requested.
    row[candle_time] = _format_difference(candle["co_diff"])
    rows.sort(key=lambda item: item[DATE_HEADER])
    _write_rows(rows)
    print(f"Saved row-wise CSV -> {candle['date']} {candle_time}")


def sync_csv_from_database(candles):
    """Stream an ordered SQLite candle iterator into the row-wise CSV.

    Only one trading-day row is kept in memory.  This matters when historical
    imports grow from one year to many years of five-minute data.
    """
    skipped = 0
    date_rows = 0
    current_date = None
    current_row = None

    with NIFTY_ROW_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        for candle in candles:
            candle_time = candle["time"]
            if candle_time not in TIME_HEADERS:
                skipped += 1
                continue

            candle_date = candle["date"]
            if candle_date != current_date:
                if current_row is not None:
                    writer.writerow(current_row)
                current_date = candle_date
                current_row = {header: "" for header in HEADERS}
                current_row[DATE_HEADER] = candle_date
                date_rows += 1
            current_row[candle_time] = _format_difference(candle["co_diff"])

        if current_row is not None:
            writer.writerow(current_row)

    print(f"Row-wise CSV synced from SQLite -> {date_rows} date row(s)")
    if skipped:
        print(f"Skipped {skipped} database candle(s) outside 5-minute market time slots")
