# main.py
# Entry point — connects everything and runs the scheduler

import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse

try:
    import schedule
except ImportError:
    schedule = None

from angel_feed import (
    RateLimitError,
    connect_angel,
    fetch_completed_candles,
    fetch_historical_candles,
    fetch_live_quote,
)
from config import (
    COLLECTION_INTERVAL_MINUTES,
    HISTORICAL_BATCH_DAYS,
    RATE_LIMIT_COOLDOWN_MINUTES,
)
from database import fetch_candles_for_csv, fetch_first_missing_candle_time, init_db, save_candle
from export_csv import init_csv, save_to_csv, sync_csv_from_database
from market_time import market_now

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

# Global session object
session = None
MOCK_MODE = False
LIVE_MODE = False
next_fetch_allowed_at = None


def parse_date(value: str) -> date:
    """Parse a command-line date and give argparse a useful error on failure."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD, for example 2025-09-04") from error


def persist_candles(candles, update_csv=True):
    """Save an iterable of candles and return received/new row counts."""
    received = 0
    saved = 0
    for candle in candles:
        received += 1
        if candle is not None and save_candle(candle):
            saved += 1
            if update_csv:
                save_to_csv(candle)
    return received, saved


def import_historical_range(from_date: date, to_date: date, batch_days: int):
    """Import a date range in restart-safe batches, then rebuild the CSV once."""
    global next_fetch_allowed_at, session

    if batch_days < 1:
        raise ValueError("batch_days must be at least 1")

    print(
        f"=== Historical import: {from_date.isoformat()} to {to_date.isoformat()} "
        f"({batch_days}-day batches) ==="
    )
    session = connect_angel()
    if session is None:
        print("Could not connect to Angel One — historical import stopped")
        return

    cursor = from_date
    requested_batches = 0
    saved_total = 0
    while cursor <= to_date:
        batch_end = min(cursor + timedelta(days=batch_days - 1), to_date)
        requested_batches += 1
        try:
            candles = fetch_historical_candles(session, cursor, batch_end)
        except RateLimitError:
            next_fetch_allowed_at = market_now() + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
            print(
                "Angel One rate limit hit. The import is safe to rerun; existing "
                "candles will be skipped."
            )
            break

        if candles is None:
            print("Historical request failed; stopping so it can be retried safely.")
            break
        received, saved = persist_candles(candles, update_csv=False)
        saved_total += saved
        print(
            f"Completed batch {requested_batches}: {cursor.isoformat()} to "
            f"{batch_end.isoformat()} ({received} received, {saved_total} new total)"
        )
        # Release the batch before requesting the next one.  The maximum
        # in-memory API payload is therefore limited to ``batch_days``.
        del candles
        cursor = batch_end + timedelta(days=1)

    # Rebuilding once avoids rewriting the whole row-wise CSV for every candle.
    sync_csv_from_database(fetch_candles_for_csv())
    print(f"Historical import finished: {saved_total} new candle(s) saved")


def generate_mock_candle():
    now = market_now()
    base = 10000.0
    o = base
    h = base + 50.0
    l = base - 50.0
    c = base + 20.0
    return {
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M'),
        'open': o,
        'high': h,
        'low': l,
        'close': c,
        'volume': 1000,
        'hl_diff': h - l,
        'co_diff': c - o
    }


def is_market_open():
    """Check if current time is within market hours."""
    now = market_now()

    # Skip weekends
    if now.weekday() >= 5:
        return False

    # Market hours 09:15 to 15:30
    market_start = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_end   = now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_start <= now <= market_end


def market_session_state():
    """Return before_open, open, after_close, or weekend for the current time."""
    now = market_now()

    if now.weekday() >= 5:
        return "weekend"

    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < market_start:
        return "before_open"
    if now > market_end:
        return "after_close"
    return "open"


def collect():
    """Main collection function — runs every 5 minutes."""
    global next_fetch_allowed_at, session

    if not MOCK_MODE and not is_market_open():
        print(f"Market closed — skipping ({market_now().strftime('%H:%M')})")
        return

    if next_fetch_allowed_at is not None and market_now() < next_fetch_allowed_at:
        wait_seconds = int((next_fetch_allowed_at - market_now()).total_seconds())
        wait_minutes = max(1, wait_seconds // 60)
        print(f"Angel One rate-limit cooldown active - waiting about {wait_minutes} minute(s)")
        return

    print(f"\n--- Running collection at {market_now().strftime('%H:%M')} ---")

    # Reconnect if session is lost
    if session is None and not MOCK_MODE:
        print("No active session — reconnecting...")
        session = connect_angel()

    if session is None and not MOCK_MODE:
        print("Could not connect to Angel One — skipping this cycle")
        return

    # Fetch candle
    try:
        if MOCK_MODE and session is None:
            candles = [generate_mock_candle()]
        elif LIVE_MODE:
            candles = [fetch_live_quote(session)]
        else:
            now = market_now()
            current_boundary = now.replace(second=0, microsecond=0)
            current_boundary -= timedelta(minutes=current_boundary.minute % 5)
            first_missing_time = fetch_first_missing_candle_time(
                now.strftime('%Y-%m-%d'), current_boundary.strftime('%H:%M')
            )
            from_time = datetime.strptime(
                f"{now.strftime('%Y-%m-%d')} {first_missing_time}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=now.tzinfo)
            candles = fetch_completed_candles(session, from_time)
    except RateLimitError:
        next_fetch_allowed_at = market_now() + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
        print(
            "Angel One rate limit hit while fetching data. "
            f"Pausing fetch attempts until {next_fetch_allowed_at.strftime('%H:%M:%S')}."
        )
        return

    if candles is None:
        print("No candle data received — skipping this cycle")
        return

    # SQLite is the only full-data store. The CSV receives only the signed
    # close-minus-open value in the cell matching this candle's date and time.
    # Saving only after the DB accepts a new candle prevents repeat exports.
    if not candles:
        print("No new completed candles available")
        return

    persist_candles(candles)


def main():
    global session

    global MOCK_MODE, LIVE_MODE

    parser = argparse.ArgumentParser(description="IndexCollector")
    parser.add_argument('--mock', action='store_true', help='Run in mock mode (generate fake candles)')
    parser.add_argument('--live', action='store_true', help='Use live quote API instead of historical candles')
    parser.add_argument('--once', action='store_true', help='Run one collection cycle and exit')
    parser.add_argument('--from-date', type=parse_date, metavar='YYYY-MM-DD',
                        help='First date to import (inclusive). Defaults to one year ago.')
    parser.add_argument('--to-date', type=parse_date, metavar='YYYY-MM-DD',
                        help='Last date to import (inclusive). Defaults to yesterday.')
    parser.add_argument('--batch-days', type=int, default=HISTORICAL_BATCH_DAYS,
                        help=f'Days per Angel historical request (default: {HISTORICAL_BATCH_DAYS})')
    args = parser.parse_args()

    if args.live and args.mock:
        parser.error('--live and --mock cannot be used together')
    if (args.from_date or args.to_date or args.batch_days != HISTORICAL_BATCH_DAYS) and (args.live or args.mock):
        parser.error('historical date options cannot be combined with --live or --mock')
    if args.batch_days < 1:
        parser.error('--batch-days must be at least 1')

    if args.mock:
        MOCK_MODE = True
        print('Running in MOCK mode — no Angel API required')

    if args.live:
        LIVE_MODE = True
        print('Running in LIVE quote mode')

    print("=== IndexCollector Starting ===")

    # Initialise database and CSV
    init_db()
    init_csv()

    # Historical mode is intentionally a one-shot import.  It does not depend
    # on current market hours and can be rerun to resume an interrupted range.
    if not LIVE_MODE and not MOCK_MODE:
        today = market_now().date()
        to_date = args.to_date or (today - timedelta(days=1))
        from_date = args.from_date or (to_date - timedelta(days=364))
        if from_date > to_date:
            parser.error('--from-date must not be after --to-date')
        if to_date > today:
            parser.error('--to-date cannot be in the future')
        import_historical_range(from_date, to_date, args.batch_days)
        return

    # Live/mock modes may update a pre-existing database, so make sure their
    # lightweight CSV view starts in sync.
    sync_csv_from_database(fetch_candles_for_csv())

    if schedule is None:
        print("schedule package is not installed. Running one collection cycle once instead.")
        collect()
        return

    # Schedule collection at the configured interval.
    schedule.every(COLLECTION_INTERVAL_MINUTES).minutes.do(collect)

    print("Scheduler running — press Ctrl+C to stop\n")

    # If user asked to run once, do that and exit
    if args.once:
        collect()
        return

    # Keep running until today's market session ends.
    while True:
        if not MOCK_MODE:
            state = market_session_state()

            if state == "weekend":
                print("Market is closed for the weekend - stopping collector")
                break

            if state == "after_close":
                print("Market session ended - stopping collector")
                break

            if state == "before_open":
                print(f"Waiting for market open ({market_now().strftime('%H:%M')})")

        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
