# payments/cryptopay/client.py
import aiohttp
import ssl
import certifi
from .settings import CRYPTO_PAY_API_BASE, CRYPTO_PAY_TOKEN

class CryptoPayClient:
    def __init__(self, session: aiohttp.ClientSession | None = None, timeout: float = 15.0):
        self._session = session
        self._own_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._headers = {
            "Content-Type": "application/json",
            "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN,
        }
        if not CRYPTO_PAY_TOKEN:
            raise RuntimeError("CRYPTO_PAY_TOKEN is not set")

        # 🔐 SSL-контекст на основе certifi
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        # Если у тебя корпоративный/локальный CA, добавь так:
        # self._ssl_context.load_verify_locations(cafile="/путь/к/вашему_CA.pem")

        self._connector = aiohttp.TCPConnector(
            ssl=self._ssl_context,
            force_close=True,           # меньше «висящих» keep-alive — полезно за VPN
            ttl_dns_cache=300,
            limit=50,
            limit_per_host=20,
        )

    async def __aenter__(self):
        if self._own_session:
            self._session = aiohttp.ClientSession(timeout=self._timeout, connector=self._connector)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._own_session and self._session:
            await self._session.close()


    async def create_invoice(
        self,
        *,
        asset: str,
        amount: float,
        description: str,
        payload: str | None = None,
        allow_comments: bool = False,
        allow_anonymous: bool = True,
        expires_in: int | None = None,
    ) -> dict:
        body: dict = {
            "asset": asset,
            "amount": amount,
            "description": description,
            "allow_comments": allow_comments,
            "allow_anonymous": allow_anonymous,
        }
        if payload is not None:
            body["payload"] = payload
        if expires_in:
            body["expires_in"] = int(expires_in)

        async with self._session.post(
            f"{CRYPTO_PAY_API_BASE}/createInvoice",
            headers=self._headers,
            json=body,
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"createInvoice failed: {data}")
            return data["result"]

    async def get_invoices(self, *, invoice_ids: list[str]) -> dict:
        if not invoice_ids:
            raise ValueError("invoice_ids must be a non-empty list")

        params = {"invoice_ids": ",".join(invoice_ids)}
        async with self._session.get(
            f"{CRYPTO_PAY_API_BASE}/getInvoices",
            headers=self._headers,
            params=params,
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"getInvoices failed: {data}")
            return data["result"]
