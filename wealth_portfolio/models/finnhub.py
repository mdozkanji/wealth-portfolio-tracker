"""Tiny Finnhub client (stock quotes). Plain functions, easy to mock in tests.

Finnhub has no batch quote endpoint: one request per symbol (`GET /quote`).
The free plan allows 60 calls per minute and covers US-listed stocks, quoted in USD.
"""
import logging

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://finnhub.io/api/v1'


class FinnhubError(Exception):
    """Raised when NO quote can be obtained at all (missing or rejected API key)."""


def fetch_quotes(symbols, api_key, base_url=DEFAULT_BASE_URL, timeout=10):
    """Return ``(prices, errors)``: ``{symbol: price}`` and ``{symbol: reason}``.

    Every requested symbol ends up in exactly one of the two dicts, so the caller
    can tell the user precisely what failed. Only a missing or rejected key raises:
    those problems make every other symbol pointless to try.

    The key travels in the ``X-Finnhub-Token`` header, never in the URL, so it
    cannot leak into access logs or tracebacks.
    """
    if not api_key:
        raise FinnhubError("Finnhub API key is not configured")
    symbols = sorted(set(symbols))
    prices, errors = {}, {}
    headers = {'X-Finnhub-Token': api_key, 'accept': 'application/json'}

    def fail_remaining(from_index, reason):
        for symbol in symbols[from_index:]:
            errors[symbol] = reason

    with requests.Session() as session:
        for index, symbol in enumerate(symbols):
            try:
                response = session.get(
                    f"{base_url.rstrip('/')}/quote", params={'symbol': symbol},
                    headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                # Network trouble: no point hammering on, report the rest as failed.
                fail_remaining(index, f"network error ({exc.__class__.__name__})")
                break
            if response.status_code == 401:
                raise FinnhubError("Finnhub rejected the API key (HTTP 401)")
            if response.status_code == 429:
                fail_remaining(index, "rate limited by Finnhub (HTTP 429), will retry next run")
                break
            if response.status_code == 403:
                errors[symbol] = "not available on the free Finnhub plan (HTTP 403)"
                continue
            if response.status_code != 200:
                errors[symbol] = f"Finnhub answered HTTP {response.status_code}"
                continue
            try:
                payload = response.json()
            except ValueError:
                errors[symbol] = "Finnhub returned invalid JSON"
                continue
            # Finnhub answers 200 with all zeros for an unknown symbol, so 0 means "no quote".
            price = payload.get('c') if isinstance(payload, dict) else None
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
                errors[symbol] = "no quote (unknown symbol or no trading data)"
                continue
            prices[symbol] = float(price)
    return prices, errors
