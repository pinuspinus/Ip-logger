# payments/nowpayments/service.py
from __future__ import annotations
from typing import Optional, Dict, Any

from .client import api_post, api_get
from .models import (
    PaymentResponse, MinAmountResponse,
    InvoiceResponse, InvoicePaymentResponse
)
from .repository import (
    save_nowp_draft,
    update_nowp_payment_details,
)
from .auth import get_bearer_token


# ---------- список платежей по invoiceId с автоподстановкой Bearer ----------

async def list_payments_by_invoice(iid: int | str, limit: int = 10, page: int = 0):
    """
    GET /payment/?invoiceId=...
    Сам получает/кэширует Bearer-токен через /auth.
    При 401/403 принудительно обновляет токен и повторяет запрос.
    """
    bearer = await get_bearer_token()
    extra = {"Authorization": f"Bearer {bearer}"}
    params = {
        "invoiceId": str(iid),
        "limit": int(limit),
        "page": int(page),
        "orderBy": "desc",
        "sortBy": "created_at",
    }
    try:
        return await api_get("/payment/", params=params, extra_headers=extra)
    except Exception as e:
        # опционально: попытка рефреша при 401/403
        try:
            from httpx import HTTPStatusError
            if isinstance(e, HTTPStatusError) and e.response.status_code in (401, 403):
                fresh = await get_bearer_token(force=True)
                extra["Authorization"] = f"Bearer {fresh}"
                return await api_get("/payment/", params=params, extra_headers=extra)
        except Exception:
            pass
        raise


async def get_payment_status(payment_id: str):
    # /payment/{id}: достаточно x-api-key (Bearer не обязателен)
    return await api_get(f"/payment/{payment_id}")


# ---------- вспомогательное ----------

def _opt_bool(val: Optional[bool]) -> Optional[bool]:
    return bool(val) if isinstance(val, bool) else None


# ---------- публичный API сервиса ----------

async def get_min_amount(currency_from: str, currency_to: str) -> MinAmountResponse:
    data = await api_get("/min-amount", params={
        "currency_from": currency_from,
        "currency_to": currency_to
    })
    return MinAmountResponse(**data)


async def create_payment(
    amount: float,
    currency: str,
    order_id: str,
    ipn_url: str,
    pay_currency: Optional[str] = None,
    is_fixed_rate: Optional[bool] = None,
    is_fee_paid_by_user: Optional[bool] = None,
) -> PaymentResponse:
    payload: Dict[str, Any] = {
        "price_amount": amount,
        "price_currency": currency,
        "order_id": order_id,
        "ipn_callback_url": ipn_url,
    }
    if pay_currency is not None:
        payload["pay_currency"] = pay_currency
    if _opt_bool(is_fixed_rate) is not None:
        payload["is_fixed_rate"] = bool(is_fixed_rate)
    if _opt_bool(is_fee_paid_by_user) is not None:
        payload["is_fee_paid_by_user"] = bool(is_fee_paid_by_user)

    data = await api_post("/payment", payload)
    return PaymentResponse(**data)


async def create_invoice(
    *,
    amount: float,
    price_currency: str,
    order_id: str,
    user_telegram_id: int,
    success_url: str,
    cancel_url: str,
    ipn_url: Optional[str] = None,
    pay_currency: Optional[str] = None,
    is_fixed_rate: Optional[bool] = None,
    is_fee_paid_by_user: Optional[bool] = None,
    order_description: Optional[str] = None,
) -> InvoiceResponse:
    """
    Создаёт hosted-инвойс на стороне NOWPayments и сохраняет «черновик» в нашей БД.
    """
    payload: Dict[str, Any] = {
        "price_amount": amount,
        "price_currency": price_currency,
        "order_id": order_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if ipn_url:
        payload["ipn_callback_url"] = ipn_url
    if pay_currency:
        payload["pay_currency"] = pay_currency
    if _opt_bool(is_fixed_rate) is not None:
        payload["is_fixed_rate"] = bool(is_fixed_rate)
    if _opt_bool(is_fee_paid_by_user) is not None:
        payload["is_fee_paid_by_user"] = bool(is_fee_paid_by_user)
    if order_description:
        payload["order_description"] = order_description

    data = await api_post("/invoice", payload)

    save_nowp_draft(
        order_id=order_id,
        user_telegram_id=user_telegram_id,
        price_amount_usd=float(amount),  # если price_currency != "usd", сконвертируй заранее при желании
        iid=str(data["id"]),
        invoice_url=data["invoice_url"],
    )

    return InvoiceResponse(
        id=data["id"],
        status=data.get("status", "created"),
        order_id=data.get("order_id"),
        price_amount=data["price_amount"],
        price_currency=data["price_currency"],
        pay_currency=data.get("pay_currency"),
        invoice_url=data["invoice_url"],
        success_url=data.get("success_url"),
        cancel_url=data.get("cancel_url"),
        is_fixed_rate=data.get("is_fixed_rate"),
        is_fee_paid_by_user=data.get("is_fee_paid_by_user"),
    )


async def create_payment_by_invoice(
    *,
    iid: int,
    pay_currency: str,
    order_id: Optional[str] = None,
    order_description: Optional[str] = None,
    customer_email: Optional[str] = None,
    payout_address: Optional[str] = None,
    payout_currency: Optional[str] = None,
    payout_extra_id: Optional[str] = None,
) -> InvoicePaymentResponse:
    """
    Создаёт конкретный платёж под hosted-инвойс (если хочешь зафиксировать монету программно).
    Если передан order_id — сразу прольём детали в локную запись (payment_id/pay_*).
    """
    payload: Dict[str, Any] = {
        "iid": iid,
        "pay_currency": pay_currency,
    }
    if order_description:
        payload["order_description"] = order_description
    if customer_email:
        payload["customer_email"] = customer_email
    if payout_address:
        payload["payout_address"] = payout_address
    if payout_currency:
        payload["payout_currency"] = payout_currency
    if payout_extra_id:
        payload["payout_extra_id"] = payout_extra_id

    data = await api_post("/invoice-payment", payload)
    resp = InvoicePaymentResponse(
        payment_id=data["payment_id"],
        payment_status=data["payment_status"],
        pay_address=data["pay_address"],
        pay_amount=data["pay_amount"],
        pay_currency=data["pay_currency"],
        payin_extra_id=data.get("payin_extra_id"),
        purchase_id=data.get("purchase_id"),
    )

    if order_id:
        update_nowp_payment_details(
            order_id=order_id,
            payment_id=str(resp.payment_id),
            pay_currency=str(resp.pay_currency),
            pay_amount=float(resp.pay_amount),
            extra={
                "payin_extra_id": data.get("payin_extra_id"),
                "purchase_id": data.get("purchase_id"),
            }
        )

    return resp