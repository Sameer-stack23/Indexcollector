"""Time helpers for the Indian market."""

from datetime import datetime, timedelta, timezone

# India does not observe daylight-saving time, so a fixed UTC+05:30 offset is
# reliable and works on Windows installations without the optional tzdata data.
MARKET_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


def market_now():
    """Return the current time in the NSE/Angel One market timezone."""
    return datetime.now(MARKET_TIMEZONE)


def split_candle_timestamp(timestamp):
    """Return an ISO date and HH:MM time from an Angel One candle timestamp."""
    parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    else:
        parsed = parsed.astimezone(MARKET_TIMEZONE)
    return parsed.date().isoformat(), parsed.strftime("%H:%M")
