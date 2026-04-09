import time
from datetime import datetime
from dateutil.relativedelta import relativedelta


def _is_transient_network_error(err):
    transient_types = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "RemoteProtocolError",
        "NetworkError",
        "TimeoutException",
    }
    if err.__class__.__name__ in transient_types:
        return True

    text = str(err).lower()
    markers = [
        "connection error",
        "temporary failure in name resolution",
        "name resolution",
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "503",
        "502",
        "429",
    ]
    return any(marker in text for marker in markers)


def call_with_network_retry(operation_name, fn, network_config, log):
    """Retry transient network operations with exponential backoff."""
    max_attempts = network_config["openai_retry_max_attempts"]
    base_sleep = network_config["openai_retry_base_seconds"]
    max_sleep = network_config["openai_retry_max_seconds"]

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as err:
            if (not _is_transient_network_error(err)) or attempt == max_attempts:
                raise
            sleep_seconds = min(max_sleep, base_sleep * (2 ** min(attempt - 1, 6)))
            log(
                "Transient network error during %s (attempt %s/%s): %s. Retrying in %.1f seconds."
                % (operation_name, attempt, max_attempts, err, sleep_seconds)
            )
            time.sleep(sleep_seconds)


def resolve_cutoff_date(lookback_config):
    """Return None for default behavior, or a configured absolute cutoff date."""
    years = lookback_config["years"]
    months = lookback_config["months"]
    days = lookback_config["days"]

    if years == 0 and months == 0 and days == 0:
        return None

    return datetime.now() - relativedelta(years=years, months=months, days=days)
