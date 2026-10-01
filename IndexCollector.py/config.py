import importlib.util
import os
from pathlib import Path


def _load_legacy_config():
    legacy_path = Path(__file__).resolve().parent / "config..py"
    if not legacy_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("legacy_config", legacy_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dotenv_values():
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if not dotenv_path.exists():
        return {}

    values = {}
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_legacy = _load_legacy_config()
_dotenv_values = _load_dotenv_values()


def _get_value(*names, default=""):
    for name in names:
        env_value = os.getenv(name)
        if env_value not in (None, ""):
            return env_value

        if name in _dotenv_values and _dotenv_values[name] not in (None, ""):
            return _dotenv_values[name]

        if _legacy is not None:
            legacy_value = getattr(_legacy, name, None)
            if legacy_value not in (None, ""):
                return legacy_value

    return default


ANGEL_API_KEY = _get_value("ANGEL_API_KEY", "API_KEY")
ANGEL_CLIENT_ID = _get_value("ANGEL_CLIENT_ID", "CLIENT_ID")
ANGEL_PASSWORD = _get_value("ANGEL_PASSWORD", "PASSWORD")
ANGEL_TOTP_SECRET = _get_value("ANGEL_TOTP_SECRET", "TOTP_SECRET")

# Backward-compatible aliases for older modules/config files.
API_KEY = ANGEL_API_KEY
CLIENT_ID = ANGEL_CLIENT_ID
PASSWORD = ANGEL_PASSWORD
TOTP_SECRET = ANGEL_TOTP_SECRET

NIFTY_TOKEN = _get_value("ANGEL_NIFTY_TOKEN", "NIFTY_TOKEN", default="99926000")
NIFTY_SYMBOL = _get_value("ANGEL_NIFTY_SYMBOL", "NIFTY_SYMBOL", default="Nifty 50")
EXCHANGE = _get_value("ANGEL_EXCHANGE", "EXCHANGE", default="NSE")
INTERVAL = _get_value("ANGEL_INTERVAL", "INTERVAL", default="FIVE_MINUTE")

DB_NAME = _get_value("INDEX_COLLECTOR_DB", "DB_NAME", default="market.db")


def _get_int_value(*names, default):
    value = _get_value(*names, default=str(default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


COLLECTION_INTERVAL_MINUTES = _get_int_value(
    "INDEX_COLLECTOR_INTERVAL_MINUTES",
    "COLLECTION_INTERVAL_MINUTES",
    default=5,
)
RATE_LIMIT_COOLDOWN_MINUTES = _get_int_value(
    "INDEX_COLLECTOR_RATE_LIMIT_COOLDOWN_MINUTES",
    "RATE_LIMIT_COOLDOWN_MINUTES",
    default=10,
)

# Minimum time between all Angel One API requests (login, quote, or candle).
# 60 seconds is deliberately conservative; at a 5-minute collection interval
# it stays far below the provider's nominal per-minute quota and helps avoid
# intermittent NSE historical-endpoint throttling.
# Set INDEX_COLLECTOR_REQUEST_GAP_SECONDS=60 in .env to override it.
ANGEL_REQUEST_GAP_SECONDS = _get_int_value(
    "INDEX_COLLECTOR_REQUEST_GAP_SECONDS",
    "ANGEL_REQUEST_GAP_SECONDS",
    default=60,
)

# Angel One limits the amount of intraday history returned in one request.
# Keeping this modest also makes a long import resumable: stored candles are
# skipped safely on the next run.
HISTORICAL_BATCH_DAYS = _get_int_value(
    "INDEX_COLLECTOR_HISTORICAL_BATCH_DAYS",
    "HISTORICAL_BATCH_DAYS",
    default=30,
)
