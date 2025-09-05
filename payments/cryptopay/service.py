# payments/cryptopay/service.py
import json, time, aiohttp
from .client import CryptoPayClient
from .settings import CRYPTO_DESC, CRYPTO_EXPIRES_IN
from .repository import save_invoice, mark_invoice_paid, get_invoice, update_invoice_payload  # 👈 ДОБАВИЛ get_invoice

# payments/shared_credit.py
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from database.db_api import add_balance
from payments.cryptopay.rates import get_rate_usdt

async def credit_if_first_time(*, invoice_id: str, tg_id: int, amount: float, asset: str) -> bool:
    """
    Идемпотентное зачисление (без изменения схемы БД).
    Возвращает True, если зачислили (первый раз), иначе False.
    """
    if not mark_invoice_paid(invoice_id):
        return False  # уже было оплачено — выходим

    rate = await get_rate_usdt(asset)
    credits = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    add_balance(tg_id, credits)
    return True

async def create_topup_invoice(*, user_id: int, amount: float, asset: str) -> tuple[str, str]:
    """
    user_id здесь — это telegram_id пользователя (из Telegram).
    """
    payload = json.dumps({
        "provider": "cryptobot",  # 👈 видно, откуда инвойс
        "user_id": user_id,       # 👈 кладём tg-id
        "amount": amount,
        "asset": asset,
        "ts": int(time.time())
    })
    async with CryptoPayClient() as client:
        inv = await client.create_invoice(
            asset=asset,
            amount=amount,
            description=f"{CRYPTO_DESC}: {amount} {asset}",
            payload=payload,
            allow_comments=False,
            allow_anonymous=True,
            expires_in=CRYPTO_EXPIRES_IN or None
        )

    # пишем инвойс в БД
    save_invoice(
        invoice_id=inv["invoice_id"],
        user_telegram_id=user_id,
        amount=amount,
        asset=asset,
        status="pending",
        payload=payload
    )

    return inv["invoice_id"], inv["pay_url"]

async def check_and_credit(invoice_id: str, *, credit_callback=None):
    """
    Тянет статус у CryptoBot и:
      - при remote 'paid' кредитует РОВНО ОДИН РАЗ (по флагу payload.credited),
        затем выставляет credited=true и mark_invoice_paid(...).
    Возвращает: 'not_found' | 'active' | 'paid' | 'already_paid' | 'expired'
    """
    async with CryptoPayClient() as client:
        res = await client.get_invoices(invoice_ids=[invoice_id])

    items = res.get("items") or []
    if not items:
        return "not_found"

    item   = items[0]
    status = (item.get("status") or "").lower()     # 'active' | 'paid' | 'expired'
    amount = float(item["amount"])
    asset  = item["asset"]

    # --- извлекаем tg_id ---
    tg_id = None
    try:
        pr = json.loads(item.get("payload") or "{}")
        if pr.get("user_id") is not None:
            tg_id = int(pr["user_id"])
    except Exception:
        pass

    local = get_invoice(invoice_id) or {}
    lp = {}
    if local.get("payload"):
        try:
            lp = json.loads(local["payload"])
        except Exception:
            lp = {}
    if tg_id is None and lp.get("user_id") is not None:
        tg_id = int(lp["user_id"])

    already_credited = bool(lp.get("credited") is True)

    if status == "active":
        return "active"

    if status == "expired":
        # локально можно не обязательно ставить expired — по желанию
        return "expired"

    if status == "paid":
        # если ещё не кредитовали — кредитуем сейчас
        just_credited = False
        if not already_credited and tg_id is not None:
            # либо используем внешний callback, либо кредитуем прямо тут
            if credit_callback is not None:
                # callback должен начислить баланс; после этого ставим флаг
                await credit_callback(user_id=tg_id, amount=amount, asset=asset)
            else:
                # кредитуем на месте
                rate = await get_rate_usdt(asset)
                credits = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                add_balance(int(tg_id), credits)

            update_invoice_payload(
                invoice_id,
                credited=True,
                credited_ts=int(time.time()),
                src_asset=asset,
                src_amount=float(amount),
            )
            just_credited = True

        # статус всегда выставляем (идемпотентно)
        mark_invoice_paid(invoice_id)

        return "paid" if just_credited else "already_paid"

    # на всякий
    return "not_found"