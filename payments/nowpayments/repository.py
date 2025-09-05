# payments/nowpayments/repository.py
from __future__ import annotations
import json, time
from typing import Optional, Any, Dict
from database.db_api import get_connection

def _ensure_user(cur, user_telegram_id: int) -> int:
    """Вернёт внутренний users.id, создаст пользователя при необходимости."""
    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (user_telegram_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (user_telegram_id,))
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (user_telegram_id,))
        row = cur.fetchone()
    return row["id"]

def _json_merge(base: Optional[str], extra: Dict[str, Any]) -> str:
    """Слить JSON-строку payload с новыми ключами (поверх)."""
    try:
        data = json.loads(base) if base else {}
    except Exception:
        data = {}
    data.update(extra or {})
    return json.dumps(data, ensure_ascii=False)

# --- Сохранение черновика hosted-инвойса NOWPayments ---
def save_nowp_draft(
    *,
    order_id: str,                 # наш ключ в таблице invoices (кладём в invoice_id)
    user_telegram_id: int,
    price_amount_usd: float,       # сумма инвойса в USD
    iid: str,                      # invoice id у NOWPayments (поле id из ответа /invoice)
    invoice_url: str,              # hosted страница оплаты
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        user_id = _ensure_user(cur, user_telegram_id)
        payload = json.dumps({
            "provider": "nowpayments",
            "iid": str(iid),
            "invoice_url": invoice_url,
            "tg": user_telegram_id,
            "ts": int(time.time()),
        }, ensure_ascii=False)

        cur.execute(
            "INSERT OR REPLACE INTO invoices (invoice_id, user_id, amount, asset, status, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, user_id, float(price_amount_usd), "USD", "pending", payload),
        )
        conn.commit()
    finally:
        conn.close()

# --- Доливка деталей платежа в payload (payment_id, валюта, сумма и т.п.) ---
def update_nowp_payment_details(
    *,
    order_id: str,
    payment_id: Optional[str] = None,
    pay_currency: Optional[str] = None,
    pay_amount: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT payload FROM invoices WHERE invoice_id=?", (order_id,))
        row = cur.fetchone()
        if not row:
            return

        additions: Dict[str, Any] = {}
        if payment_id is not None:
            additions["payment_id"] = str(payment_id)
        if pay_currency is not None:
            additions["pay_currency"] = pay_currency.upper()
        if pay_amount is not None:
            additions["pay_amount"] = float(pay_amount)
        if extra:
            additions.update(extra)

        merged = _json_merge(row["payload"], additions)
        cur.execute("UPDATE invoices SET payload=? WHERE invoice_id=?", (merged, order_id))
        conn.commit()
    finally:
        conn.close()

# --- Идемпотентная пометка «оплачено» ---
def mark_nowp_paid(order_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE invoices SET status='paid' WHERE invoice_id=? AND status!='paid'",
            (order_id,),
        )
        changed = (cur.rowcount == 1)
        conn.commit()
        return changed
    finally:
        conn.close()

# --- Получить запись инвойса по нашему order_id ---
def get_nowp_invoice(order_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM invoices WHERE invoice_id=?", (order_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()