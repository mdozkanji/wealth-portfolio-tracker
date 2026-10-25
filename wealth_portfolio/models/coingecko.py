"""Tiny CoinGecko client. Not an Odoo model: plain functions, easy to mock in tests."""
import logging

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://api.coingecko.com/api/v3'
CHUNK_SIZE = 100  # ids per request, keeps the URL short


class CoinGeckoError(Exception):
    """Raised for any failure to obtain prices (network, rate limit, bad payload)."""


def fetch_simple_prices(coin_ids, vs_currencies, base_url=DEFAULT_BASE_URL, api_key=None, timeout=10):
    """Return {coin_id: {currency: price}} using /simple/price.

    One HTTP request per CHUNK_SIZE coins, however many assets use them.
    """
    coin_ids = sorted(set(coin_ids))
    vs_currencies = sorted(set(vs_currencies))
    if not coin_ids or not vs_currencies:
        return {}
    headers = {'accept': 'application/json'}
    if api_key:
        headers['x-cg-demo-api-key'] = api_key

    prices = {}
    for start in range(0, len(coin_ids), CHUNK_SIZE):
        chunk = coin_ids[start:start + CHUNK_SIZE]
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/simple/price",
                params={'ids': ','.join(chunk), 'vs_currencies': ','.join(vs_currencies)},
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise CoinGeckoError(f"network error ({exc.__class__.__name__})") from exc
        if response.status_code == 429:
            raise CoinGeckoError("rate limited by CoinGecko (HTTP 429), will retry next run")
        if response.status_code != 200:
            raise CoinGeckoError(f"CoinGecko answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CoinGeckoError("CoinGecko returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CoinGeckoError("unexpected CoinGecko response shape")
        prices.update(payload)
    return prices
