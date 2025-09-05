# payments/nowpayments/client.py
import httpx
from .config import NOWPAYMENTS_API_BASE, NOWPAYMENTS_API_KEY

class NowPClientError(Exception): ...

def _base_headers() -> dict:
    if not NOWPAYMENTS_API_KEY:
        raise NowPClientError("NOWPAYMENTS_API_KEY is not set")
    return {"x-api-key": str(NOWPAYMENTS_API_KEY), "Content-Type": "application/json"}

async def api_get(path: str, params: dict | None = None, extra_headers: dict | None = None):
    url = f"{NOWPAYMENTS_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    headers = _base_headers()
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v is not None})
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()

async def api_post(path: str, json_body: dict, extra_headers: dict | None = None):
    url = f"{NOWPAYMENTS_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    headers = _base_headers()
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v is not None})
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=json_body, headers=headers)
        r.raise_for_status()
        return r.json()