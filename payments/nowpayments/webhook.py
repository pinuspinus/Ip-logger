# payments/nowpayments/webhook.py
from __future__ import annotations

import os
import json
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_UP

from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse

from bot import bot  # aiogram Bot
from .config import NOWPAYMENTS_IPN_SECRET
from .repository import (
    update_nowp_payment_details,
    mark_nowp_paid,
    get_nowp_invoice,
)
from payments.cryptopay.rates import get_rate_usdt
from database.db_api import add_balance

webhook_router = APIRouter()
log = logging.getLogger("nowpayments.ipn")

# Включи для подробных логов и мягкой реакции на ошибку подписи
DEBUG_IPN = os.getenv("NOWPAYMENTS_IPN_DEBUG", "0") == "1"


def _verify_signature_both_ways(raw_body: bytes, x_sig: str | None) -> tuple[bool, str]:
    """
    Пробуем 2 варианта HMAC-SHA512 подписи с ключом NOWPAYMENTS_IPN_SECRET:
      1) по отсортированному JSON (separators=(',', ':'), sort_keys=True) — как в доке
      2) по raw-телу (на случай, если их сторона подписывает 'как есть')
    Возвращаем (ok, reason).
    """
    if not x_sig or not NOWPAYMENTS_IPN_SECRET:
        return False, "no_header_or_secret"

    # Попробуем распарсить JSON (для варианта 1)
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return False, "bad_json"

    # 1) отсортированный JSON
    try:
        payload_sorted = json.dumps(body, separators=(",", ":"), sort_keys=True)
        digest_sorted = hmac.new(
            NOWPAYMENTS_IPN_SECRET.encode("utf-8"),
            payload_sorted.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        if hmac.compare_digest(digest_sorted, x_sig):
            return True, "sorted_ok"
    except Exception:
        pass

    # 2) HMAC по raw телу
    try:
        digest_raw = hmac.new(
            NOWPAYMENTS_IPN_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        if hmac.compare_digest(digest_raw, x_sig):
            return True, "raw_ok"
    except Exception:
        pass

    return False, "mismatch"


@webhook_router.post("/nowpayments/ipn")
async def nowpayments_ipn(request: Request, x_nowpayments_sig: str = Header(default=None)):
    """
    IPN эндпоинт для кабинета NOWPayments.
    В кабинете укажи: https(s)://<host>/nowpayments/ipn
    """
    raw = await request.body()

    # Полезные логи на время отладки
    if DEBUG_IPN:
        try:
            log.info("IPN HEADERS: %s", dict(request.headers))
            log.info("IPN RAW: %s", raw.decode("utf-8"))
            log.info("IPN SIG: %s", x_nowpayments_sig)
        except Exception:
            pass

    # 1) Проверка подписи (в 2 режимах)
    ok_sig, why = _verify_signature_both_ways(raw, x_nowpayments_sig)
    if not ok_sig:
        if DEBUG_IPN:
            # В дебаге не роняем 400, чтобы видеть дальнейшую обработку/формат
            try:
                data_dbg = json.loads(raw.decode("utf-8"))
            except Exception:
                data_dbg = None
            log.warning("[IPN DEBUG] Signature fail: %s. Parsed: %s", why, data_dbg)
            return JSONResponse({"ok": True, "debug": True, "sig": why})
        return JSONResponse({"ok": False, "error": f"bad_signature:{why}"}, status_code=400)

    # 2) JSON
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        log.warning("NOWP IPN bad_json: %r", raw[:200])
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

    # 3) Основные поля
    order_id        = data.get("order_id") or ""   # это наш invoice_id (order_id) в локальной БД
    status          = (data.get("payment_status") or "").lower()
    payment_id      = data.get("payment_id")
    pay_currency    = (data.get("pay_currency") or "").upper()
    pay_amount      = Decimal(str(data.get("pay_amount") or 0))
    amount_received = Decimal(str(data.get("amount_received") or 0))  # если прислали — используем её

    if not order_id:
        return JSONResponse({"ok": False, "error": "no_order_id"}, status_code=400)

    # 4) Обновим payload деталями платежа (payment_id/pay_currency/pay_amount/extra)
    try:
        extra = {}
        for k in (
            "payin_extra_id", "purchase_id", "amount_received", "invoice_id",
            "outcome_amount", "outcome_currency", "actually_paid"
        ):
            if data.get(k) is not None:
                extra[k] = data[k]

        update_nowp_payment_details(
            order_id=order_id,
            payment_id=str(payment_id) if payment_id else None,
            pay_currency=pay_currency or None,
            pay_amount=float(pay_amount) if pay_amount else None,
            extra=extra or None,
        )
    except Exception as e:
        log.exception("NOWP IPN: failed to update payload for order_id=%s: %s", order_id, e)
        # Возвращаем 200, чтобы NOWP не зацикливал ретраи — но отметим предупреждение
        return JSONResponse({"ok": True, "warning": "payload_update_failed"})

    # 5) Промежуточные статусы — просто подтверждаем приём
    if status not in {"finished", "confirmed", "sending"}:
        return JSONResponse({"ok": True, "status": status})

    # 6) Финальные статусы — идемпотентно зачисляем баланс
    try:
        if mark_nowp_paid(order_id):
            inv = get_nowp_invoice(order_id)
            if not inv:
                return JSONResponse({"ok": False, "error": "invoice_not_found"}, status_code=404)

            # извлекаем tg_id, который сохраняли в payload драфта
            try:
                payload_local = json.loads(inv.get("payload") or "{}")
            except Exception:
                payload_local = {}
            tg_id = payload_local.get("tg")

            if tg_id:
                # Берём максимально точную сумму: сначала amount_received, иначе pay_amount
                amount_for_credit = amount_received if amount_received > 0 else pay_amount
                try:
                    rate = await get_rate_usdt(pay_currency or "USDT")
                    # Округляем вверх до 2 знаков (например, 2.261 -> 2.27)
                    credits = (amount_for_credit * rate).quantize(Decimal("0.01"), rounding=ROUND_UP)

                    # add_balance может принимать Decimal — если нет, оберни в float(credits)
                    add_balance(int(tg_id), credits)

                    # уведомление пользователю
                    try:
                        await bot.send_message(
                            chat_id=int(tg_id),
                            text=(
                                "✅ Оплата получена!\n\n"
                                f"Зачислено: {credits} USDT (экв.)\n"
                                f"Монета: {pay_currency or '—'}, сумма: {amount_for_credit}"
                            )
                        )
                    except Exception as e:
                        log.warning("NOWP IPN: TG notify failed for tg=%s: %s", tg_id, e)

                except Exception as e:
                    log.exception("NOWP IPN: credit failed for order_id=%s: %s", order_id, e)
                    # Не роняем IPN — возвращаем 200, чтобы NOWP не ретраил бесконечно
                    return JSONResponse({"ok": True, "status": status, "credited": False, "err": str(e)})

            return JSONResponse({"ok": True, "status": status, "credited": True})

        # Повторный IPN для уже зачисленного счёта
        return JSONResponse({"ok": True, "status": "already_paid"})

    except Exception as e:
        log.exception("NOWP IPN: fatal for order_id=%s: %s", order_id, e)
        # Лучше вернуть 200, чтобы остановить ретраи на их стороне
        return JSONResponse({"ok": True, "status": status, "credited": False, "fatal": str(e)})