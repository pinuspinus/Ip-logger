import json

from database.db_api import get_connection

def save_invoice(*, invoice_id: str, user_telegram_id: int, amount: float, asset: str,
                 status: str, payload: str | None):
    conn = get_connection()
    cur = conn.cursor()

    # найти/создать пользователя по telegram_id и получить его DB id
    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (user_telegram_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (user_telegram_id,))
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (user_telegram_id,))
        row = cur.fetchone()
    user_id = row["id"]

    cur.execute(
        "INSERT OR REPLACE INTO invoices (invoice_id, user_id, amount, asset, status, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (invoice_id, user_id, amount, asset, status, payload),
    )
    conn.commit()
    conn.close()

def mark_invoice_paid(invoice_id: str) -> bool:
    """
    Ставит status='paid' только если ещё не был оплачен.
    Возвращает True, если статус реально изменился.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE invoices SET status='paid' WHERE invoice_id=? AND status!='paid'",
            (invoice_id,)
        )
        changed = (cur.rowcount == 1)
        conn.commit()
        return changed
    finally:
        conn.close()

def get_invoice(invoice_id: str) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def update_invoice_payload(invoice_id: str, **fields) -> None:
    """
    Мягко обновляет JSON payload в invoices.payload, не меняя схему БД.
    """
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT payload FROM invoices WHERE invoice_id=?", (invoice_id,))
    row = cur.fetchone()

    current = {}
    if row and row["payload"]:
        try:
            current = json.loads(row["payload"])
        except Exception:
            current = {}

    # дописываем/обновляем только переданные поля
    for k, v in fields.items():
        current[k] = v

    cur.execute(
        "UPDATE invoices SET payload=? WHERE invoice_id=?",
        (json.dumps(current, ensure_ascii=False), invoice_id)
    )
    conn.commit()
    conn.close()
