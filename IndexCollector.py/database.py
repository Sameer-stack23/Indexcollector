# database.py
# Handles all SQLite database operations

import sqlite3
from datetime import datetime, timedelta
from config import DB_NAME

def init_db():
    """Create the candles table if it does not exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nifty_candles (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT,
            time      TEXT,
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            volume    INTEGER,
            hl_diff   REAL,
            co_diff   REAL
        )
    ''')

    # This speeds up the duplicate check without changing any existing rows.
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_nifty_candles_date_time
        ON nifty_candles (date, time)
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_nifty_candles_date_time
        ON nifty_candles (date, time)
    ''')

    conn.commit()
    conn.close()
    print("Database initialised successfully")


def save_candle(candle):
    """Insert one candle record, unless that exchange candle is already stored.

    Returns True only when a new row is written.  Angel may repeat a response
    and the dashboard can be started at any minute, so date/time is the stable
    identity of a 5-minute candle.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        'SELECT 1 FROM nifty_candles WHERE date = ? AND time = ? LIMIT 1',
        (candle['date'], candle['time']),
    )
    if cursor.fetchone() is not None:
        conn.close()
        print(f"Candle already stored -> {candle['date']} {candle['time']}")
        return False

    cursor.execute('''
        INSERT INTO nifty_candles
        (date, time, open, high, low, close, volume, hl_diff, co_diff)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        candle['date'],
        candle['time'],
        candle['open'],
        candle['high'],
        candle['low'],
        candle['close'],
        candle['volume'],
        candle['hl_diff'],
        candle['co_diff']
    ))

    conn.commit()
    conn.close()
    print(f"Saved to DB -> {candle['date']} {candle['time']}")
    return True


def fetch_latest_candle_time(candle_date):
    """Return the latest saved HH:MM time for one trading date, if any."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT time FROM nifty_candles
           WHERE date = ?
           ORDER BY time DESC
           LIMIT 1''',
        (candle_date,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def fetch_first_missing_candle_time(candle_date, before_time):
    """Return the first missing 5-minute candle time before ``before_time``.

    If all completed market slots are present, ``before_time`` is returned.
    This lets the caller both fill old holes and collect the next new candle.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT time FROM nifty_candles WHERE date = ?', (candle_date,))
    stored_times = {row[0] for row in cursor.fetchall()}
    conn.close()

    candidate = datetime.strptime(f'{candle_date} 09:15', '%Y-%m-%d %H:%M')
    end = datetime.strptime(f'{candle_date} {before_time}', '%Y-%m-%d %H:%M')
    while candidate < end:
        candle_time = candidate.strftime('%H:%M')
        if candle_time not in stored_times:
            return candle_time
        candidate += timedelta(minutes=5)
    return before_time


def fetch_all():
    """Return all stored candles — useful for debugging."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM nifty_candles ORDER BY date, time')
    rows = cursor.fetchall()
    conn.close()
    return rows


def fetch_candles_for_csv():
    """Yield CSV fields in database order without loading all rows into RAM."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT date, time, co_diff
            FROM nifty_candles
            ORDER BY date, time, id
        ''')
        for candle_date, candle_time, co_diff in cursor:
            yield {"date": candle_date, "time": candle_time, "co_diff": co_diff}
    finally:
        conn.close()
