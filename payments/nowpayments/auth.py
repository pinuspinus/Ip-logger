# payments/nowpayments/auth.py
import time
from typing import Optional, TypedDict
from .client import api_post
from .config import (
    NOWPAYMENTS_AUTH_TOKEN as CFG_TOKEN,
    NOWPAYMENTS_LOGIN_EMAIL,
    NOWPAYMENTS_LOGIN_PASSWORD,
)

class TokenCache(TypedDict):
    value: Optional[str]
    ts: float

_TOKEN_CACHE: TokenCache = {"value": None, "ts": 0.0}
_TOKEN_TTL_SECONDS = 23 * 60 * 60  # ~23 часа

async def _fetch_token_from_api() -> str:
    if not NOWPAYMENTS_LOGIN_EMAIL or not NOWPAYMENTS_LOGIN_PASSWORD:
        raise RuntimeError("NOWPayments auth: set NOWPAYMENTS_LOGIN_EMAIL and NOWPAYMENTS_LOGIN_PASSWORD")
    # ВАЖНО: тело должно быть JSON c email/password
    data = await api_post("/auth", {
        "email": NOWPAYMENTS_LOGIN_EMAIL,
        "password": NOWPAYMENTS_LOGIN_PASSWORD,
    })
    token = data.get("token")
    if not token:
        raise RuntimeError(f"NOWPayments auth: empty token in response: {data}")
    return str(token)

async def get_bearer_token(force: bool = False) -> str:
    # 1) Если токен задан руками через .env — используем его
    if CFG_TOKEN:
        return str(CFG_TOKEN)

    # 2) Кэш
    now = time.time()
    cached = _TOKEN_CACHE["value"]
    ts = float(_TOKEN_CACHE["ts"])
    if (not force) and cached and (now - ts) < _TOKEN_TTL_SECONDS:
        return cached

    # 3) Запрашиваем новый через /auth
    token = await _fetch_token_from_api()
    _TOKEN_CACHE["value"] = token
    _TOKEN_CACHE["ts"] = now
    return token