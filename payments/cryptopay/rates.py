# payments/cryptopay/rates.py
from __future__ import annotations
import time
from decimal import Decimal
import httpx, certifi

_TTL_SECONDS = 30
_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# Маппинг монет -> CoinGecko IDs
_CG_IDS = {
    "BTC":  ["bitcoin"],
    "ETH":  ["ethereum"],
    "LTC":  ["litecoin"],
    "TRX":  ["tron"],
    "BNB":  ["binancecoin"],
    "TON":  ["toncoin", "the-open-network"],  # на всякий случай два варианта
    "USDC": ["usd-coin"],
    "USDT": ["tether"],                       # нужен для опоры в USD
    "XMR":  ["monero"],
}

_cache: dict[str, tuple[float, Decimal]] = {}  # {asset: (ts, rate)}

async def _from_coingecko(asset: str) -> Decimal:
    """
    Берём USD-цену asset и USD-цену USDT (tether), затем rate = asset_usd / usdt_usd.
    """
    ids = _CG_IDS.get(asset.upper(), [])
    if not ids:
        raise RuntimeError(f"No CoinGecko id mapping for {asset}")

    # всегда включаем tether для нормализации к USDT
    need_ids = list(dict.fromkeys(ids + ["tether"]))  # unique preserve order
    params = {"ids": ",".join(need_ids), "vs_currencies": "usd"}

    async with httpx.AsyncClient(timeout=8.0, verify=certifi.where()) as client:
        r = await client.get(
            _COINGECKO_URL,
            params=params,
            headers={"User-Agent": "rate-fetcher/1.0"}
        )
        r.raise_for_status()
        data = r.json()

    # найдём usd-цену для первого удачного ID монеты
    asset_usd = None
    for cid in ids:
        v = data.get(cid, {})
        if "usd" in v:
            asset_usd = Decimal(str(v["usd"]))
            break

    usdt_usd = Decimal(str(data.get("tether", {}).get("usd", 1)))
    if asset_usd is None:
        raise RuntimeError(f"CoinGecko: no usd price for {asset}")

    # asset/USDT ~ asset_usd / usdt_usd
    return asset_usd / (usdt_usd or Decimal("1"))

async def get_rate_usdt(asset: str) -> Decimal:
    """
    Возвращает Decimal-цену 1 единицы asset в USDT.
    USDT/USDC -> 1.
    Кэш 30 секунд. Берём курс только с CoinGecko.
    """
    a = asset.upper()
    if a in ("USDT", "USDC"):
        return Decimal("1")

    now = time.time()
    if a in _cache and (now - _cache[a][0]) <= _TTL_SECONDS:
        return _cache[a][1]

    price = await _from_coingecko(a)
    _cache[a] = (now, price)
    return price