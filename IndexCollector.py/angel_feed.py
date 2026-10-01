# angel_feed.py
# Handles connection to Angel One and fetching candle data

import pyotp
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from config import (
    ANGEL_API_KEY,
    ANGEL_CLIENT_ID,
    ANGEL_PASSWORD,
    ANGEL_REQUEST_GAP_SECONDS,
    ANGEL_TOTP_SECRET,
)
from config import NIFTY_TOKEN, NIFTY_SYMBOL, EXCHANGE, INTERVAL
from market_time import market_now, split_candle_timestamp


class RateLimitError(Exception):
    """Raised when Angel One rejects a request because of access-rate limits."""


# A single shared timer ensures login, quote, and candle requests cannot be
# sent too close together, even if more callers are added later.
_request_lock = threading.Lock()
_last_request_at = None


def _wait_for_request_slot():
    """Wait until the configured minimum gap before the next Angel API call."""
    global _last_request_at

    with _request_lock:
        now = time.monotonic()
        if _last_request_at is not None:
            remaining = ANGEL_REQUEST_GAP_SECONDS - (now - _last_request_at)
            if remaining > 0:
                print(f"Waiting {remaining:.0f} second(s) before the next Angel API request...")
                time.sleep(remaining)

        # Record immediately before sending, so request start times are spaced.
        _last_request_at = time.monotonic()


def _is_rate_limit_error(error):
    message = str(error).lower()
    return (
        "exceeding access rate" in message
        or "too many requests" in message
        or "ab1021" in message
    )


def _raise_if_rate_limited(data):
    if _is_rate_limit_error(data):
        raise RateLimitError(str(data))


@contextmanager
def direct_requests():
    """Make requests ignore system proxy settings inside this block only."""
    import requests

    original_merge = requests.sessions.Session.merge_environment_settings

    def merge_without_proxy(self, url, proxies, stream, verify, cert):
        settings = original_merge(self, url, proxies or {}, stream, verify, cert)
        settings["proxies"] = {}
        return settings

    requests.sessions.Session.merge_environment_settings = merge_without_proxy
    try:
        yield
    finally:
        requests.sessions.Session.merge_environment_settings = original_merge


def _get_smart_connect():
    with direct_requests():
        try:
            from SmartApi import SmartConnect
        except ImportError:
            try:
                from smartapi import SmartConnect
            except ImportError:
                return None
    return SmartConnect


def connect_angel():
    """Connect to Angel One and return authenticated session."""
    if not ANGEL_API_KEY or not ANGEL_CLIENT_ID or not ANGEL_PASSWORD or not ANGEL_TOTP_SECRET:
        print("Angel One credentials are not configured. Set environment variables before running.")
        return None

    SmartConnect = _get_smart_connect()
    if SmartConnect is None:
        print("Angel One SDK is not installed. Install smartapi-python and pyotp to continue.")
        return None

    try:
        with direct_requests():
            obj = SmartConnect(api_key=ANGEL_API_KEY)
            totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
            _wait_for_request_slot()
            data = obj.generateSession(ANGEL_CLIENT_ID, ANGEL_PASSWORD, totp)

        if data['status'] == True:
            print("Connected to Angel One successfully")
            return obj
        else:
            print("Connection failed:", data)
            return None

    except Exception as e:
        print("Error connecting to Angel One:", e)
        return None


def _first_value(row, *names, default=None):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return default


def fetch_live_quote(obj):
    """Fetch a live *session* quote, not an interval candle.

    Angel's quote API supplies session open/high/low and a previous close.  Its
    LTP is the current price, so that is the only sensible value to expose as
    ``close`` in this snapshot.  Use ``fetch_candle`` for 5-minute OHLC data.
    """
    try:
        with direct_requests():
            _wait_for_request_slot()
            data = obj.getMarketData("FULL", {EXCHANGE: [NIFTY_TOKEN]})

        _raise_if_rate_limited(data)

        if not (data.get("status") is True or data.get("success") is True):
            print("Failed to fetch live quote:", data)
            return None

        fetched = data.get("data", {}).get("fetched", [])
        if not fetched:
            print("No live quote data received:", data)
            return None

        quote = fetched[0]
        now = market_now()

        open_price = _first_value(quote, "open", "openPrice", default=0)
        high_price = _first_value(quote, "high", "highPrice", default=open_price)
        low_price = _first_value(quote, "low", "lowPrice", default=open_price)
        # ``close`` from the quote endpoint is the previous-session close,
        # not a current 5-minute candle close.  Prefer LTP deliberately.
        close_price = _first_value(quote, "ltp", "lastTradedPrice", default=open_price)
        volume = _first_value(quote, "tradeVolume", "volume", default=0)

        result = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "open": float(open_price),
            "high": float(high_price),
            "low": float(low_price),
            "close": float(close_price),
            "volume": int(float(volume)),
            "hl_diff": float(high_price) - float(low_price),
            "co_diff": float(close_price) - float(open_price),
        }
        print(
            f"Live session quote fetched (not a 5-minute candle) -> "
            f"O:{result['open']} H:{result['high']} L:{result['low']} "
            f"LTP:{result['close']} V:{result['volume']}"
        )
        return result

    except Exception as e:
        if _is_rate_limit_error(e):
            raise RateLimitError(str(e)) from e
        print("Error fetching live quote:", e)
        return None


def fetch_completed_candles(obj, from_time=None):
    """Fetch every final 5-minute candle in the requested range.

    The still-forming candle is excluded.  Supplying the first missing time
    lets the caller repair gaps using one API request after a rate limit.
    """
    try:
        now = market_now()

        # Request a complete, exchange-aligned bar.  The previous code used
        # the current minute (for example 13:43), which asks Angel for a
        # non-aligned range and can cause it to return stale/repeated data.
        current_boundary = now.replace(second=0, microsecond=0)
        current_boundary -= timedelta(minutes=current_boundary.minute % 5)
        if from_time is None:
            from_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        to_time = current_boundary

        if from_time >= to_time:
            return []

        params = {
            "exchange":     EXCHANGE,
            "symboltoken":  NIFTY_TOKEN,
            "interval":     INTERVAL,
            "fromdate":     from_time.strftime("%Y-%m-%d %H:%M"),
            "todate":       to_time.strftime("%Y-%m-%d %H:%M")
        }

        with direct_requests():
            _wait_for_request_slot()
            data = obj.getCandleData(params)

        _raise_if_rate_limited(data)

        if data.get('status') is True or data.get('success') is True:
            candles = data.get('data') or []
            if not candles:
                print("No completed candle data received")
                return []

            # Angel may return a wider range than requested.  Keep only
            # requested, completed bars; the current bar is never stored.
            completed = [
                row for row in candles
                if len(row) >= 6
                and (from_time.date().isoformat(), from_time.strftime("%H:%M"))
                <= split_candle_timestamp(row[0])
                < (to_time.date().isoformat(), to_time.strftime("%H:%M"))
            ]
            if not completed:
                print("No completed candle in Angel One response")
                return []

            results = []
            for candle in sorted(completed, key=lambda row: split_candle_timestamp(row[0])):
                candle_date, candle_time = split_candle_timestamp(candle[0])
                open_price, high_price, low_price, close_price = map(float, candle[1:5])
                if not (low_price <= min(open_price, close_price)
                        and max(open_price, close_price) <= high_price):
                    print(f"Invalid OHLC candle received; skipping: {candle}")
                    continue
                results.append({
                    "date": candle_date, "time": candle_time,
                    "open": open_price, "high": high_price,
                    "low": low_price, "close": close_price,
                    "volume": candle[5],
                    "hl_diff": high_price - low_price,
                    "co_diff": close_price - open_price,
                })
            print(f"Fetched {len(results)} completed candle(s) from Angel One")
            return results
        else:
            print("Failed to fetch candle:", data)
            return None

    except Exception as e:
        if _is_rate_limit_error(e):
            raise RateLimitError(str(e)) from e
        print("Error fetching candle:", e)
        return None


def fetch_historical_candles(obj, from_date: date, to_date: date):
    """Fetch all interval candles between two inclusive calendar dates.

    This function intentionally does not use ``market_now`` or exclude the
    current candle.  It is for completed, past trading sessions only.  Callers
    should split long ranges into provider-safe batches (30 days by default).
    """
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")

    try:
        params = {
            "exchange": EXCHANGE,
            "symboltoken": NIFTY_TOKEN,
            "interval": INTERVAL,
            "fromdate": f"{from_date.isoformat()} 09:15",
            "todate": f"{to_date.isoformat()} 15:30",
        }
        print(
            "Requesting historical candles "
            f"from {params['fromdate']} to {params['todate']}"
        )

        with direct_requests():
            _wait_for_request_slot()
            data = obj.getCandleData(params)

        _raise_if_rate_limited(data)
        if not (data.get("status") is True or data.get("success") is True):
            print("Failed to fetch historical candles:", data)
            return None

        raw_candles = data.get("data") or []
        print(f"Received {len(raw_candles)} raw historical candle(s) from Angel One")

        # Keep Angel's one request payload, but do not create a second list of
        # parsed dictionaries.  The importer consumes this iterator one candle
        # at a time and releases the payload after each configured batch.
        def parsed_candles():
            for candle in raw_candles:
                if len(candle) < 6:
                    continue
                candle_date, candle_time = split_candle_timestamp(candle[0])
                if not from_date.isoformat() <= candle_date <= to_date.isoformat():
                    continue

                open_price, high_price, low_price, close_price = map(float, candle[1:5])
                if not (low_price <= min(open_price, close_price)
                        and max(open_price, close_price) <= high_price):
                    print(f"Invalid OHLC candle received; skipping: {candle}")
                    continue
                yield {
                    "date": candle_date, "time": candle_time,
                    "open": open_price, "high": high_price,
                    "low": low_price, "close": close_price,
                    "volume": candle[5],
                    "hl_diff": high_price - low_price,
                    "co_diff": close_price - open_price,
                }

        return parsed_candles()
    except Exception as e:
        if _is_rate_limit_error(e):
            raise RateLimitError(str(e)) from e
        print("Error fetching historical candles:", e)
        return None


def fetch_candle(obj):
    """Backward-compatible helper returning the newest completed candle."""
    candles = fetch_completed_candles(obj, market_now() - timedelta(minutes=5))
    return candles[-1] if candles else None
