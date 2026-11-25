"""USD amount for a verified transfer, from two independent price feeds.

Natives: Coinbase spot and CoinGecko must agree within 1%; the lower is used.
Stablecoins: both feeds must sit inside 0.99-1.01 (depeg guard), then 1:1.
Anything else (a feed down, disagreement, depeg, stale verification) is a hold,
never a rejection: the deposit passed the on-chain checks and staff credit it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, Context, Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

COINBASE_URL = "https://api.coinbase.com/v2/prices/{symbol}-USD/spot"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_API_KEY_ENV = "COINGECKO_API_KEY"
HTTP_TIMEOUT_SEC = 10.0

COINGECKO_IDS = {
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "TRX": "tron",
    "SOL": "solana",
    "BTC": "bitcoin",
    "USDT": "tether",
    "USDC": "usd-coin",
}
STABLES = frozenset({"USDT", "USDC"})
MAX_NATIVE_SPREAD = Decimal("0.01")
PEG_LOW = Decimal("0.99")
PEG_HIGH = Decimal("1.01")
DUST_USD = Decimal("1.00")
CENT = Decimal("0.01")
_USD_RE = re.compile(r"^\d+\.\d{2}$")
# Wide enough that no uint256 amount times a price ever rounds before ROUND_DOWN.
_EXACT = Context(prec=120)


@dataclass(frozen=True)
class Quote:
    ok: bool
    price: Decimal | None = None
    sources: str = ""
    reason: str | None = None


class PriceFeedError(Exception):
    pass


async def coinbase_price(client: httpx.AsyncClient, symbol: str) -> Decimal:
    resp = await client.get(COINBASE_URL.format(symbol=symbol))
    resp.raise_for_status()
    return _positive(resp.json()["data"]["amount"])


async def coingecko_price(client: httpx.AsyncClient, symbol: str) -> Decimal:
    coin = COINGECKO_IDS[symbol]
    headers = {}
    key = (os.getenv(COINGECKO_API_KEY_ENV) or "").strip()
    if key:
        headers["x-cg-demo-api-key"] = key
    resp = await client.get(
        COINGECKO_URL, params={"ids": coin, "vs_currencies": "usd"}, headers=headers
    )
    resp.raise_for_status()
    return _positive(resp.json()[coin]["usd"])


def _positive(value) -> Decimal:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PriceFeedError("bad price") from exc
    if not price.is_finite() or price <= 0:
        raise PriceFeedError("bad price")
    return price


def decide_quote(
    symbol: str, coinbase: Decimal | None, coingecko: Decimal | None
) -> Quote:
    """Pure rule on two feed values (None = that feed failed)."""
    sources = f"coinbase={coinbase if coinbase is not None else 'down'} coingecko={coingecko if coingecko is not None else 'down'}"
    if coinbase is None or coingecko is None:
        return Quote(ok=False, sources=sources, reason="price_feed_down")
    if symbol in STABLES:
        if not (PEG_LOW <= coinbase <= PEG_HIGH and PEG_LOW <= coingecko <= PEG_HIGH):
            return Quote(ok=False, sources=sources, reason="depeg")
        return Quote(ok=True, price=Decimal("1"), sources=sources)
    low, high = sorted((coinbase, coingecko))
    if (high - low) / low > MAX_NATIVE_SPREAD:
        return Quote(ok=False, sources=sources, reason="price_feeds_disagree")
    return Quote(ok=True, price=low, sources=sources)


class PriceBook:
    """Quotes for one verifier run; each symbol is fetched at most once."""

    def __init__(self) -> None:
        self._cache: dict[str, Quote] = {}

    async def quote(self, symbol: str) -> Quote:
        if symbol not in self._cache:
            self._cache[symbol] = await self._fetch(symbol)
        return self._cache[symbol]

    async def _fetch(self, symbol: str) -> Quote:
        if symbol not in COINGECKO_IDS:
            return Quote(ok=False, reason="no_price_feed")
        cb = cg = None
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            try:
                cb = await coinbase_price(client, symbol)
            except Exception as exc:
                logger.warning(
                    "quicknode: coinbase price failed %s %s", symbol, type(exc).__name__
                )
            try:
                cg = await coingecko_price(client, symbol)
            except Exception as exc:
                logger.warning(
                    "quicknode: coingecko price failed %s %s",
                    symbol,
                    type(exc).__name__,
                )
        return decide_quote(symbol, cb, cg)


def token_amount(raw: int, decimals: int) -> Decimal:
    return Decimal(int(raw)).scaleb(-int(decimals), context=_EXACT)


def usd_cents_string(raw: int, decimals: int, price: Decimal) -> str:
    """Round DOWN to cents; the result always matches ``^\\d+\\.\\d{2}$``."""
    product = _EXACT.multiply(token_amount(raw, decimals), price)
    value = product.quantize(CENT, rounding=ROUND_DOWN, context=_EXACT)
    text = f"{value:.2f}"
    if not _USD_RE.match(text):
        raise ValueError(f"bad usd amount {text!r}")
    return text
