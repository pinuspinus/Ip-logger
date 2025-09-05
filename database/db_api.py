import sqlite3
from decimal import Decimal, ROUND_UP, ROUND_HALF_UP

DB_NAME = "database/database.db"
def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # чтобы результаты были как словари
    return conn

def add_user(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
        (telegram_id,)
    )

    conn.commit()
    conn.close()


def get_user_id(telegram_id, cursor):
    cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()  # получаем первую строку результата

    if row:
        return row["id"]  # возвращаем id пользователя
    return None  # если пользователя нет


def get_user(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            "id": row["id"],
            "telegram_id": row["telegram_id"],
            "balance": row["balance"],
            "created_at": row["created_at"],
            "banned": row["banned"],
        }
    else:
        return {}

def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    conn.close()

    users = []
    for row in rows:
        users.append({
            "id": row["id"],
            "telegram_id": row["telegram_id"],
            "balance": row["balance"],
            "created_at": row["created_at"],
            "banned": row["banned"],
        })
    return users


def add_link(original_url, short_link, telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    user_id = get_user_id(telegram_id, cursor)
    if user_id is None:
        conn.close()
        return None  # пользователь не найден

    cursor.execute(
        "INSERT INTO links (user_id, link, original_url) VALUES (?, ?, ?)",
        (user_id, short_link, original_url)
    )

    conn.commit()
    conn.close()
    return True

def get_links(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()
    user_id = get_user_id(telegram_id, cursor)
    cursor.execute("SELECT original_url, link, created_at, clicks, max_clicks, short_host FROM links WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "original_url": r["original_url"],
            "link": r["link"],
            "created_at": r["created_at"],
            "clicks": r["clicks"],
            'max_clicks': r["max_clicks"],
            "short_host": r["short_host"]

        } for r in rows
    ]


def get_balance(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return row["balance"]  # возвращаем баланс
    return 0.0  # если пользователя нет


def add_balance(telegram_id, amount):
    # приводим к Decimal с округлением до 2 знаков
    amt = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_UP)

    conn = get_connection()
    cursor = conn.cursor()

    # читаем текущий баланс как Decimal
    cursor.execute("SELECT balance FROM users WHERE telegram_id=?", (telegram_id,))
    row = cursor.fetchone()
    curr = Decimal(str(row["balance"] or "0"))

    new_bal = (curr + amt).quantize(Decimal("0.01"), rounding=ROUND_UP)

    # сохраняем как строку "2.67", чтобы не появлялся хвост
    cursor.execute("UPDATE users SET balance=? WHERE telegram_id=?", (f"{new_bal:.2f}", telegram_id))

    conn.commit()
    conn.close()
    return True


from decimal import Decimal, ROUND_DOWN

def minus_balance(telegram_id: int, amount: Decimal):
    """
    Пытается списать `amount` (Decimal) с баланса пользователя.
    Если средств недостаточно — баланс обнуляется.
    Возвращает кортеж: (ok: bool, new_balance: Decimal|None, err: str|None)
    """
    if amount <= 0:
        return False, None, "Сумма должна быть > 0"

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Проверяем текущий баланс
        cur.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row is None:
            return False, None, "Пользователь не найден"

        current_balance = Decimal(str(row["balance"]))

        if current_balance >= amount:
            # обычное списание
            cur.execute(
                "UPDATE users SET balance = balance - ? WHERE telegram_id = ?",
                (float(amount), telegram_id),  # SQLite принимает float
            )
            new_balance = (current_balance - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            # если не хватает — обнуляем
            cur.execute(
                "UPDATE users SET balance = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
            new_balance = Decimal("0.00")

        conn.commit()
        return True, new_balance, None
    finally:
        conn.close()

def ban_user(telegram_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Проверяем, есть ли пользователь
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None, "Пользователь не найден"

        # Обновляем статус
        cur.execute("UPDATE users SET banned = 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()

        # Получаем обновлённые данные
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        updated = cur.fetchone()
        conn.close()
        return updated, None

    except Exception as e:
        return None, f"Ошибка БД: {e}"


def unban_user(telegram_id):
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Проверяем, есть ли пользователь
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None, "Пользователь не найден"

        # Обновляем статус
        cur.execute("UPDATE users SET banned = 0 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()

        # Получаем обновлённые данные
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        updated = cur.fetchone()
        conn.close()
        return updated, None

    except Exception as e:
        return None, f"Ошибка БД: {e}"


def change_count_clicks(link: str, count: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM links WHERE link = ?", (link,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None, "Ссылка не найдена!"

    # увеличиваем количество разрешённых переходов
    cursor.execute(
        "UPDATE links SET max_clicks = MAX(max_clicks + ?, 0) WHERE link = ?",
        (count, link)
    )
    conn.commit()

    cursor.execute("SELECT max_clicks FROM links WHERE link = ?", (link,))
    row = cursor.fetchone()
    conn.close()

    return link, row["max_clicks"] if row else None




